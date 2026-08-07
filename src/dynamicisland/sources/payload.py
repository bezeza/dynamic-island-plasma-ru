"""What a source hands over, and how loosely it is allowed to say it.

Everything that is not a desktop application — a feed, a REST endpoint, a
webhook from a machine on the network — arrives here as a plain dictionary and
leaves as an :class:`Item`.  Being strict about that boundary is what lets the
rest of the island stay ignorant of where a card came from: by the time the
controller sees it, a Grafana alert and a Discord message are the same thing.

The parsing is deliberately forgiving about names — ``summary``/``title``,
``body``/``message``/``text`` — because the sender is usually a webhook
template someone wrote in a hurry, and a notification that does not arrive is
worse than one that guessed which field was the headline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: What a button may do. Anything else is refused at parse time.
#:
#: Deliberately short. The island receives; it is not a client for anything,
#: so there is no kind that calls an endpoint back — a button either opens
#: something here, runs something here, or just puts the card away.
KINDS = ("url", "command", "dismiss")

URGENCY_LOW = 0
URGENCY_NORMAL = 1
URGENCY_CRITICAL = 2

_URGENCY_WORDS = {
    "low": URGENCY_LOW, "normal": URGENCY_NORMAL, "default": URGENCY_NORMAL,
    "high": URGENCY_CRITICAL, "critical": URGENCY_CRITICAL,
    "warning": URGENCY_NORMAL, "error": URGENCY_CRITICAL,
    "info": URGENCY_LOW,
}


@dataclass
class Action:
    """One button, or the click on the card itself."""

    key: str
    label: str
    kind: str = "url"
    url: str = ""
    command: list[str] = field(default_factory=list)


@dataclass
class Item:
    """One card's worth of content, in the island's own terms."""

    #: Stable within its source: the same key twice replaces the card rather
    #: than stacking a second one. A feed uses the entry's guid for this.
    key: str
    summary: str
    body: str = ""
    app_name: str = ""
    #: A theme icon name, an absolute path, or a file:// url.
    icon: str = ""
    urgency: int = URGENCY_NORMAL
    #: Milliseconds. -1 leaves it to the island's own default, 0 stays put.
    timeout: int = -1
    #: 0..100, or -1 for a card without a progress bar.
    progress: int = -1
    actions: list[Action] = field(default_factory=list)
    #: What a click on the card does, if anything.
    default: Action | None = None
    #: When the thing itself happened, as seconds since the epoch, or 0 when
    #: the source does not say. A feed uses it to keep old news old.
    published: float = 0.0


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _first(raw: dict[str, Any], *names: str) -> str:
    for name in names:
        if name in raw:
            text = _text(raw[name])
            if text:
                return text
    return ""


def urgency(value: Any) -> int:
    """A number, or the word a monitoring system would have used."""
    if isinstance(value, bool):
        return URGENCY_NORMAL
    if isinstance(value, (int, float)):
        return max(URGENCY_LOW, min(URGENCY_CRITICAL, int(value)))
    word = _text(value).lower()
    return _URGENCY_WORDS.get(word, URGENCY_NORMAL)


def parse_action(raw: Any, index: int, allow_commands: bool) -> Action | None:
    """One entry of an ``actions`` list.

    A bare string is taken as a url, which is what most senders mean when they
    put a link next to a message.
    """
    if isinstance(raw, str):
        raw = {"url": raw}
    if not isinstance(raw, dict):
        return None

    url = _first(raw, "url", "link", "href")
    command = raw.get("command")
    kind = _text(raw.get("kind") or raw.get("type")).lower()
    if not kind:
        # Guessed from what the sender bothered to fill in.
        kind = "command" if command else ("url" if url else "dismiss")
    if kind not in KINDS:
        return None

    if kind == "command":
        if not allow_commands:
            # Running commands for a stranger on the network is a remote
            # shell; it stays off until the user says otherwise in the config.
            return None
        if isinstance(command, str):
            command = command.split()
        if not isinstance(command, (list, tuple)) or not command:
            return None
        command = [str(part) for part in command]
    else:
        command = []

    if kind == "url" and not url:
        return None

    label = _first(raw, "label", "title", "text") or {
        "url": "Open", "command": "Run", "dismiss": "Dismiss",
    }[kind]

    return Action(
        key=_text(raw.get("key")) or f"source-action-{index}",
        label=label,
        kind=kind,
        url=url,
        command=list(command),
    )


def parse_item(raw: Any, allow_commands: bool = False,
               app_name: str = "", icon: str = "") -> Item | None:
    """A payload as it arrived, or None when there is nothing to show.

    *app_name* and *icon* are what the source is configured to call itself;
    the payload may override both, which is what lets one webhook endpoint
    serve several services.
    """
    if not isinstance(raw, dict):
        return None

    summary = _first(raw, "summary", "title", "subject", "headline")
    body = _first(raw, "body", "message", "text", "description", "content")
    if not summary and not body:
        return None
    if not summary:
        # A message without a headline reads better as the headline itself.
        summary, body = body, ""

    actions: list[Action] = []
    for index, entry in enumerate(raw.get("actions") or []):
        action = parse_action(entry, index, allow_commands)
        if action is not None:
            actions.append(action)

    default: Action | None = None
    if raw.get("default") is not None:
        default = parse_action(raw["default"], 0, allow_commands)
    if default is None:
        link = _first(raw, "url", "link", "href")
        if link:
            # A card that carries a link opens it when clicked; that is what
            # every feed reader has done since feeds existed.
            default = Action(key="default", label="Open", kind="url", url=link)

    timeout = raw.get("timeout")
    try:
        timeout = int(timeout) if timeout is not None else -1
    except (TypeError, ValueError):
        timeout = -1

    progress = raw.get("progress")
    try:
        progress = max(0, min(100, int(progress))) if progress is not None else -1
    except (TypeError, ValueError):
        progress = -1

    return Item(
        key=_first(raw, "id", "key", "guid") or summary,
        summary=summary,
        body=body,
        app_name=_first(raw, "appName", "app_name", "app", "source") or app_name,
        icon=_first(raw, "icon", "iconName", "image") or icon,
        urgency=urgency(raw.get("urgency", raw.get("priority", raw.get("level")))),
        timeout=timeout,
        progress=progress,
        actions=actions,
        default=default,
    )
