"""The shape every source has, and the polling most of them do.

A source is anything that can produce :class:`~.payload.Item` objects: a feed,
an endpoint that is asked every few minutes, a server that is told.  They all
report through one signal, and the manager above them does not care which is
which.

Polling sources share the two things that are easy to get wrong.  The first is
the fetch itself: it goes through Qt's network stack rather than ``urllib``,
because a blocking read in the main thread freezes the island mid-animation.
The second is the first poll, which is seeded silently — a feed reader that
announces twenty old headlines the moment it starts is a feed reader nobody
keeps running.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtNetwork import (
    QNetworkAccessManager,
    QNetworkReply,
    QNetworkRequest,
)

from .. import __version__, debug
from .payload import Item

#: Nothing is polled faster than this, whatever the configuration says.
MIN_INTERVAL = 30

#: How many new entries one poll may announce before the rest are dropped.
DEFAULT_MAX_ITEMS = 3

USER_AGENT = f"DynamicIsland/{__version__}"

#: How many entry keys one source remembers before the oldest are dropped.
_SEEN_LIMIT = 500


class Source(QObject):
    """Something that produces cards."""

    #: Carries a list of Item — everything one round turned up.
    produced = Signal(object)

    def __init__(self, config: dict[str, Any], parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.config = dict(config)
        self.name = str(config.get("name") or config.get("type") or "source")
        self.app_name = str(config.get("appName") or self.name)
        self.icon = str(config.get("icon") or "")

    def start(self) -> None:
        """Begin producing. Called once, after everything is wired up."""

    def stop(self) -> None:
        """Stop for good — the configuration changed under us."""

    def describe(self) -> str:
        return self.name


class PollingSource(Source):
    """A source that asks the same address over and over."""

    def __init__(self, config: dict[str, Any], parent: QObject | None = None) -> None:
        super().__init__(config, parent)
        self.url = str(config.get("url") or "")
        self.interval = max(MIN_INTERVAL, int(config.get("interval") or 300))
        self.max_items = max(1, int(config.get("maxItems") or DEFAULT_MAX_ITEMS))
        self.headers = {
            str(key): str(value)
            for key, value in (config.get("headers") or {}).items()
        }
        #: Entry keys already shown, so a poll only reports what is new. An
        #: ordered mapping rather than a set, so the trim below can drop the
        #: oldest keys instead of forgetting all of them at once.
        self._seen: OrderedDict[str, None] = OrderedDict()
        #: The first round only fills that set — see the module docstring.
        self._seeded = bool(config.get("announceFirst"))
        #: Kept apart from ``_seeded``, which stops meaning "announceFirst was
        #: asked for" the moment the first round has gone through.
        self._announce_first = bool(config.get("announceFirst"))
        #: True until the first round has been through :meth:`announce`.
        self._first_round = True

        self._network = QNetworkAccessManager(self)
        self._timer = QTimer(self)
        self._timer.setInterval(self.interval * 1000)
        self._timer.timeout.connect(self.poll)
        self._reply: QNetworkReply | None = None

    # -- lifetime ---------------------------------------------------------
    def start(self) -> None:
        if not self.url:
            print(f"[dynamic-island] source {self.name!r} has no url, skipping")
            return
        self._timer.start()
        # Straight away, so a wrong url is reported now and not in five
        # minutes' time.
        QTimer.singleShot(0, self.poll)

    def stop(self) -> None:
        self._timer.stop()
        if self._reply is not None:
            self._reply.abort()

    # -- the round trip ---------------------------------------------------
    def poll(self) -> None:
        if self._reply is not None:
            return  # the last round has not come back yet; skip this one

        request = QNetworkRequest(QUrl(self.url))
        request.setRawHeader(b"User-Agent", USER_AGENT.encode())
        for name, value in self.headers.items():
            request.setRawHeader(name.encode(), value.encode())
        request.setAttribute(
            QNetworkRequest.Attribute.RedirectPolicyAttribute,
            QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy,
        )

        reply = self._network.get(request)
        self._reply = reply
        reply.finished.connect(lambda: self._finished(reply))

    def _finished(self, reply: QNetworkReply) -> None:
        self._reply = None
        error = reply.error()
        data = bytes(reply.readAll())
        reply.deleteLater()

        if error != QNetworkReply.NetworkError.NoError:
            print(f"[dynamic-island] {self.name}: {reply.errorString()}")
            return

        try:
            items = self.parse(data)
        except Exception as failure:  # noqa: BLE001 - a bad feed is not fatal
            print(f"[dynamic-island] {self.name}: cannot read the answer: {failure}")
            return

        self.announce(items)

    def parse(self, data: bytes) -> list[Item]:
        """Turn the body of the answer into items. Implemented per type."""
        raise NotImplementedError

    # -- what is new ------------------------------------------------------
    def announce(self, items: list[Item]) -> None:
        """Emit the items nobody has seen yet, newest first."""
        self._first_round = False
        fresh = [item for item in items if item.key not in self._seen]
        for item in items:
            self._seen[item.key] = None
        # The set would otherwise grow for as long as the island runs. Drop the
        # oldest keys rather than everything but this round's: a rotating feed
        # hands the same entry back for hours, and a set that forgets in one go
        # announces whatever scrolled out and back in as though it were new.
        while len(self._seen) > _SEEN_LIMIT:
            self._seen.popitem(last=False)

        if not self._seeded:
            self._seeded = True
            debug(f"{self.name}: {len(items)} entries on the first round, quietly")
            return
        if not fresh:
            return

        dropped = len(fresh) - self.max_items
        if dropped > 0:
            print(f"[dynamic-island] {self.name}: {dropped} more entries not shown")
        self.produced.emit(fresh[: self.max_items])
