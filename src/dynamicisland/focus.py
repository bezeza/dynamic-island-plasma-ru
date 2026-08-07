"""Which window has the focus, so the track banner knows when to stay away.

A track change is worth a card when the player is somewhere in the back —
minimised, behind the browser, on another desktop.  With the player's own
window in front the card only repeats what the screen already shows: whoever
watches the video or sits in Spotify sees the track change right there.

On Wayland nobody outside the compositor is told which window is active, but
KWin runs scripts, and a script may call home over the bus.  So the same trick
:mod:`dynamicisland.apps` uses to raise a window is used again — written once
and kept loaded instead of thrown away, reporting every activation to our
``Activated`` method.  The names it sends are the same three the raise script
matches windows by, folded the same way, so the two always agree on what an
application is called.
"""

from __future__ import annotations

import threading

import dbus

from PySide6.QtCore import QObject, QTimer, Signal

from . import apps, debug
from .server import ISLAND_INTERFACE, OBJECT_PATH

#: The name the script is registered under in KWin — fixed, so a leftover from
#: a crashed run is replaced on the next start instead of piling up.
_PLUGIN = "dynamic-island-focus"

#: Reports the active window, at load and on every change.  A null window —
#: the desktop itself has the focus — is reported as empty strings, which
#: match nothing and therefore keep no card away.
_WATCH_SCRIPT = """
function report(window) {
    callDBus("%(bus)s", "%(path)s", "%(interface)s", "Activated",
             window ? String(window.desktopFileName || "") : "",
             window ? String(window.resourceClass || "") : "",
             window ? String(window.resourceName || "") : "");
}
workspace.windowActivated.connect(report);
report(workspace.activeWindow);
"""

#: Below this a needle is only trusted for exact matches — the same rule the
#: raise script applies, for the same reason: "fire" being *contained* in a
#: class name is plausibly Firefox, two shared letters are not.
_LOOSE_NEEDLE = 5


class FocusWatcher(QObject):
    """Keeps the compositor's answer to "what is in front?" at hand.

    The answer arrives by callback, so asking :meth:`is_active` costs nothing —
    which matters because it is asked on every track change.
    """

    #: The active window changed. Carries nothing: ask :meth:`is_active`.
    changed = Signal()

    def __init__(self, service, parent: QObject | None = None, claim: bool = True) -> None:
        super().__init__(parent)
        self._names: tuple[str, ...] = ()
        self._destination = ""
        self._armed = False
        self.watching = False
        if not claim or service is None:
            return

        # The script calls our unique bus name, not the well-known one: two
        # islands may be alive at once — one serving, one queued behind it —
        # and each report has to reach the instance that asked for it.
        self._destination = service.unique_name
        if not self._destination:
            return  # --no-bus: there is no surface to call home to
        service.activated_handler = self._on_activated

        # Only the instance that actually serves notifications loads the
        # script: the plugin name is fixed, so a queued second island would
        # otherwise replace the serving one's watcher with a script calling
        # home to the wrong address. The queued one arms the moment the name
        # comes to it.
        if service.owns_name:
            self._arm()
        else:
            service.nameOwnedChanged.connect(self._on_name_owned)

        # A compositor that restarts has forgotten the script — and everything
        # it ever reported. Watching its bus name is how we notice.
        try:
            dbus.SessionBus().add_signal_receiver(
                self._on_kwin_owner,
                signal_name="NameOwnerChanged",
                dbus_interface="org.freedesktop.DBus",
                arg0="org.kde.KWin",
            )
        except dbus.DBusException:
            pass

    # -- the script -------------------------------------------------------
    def _on_name_owned(self, owned: bool) -> None:
        if owned and not self._armed:
            self._arm()

    def _on_kwin_owner(self, name, old_owner, new_owner) -> None:
        # Whatever KWin told us died with it: names kept from before a restart
        # would hold banners back for a window that may not even exist. Saying
        # so through changed keeps the controller honest too.
        self.watching = False
        if self._names:
            self._names = ()
            self.changed.emit()
        # The new compositor has to be handed the script again; its scripting
        # interface needs a moment before it answers.
        if str(new_owner) and self._armed:
            QTimer.singleShot(2000, self._arm)

    def _arm(self) -> None:
        # Loading is a few gdbus round trips with four-second timeouts each.
        # This instance is the session's notification server the moment it
        # owns the name — a slow compositor must not leave Notify unanswered,
        # so the loading happens off the event loop.
        self._armed = True
        threading.Thread(
            target=self._install, name="dynamic-island-focus", daemon=True
        ).start()

    def _install(self) -> None:
        source = _WATCH_SCRIPT % {
            "bus": self._destination,
            "path": OBJECT_PATH,
            "interface": ISLAND_INTERFACE,
        }
        self.watching = apps.run_script(source, _PLUGIN, keep=True)
        # Say which way it went either way: an island that shows the banner
        # regardless of focus is otherwise indistinguishable from one that
        # merely has no music playing behind a focused window.
        print(
            "[dynamic-island] media focus: "
            + ("watching" if self.watching
               else "KWin is not answering, the track banner shows regardless")
        )

    def stop(self) -> None:
        """Take the script out of KWin again — connected to aboutToQuit."""
        if not self.watching:
            return
        self.watching = False
        apps.unload_script(_PLUGIN)

    # -- what is in front -------------------------------------------------
    def _on_activated(self, desktop_file: str, resource_class: str, resource_name: str) -> None:
        debug(f"focus: {desktop_file!r} / {resource_class!r} / {resource_name!r}")
        names = tuple(
            folded
            for folded in {
                apps.fold(name)
                for name in (desktop_file, resource_class, resource_name)
            }
            if folded
        )
        if set(names) == set(self._names):
            return  # another window of the same application — same answer
        self._names = names
        self.changed.emit()

    def is_active(self, needles: list[str]) -> bool:
        """Whether the focused window answers to one of these names.

        The same two-step match as the raise script: exact on any of the
        window's three names, and containment — only that way round — for a
        needle long enough to be trusted with it.  When nothing is known,
        nothing matches: a card too many beats music going by unseen.
        """
        for needle in needles:
            if needle in self._names:
                return True
            if len(needle) >= _LOOSE_NEEDLE and any(
                needle in name for name in self._names
            ):
                return True
        return False
