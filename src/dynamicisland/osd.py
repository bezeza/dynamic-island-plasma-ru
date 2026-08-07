"""Volume, brightness and the other little on-screen displays.

Plasma draws these itself: pressing a volume key makes plasma-pa call
``org.kde.osdService.volumeChanged`` on plasmashell, which pops up a grey box in
the middle of the screen.  The name belongs to plasmashell and cannot be taken
the way the notification service can — so instead the island listens in.

The calls keep happening even when Plasma's own OSD is switched off
(``plasmarc``, ``[OSD] Enabled=false``, which ``install.sh`` sets), so turning
that off and watching the bus is what moves the display into the island without
showing everything twice.

Listening in needs a monitor connection, which is a one-way street: a monitor
can receive but not send, hence the private connection of its own.
"""

from __future__ import annotations

import dbus
import dbus.lowlevel

from PySide6.QtCore import QObject, Signal

INTERFACE = "org.kde.osdService"
MATCH = f"type='method_call',interface='{INTERFACE}'"

#: Volume levels, from silent to loud, as the icon theme names them.
_VOLUME_ICONS = ("audio-volume-muted", "audio-volume-low",
                 "audio-volume-medium", "audio-volume-high")


def _volume_icon(percent: int) -> str:
    if percent <= 0:
        return _VOLUME_ICONS[0]
    if percent < 34:
        return _VOLUME_ICONS[1]
    if percent < 67:
        return _VOLUME_ICONS[2]
    return _VOLUME_ICONS[3]


class OsdWatcher(QObject):
    """Turns the calls Plasma would have drawn into one Qt signal.

    ``kind`` names what happened so the controller can put a word to it;
    ``percent`` is -1 when there is nothing to fill a bar with.
    """

    #: (kind, icon, percent, text)
    shown = Signal(str, str, int, str)
    hidden = Signal()

    def __init__(self, parent: QObject | None = None, claim: bool = True) -> None:
        super().__init__(parent)
        self._bus = None
        self.watching = False
        if not claim:
            return

        try:
            # A private connection: once it becomes a monitor it can no longer
            # be used for anything else.
            self._bus = dbus.bus.BusConnection(dbus.bus.BusConnection.TYPE_SESSION)
            # A private connection kills the process when the bus drops it —
            # which is exactly what happens to monitors. The island must
            # survive losing this, it is only the sugar on top.
            self._bus.set_exit_on_disconnect(False)
            self._bus.call_blocking(
                "org.freedesktop.DBus", "/org/freedesktop/DBus",
                "org.freedesktop.DBus.Monitoring", "BecomeMonitor", "asu",
                ([MATCH], dbus.UInt32(0)),
            )
            self._bus.add_message_filter(self._on_message)
            self.watching = True
        except dbus.DBusException as error:
            print(f"[dynamic-island] cannot watch the OSD: {error}")

    # -- incoming ---------------------------------------------------------
    def _on_message(self, _connection, message) -> int:
        """Take the copy and stop.

        Handing the message on would let dbus-python's dispatcher treat it as a
        call to us and answer it — and a monitor that sends anything is thrown
        off the bus by the daemon, which is why anything less than HANDLED gets
        exactly one on-screen display and then silence.
        """
        if (
            isinstance(message, dbus.lowlevel.MethodCallMessage)
            and message.get_interface() == INTERFACE
        ):
            try:
                self._dispatch(message.get_member(), message.get_args_list())
            except Exception as error:  # noqa: BLE001 - never break the filter
                print(f"[dynamic-island] bad OSD message: {error}")
        return dbus.lowlevel.HANDLER_RESULT_HANDLED

    def _dispatch(self, member: str, arguments: list) -> None:
        def number(index: int = 0) -> int:
            return int(arguments[index]) if len(arguments) > index else -1

        def text(index: int = 0) -> str:
            return str(arguments[index]) if len(arguments) > index else ""

        if member == "hide":
            self.hidden.emit()

        elif member == "volumeChanged":
            percent = number()
            self.shown.emit("volume", _volume_icon(percent), percent, "")
        elif member == "microphoneVolumeChanged":
            percent = number()
            icon = "audio-input-microphone" if percent > 0 else "audio-input-microphone-muted"
            self.shown.emit("microphone", icon, percent, "")
        elif member == "mediaPlayerVolumeChanged":
            self.shown.emit(
                "volume", text(2) or _volume_icon(number()), number(), text(1)
            )

        elif member == "brightnessChanged":
            self.shown.emit("brightness", "video-display-brightness", number(), "")
        elif member == "screenBrightnessChanged":
            self.shown.emit(
                "brightness", "video-display-brightness", number(), text(2)
            )
        elif member == "keyboardBrightnessChanged":
            self.shown.emit(
                "keyboard", "input-keyboard-brightness", number(), ""
            )

        elif member == "kbdLayoutChanged":
            self.shown.emit("text", "input-keyboard", -1, text())
        elif member == "virtualDesktopChanged":
            self.shown.emit("text", "virtual-desktops", -1, text())
        elif member == "powerProfileChanged":
            self.shown.emit("text", "battery-profile-powersave", -1, text())
        elif member == "showText":
            self.shown.emit("text", text(0), -1, text(1))

        # The switches: Plasma shows an icon, we put a word next to it.
        elif member == "touchpadEnabledChanged":
            self._toggle("input-touchpad", bool(arguments[0]))
        elif member == "wifiEnabledChanged":
            self._toggle("network-wireless", bool(arguments[0]))
        elif member == "bluetoothEnabledChanged":
            self._toggle("preferences-system-bluetooth", bool(arguments[0]))
        elif member == "wwanEnabledChanged":
            self._toggle("network-mobile", bool(arguments[0]))
        elif member == "virtualKeyboardEnabledChanged":
            self._toggle("input-keyboard-virtual", bool(arguments[0]))
        elif member == "powerManagementInhibitedChanged":
            self._toggle("preferences-system-power-management", bool(arguments[0]))

    def _toggle(self, icon: str, enabled: bool) -> None:
        self.shown.emit("on" if enabled else "off", icon, -1, "")
