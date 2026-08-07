"""Surface plumbing that QML cannot reach on its own.

The island lives on a transparent, screen-wide layer-shell surface.  Without an
input region that follows the pill, that transparent area would swallow every
click meant for the windows underneath, so QML reports the pill geometry here
and we hand it to the compositor.
"""

from __future__ import annotations

import os

from PySide6.QtCore import QObject, QRect, Slot
from PySide6.QtGui import QGuiApplication, QRegion

from . import DEBUG, debug

# Anything but Wayland means no layer shell, and the mask semantics differ.
WAYLAND = (os.environ.get("XDG_SESSION_TYPE") or "").lower() == "wayland"


def enable_layer_shell() -> bool:
    """Route Qt's Wayland windows through wlr-layer-shell.

    Must run before the first ``QGuiApplication`` exists, because the shell
    integration is chosen when the platform plugin comes up.
    """
    if not WAYLAND:
        return False
    os.environ.setdefault("QT_WAYLAND_SHELL_INTEGRATION", "layer-shell")
    return True


class WindowTools(QObject):
    """Exposed to QML as ``WindowTools``."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._window = None
        self._masked = QRect()

    def attach(self, window) -> None:
        self._window = window

    # -- input region ----------------------------------------------------
    @Slot(int, int, int, int)
    def setInputRegion(self, x: int, y: int, width: int, height: int) -> None:
        """Let clicks through everywhere except the given rectangle."""
        if self._window is None:
            return
        rect = QRect(int(x), int(y), max(0, int(width)), max(0, int(height)))
        if rect == self._masked:
            return
        self._masked = rect
        self._window.setMask(QRegion(rect))
        debug(
            f"mask={rect.x()},{rect.y()} {rect.width()}x{rect.height()} "
            f"window={self._window.width()}x{self._window.height()} "
            f"dpr={self._window.devicePixelRatio()}"
        )

    @Slot(str)
    def log(self, message: str) -> None:
        """Debug channel for QML, quiet unless DYNAMIC_ISLAND_DEBUG is set."""
        debug(f"qml: {message}")

    @Slot()
    def clearInputRegion(self) -> None:
        """Make the whole surface transparent to input."""
        self.setInputRegion(0, 0, 0, 0)

    # -- screens ----------------------------------------------------------
    @Slot(str)
    def moveToScreen(self, connector: str) -> None:
        """Pin the surface to an output by connector name, e.g. ``DP-1``."""
        if self._window is None or not connector or connector == "active":
            return
        for screen in QGuiApplication.screens():
            if screen.name() == connector:
                if self._window.screen() is not screen:
                    self._window.setScreen(screen)
                return

    @Slot(result="QStringList")
    def screenNames(self) -> list[str]:
        return [screen.name() for screen in QGuiApplication.screens()]
