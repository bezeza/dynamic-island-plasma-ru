"""Small, side-effect-free helpers for the Telegram smart notification filter.

The filter is deliberately Telegram-specific: unrelated applications keep their
existing behaviour.  Critical notifications and configured important keywords
are handled by the controller before duplicate collapsing is considered.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable


def _normalise(value: object) -> str:
    """Case-fold text and collapse whitespace for stable comparisons."""
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def is_telegram(
    app_name: object = "",
    desktop_entry: object = "",
    display_app_name: object = "",
) -> bool:
    """Return whether any sender identity clearly identifies Telegram."""
    for value in (app_name, desktop_entry, display_app_name):
        candidate = re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())
        if "telegram" in candidate:
            return True
    return False


def matches_keywords(text: object, keywords: object) -> bool:
    """Case-insensitive substring match; blank rules are ignored."""
    haystack = _normalise(text)
    if not haystack or not isinstance(keywords, (list, tuple, set)):
        return False
    for item in keywords:
        needle = _normalise(item)
        if needle and needle in haystack:
            return True
    return False


def important_keyword_matches(summary: object, body: object, keywords: object) -> bool:
    """Important keywords are exceptions to keyword suppression and deduplication."""
    text = f"{summary or ''}\n{body or ''}"
    return matches_keywords(text, keywords)


def telegram_duplicate_key(
    app_name: object,
    desktop_entry: object,
    display_app_name: object,
    summary: object,
    body: object,
) -> tuple[str, str, str] | None:
    """Build a key for identical Telegram notifications, or None for other senders.

    Both summary and body are part of the key to avoid merging two different
    messages that happen to use the same headline.
    """
    if not is_telegram(app_name, desktop_entry, display_app_name):
        return None
    normal_summary = _normalise(summary)
    normal_body = _normalise(body)
    if not normal_summary and not normal_body:
        return None
    sender = (
        _normalise(desktop_entry)
        or _normalise(display_app_name)
        or _normalise(app_name)
        or "telegram"
    )
    return sender, normal_summary, normal_body


@dataclass
class DuplicateRecord:
    notification_id: int
    base_summary: str
    last_seen: float
    count: int = 1


class TelegramDuplicateTracker:
    """Track duplicate Telegram notifications within a configurable time window."""

    def __init__(self) -> None:
        self._records: dict[tuple[str, str, str], DuplicateRecord] = {}

    def match_or_remember(
        self,
        key: tuple[str, str, str],
        notification_id: int,
        summary: str,
        now: float,
        window_seconds: float,
        is_live: Callable[[int], bool],
    ) -> DuplicateRecord | None:
        """Return an incremented record for a duplicate; otherwise remember a first.

        Records whose original card expired or whose time window elapsed are
        discarded.  The first occurrence returns None; a repeat returns the
        original card's record with an incremented count.
        """
        window = max(0.0, float(window_seconds))
        for old_key, record in list(self._records.items()):
            if now - record.last_seen > window or not is_live(record.notification_id):
                self._records.pop(old_key, None)

        record = self._records.get(key)
        if (
            window > 0
            and record is not None
            and now - record.last_seen <= window
            and is_live(record.notification_id)
        ):
            record.count += 1
            record.last_seen = now
            return record

        self._records[key] = DuplicateRecord(
            notification_id=int(notification_id),
            base_summary=str(summary or ""),
            last_seen=float(now),
        )
        return None

    def clear(self) -> None:
        self._records.clear()
