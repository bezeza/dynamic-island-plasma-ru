"""The org.freedesktop.Notifications service.

We take the well-known name away from whatever owned it before (on Plasma that
is ``plasmashell``) and hand every incoming call to Qt through plain signals.
D-Bus is served by dbus-python on the GLib main context, which Qt drives for us
— see :mod:`dynamicisland.glibpump`.
"""

from __future__ import annotations

import json
import sys
import traceback
from typing import Any

import dbus
import dbus.service

from PySide6.QtCore import QObject, Signal

from . import __version__, debug

BUS_NAME = "org.freedesktop.Notifications"
OBJECT_PATH = "/org/freedesktop/Notifications"
INTERFACE = "org.freedesktop.Notifications"
PROPERTIES_INTERFACE = "org.freedesktop.DBus.Properties"
#: Our own additions, on the same object — the panel applet talks to these.
ISLAND_INTERFACE = "org.dynamicisland.Island"

CAPABILITIES = [
    "actions",
    "body",
    "body-hyperlinks",
    "body-markup",
    "icon-static",
    "persistence",
    "inline-reply",
    "x-kde-display-appname",
    "x-kde-urls",
]


class NameLost(RuntimeError):
    """Raised when the bus refuses to hand us the notification service."""


def plain(value: Any) -> Any:
    """Strip dbus wrapper types so the rest of the app sees plain Python."""
    if isinstance(value, dbus.ByteArray):
        return bytes(value)
    if isinstance(value, dbus.Array):
        # A byte array is the common case for image-data; converting it element
        # by element would be needlessly slow for a megapixel bitmap.
        if str(value.signature) == "y":
            return bytes(bytearray(value))
        return [plain(item) for item in value]
    if isinstance(value, dbus.Dictionary) or isinstance(value, dict):
        return {str(key): plain(item) for key, item in value.items()}
    if isinstance(value, dbus.Struct) or isinstance(value, tuple):
        return tuple(plain(item) for item in value)
    if isinstance(value, dbus.Boolean):
        return bool(value)
    if isinstance(value, dbus.Double):
        return float(value)
    if isinstance(value, (dbus.String, dbus.ObjectPath, dbus.Signature)):
        return str(value)
    if isinstance(value, int) and type(value) is not int:  # Byte, Int16, UInt32…
        return int(value)
    if isinstance(value, (list, tuple)):
        return [plain(item) for item in value]
    return value


class _Adaptor(dbus.service.Object):
    """The raw D-Bus surface. All logic lives in :class:`NotificationService`."""

    def __init__(self, bus: dbus.Bus, service: "NotificationService") -> None:
        super().__init__(bus, OBJECT_PATH)
        self._service = service

    # -- freedesktop notification spec ---------------------------------
    @dbus.service.method(INTERFACE, in_signature="", out_signature="as")
    def GetCapabilities(self) -> list[str]:
        return CAPABILITIES

    @dbus.service.method(INTERFACE, in_signature="", out_signature="ssss")
    def GetServerInformation(self):
        return ("Dynamic Island", "dynamicisland", __version__, "1.2")

    @dbus.service.method(
        INTERFACE, in_signature="susssasa{sv}i", out_signature="u"
    )
    def Notify(
        self,
        app_name,
        replaces_id,
        app_icon,
        summary,
        body,
        actions,
        hints,
        expire_timeout,
    ) -> int:
        return self._service.handle_notify(
            str(app_name),
            int(replaces_id),
            str(app_icon),
            str(summary),
            str(body),
            [str(item) for item in actions],
            plain(hints),
            int(expire_timeout),
        )

    @dbus.service.method(INTERFACE, in_signature="u", out_signature="")
    def CloseNotification(self, notification_id) -> None:
        self._service.handle_close_request(int(notification_id))

    @dbus.service.signal(INTERFACE, signature="uu")
    def NotificationClosed(self, notification_id, reason) -> None:
        pass

    @dbus.service.signal(INTERFACE, signature="us")
    def ActionInvoked(self, notification_id, action_key) -> None:
        pass

    @dbus.service.signal(INTERFACE, signature="us")
    def ActivationToken(self, notification_id, token) -> None:
        pass

    # -- KDE extensions -------------------------------------------------
    @dbus.service.signal(INTERFACE, signature="us")
    def NotificationReplied(self, notification_id, text) -> None:
        pass

    @dbus.service.method(INTERFACE, in_signature="ssa{sv}", out_signature="u")
    def Inhibit(self, desktop_entry, reason, hints) -> int:
        return self._service.handle_inhibit(str(desktop_entry), str(reason))

    @dbus.service.method(INTERFACE, in_signature="u", out_signature="")
    def UnInhibit(self, cookie) -> None:
        self._service.handle_uninhibit(int(cookie))

    # -- our own surface -------------------------------------------------
    @dbus.service.method(ISLAND_INTERFACE, in_signature="", out_signature="s")
    def HistoryPath(self) -> str:
        from .history import HISTORY_PATH

        return HISTORY_PATH

    @dbus.service.method(ISLAND_INTERFACE, in_signature="s", out_signature="b")
    def Open(self, application) -> bool:
        """Show an application — the panel applet asks for this on a click."""
        if self._service.open_handler is None:
            return False
        return bool(self._service.open_handler(str(application)))

    @dbus.service.method(ISLAND_INTERFACE, in_signature="s", out_signature="b")
    def Start(self, desktop_file) -> bool:
        """Start an application without looking for a window first.

        This is the other end of the KWin script that :func:`apps.show` runs:
        the script does the window matching inside the compositor and calls
        back here when it found nothing to bring forward.
        """
        if self._service.start_handler is None:
            return False
        return bool(self._service.start_handler(str(desktop_file)))

    @dbus.service.method(ISLAND_INTERFACE, in_signature="s", out_signature="")
    def Raised(self, window) -> None:
        """The KWin script reporting which window it brought forward."""
        print(f"[dynamic-island] raised {window}")

    @dbus.service.method(ISLAND_INTERFACE, in_signature="sss", out_signature="")
    def Activated(self, desktop_file, resource_class, resource_name) -> None:
        """The KWin focus watcher reporting which window is in front now.

        See :mod:`dynamicisland.focus` — this is how the island knows to keep
        the track banner away while the player itself is being looked at.
        """
        if self._service.activated_handler is not None:
            self._service.activated_handler(
                str(desktop_file), str(resource_class), str(resource_name)
            )

    @dbus.service.method(ISLAND_INTERFACE, in_signature="", out_signature="s")
    def GetConfig(self) -> str:
        """The whole configuration as JSON — the applet's settings read it."""
        if self._service.config_handler is None:
            return "{}"
        return str(self._service.config_handler())

    @dbus.service.method(ISLAND_INTERFACE, in_signature="s", out_signature="b")
    def SetConfig(self, document) -> bool:
        """Merge a JSON object into the configuration and write it out.

        The applet's settings pages live inside plasmashell and have no
        business writing our file; they send the keys they changed here and
        the island saves them, which is also what makes the change take
        effect straight away.
        """
        if self._service.set_config_handler is None:
            return False
        try:
            values = json.loads(str(document))
        except ValueError as error:
            print(f"[dynamic-island] settings sent broken JSON: {error}")
            return False
        if not isinstance(values, dict):
            return False
        self._service.set_config_handler(values)
        return True

    @dbus.service.method(ISLAND_INTERFACE, in_signature="", out_signature="")
    def ClearHistory(self) -> None:
        if self._service.clear_history_handler is not None:
            self._service.clear_history_handler()

    # -- properties -----------------------------------------------------
    @dbus.service.method(
        PROPERTIES_INTERFACE, in_signature="ss", out_signature="v"
    )
    def Get(self, interface_name, property_name):
        if str(property_name) == "Inhibited":
            return dbus.Boolean(self._service.inhibited)
        raise dbus.exceptions.DBusException(
            "org.freedesktop.DBus.Error.UnknownProperty",
            f"No such property {property_name}",
        )

    @dbus.service.method(
        PROPERTIES_INTERFACE, in_signature="s", out_signature="a{sv}"
    )
    def GetAll(self, interface_name):
        if str(interface_name) in (INTERFACE, ""):
            return dbus.Dictionary(
                {"Inhibited": dbus.Boolean(self._service.inhibited)},
                signature="sv",
            )
        return dbus.Dictionary({}, signature="sv")

    @dbus.service.signal(PROPERTIES_INTERFACE, signature="sa{sv}as")
    def PropertiesChanged(self, interface_name, changed, invalidated) -> None:
        pass


class NotificationService(QObject):
    """Qt-facing wrapper around the D-Bus object.

    Incoming calls are handed on as plain Python callbacks rather than Qt
    signals: ``hints`` can carry a raw bitmap, and pushing that through
    QVariant conversion would only risk mangling it.  Everything runs on the
    one thread anyway, so a direct call is also the honest description of what
    happens.
    """

    #: True while at least one client holds an inhibition
    inhibitedChanged = Signal(bool)
    #: True once the well-known name is actually ours
    nameOwnedChanged = Signal(bool)

    #: Called with (app_name, id, app_icon, summary, body, actions, hints, timeout)
    notify_handler = None
    #: Called with the id of a notification the sender wants taken down
    close_handler = None
    #: Called with no arguments when the panel applet empties the history
    clear_history_handler = None
    #: Called with a desktop entry or file when something asks to open an app
    open_handler = None
    #: Called with a desktop file that is to be started outright — see Start()
    start_handler = None
    #: Called with no arguments, returns the configuration as a JSON string
    config_handler = None
    #: Called with a dict of settings the applet wants written
    set_config_handler = None
    #: Called with the active window's three names whenever the focus moves
    activated_handler = None

    def __init__(self, parent: QObject | None = None, claim: bool = True) -> None:
        super().__init__(parent)
        self._next_id = 1
        self._next_cookie = 1
        self._inhibitors: dict[int, str] = {}
        self._bus = None
        self._adaptor = None
        self._owned = False

        # claim=False leaves the session's notification server alone, which is
        # what you want while working on the looks of the island.
        if not claim:
            return

        self._bus = dbus.SessionBus()
        self._claim_name()
        self._adaptor = _Adaptor(self._bus, self)
        self._bus.add_signal_receiver(
            self._on_name_owner_changed,
            signal_name="NameOwnerChanged",
            dbus_interface="org.freedesktop.DBus",
            arg0=BUS_NAME,
        )

    # -- name ownership -------------------------------------------------
    def _claim_name(self) -> None:
        """Become the notification server, or queue up behind the current one.

        plasmashell registers the name without ``AllowReplacement``, so asking
        it to step aside gets us nothing.  We queue instead: the moment
        plasmashell releases the name — a restart is enough — the bus hands it
        to us, and the shell then finds the name taken and leaves it alone.

        We deliberately do *not* pass ``ALLOW_REPLACEMENT`` ourselves: the
        restarting shell asks for the name with ``REPLACE_EXISTING``, and with
        that flag set it would simply take the service straight back off us.
        """
        flags = dbus.bus.NAME_FLAG_REPLACE_EXISTING
        reply = self._bus.request_name(BUS_NAME, flags)
        if reply == dbus.bus.REQUEST_NAME_REPLY_PRIMARY_OWNER:
            self._owned = True
        elif reply == dbus.bus.REQUEST_NAME_REPLY_IN_QUEUE:
            self._owned = False
        else:
            raise NameLost(
                f"the bus refused {BUS_NAME} (reply {reply})."
            )

    def _on_name_owner_changed(self, name, old_owner, new_owner) -> None:
        if str(name) != BUS_NAME:
            return
        is_ours = str(new_owner) == self._bus.get_unique_name()

        if is_ours and not self._owned:
            self._owned = True
            print("[dynamic-island] took over the notification service")
            self.nameOwnedChanged.emit(True)
        elif not is_ours and self._owned:
            # Somebody else grabbed it; retreat instead of running blind.
            self._owned = False
            print("[dynamic-island] lost the notification service, quitting")
            self.nameOwnedChanged.emit(False)
            from PySide6.QtCore import QCoreApplication

            application = QCoreApplication.instance()
            if application is not None:
                application.quit()

    @property
    def owns_name(self) -> bool:
        return self._owned

    @property
    def unique_name(self) -> str:
        """Our connection's own bus address — good even while queued."""
        return str(self._bus.get_unique_name()) if self._bus is not None else ""

    # -- incoming calls -------------------------------------------------
    def handle_notify(
        self,
        app_name: str,
        replaces_id: int,
        app_icon: str,
        summary: str,
        body: str,
        actions: list[str],
        hints: dict[str, Any],
        expire_timeout: int,
    ) -> int:
        notification_id = replaces_id or self._take_id()
        debug(f"Notify from {app_name!r}: {summary!r} hints={sorted(hints)}")
        if self.notify_handler is not None:
            try:
                self.notify_handler(
                    app_name,
                    notification_id,
                    app_icon,
                    summary,
                    body,
                    actions,
                    hints,
                    expire_timeout,
                )
            except Exception:  # noqa: BLE001
                # One malformed notification must never take the daemon with
                # it, and it must never disappear quietly either.
                print(
                    f"[dynamic-island] failed to show a notification from "
                    f"{app_name!r}:",
                    file=sys.stderr,
                )
                traceback.print_exc()
        return notification_id

    def handle_close_request(self, notification_id: int) -> None:
        if self.close_handler is not None:
            self.close_handler(notification_id)

    def handle_inhibit(self, desktop_entry: str, reason: str) -> int:
        was_inhibited = self.inhibited
        cookie = self._next_cookie
        self._next_cookie += 1
        self._inhibitors[cookie] = desktop_entry or reason
        if not was_inhibited:
            self._announce_inhibited()
        return cookie

    def handle_uninhibit(self, cookie: int) -> None:
        if self._inhibitors.pop(cookie, None) is None:
            return
        if not self._inhibitors:
            self._announce_inhibited()

    def _take_id(self) -> int:
        notification_id = self._next_id
        # Stay inside uint32 and never hand out 0, which means "new" in the spec.
        self._next_id = self._next_id % 0xFFFFFFFF + 1
        return notification_id

    def _announce_inhibited(self) -> None:
        self.inhibitedChanged.emit(self.inhibited)
        if self._adaptor is None:
            return
        self._adaptor.PropertiesChanged(
            INTERFACE,
            dbus.Dictionary(
                {"Inhibited": dbus.Boolean(self.inhibited)}, signature="sv"
            ),
            [],
        )

    @property
    def inhibited(self) -> bool:
        return bool(self._inhibitors)

    # -- outgoing signals -----------------------------------------------
    def emit_closed(self, notification_id: int, reason: int) -> None:
        if self._adaptor is None:
            return
        self._adaptor.NotificationClosed(
            dbus.UInt32(notification_id), dbus.UInt32(reason)
        )

    def emit_action(self, notification_id: int, action_key: str) -> None:
        if self._adaptor is None:
            return
        self._adaptor.ActionInvoked(dbus.UInt32(notification_id), action_key)

    def emit_reply(self, notification_id: int, text: str) -> None:
        if self._adaptor is None:
            return
        self._adaptor.NotificationReplied(dbus.UInt32(notification_id), text)
