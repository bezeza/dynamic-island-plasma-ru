"""The live notification store that the island renders.

Row 0 is always the newest notification — the one the island shows in front.
Everything behind it is drawn as the stack peeking out below.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from PySide6.QtCore import (
    QAbstractListModel,
    QByteArray,
    QModelIndex,
    Property,
    Qt,
    Signal,
    Slot,
)

URGENCY_LOW = 0
URGENCY_NORMAL = 1
URGENCY_CRITICAL = 2

# Reasons for org.freedesktop.Notifications.NotificationClosed.
CLOSED_EXPIRED = 1
CLOSED_DISMISSED = 2
CLOSED_BY_CALL = 3
CLOSED_UNDEFINED = 4

#: Hints the controller sets on its own cards — no application on the bus ever
#: sends them. They live here because a card reports what it is through them.
JOB_HINT = "x-dynamic-island-job"
OSD_HINT = "x-dynamic-island-osd"
MEDIA_HINT = "x-dynamic-island-media"

#: Carries what is playing on every card that draws a player: the track banner
#: itself, and the volume display while something is on.
MEDIA_INFO_HINT = "x-dynamic-island-media-info"

#: The link behind a card whose click opens a page — a feed entry, a webhook
#: with a url. Set so the history can keep it: the buttons die with the card,
#: but the article is still worth a click a day later.
LINK_HINT = "x-dynamic-island-link"


@dataclass
class Notification:
    """One notification, already normalised out of its D-Bus representation."""

    id: int
    app_name: str = ""
    desktop_entry: str = ""
    summary: str = ""
    body: str = ""
    # [{"key": "reply", "label": "Reply"}, ...] — without the "default" entry.
    actions: list[dict[str, str]] = field(default_factory=list)
    default_action: str = ""
    hints: dict[str, Any] = field(default_factory=dict)
    urgency: int = URGENCY_NORMAL
    timeout: int = -1
    accent: str = "#0A84FF"
    has_image: bool = False
    # Bumped on every replace so QML reloads the image URL.
    serial: int = 0
    created: float = field(default_factory=time.monotonic)
    reply_placeholder: str = ""
    resident: bool = False
    transient: bool = False
    # -1 when the sender did not report progress, 0..100 otherwise.
    progress: int = -1

    @property
    def icon_source(self) -> str:
        return f"image://island/{self.id}?{self.serial}" if self.has_image else ""

    @property
    def kind(self) -> str:
        """Which of our card layouts this one asks for."""
        for hint, kind in ((JOB_HINT, "job"), (OSD_HINT, "osd"), (MEDIA_HINT, "media")):
            if self.hints.get(hint):
                return kind
        return "notification"

    @property
    def media(self) -> dict[str, Any]:
        """What is playing, or nothing — see :data:`MEDIA_INFO_HINT`."""
        payload = self.hints.get(MEDIA_INFO_HINT)
        return payload if isinstance(payload, dict) else {}


class NotificationModel(QAbstractListModel):
    """List model over :class:`Notification`, newest first."""

    IdRole = Qt.UserRole + 1
    AppNameRole = Qt.UserRole + 2
    SummaryRole = Qt.UserRole + 3
    BodyRole = Qt.UserRole + 4
    IconRole = Qt.UserRole + 5
    AccentRole = Qt.UserRole + 6
    ActionsRole = Qt.UserRole + 7
    UrgencyRole = Qt.UserRole + 8
    ReplyRole = Qt.UserRole + 9
    ReplyPlaceholderRole = Qt.UserRole + 10
    ProgressRole = Qt.UserRole + 11
    HasDefaultRole = Qt.UserRole + 12
    KindRole = Qt.UserRole + 13
    MediaRole = Qt.UserRole + 14

    countChanged = Signal()

    _ROLE_NAMES = {
        IdRole: b"notificationId",
        AppNameRole: b"appName",
        SummaryRole: b"summary",
        BodyRole: b"body",
        IconRole: b"iconSource",
        AccentRole: b"accent",
        ActionsRole: b"actions",
        UrgencyRole: b"urgency",
        ReplyRole: b"hasReply",
        ReplyPlaceholderRole: b"replyPlaceholder",
        ProgressRole: b"progress",
        HasDefaultRole: b"hasDefaultAction",
        KindRole: b"kind",
        MediaRole: b"media",
    }

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._items: list[Notification] = []

    # -- QAbstractListModel -------------------------------------------
    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._items)

    def roleNames(self) -> dict[int, QByteArray]:
        return {role: QByteArray(name) for role, name in self._ROLE_NAMES.items()}

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole) -> Any:
        if not index.isValid() or not 0 <= index.row() < len(self._items):
            return None
        item = self._items[index.row()]
        if role == self.IdRole:
            return item.id
        if role == self.AppNameRole:
            return item.app_name
        if role == self.SummaryRole:
            return item.summary
        if role == self.BodyRole:
            return item.body
        if role == self.IconRole:
            return item.icon_source
        if role == self.AccentRole:
            return item.accent
        if role == self.ActionsRole:
            return item.actions
        if role == self.UrgencyRole:
            return item.urgency
        if role == self.ReplyRole:
            return bool(item.reply_placeholder)
        if role == self.ReplyPlaceholderRole:
            return item.reply_placeholder
        if role == self.ProgressRole:
            return item.progress
        if role == self.HasDefaultRole:
            return bool(item.default_action)
        if role == self.KindRole:
            return item.kind
        if role == self.MediaRole:
            return item.media
        return None

    # -- store ---------------------------------------------------------
    def put(self, notification: Notification) -> None:
        """Insert *notification*, or update it in place when the id is known."""
        row = self.row_of(notification.id)
        if row < 0:
            self.beginInsertRows(QModelIndex(), 0, 0)
            self._items.insert(0, notification)
            self.endInsertRows()
            self.countChanged.emit()
            return

        # A replacement keeps its position so the island does not jump around.
        previous = self._items[row]
        notification.serial = previous.serial + 1
        notification.created = time.monotonic()
        self._items[row] = notification
        index = self.index(row, 0)
        self.dataChanged.emit(index, index, list(self._ROLE_NAMES))

    def promote(self, notification_id: int) -> bool:
        """Move a row to the front without changing it.

        An on-screen display answers a key the user pressed a moment ago, so it
        has to be the card in front even when something sticky is already up.
        """
        row = self.row_of(notification_id)
        if row < 0:
            return False
        if row == 0:
            return True
        self.beginMoveRows(QModelIndex(), row, row, QModelIndex(), 0)
        self._items.insert(0, self._items.pop(row))
        self.endMoveRows()
        return True

    def remove(self, notification_id: int) -> bool:
        row = self.row_of(notification_id)
        if row < 0:
            return False
        self.beginRemoveRows(QModelIndex(), row, row)
        del self._items[row]
        self.endRemoveRows()
        self.countChanged.emit()
        return True

    def clear(self) -> None:
        if not self._items:
            return
        self.beginResetModel()
        self._items.clear()
        self.endResetModel()
        self.countChanged.emit()

    def row_of(self, notification_id: int) -> int:
        for row, item in enumerate(self._items):
            if item.id == notification_id:
                return row
        return -1

    def get(self, notification_id: int) -> Notification | None:
        row = self.row_of(notification_id)
        return self._items[row] if row >= 0 else None

    def ids(self) -> list[int]:
        return [item.id for item in self._items]

    # -- QML convenience ------------------------------------------------
    @Slot(int, result="QVariant")
    def at(self, row: int) -> Any:
        if not 0 <= row < len(self._items):
            return {}
        index = self.index(row, 0)
        return {
            name.decode(): self.data(index, role)
            for role, name in self._ROLE_NAMES.items()
        }

    def _count(self) -> int:
        return len(self._items)

    count = Property(int, _count, notify=countChanged)
