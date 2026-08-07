"""Everything that puts a card on the island without being an application.

The manager builds the configured sources, gives what they produce a
notification id of its own, and remembers which button belongs to which action
so that a press can be carried out later.  To the controller it looks like one
more thing that sends notifications, which is the point: a Grafana alert
arriving over a webhook takes the same path through the island as a message
from a chat client.

Configuration lives under ``sources`` in ``config.json``:

    "sources": [
      {"type": "rss",  "name": "Kernel", "url": "https://…/feed.xml",
       "interval": 900, "filter": "6\\\\.1[0-9]"},
      {"type": "rest", "name": "Uptime", "url": "https://…/api/status",
       "itemsPath": "incidents", "interval": 120,
       "fields": {"summary": "name", "body": "status", "id": "id"}},
      {"type": "webhook", "bind": "127.0.0.1", "port": 8757, "token": "…"}
    ]

Every entry may carry ``enabled: false`` to keep it in the file but out of the
way, which is what the settings interface toggles.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, Signal

from .. import debug
from ..model import LINK_HINT, URGENCY_LOW
from .actions import ActionRunner
from .base import Source
from .feed import FeedSource
from .payload import Action, Item
from .rest import RestSource
from .webhook import WebhookSource

#: Cards from a source get ids of their own, well clear of the ones the bus
#: hands out and of the ranges the controller keeps for jobs and media — and
#: still inside a signed 32-bit int, which is what a Qt signal carries an
#: ``int`` in.
SOURCE_ID_BASE = 2_100_000_001

#: Set on every card a source produced; the controller only needs to know that
#: the buttons are ours, not which source it was.
SOURCE_HINT = "x-dynamic-island-source"

#: How many cards' worth of buttons are remembered. Long past the point where
#: anybody would still press one, and it keeps the map from growing forever.
_MEMORY = 200

_TYPES = {
    "rss": FeedSource,
    "atom": FeedSource,
    "feed": FeedSource,
    "rest": RestSource,
    "json": RestSource,
    "api": RestSource,
    "webhook": WebhookSource,
    "http": WebhookSource,
}


class SourceManager(QObject):
    """Owns the configured sources and what their buttons do."""

    #: (app_name, id, icon, summary, body, actions, hints, timeout) — the same
    #: arguments an application would have sent over the bus.
    notify = Signal(str, int, str, str, str, object, object, int)

    def __init__(self, config, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._config = config
        self._sources: list[Source] = []
        self._runner = ActionRunner(self)
        #: (source, item key) -> notification id, so an update replaces
        self._ids: dict[tuple[str, str], int] = {}
        #: notification id -> {action key: Action}, plus "" for the card click
        self._actions: dict[int, dict[str, Action]] = {}
        self._order: list[int] = []
        self._next_id = SOURCE_ID_BASE
        self._signature = ""

    # -- lifetime ---------------------------------------------------------
    def start(self) -> None:
        """Build what the configuration asks for and set it going."""
        entries = self._config.get("sources") or []
        self._signature = repr(entries)
        for entry in entries:
            if not isinstance(entry, dict) or entry.get("enabled") is False:
                continue
            source = self._build(entry)
            if source is None:
                continue
            source.produced.connect(
                lambda items, origin=source: self._on_produced(origin, items)
            )
            self._sources.append(source)
            source.start()
            print(f"[dynamic-island] source: {source.describe()}")

    def reload(self) -> None:
        """Rebuild after the configuration changed, if it changed at all."""
        entries = self._config.get("sources") or []
        if repr(entries) == self._signature:
            return
        self.stop()
        self.start()

    def stop(self) -> None:
        for source in self._sources:
            source.stop()
            source.deleteLater()
        self._sources.clear()

    def _build(self, entry: dict[str, Any]) -> Source | None:
        kind = str(entry.get("type") or "").lower()
        factory = _TYPES.get(kind)
        if factory is None:
            print(f"[dynamic-island] unknown source type {kind!r}, skipping")
            return None
        try:
            return factory(entry, self)
        except Exception as error:  # noqa: BLE001 - one bad entry, not all of them
            print(f"[dynamic-island] source {entry.get('name')!r} is broken: {error}")
            return None

    # -- from a source to a card ------------------------------------------
    def _on_produced(self, source: Source, items: list[Item]) -> None:
        for item in items:
            self._show(source, item)

    def _show(self, source: Source, item: Item) -> None:
        key = (source.name, item.key)
        notification_id = self._ids.get(key)
        if notification_id is None:
            notification_id = self._next_id
            self._next_id += 1
            self._ids[key] = notification_id

        # The island speaks the D-Bus dialect: actions are a flat list of
        # key, label, key, label…, and the rest travels as hints.
        actions: list[str] = []
        remembered: dict[str, Action] = {}
        for action in item.actions:
            actions += [action.key, action.label]
            remembered[action.key] = action
        if item.default is not None:
            # "default" is what the spec calls the click on the card itself.
            actions += ["default", item.default.label]
            remembered["default"] = item.default

        self._remember(notification_id, remembered)

        hints: dict[str, Any] = {
            "urgency": item.urgency,
            SOURCE_HINT: source.name,
            "suppress-sound": item.urgency == URGENCY_LOW,
        }
        if item.icon:
            hints["image-path"] = item.icon
        if item.progress >= 0:
            hints["value"] = item.progress
        # The click's link rides along as a hint so the history can keep it:
        # the action map here dies with the card, but a feed entry is exactly
        # the kind of thing somebody wants to open a day later. Links only —
        # a command from an old history row would be a surprise, not a click.
        if (
            item.default is not None
            and item.default.kind == "url"
            and item.default.url
        ):
            hints[LINK_HINT] = item.default.url

        debug(f"{source.name}: {item.summary!r} as #{notification_id}")
        self.notify.emit(
            item.app_name or source.app_name,
            notification_id,
            item.icon,
            item.summary,
            item.body,
            actions,
            hints,
            item.timeout,
        )

    def _remember(self, notification_id: int, actions: dict[str, Action]) -> None:
        if notification_id not in self._actions:
            self._order.append(notification_id)
        self._actions[notification_id] = actions
        while len(self._order) > _MEMORY:
            self._actions.pop(self._order.pop(0), None)

    # -- what the controller asks ------------------------------------------
    def owns(self, notification_id: int) -> bool:
        return notification_id >= SOURCE_ID_BASE

    def handle_action(self, notification_id: int, action_key: str) -> bool:
        """Carry out a press. False means this card is not ours."""
        actions = self._actions.get(notification_id)
        if actions is None:
            return False
        action = actions.get(action_key)
        if action is None:
            # Ours, but the button is gone — the card outlived a reload. Say
            # yes anyway: the card still closes, which is what a press means.
            return True
        return self._runner.run(action) or True
