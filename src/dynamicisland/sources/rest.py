"""Any JSON endpoint, asked on a timer.

The point of this one is that no two APIs agree on anything, so nothing is
assumed: ``itemsPath`` says where the list lives (``data.alerts``, and an empty
path means the answer *is* the item), and the field names are configured rather
than guessed.  With ``fields`` left out it falls back to
:func:`payload.parse_item`, which knows the handful of names — title, message,
url — that most services happen to use anyway.
"""

from __future__ import annotations

import json
from typing import Any

from .base import PollingSource
from .payload import Item, parse_item


def dig(data: Any, path: str) -> Any:
    """``a.b.0.c`` through nested dictionaries and lists."""
    if not path:
        return data
    for step in path.split("."):
        if isinstance(data, dict):
            data = data.get(step)
        elif isinstance(data, list):
            try:
                data = data[int(step)]
            except (ValueError, IndexError):
                return None
        else:
            return None
        if data is None:
            return None
    return data


class RestSource(PollingSource):
    """A JSON API polled for whatever it calls an event."""

    def __init__(self, config: dict[str, Any], parent=None) -> None:
        super().__init__(config, parent)
        self.items_path = str(config.get("itemsPath") or "")
        #: {"summary": "attributes.title", …} — island field to json path.
        self.fields = {
            str(key): str(value)
            for key, value in (config.get("fields") or {}).items()
        }
        self.allow_commands = bool(config.get("allowCommands"))

    def parse(self, data: bytes) -> list[Item]:
        document = json.loads(data.decode("utf-8", errors="replace"))
        found = dig(document, self.items_path)
        entries = found if isinstance(found, list) else [found]

        items: list[Item] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            item = parse_item(
                self._map(entry),
                allow_commands=self.allow_commands,
                app_name=self.app_name,
                icon=self.icon,
            )
            if item is not None:
                items.append(item)
        return items

    def _map(self, entry: dict[str, Any]) -> dict[str, Any]:
        """Rewrite one entry into the names :func:`parse_item` knows."""
        if not self.fields:
            return entry
        mapped = {
            name: dig(entry, path)
            for name, path in self.fields.items()
        }
        # Actions are configured, not fetched: an API does not know what the
        # buttons on somebody's desktop should do.
        if "actions" not in mapped and self.config.get("actions"):
            mapped["actions"] = self.config["actions"]
        return {key: value for key, value in mapped.items() if value is not None}
