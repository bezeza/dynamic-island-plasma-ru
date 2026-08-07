"""What the island has already shown.

The pill is deliberately short-lived, so anything it showed would be gone for
good — that is what the panel applet is for.  The store keeps the last handful
of notifications on disk in a plain JSON file, which is all the applet needs to
read; there is no D-Bus round trip in the way.

Artwork is the awkward part: a notification may carry a raw bitmap that exists
nowhere on disk.  Those get written out as small PNGs next to the JSON and are
removed again when the entry falls off the end.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QImage

from . import apps
from .model import LINK_HINT

DATA_DIR = os.path.join(
    os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"),
    "dynamic-island",
)
HISTORY_PATH = os.path.join(DATA_DIR, "history.json")
ICON_DIR = os.path.join(DATA_DIR, "icons")

ICON_EDGE = 64


class HistoryStore(QObject):
    """Newest first, capped at *limit* entries."""

    changed = Signal()

    def __init__(self, limit: int = 100, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._limit = max(0, int(limit))
        self._entries: list[dict[str, Any]] = []
        self._sequence = 0
        self.load()

    # -- persistence -----------------------------------------------------
    def load(self) -> None:
        """(Re-)read the file.

        Called before every write, not just at startup: the file is the whole
        truth here, and writing a stale in-memory copy over it would silently
        throw entries away — that is exactly what happens when a second
        instance is briefly alive during a restart, or when the first read
        failed for whatever reason.
        """
        try:
            with open(HISTORY_PATH, "r", encoding="utf-8") as handle:
                stored = json.load(handle)
        except FileNotFoundError:
            return
        except (OSError, ValueError) as error:
            print(f"[dynamic-island] ignoring broken history: {error}")
            return
        if isinstance(stored, list):
            self._entries = [item for item in stored if isinstance(item, dict)]
        # Carry on numbering above whatever icon files are already there.
        for entry in self._entries:
            name = os.path.basename(str(entry.get("iconFile") or ""))
            stem = name.split(".", 1)[0]
            if stem.isdigit():
                self._sequence = max(self._sequence, int(stem) + 1)

    def save(self) -> None:
        os.makedirs(DATA_DIR, exist_ok=True)
        temporary = HISTORY_PATH + ".tmp"
        try:
            with open(temporary, "w", encoding="utf-8") as handle:
                json.dump(self._entries, handle, ensure_ascii=False, indent=1)
                handle.write("\n")
            os.replace(temporary, HISTORY_PATH)
        except OSError as error:
            print(f"[dynamic-island] could not write the history: {error}")

    # -- contents ---------------------------------------------------------
    def entries(self) -> list[dict[str, Any]]:
        return list(self._entries)

    def add(self, notification, image: QImage | None = None) -> None:
        if self._limit == 0:
            return
        self.load()
        entry = {
            "appName": notification.app_name,
            "summary": notification.summary,
            "body": notification.body,
            "accent": notification.accent,
            "urgency": notification.urgency,
            "time": time.time(),
            "iconName": self._icon_name(notification),
            "iconFile": self._store_icon(image),
            # Resolved here rather than in the applet: the panel has no way to
            # search the desktop files, and this is what a click needs.
            "desktopFile": apps.desktop_file(
                notification.desktop_entry or notification.app_name
            ) or "",
            # Kept as it arrived as well: when the desktop file could not be
            # found, the raw entry is still the best thing to match a window
            # against, and the applet has nothing else to offer.
            "desktopEntry": notification.desktop_entry,
            # A feed entry or a webhook carries the page its click opens; a
            # history row without it could only hunt for a window of "heise
            # online", which is nothing. http(s) only — the hint travels over
            # the bus, and a stored click must never become a command.
            "url": self._link(notification),
        }
        self._entries.insert(0, entry)
        self._trim()
        self.save()
        self.changed.emit()

    def clear(self) -> None:
        # Same reason as in add(): empty the file, not our idea of it.
        self.load()
        if not self._entries:
            return
        for entry in self._entries:
            self._drop_icon(entry)
        self._entries.clear()
        self.save()
        self.changed.emit()

    # -- helpers ----------------------------------------------------------
    @staticmethod
    def _link(notification) -> str:
        url = str(notification.hints.get(LINK_HINT, "") or "")
        return url if url.startswith(("http://", "https://")) else ""

    def _trim(self) -> None:
        while len(self._entries) > self._limit:
            self._drop_icon(self._entries.pop())

    @staticmethod
    def _icon_name(notification) -> str:
        """A theme icon name the applet can resolve on its own."""
        entry = notification.desktop_entry
        if entry:
            return entry.removesuffix(".desktop")
        # A hint is whatever the sender put on the bus: image-path arrives as a
        # byte array often enough, and json.dump would refuse it — from inside
        # save(), whose OSError guard would not catch a TypeError.
        path = notification.hints.get("image-path")
        if isinstance(path, (bytes, bytearray)):
            return path.decode("utf-8", "replace")
        return str(path) if path else ""

    def _store_icon(self, image: QImage | None) -> str:
        if image is None or image.isNull():
            return ""
        os.makedirs(ICON_DIR, exist_ok=True)
        path = os.path.join(ICON_DIR, f"{self._sequence}.png")
        self._sequence += 1
        thumbnail = image.scaled(
            ICON_EDGE, ICON_EDGE, Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        return path if thumbnail.save(path, "PNG") else ""

    @staticmethod
    def _drop_icon(entry: dict[str, Any]) -> None:
        path = str(entry.get("iconFile") or "")
        if path.startswith(ICON_DIR):
            try:
                os.remove(path)
            except OSError:
                pass
