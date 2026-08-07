"""Let the island photograph itself, for the pictures in the README.

Filming it from outside turns out to be the hard way round: the pill lives on
a layer-shell surface above everything, so a window screenshot cannot see it,
KWin only hands the screen to programs it recognises, and a screen recorder
wants a portal dialog clicked before it will start.

The scene graph is right here, though.  ``QQuickWindow.grabWindow()`` renders
exactly what is on the surface — pill, glow, card, alpha and all — and doing
that on a timer is a screen recorder with none of the above: no cursor, no
dialog, no other windows in shot, and frames that are already the right size.

Off unless ``DYNAMIC_ISLAND_CAPTURE`` names a pattern to write, so it costs a
running island nothing:

    DYNAMIC_ISLAND_CAPTURE=/tmp/island-%04d.png \\
    DYNAMIC_ISLAND_CAPTURE_BOX=720x300 ./tools/run.sh
"""

from __future__ import annotations

import os

from PySide6.QtCore import QObject, QRect, QTimer

#: 20 a second: enough for a spring animation to read as one, few enough that
#: the grab never becomes the reason a frame is late.
INTERVAL_MS = 50


class Recorder(QObject):
    """Writes numbered frames of the island while it moves."""

    def __init__(self, window, pattern: str, box: str = "",
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._window = window
        self._pattern = pattern
        self._frame = 0
        self._box = self._parse(box)

        folder = os.path.dirname(pattern)
        if folder:
            os.makedirs(folder, exist_ok=True)

        self._timer = QTimer(self)
        self._timer.setInterval(INTERVAL_MS)
        self._timer.timeout.connect(self._grab)
        self._timer.start()
        print(f"[dynamic-island] recording frames to {pattern}")

    @staticmethod
    def _parse(box: str) -> tuple[int, int]:
        """``WIDTHxHEIGHT`` — the strip along the top edge that is kept."""
        try:
            width, height = (int(part) for part in box.lower().split("x"))
            return width, height
        except (AttributeError, TypeError, ValueError):
            return 760, 300

    def _grab(self) -> None:
        image = self._window.grabWindow()
        if image.isNull():
            return
        # Always the same rectangle, centred on the surface: a GIF wants every
        # frame the same size, and the island grows and shrinks inside it.
        width, height = self._box
        left = max(0, (image.width() - width) // 2)
        image = image.copy(QRect(left, 0, min(width, image.width()),
                                 min(height, image.height())))
        image.save(self._pattern % self._frame)
        self._frame += 1


class DemoJob(QObject):
    """A file operation that is not one, for the picture in the README.

    Copy progress reaches the island over ``org.kde.JobViewServer``, and only
    an application with a progress dialog of its own registers there — a copy
    from the command line finishes silently, so there is no way to stage one.
    This walks a job through the real :mod:`dynamicisland.jobs` shape and the
    real controller instead; the card on screen is drawn by the same code as
    the real thing, and only the copy itself is imaginary.

    Set ``DYNAMIC_ISLAND_DEMO_JOB=1``.
    """

    path = "/JobView_demo"
    desktop_entry = "org.kde.dolphin"
    icon_name = ""
    app_name = "Dolphin"
    info_message = "Copying"
    killable = True
    suspendable = True

    def __init__(self, controller, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._controller = controller
        self.percent = 0
        self.suspended = False
        self.descriptions = {
            0: ("Source", "/home/you/Videos/holiday.mkv"),
            1: ("Destination", "/run/media/you/backup/holiday.mkv"),
        }
        self._timer = QTimer(self)
        self._timer.setInterval(400)
        self._timer.timeout.connect(self._step)
        QTimer.singleShot(1500, self._timer.start)

    def _step(self) -> None:
        self.percent = min(100, self.percent + 4)
        self._controller._on_job_changed(self)
        if self.percent >= 100:
            self._timer.stop()
            QTimer.singleShot(1200, lambda: self._controller._on_job_finished(self))

    # The three the buttons call; nothing to do but look the part.
    def request_cancel(self) -> None:
        self._timer.stop()
        self._controller._on_job_finished(self)

    def request_suspend(self) -> None:
        self.suspended = True

    def request_resume(self) -> None:
        self.suspended = False
