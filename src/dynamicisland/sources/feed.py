"""RSS and Atom, without a feed library.

Both formats are a list of entries with a title, a link and an id, and the only
real work is that they disagree about where those live: RSS says
``item/title``, ``item/link``, ``item/guid`` and ``item/pubDate``, Atom says
``entry/title``, ``link href=``, ``entry/id`` and ``entry/updated`` — and half
the feeds in the wild mix the two.  Reading both with ElementTree is a page of
code and keeps the island free of dependencies a distribution would have to
package.

Two rules decide what actually reaches the screen, and both come from the same
idea: a notification is about something that just happened.

* The first round after a start is silent.  It only writes down what is
  already in the feed, so switching the machine on does not replay the night.
* After that, an entry has to be new *and* dated after the island came up.  A
  feed that reorders itself, republishes an old article or hands out a fresh
  id for one cannot make yesterday's news pop up today.
"""

from __future__ import annotations

import email.utils
import os
import re
import time
from datetime import datetime, timezone
from typing import Any
from xml.etree import ElementTree

from PySide6.QtCore import QUrl
from PySide6.QtNetwork import QNetworkReply, QNetworkRequest

from .. import debug
from .base import PollingSource, USER_AGENT
from .payload import Action, Item, urgency

#: Atom puts everything in a namespace; RSS mostly does not.
_ATOM = "{http://www.w3.org/2005/Atom}"

#: Feed descriptions are HTML far more often than they admit, and a card shows
#: a small amount of styled text — so the markup goes, and the entities with it.
_TAGS = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")

_ENTITIES = {
    "&nbsp;": " ", "&amp;": "&", "&lt;": "<", "&gt;": ">",
    "&quot;": '"', "&#39;": "'", "&apos;": "'", "&hellip;": "…",
}

#: Where a feed's own icon is kept once it has been fetched.
ICON_DIR = os.path.join(
    os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"),
    "dynamic-island", "feed-icons",
)


def plain(text: str, limit: int = 220) -> str:
    """A summary line out of whatever the feed calls a description."""
    text = _TAGS.sub(" ", text or "")
    for entity, character in _ENTITIES.items():
        text = text.replace(entity, character)
    text = _SPACE.sub(" ", text).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def when(text: str) -> float:
    """A feed's idea of a date, as seconds since the epoch. 0 if unreadable.

    RSS uses RFC 822 ("Wed, 06 Aug 2026 19:12:00 +0200"), Atom uses RFC 3339
    ("2026-08-06T19:12:00+02:00"), and both turn up in both.
    """
    text = (text or "").strip()
    if not text:
        return 0.0
    try:
        parsed = email.utils.parsedate_to_datetime(text)
    except (TypeError, ValueError):
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return 0.0

    # Plenty of feeds leave the zone off, and a naive datetime's .timestamp()
    # reads it as *this machine's* local time. East of Greenwich that dates
    # every such entry into the past, so the "old news" guard below hides
    # genuinely new entries for as many hours as the offset — and puts them in
    # _seen on the way out, where they can never come back. UTC is the only
    # defensible reading of a date that does not say.
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


class FeedSource(PollingSource):
    """One RSS or Atom feed."""

    def __init__(self, config: dict[str, Any], parent=None) -> None:
        super().__init__(config, parent)
        # A feed is news, not an alarm; the default urgency keeps it quiet.
        self.urgency = urgency(config.get("urgency", "low"))
        # Not `or -1`: 0 is a timeout, and the one that means the most — the
        # card stays until it is dismissed. Falsy-zero would turn it into "use
        # the default" and the entry would be gone in four seconds.
        timeout = config.get("timeout")
        self.timeout = -1 if timeout is None else int(timeout)
        pattern = str(config.get("filter") or "")
        # Only headlines matching this are shown — a feed is often 90 % noise
        # and one keyword is the difference between useful and unusable.
        self.filter = re.compile(pattern, re.IGNORECASE) if pattern else None

        #: Nothing published before the island came up is news to it.
        self.started = time.time()
        #: A feed that dates nothing at all cannot be held to that rule.
        self.trust_dates = bool(config.get("trustDates", True))

        #: The feed's own icon, once it is on disk. Until then the generic one.
        self._icon_file = self._cached_icon()
        self._icon_pending = False

    def start(self) -> None:
        # Ask for the site's favicon before the first entry is even read: the
        # first round is silent anyway, so by the time a card is drawn the
        # feed's own picture is on disk and the generic RSS glyph never shows.
        if not self._icon_file and not self.icon:
            url = QUrl(self.url)
            if url.host():
                self._fetch_icon(f"{url.scheme() or 'https'}://{url.host()}/favicon.ico")
        super().start()

    # -- icon ---------------------------------------------------------------
    def _cache_name(self) -> str:
        host = QUrl(self.url).host() or self.name
        return os.path.join(ICON_DIR, re.sub(r"[^a-zA-Z0-9.-]", "_", host) + ".img")

    def _cached_icon(self) -> str:
        path = self._cache_name()
        return path if os.path.exists(path) else ""

    def _icon_for_cards(self) -> str:
        """What the card shows: the configured icon, the feed's own, or RSS."""
        return self.icon or self._icon_file or "internet-news-reader"

    def _fetch_icon(self, source: str) -> None:
        """Put the feed's own picture next to the cards it produces.

        A feed says what it looks like — Atom in ``icon`` or ``logo``, RSS in
        ``channel/image/url`` — and a site that says nothing still almost
        always has a favicon. It is fetched once and kept, because a card is
        drawn from a local file and nothing here should wait on the network.
        """
        if self._icon_file or self._icon_pending or not source:
            return
        self._icon_pending = True

        request = QNetworkRequest(QUrl(source))
        request.setRawHeader(b"User-Agent", USER_AGENT.encode())
        request.setAttribute(
            QNetworkRequest.Attribute.RedirectPolicyAttribute,
            QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy,
        )
        reply = self._network.get(request)

        def finished() -> None:
            self._icon_pending = False
            data = bytes(reply.readAll())
            error = reply.error()
            reply.deleteLater()
            if error != QNetworkReply.NetworkError.NoError or not data:
                debug(f"{self.name}: no icon at {source}")
                return
            try:
                os.makedirs(ICON_DIR, exist_ok=True)
                path = self._cache_name()
                with open(path, "wb") as handle:
                    handle.write(data)
            except OSError as failure:
                print(f"[dynamic-island] {self.name}: cannot keep the icon: {failure}")
                return
            self._icon_file = path
            debug(f"{self.name}: icon from {source}")

        reply.finished.connect(finished)

    def _icon_source(self, root) -> str:
        """Where this feed says its picture is, or its site's favicon."""
        for path in (f"{_ATOM}icon", f"{_ATOM}logo",
                     ".//channel/image/url", ".//image/url"):
            node = root.find(path)
            if node is not None and (node.text or "").strip():
                return node.text.strip()
        url = QUrl(self.url)
        host = url.host()
        return f"{url.scheme() or 'https'}://{host}/favicon.ico" if host else ""

    # -- parsing ------------------------------------------------------------
    def parse(self, data: bytes) -> list[Item]:
        root = ElementTree.fromstring(data)
        entries = root.findall(".//item") or root.findall(f".//{_ATOM}entry")
        title = self._text(root.find(".//channel/title")) or self._text(
            root.find(f"{_ATOM}title")
        )
        self._fetch_icon(self._icon_source(root))

        items: list[Item] = []
        for entry in entries:
            item = self._entry(entry, title)
            if item is not None:
                items.append(item)
        return items

    def announce(self, items: list[Item]) -> None:
        """Only what is new, and only what happened while we were watching.

        :class:`PollingSource` already drops everything it has seen before.
        What is left can still be old: a feed may reorder, republish, or hand
        an old article a new id, and none of that is a reason to interrupt
        somebody. So an entry that says when it was written has to say
        "after the island started".
        """
        # ...unless the first round was asked to speak. announceFirst wants
        # what is already in the feed, and all of that is older than this
        # process by definition — running the date gate over it would swallow
        # every entry and file it under _seen, where nothing comes back from.
        if self.trust_dates and not (self._announce_first and self._first_round):
            fresh = []
            for item in items:
                stamp = getattr(item, "published", 0.0)
                if stamp and stamp < self.started:
                    # Seen for the first time, but written before we were
                    # here. Remember it so it never comes up, and move on.
                    self._seen[item.key] = None
                    continue
                fresh.append(item)
            items = fresh
        super().announce(items)

    # -- one entry ----------------------------------------------------------
    def _entry(self, entry, feed_title: str) -> Item | None:
        headline = plain(
            self._text(entry.find("title")) or self._text(entry.find(f"{_ATOM}title")),
            limit=120,
        )
        if not headline:
            return None
        if self.filter is not None and not self.filter.search(headline):
            return None

        summary = plain(
            self._text(entry.find("description"))
            or self._text(entry.find(f"{_ATOM}summary"))
            or self._text(entry.find(f"{_ATOM}content"))
        )
        link = self._link(entry)
        key = (
            self._text(entry.find("guid"))
            or self._text(entry.find(f"{_ATOM}id"))
            or link
            or headline
        )

        item = Item(
            key=key,
            summary=headline,
            body=summary,
            app_name=self.app_name or feed_title or "Feed",
            icon=self._icon_for_cards(),
            urgency=self.urgency,
            timeout=self.timeout,
            default=self._open(link),
        )
        # Carried beside the card's own fields: announce() reads it, nothing
        # further down the line needs to know a feed has dates at all.
        item.published = self._published(entry)
        return item

    @staticmethod
    def _published(entry) -> float:
        for path in ("pubDate", "date", f"{_ATOM}published", f"{_ATOM}updated"):
            node = entry.find(path)
            if node is not None and (node.text or "").strip():
                stamp = when(node.text)
                if stamp:
                    return stamp
        return 0.0

    @staticmethod
    def _open(link: str):
        return Action(key="default", label="Open", kind="url", url=link) if link else None

    @staticmethod
    def _text(node) -> str:
        return (node.text or "").strip() if node is not None else ""

    @staticmethod
    def _link(entry) -> str:
        node = entry.find("link")
        if node is not None and (node.text or "").strip():
            return node.text.strip()
        # Atom hangs the address off the attribute and may offer several;
        # the one that is meant for a reader is rel="alternate" or none at all.
        for node in entry.findall(f"{_ATOM}link"):
            rel = node.get("rel", "alternate")
            if rel == "alternate" and node.get("href"):
                return node.get("href", "")
        node = entry.find(f"{_ATOM}link")
        return node.get("href", "") if node is not None else ""
