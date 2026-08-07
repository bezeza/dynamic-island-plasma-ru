"""Keeping dbus-python alive inside a Qt event loop.

On Linux Qt normally runs on top of GLib's default main context, in which case
dbus-python's GLib integration is already being driven and there is nothing to
do.  When Qt was built without GLib — or ``QT_NO_GLIB`` is set — nobody
iterates the context and D-Bus traffic would simply never arrive, so we pump it
from a timer instead.
"""

from __future__ import annotations

from PySide6.QtCore import QAbstractEventDispatcher, QCoreApplication, QTimer

# Small enough to feel instant, large enough to stay off the CPU when idle.
_POLL_INTERVAL_MS = 20
_MAX_ITERATIONS_PER_TICK = 32


def qt_uses_glib() -> bool:
    dispatcher = QAbstractEventDispatcher.instance()
    if dispatcher is None:
        return False
    return "glib" in dispatcher.metaObject().className().lower()


def install(parent: QCoreApplication) -> QTimer | None:
    """Make sure the GLib default context gets iterated. Returns the timer, if any."""
    if qt_uses_glib():
        return None

    from gi.repository import GLib

    context = GLib.MainContext.default()

    def pump() -> None:
        for _ in range(_MAX_ITERATIONS_PER_TICK):
            if not context.iteration(False):
                break

    timer = QTimer(parent)
    timer.setInterval(_POLL_INTERVAL_MS)
    timer.timeout.connect(pump)
    timer.start()
    print("[dynamic-island] Qt has no GLib dispatcher, pumping D-Bus manually")
    return timer
