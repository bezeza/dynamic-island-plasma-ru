"""Everything that happens between a D-Bus call and a pixel on screen.

The controller owns the lifetime of a notification: it normalises the incoming
call, decides whether it is shown at all, keeps its expiry timer, and reports
back to the sending application when the user acts on it.
"""

from __future__ import annotations

import os
import subprocess
import time
from html import escape
from typing import Any

from PySide6.QtCore import QObject, Property, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QImage
from PySide6.QtNetwork import (
    QNetworkAccessManager,
    QNetworkReply,
    QNetworkRequest,
)

from . import apps, artwork, debug, sound
from .config import Config
from .history import HistoryStore
from .model import (
    CLOSED_BY_CALL,
    CLOSED_DISMISSED,
    CLOSED_EXPIRED,
    CLOSED_UNDEFINED,
    JOB_HINT,
    MEDIA_HINT,
    MEDIA_INFO_HINT,
    OSD_HINT,
    URGENCY_CRITICAL,
    URGENCY_LOW,
    URGENCY_NORMAL,
    Notification,
    NotificationModel,
)
from .server import NotificationService
from .strings import Strings

#: Where a desktop file may live, in the order XDG looks. The flatpak exports
#: are spelled out because a service started by systemd does not always inherit
#: the session's XDG_DATA_DIRS.
_DATA_DIRS = [
    os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"),
    *(os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":"),
    os.path.expanduser("~/.local/share/flatpak/exports/share"),
    "/var/lib/flatpak/exports/share",
]

#: Job cards live in the same model as notifications but never on the bus, so
#: their ids are kept far away from the ones the server hands out.
JOB_ID_BASE = 1_000_000_001

#: The on-screen displays share one card: a second volume tap replaces the
#: first rather than stacking behind it.
OSD_ID = 2_000_000_001

#: Same idea for what is playing: one card, replaced on every track change.
MEDIA_ID = 2_000_000_002

#: The cover gets a slot of its own in the artwork provider because the volume
#: display shows it too, so it has to outlive the card it arrived with.
MEDIA_ART_ID = 2_000_000_003


class _Countdown:
    """A pausable single-shot timer."""

    def __init__(self, parent: QObject, milliseconds: int, on_fire) -> None:
        self._remaining = milliseconds
        self._started_at = 0.0
        self._timer = QTimer(parent)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(on_fire)
        self.resume()

    def pause(self) -> None:
        if not self._timer.isActive():
            return
        elapsed = (time.monotonic() - self._started_at) * 1000.0
        self._remaining = max(0, int(self._remaining - elapsed))
        self._timer.stop()

    def resume(self) -> None:
        if self._timer.isActive() or self._remaining <= 0:
            return
        self._started_at = time.monotonic()
        self._timer.start(self._remaining)

    def stop(self) -> None:
        self._timer.stop()
        self._timer.deleteLater()


class IslandController(QObject):
    """Exposed to QML as ``Island``."""

    hoveredChanged = Signal()
    inhibitedChanged = Signal()

    def __init__(
        self,
        config: Config,
        model: NotificationModel,
        service: NotificationService,
        provider: artwork.ArtworkProvider,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._config = config
        self._model = model
        self._service = service
        self._provider = provider
        self._timers: dict[int, _Countdown] = {}
        self._hovered = False
        self._history = HistoryStore(int(config.get("historyLength")), self)
        self._strings = Strings(self, str(config.get("language") or ""))
        #: notification id -> the JobView behind it
        self._jobs: dict[int, Any] = {}
        self._job_ids: dict[str, int] = {}
        self._next_job_id = JOB_ID_BASE
        self._media = None
        self._focus = None
        self._sources = None
        self._track = None
        self._cover_image = QImage()
        #: cover url -> image, so skipping back and forth fetches nothing twice
        self._covers: dict[str, QImage] = {}
        self._network = None
        self._cover = ""
        # Bumped per cover so QML reloads the url instead of the cached image.
        self._cover_serial = 0

        # The language is a setting like any other, and the words this side
        # produces — the volume label, the buttons on a copy — have to follow
        # it without a restart.
        config.changed.connect(
            lambda: self._strings.follow(str(config.get("language") or ""))
        )

        service.notify_handler = self._on_notify
        service.close_handler = self._on_close_requested
        service.clear_history_handler = self._history.clear
        # The applet hands over whatever the history entry stored — a desktop
        # file, a desktop entry or a bare application name — so it goes in as
        # both halves of the lookup.
        service.open_handler = lambda entry: apps.show(entry, entry)
        service.start_handler = apps.start
        service.inhibitedChanged.connect(lambda _: self.inhibitedChanged.emit())

    # -- what is playing ---------------------------------------------------
    def attach_media(self, watcher) -> None:
        """Show a track when it starts, with the buttons to steer it."""
        self._media = watcher
        watcher.changed.connect(self._on_track)
        watcher.replaced.connect(self._on_media_replaced)
        # A player that was already running before us says nothing until the
        # track changes, so ask once. Quietly: nobody pressed anything, and a
        # card announcing music that has been playing for an hour is noise.
        # What this is for is the volume display, which draws whatever is
        # playing underneath its bar — and that has to work on the first tap.
        self._adopt(watcher.current())

    def _adopt(self, track) -> None:
        """Take a track on without showing a card for it."""
        self._track = track
        if track is None:
            self._cover = ""
            self._provider.drop(MEDIA_ART_ID)
            return
        self._remember_cover(track)

    def _on_media_replaced(self, track) -> None:
        """A player quit; whatever is left takes over, without a card."""
        self._adopt(track)
        if track is None:
            self._on_media_gone()

    def attach_focus(self, watcher) -> None:
        """Keep the track banner away while the player itself is in front.

        Whoever watches the video in the browser or sits in Spotify sees the
        track change right there; the card would only repeat it.  The banner
        is for music coming from somewhere in the back.
        """
        self._focus = watcher
        watcher.changed.connect(self._on_focus_moved)

    def _on_focus_moved(self) -> None:
        # Walking over to the player is the strongest "seen it" there is: the
        # banner now doubles the window in front of it, so it leaves.
        if self._model.row_of(MEDIA_ID) >= 0 and self._player_in_front():
            self._close(MEDIA_ID, CLOSED_EXPIRED)

    def _player_in_front(self) -> bool:
        """Whether the window the music comes from has the focus."""
        if self._focus is None or self._track is None:
            return False
        if not self._config.get("mediaHideWhenFocused"):
            return False
        return self._focus.is_active(
            apps.needles(self._track.desktop_entry, self._track.identity)
        )

    def _on_track(self, track) -> None:
        self._track = track
        self._remember_cover(track)
        if self._player_in_front():
            # No card — but the track is taken on all the same, so the volume
            # display keeps drawing what is actually playing.  Said out loud
            # for the same reason a suppression is: a banner that never came
            # is otherwise indistinguishable from one that was never sent.
            print(
                f"[dynamic-island] holding the track banner back: "
                f"{track.identity or track.bus_name} has the focus"
            )
            # A card still up belongs to the previous track, possibly another
            # player's — and its buttons would steer this one now. Down it goes.
            if self._model.row_of(MEDIA_ID) >= 0:
                self._close(MEDIA_ID, CLOSED_EXPIRED)
            return
        actions = self._media_actions()
        subtitle = " — ".join(part for part in (track.artist, track.album) if part)

        hints: dict[str, Any] = {
            "desktop-entry": track.desktop_entry,
            "urgency": URGENCY_LOW,
            "suppress-sound": True,
            "transient": True,
            MEDIA_HINT: True,
            MEDIA_INFO_HINT: self._media_payload(),
        }
        # The cover comes as a url; a local one is a file the artwork code can
        # open, anything else stays out and the player icon steps in.
        if track.art_url.startswith("file://"):
            hints["image-path"] = track.art_url

        self._on_notify(
            track.identity or self._strings.word("media"),
            MEDIA_ID,
            "",
            track.title,
            subtitle,
            actions,
            hints,
            int(self._config.get("mediaTimeout")),
            image=self._cover_image,
        )
        self._model.promote(MEDIA_ID)

    def _on_media_gone(self) -> None:
        """The player quit: forget the track, not just its card.

        A pause only stops the music, so the track stays known and keeps being
        drawn under the volume bar; this is the one thing that clears it.
        """
        self._track = None
        self._cover = ""
        self._provider.drop(MEDIA_ART_ID)
        self._close(MEDIA_ID, CLOSED_EXPIRED)

    def _remember_cover(self, track) -> None:
        """Get the cover into the artwork provider, wherever it lives.

        Players disagree about that: some keep a local file, Spotify and the
        browsers hand out an https link. Letting QML fetch the link itself
        looked simpler but left the card showing the previous record while the
        new image was still on its way — and the accent, which is read off the
        artwork, never became the colour of the cover at all. So every cover is
        fetched here and served from the provider under one url that changes
        per track.
        """
        url = track.art_url
        if not url:
            self._show_cover(QImage())
            return

        cached = self._covers.get(url)
        if cached is not None:
            self._show_cover(cached)
            return

        if url.startswith(("http://", "https://")):
            # Nothing to show until it arrives; the card is drawn again when it
            # does, which is the one place a notification redraws itself.
            self._show_cover(QImage())
            self._download_cover(url)
            return

        path = QUrl(url).toLocalFile() if url.startswith("file://") else url
        image = QImage(path)
        self._covers[url] = image
        self._show_cover(image)

    def _show_cover(self, image: QImage) -> None:
        self._cover_image = image
        if image.isNull():
            self._provider.drop(MEDIA_ART_ID)
            self._cover = ""
            return
        self._cover_serial += 1
        self._provider.put(MEDIA_ART_ID, image)
        self._cover = f"image://island/{MEDIA_ART_ID}?{self._cover_serial}"

    def _download_cover(self, url: str) -> None:
        """Fetch a remote cover once, then draw the card again."""
        if self._network is None:
            self._network = QNetworkAccessManager(self)

        reply = self._network.get(QNetworkRequest(QUrl(url)))

        def finished() -> None:
            image = QImage()
            if reply.error() == QNetworkReply.NetworkError.NoError:
                image.loadFromData(reply.readAll())
            reply.deleteLater()
            self._covers[url] = image
            # Only redraw when this is still the record on the turntable.
            if self._track is None or self._track.art_url != url:
                return
            self._show_cover(image)
            # And only when there is a card to redraw: the cover of a track
            # adopted at startup arrives the same way, and that one must not
            # put a card on screen nobody asked for.
            if self._model.row_of(MEDIA_ID) >= 0:
                self._on_track(self._track)

        reply.finished.connect(finished)

    def _media_actions(self) -> list[str]:
        """Previous, play/pause and next — symbols need no translation."""
        if self._track is None:
            return []
        return [
            "media-previous", "\u23ee",
            "media-playpause", "\u23f8" if self._track.playing else "\u25b6",
            "media-next", "\u23ed",
        ]

    def _media_payload(self) -> dict[str, Any]:
        """What is playing, for every card that draws a player."""
        if self._track is None:
            return {}
        return {
            "title": self._track.title,
            "artist": self._track.artist,
            "cover": self._cover,
            "playing": bool(self._track.playing),
        }

    # -- feeds, endpoints and webhooks -------------------------------------
    def attach_sources(self, manager) -> None:
        """Let the configured sources put cards up like any application."""
        self._sources = manager
        manager.notify.connect(self._on_notify)

    # -- on-screen displays -----------------------------------------------
    def attach_osd(self, watcher) -> None:
        """Show what Plasma would have drawn in the middle of the screen."""
        watcher.shown.connect(self._on_osd)
        watcher.hidden.connect(lambda: self._close(OSD_ID, CLOSED_EXPIRED))

    def _on_osd(self, kind: str, icon: str, percent: int, text: str) -> None:
        """One card for all of them, replaced in place.

        Holding the volume key down must move the bar inside the banner that is
        already there rather than stack a new one behind it — which is what
        reusing OSD_ID does: the model treats it as a replacement.
        """
        try:
            label = self._strings.word(kind)
        except KeyError:
            label = ""

        if percent >= 0:
            summary = f"{percent} %"
        else:
            summary = text or label
            label = "" if summary == label else label

        hints: dict[str, Any] = {
            "urgency": URGENCY_LOW,
            "suppress-sound": True,
            "transient": True,
            OSD_HINT: True,
        }
        # A layout switch or a toggle has nothing to fill a bar with; only ask
        # for one when there is a number behind it.
        if percent >= 0:
            hints["value"] = percent
        # Turning the volume down while music runs is about that music, so the
        # banner carries it and the card can draw a small player below the bar.
        media = self._media_payload()
        actions: list[str] = []
        if media:
            hints[MEDIA_INFO_HINT] = media
            # Seeing what is playing is half of it; the buttons belong with it.
            actions = self._media_actions()

        self._on_notify(
            label,  # the kind sits where an application name would
            OSD_ID,
            icon,
            summary,
            text if text and text != summary else "",
            actions,
            hints,
            int(self._config.get("osdTimeout")),
        )
        self._model.promote(OSD_ID)

    # -- file operations --------------------------------------------------
    def attach_jobs(self, service) -> None:
        """Show copy/move/delete progress in the island as well."""
        service.changed.connect(self._on_job_changed)
        service.finished.connect(self._on_job_finished)

    def _on_job_changed(self, job) -> None:
        notification_id = self._job_ids.get(job.path)
        if notification_id is None:
            # Counted up, not derived from how many jobs are open: a finished
            # job leaves the dict, so the length goes back down and the next
            # copy would be handed the id of one that is still running — and
            # would take over its card and its pause and cancel buttons.
            notification_id = self._next_job_id
            self._next_job_id += 1
            self._job_ids[job.path] = notification_id
        self._jobs[notification_id] = job

        actions: list[str] = []
        if job.suspendable:
            actions += [
                "job-suspend",
                self._strings.word("resume" if job.suspended else "pause"),
            ]
        if job.killable:
            actions += ["job-cancel", self._strings.word("cancel")]

        # A job carries no icon of its own; the desktop entry is what artwork
        # needs to find the sending application's.
        self._on_notify(
            job.app_name or job.desktop_entry or self._strings.word("job"),
            notification_id,
            job.icon_name,
            self._job_summary(job),
            self._job_body(job),
            actions,
            {
                "desktop-entry": job.desktop_entry,
                "value": job.percent,
                "urgency": URGENCY_NORMAL,
                "suppress-sound": True,
                "resident": True,
                JOB_HINT: True,
            },
            0,  # stays until the job is over
        )

    def _on_job_finished(self, job) -> None:
        notification_id = self._job_ids.pop(job.path, None)
        if notification_id is None:
            return
        self._jobs.pop(notification_id, None)
        self._close(notification_id, CLOSED_EXPIRED)

    def _job_summary(self, job) -> str:
        """What the operation calls itself: "Copying", "Moving", "Deleting"."""
        return (
            job.info_message
            or getattr(job, "title", "")
            or job.app_name
            or self._strings.word("job")
        )

    @staticmethod
    def _job_body(job) -> str:
        """The description fields, in the order the sender numbered them.

        The card renders its body as styled text, where a newline is just
        whitespace — hence <br>, and hence the escaping around it.
        """
        lines = []
        for _, (label, value) in sorted(job.descriptions.items()):
            if not value:
                continue
            text = escape(str(value))
            lines.append(f"{escape(str(label))}: {text}" if label else text)
        return "<br>".join(lines)

    # -- incoming -------------------------------------------------------
    def _on_notify(
        self,
        app_name: str,
        notification_id: int,
        app_icon: str,
        summary: str,
        body: str,
        raw_actions: list,
        hints: dict[str, Any],
        expire_timeout: int,
        image: QImage | None = None,
    ) -> None:
        urgency = self._urgency(hints)
        reason = self._suppression_reason(app_name, hints, urgency)
        if reason:
            # Dropping a notification without a word is how they go missing, so
            # say so — an inhibition in particular is otherwise invisible.
            print(f"[dynamic-island] suppressed {app_name!r} notification: {reason}")
            # Tell the sender it is gone rather than leaving it waiting for a
            # close that would never come. A file operation has no sender on
            # this bus, so there is nobody to tell.
            if not any(hints.get(key) for key in (JOB_HINT, OSD_HINT, MEDIA_HINT)):
                self._service.emit_closed(notification_id, CLOSED_UNDEFINED)
            return

        # A progress update replacing an existing notification must not beep
        # again on every step.
        is_replacement = self._model.row_of(notification_id) >= 0

        actions, default_action = self._split_actions(raw_actions)
        desktop_entry = str(hints.get("desktop-entry", "") or "")
        # A caller that already holds the artwork — the media card with its
        # downloaded cover — hands it over instead of having it looked up.
        if image is None or image.isNull():
            image = artwork.resolve(hints, app_icon, desktop_entry, app_name=app_name)

        notification = Notification(
            id=notification_id,
            app_name=str(hints.get("x-kde-display-appname", "") or app_name),
            desktop_entry=desktop_entry,
            summary=summary,
            body=body,
            actions=actions,
            default_action=default_action,
            hints=hints,
            urgency=urgency,
            timeout=expire_timeout,
            has_image=not image.isNull(),
            reply_placeholder=self._reply_placeholder(hints, raw_actions),
            resident=bool(hints.get("resident", False)),
            transient=bool(hints.get("transient", False)),
            progress=self._progress(hints),
        )
        notification.accent = (
            artwork.accent_color(image, str(self._config.get("fallbackAccent")))
            if self._config.get("accentFromIcon")
            else str(self._config.get("fallbackAccent"))
        )

        if notification.has_image:
            self._provider.put(notification_id, image)

        self._model.put(notification)
        self._trim_stack()
        self._arm(notification)

        if not is_replacement and self._config.get("soundEnabled"):
            sound.play_for(hints, str(self._config.get("defaultSound") or ""))

    def _on_close_requested(self, notification_id: int) -> None:
        self._close(notification_id, CLOSED_BY_CALL)

    # -- lifetime -------------------------------------------------------
    def _arm(self, notification: Notification) -> None:
        self._disarm(notification.id)
        duration = self._duration_for(notification)
        if duration <= 0:
            return
        countdown = _Countdown(
            self, duration, lambda nid=notification.id: self._close(nid, CLOSED_EXPIRED)
        )
        if self._hovered and self._config.get("pauseOnHover"):
            countdown.pause()
        self._timers[notification.id] = countdown

    def _disarm(self, notification_id: int) -> None:
        countdown = self._timers.pop(notification_id, None)
        if countdown is not None:
            countdown.stop()

    def _duration_for(self, notification: Notification) -> int:
        if notification.timeout > 0:
            return notification.timeout
        if notification.timeout == 0:
            return 0  # the sender asked us to keep it until dismissed
        if notification.urgency == URGENCY_CRITICAL and self._config.get(
            "criticalNeverExpires"
        ):
            return 0
        key = "lowUrgencyTimeout" if notification.urgency == URGENCY_LOW else "defaultTimeout"
        return int(self._config.get(key))

    def _close(self, notification_id: int, reason: int) -> None:
        self._disarm(notification_id)
        notification = self._model.get(notification_id)
        if not self._model.remove(notification_id):
            return
        # Which card went away, and why. Notifications arrive faster than a
        # person can follow, so "it disappeared" is otherwise unattributable —
        # and it is what the selftest asserts on.
        debug(f"closed #{notification_id} reason={reason}")
        # A file operation was never a notification: nobody is waiting for a
        # NotificationClosed with this id, and a finished copy is not something
        # to keep in the history either.
        is_job = notification is not None and bool(
            notification.hints.get(JOB_HINT)
            or notification.hints.get(OSD_HINT)
            or notification.hints.get(MEDIA_HINT)
        )

        if notification is not None and not is_job:
            self._history.add(notification, self._provider.get(notification_id))
        self._provider.drop(notification_id)
        if not is_job:
            self._service.emit_closed(notification_id, reason)

    def _trim_stack(self) -> None:
        """Drop the oldest entries once the stack grows past what we render."""
        limit = max(1, int(self._config.get("stackDepth")) + 1)
        for notification_id in self._model.ids()[limit:]:
            self._close(notification_id, CLOSED_EXPIRED)

    # -- filtering ------------------------------------------------------
    def _suppression_reason(
        self, app_name: str, hints: dict[str, Any], urgency: int
    ) -> str:
        """Why this notification is not shown — empty string means it is."""
        if urgency == URGENCY_CRITICAL:
            return ""  # critical always gets through
        if self._config.get("doNotDisturb"):
            return "do not disturb is on"
        if self._service.inhibited:
            return "another application is holding a notification inhibition"
        blacklist = {str(entry).lower() for entry in self._config.get("blacklist") or []}
        candidates = {
            app_name.lower(),
            str(hints.get("desktop-entry", "")).lower(),
        }
        if blacklist & candidates:
            return "the sender is on the blacklist"
        return ""

    # -- normalisation ---------------------------------------------------
    @staticmethod
    def _urgency(hints: dict[str, Any]) -> int:
        try:
            value = int(hints.get("urgency", URGENCY_NORMAL))
        except (TypeError, ValueError):
            return URGENCY_NORMAL
        return value if value in (URGENCY_LOW, URGENCY_NORMAL, URGENCY_CRITICAL) else URGENCY_NORMAL

    @staticmethod
    def _split_actions(raw: list) -> tuple[list[dict[str, str]], str]:
        """``[key, label, key, label…]`` → button list plus the default action."""
        actions: list[dict[str, str]] = []
        default_action = ""
        pairs = list(zip(raw[::2], raw[1::2]))
        for key, label in pairs:
            key, label = str(key), str(label)
            if key == "default":
                default_action = key
                continue
            if key == "inline-reply":
                continue  # rendered as the reply field, not as a button
            actions.append({"key": key, "label": label or key})
        return actions, default_action

    @staticmethod
    def _reply_placeholder(hints: dict[str, Any], raw_actions: list) -> str:
        keys = {str(key) for key in raw_actions[::2]}
        if "inline-reply" not in keys:
            return ""
        return str(hints.get("x-kde-reply-placeholder-text", "") or "Reply…")

    @staticmethod
    def _progress(hints: dict[str, Any]) -> int:
        if "value" not in hints:
            return -1
        try:
            return max(0, min(100, int(hints["value"])))
        except (TypeError, ValueError):
            return -1

    # -- QML API ---------------------------------------------------------
    @Slot(int)
    def dismiss(self, notification_id: int) -> None:
        self._close(notification_id, CLOSED_DISMISSED)

    @Slot()
    def dismissAll(self) -> None:
        for notification_id in self._model.ids():
            self._close(notification_id, CLOSED_DISMISSED)

    @Slot(int, str)
    def invokeAction(self, notification_id: int, action_key: str) -> None:
        notification = self._model.get(notification_id)
        if notification is None:
            return

        # The player buttons ride along on the volume banner too, so route by
        # the key rather than by which card it was drawn on.
        member = {
            "media-previous": "Previous",
            "media-playpause": "PlayPause",
            "media-next": "Next",
        }.get(action_key)
        if member is not None:
            if self._media is not None and self._track is not None:
                self._media.command(self._track.bus_name, member)
            return

        # A card from a feed, an endpoint or a webhook carries its buttons in
        # the manager rather than on the bus: there is no sender to tell.
        if self._sources is not None and self._sources.owns(notification_id):
            self._sources.handle_action(notification_id, action_key)
            if not notification.resident:
                self._close(notification_id, CLOSED_DISMISSED)
            return

        job = self._jobs.get(notification_id)
        if job is not None:
            # The buttons on a file operation talk to the copying application,
            # not to the notification bus. The card stays: the sender answers
            # by suspending or terminating, and that is what redraws it.
            if action_key == "job-cancel":
                job.request_cancel()
            elif action_key == "job-suspend":
                job.request_resume() if job.suspended else job.request_suspend()
            return

        self._service.emit_action(notification_id, action_key)
        if not notification.resident:
            self._close(notification_id, CLOSED_DISMISSED)

    @Slot(int)
    def activateDefault(self, notification_id: int) -> None:
        notification = self._model.get(notification_id)
        if notification is None:
            return
        # The track banner, and the volume display while it carries one: a
        # click on either means the player. The card stays — its buttons are
        # what a click on it is usually for, and losing the banner because you
        # reached for the wrong pixel is rude.
        plays_media = notification_id == MEDIA_ID or (
            notification_id == OSD_ID and bool(notification.hints.get(MEDIA_INFO_HINT))
        )
        if plays_media:
            if self._track is not None:
                apps.show(self._track.desktop_entry, self._track.identity)
            return

        if notification_id == OSD_ID:
            self.dismiss(notification_id)  # a volume bar has nothing to open
            return

        if notification_id in self._jobs:
            # A stray click must not disturb a running copy; the card has its
            # own buttons, and swiping still gets rid of it.
            return

        if self._sources is not None and self._sources.owns(notification_id):
            # A feed entry or a webhook card. Its click opens the link it
            # carried, and there is no application behind it to bring
            # forward — "heise online" is not something to look for a window
            # of, however much the card says it came from there.
            if notification.default_action:
                self.invokeAction(notification_id, notification.default_action)
            else:
                self.dismiss(notification_id)
            return

        if notification.default_action:
            self.invokeAction(notification_id, notification.default_action)
            # And bring the sender forward ourselves. The action tells the
            # application which message the click was about — Discord opens
            # that channel, a mail client that mail — but the window it
            # happens in cannot come to the front on its own: on Wayland only
            # the holder of an activation token may raise a window, and the
            # spec's way of handing one over (the ActivationToken signal)
            # needs a token from the compositor that KWin only gives to
            # surfaces, not over the bus. Without this the application does
            # the right thing where nobody can see it.
            self._raise_sender(notification)
            return

        # Nothing declared, so do what a person expects from a click: bring up
        # whoever is talking. Most applications are single-instance and raise
        # their existing window rather than starting a second one.
        self._raise_sender(notification)
        self.dismiss(notification_id)

    @staticmethod
    def _raise_sender(notification) -> None:
        """Show the application behind a notification, if we can name it.

        Both names go in: the desktop entry is the reliable one, the app name
        is what is left when the sender did not send an entry — and "Firefox"
        still finds firefox.desktop.
        """
        apps.show(notification.desktop_entry, notification.app_name)

    @Slot(int, str)
    def sendReply(self, notification_id: int, text: str) -> None:
        text = text.strip()
        if not text:
            return
        self._service.emit_reply(notification_id, text)
        self._close(notification_id, CLOSED_DISMISSED)

    @Slot(bool)
    def setHovered(self, hovered: bool) -> None:
        if hovered == self._hovered:
            return
        self._hovered = hovered
        self.hoveredChanged.emit()
        if not self._config.get("pauseOnHover"):
            return
        for countdown in self._timers.values():
            countdown.pause() if hovered else countdown.resume()

    def _is_hovered(self) -> bool:
        return self._hovered

    def _is_inhibited(self) -> bool:
        return self._service.inhibited

    hovered = Property(bool, _is_hovered, notify=hoveredChanged)
    inhibited = Property(bool, _is_inhibited, notify=inhibitedChanged)
