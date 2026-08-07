"""What is playing, and the buttons to steer it.

Every media player on the session bus registers a name under
``org.mpris.MediaPlayer2.*`` and announces what it is doing through the usual
``PropertiesChanged`` signal.  Listening to that is enough to notice a track
change, get its title, artist and cover, and to send Previous / PlayPause /
Next back — no player-specific code anywhere.

Unlike the notification service this takes nothing away from anybody: we only
listen, so Plasma's media applet keeps working exactly as before.
"""

from __future__ import annotations

from typing import Any

import dbus

from PySide6.QtCore import QObject, Signal

from .server import plain

PREFIX = "org.mpris.MediaPlayer2."
PATH = "/org/mpris/MediaPlayer2"
PLAYER = "org.mpris.MediaPlayer2.Player"
ROOT = "org.mpris.MediaPlayer2"
PROPERTIES = "org.freedesktop.DBus.Properties"


class Track:
    """The bits of a track worth putting on a card."""

    def __init__(self, bus_name: str) -> None:
        self.bus_name = bus_name
        self.identity = ""
        self.desktop_entry = ""
        self.title = ""
        self.artist = ""
        self.album = ""
        self.art_url = ""
        self.playing = False
        #: Bumped whenever this player says anything, so the newest one wins
        #: when several are loaded at once.
        self.seen = 0

    @property
    def key(self) -> tuple:
        """What has to change before this counts as a new track."""
        return (self.bus_name, self.title, self.artist, self.album)


class MediaWatcher(QObject):
    """Watches every MPRIS player at once and reports the one that moved."""

    #: A track started, changed or was paused; carries a Track.
    changed = Signal(object)
    #: A player went away and another one — or nothing — takes over. Carries a
    #: Track or None, and unlike ``changed`` it is not worth a card of its own.
    replaced = Signal(object)

    def __init__(self, parent: QObject | None = None, claim: bool = True) -> None:
        super().__init__(parent)
        self._bus = None
        self._tracks: dict[str, Track] = {}
        self._tick = 0
        self.watching = False
        if not claim:
            return

        try:
            self._bus = dbus.SessionBus()
            self._bus.add_signal_receiver(
                self._on_properties,
                signal_name="PropertiesChanged",
                dbus_interface=PROPERTIES,
                path=PATH,
                sender_keyword="sender",
            )
            self._bus.add_signal_receiver(
                self._on_name_owner,
                signal_name="NameOwnerChanged",
                dbus_interface="org.freedesktop.DBus",
            )
            self.watching = True
        except dbus.DBusException as error:
            print(f"[dynamic-island] cannot watch media players: {error}")
            return

        # Players that were already running before we started.
        for name in self._bus.list_names():
            if str(name).startswith(PREFIX):
                self._read(str(name))

    # -- what is playing right now -----------------------------------------
    def current(self) -> Track | None:
        """The track the island should be showing, without being told.

        Nothing announces itself on the bus when a player has been sitting
        there since before we started, so anything that wants to draw a player
        — the volume display above all — has to ask.
        """
        known = [track for track in self._tracks.values() if track.title]
        if not known:
            return None
        # A player that is actually playing beats one that is merely loaded;
        # between equals, whichever spoke last.
        known.sort(key=lambda track: (track.playing, track.seen))
        return known[-1]

    # -- bus ---------------------------------------------------------------
    def _on_name_owner(self, name, old_owner, new_owner) -> None:
        name = str(name)
        if not name.startswith(PREFIX):
            return
        if not str(new_owner):
            if self._tracks.pop(name, None) is None:
                return
            # Whoever is left takes over — and when nobody is, that is what
            # None means. Either way it is not news, so it is not a card.
            self.replaced.emit(self.current())

    def _on_properties(self, interface, changed, invalidated, sender=None) -> None:
        if str(interface) != PLAYER:
            return
        name = self._name_of(sender)
        if name is None:
            return

        track = self._tracks.get(name)
        if track is None:
            # First word from this player. A signal only carries what actually
            # changed, so a bare PlaybackStatus would leave us with a track
            # that has no title and never gets shown — read the rest now.
            self._read(name)
            track = self._tracks.get(name) or Track(name)

        before = (track.key, track.playing)
        self._identify(track)
        self._apply(track, plain(changed))
        self._tick += 1
        track.seen = self._tick
        self._tracks[name] = track

        # Property signals arrive in bursts; only say something when the card
        # would actually look different.
        if (track.key, track.playing) != before and track.title:
            self.changed.emit(track)

    def _name_of(self, sender: str | None) -> str | None:
        """Map the unique sender back to its org.mpris.MediaPlayer2.* name."""
        if sender is None:
            return None
        for name in self._bus.list_names():
            name = str(name)
            if not name.startswith(PREFIX):
                continue
            try:
                if str(self._bus.get_name_owner(name)) == str(sender):
                    return name
            except dbus.DBusException:
                continue
        return None

    def _read(self, name: str) -> None:
        track = self._tracks.get(name) or Track(name)
        self._identify(track)
        try:
            properties = plain(
                self._bus.call_blocking(
                    name, PATH, PROPERTIES, "GetAll", "s", (PLAYER,), timeout=2
                )
            )
        except dbus.DBusException:
            return
        self._apply(track, properties)
        self._tick += 1
        track.seen = self._tick
        self._tracks[name] = track

    def _identify(self, track: Track) -> None:
        if track.identity:
            return
        for key, attribute in (("Identity", "identity"),
                               ("DesktopEntry", "desktop_entry")):
            try:
                value = self._bus.call_blocking(
                    track.bus_name, PATH, PROPERTIES, "Get", "ss",
                    (ROOT, key), timeout=2,
                )
                setattr(track, attribute, str(plain(value)))
            except dbus.DBusException:
                pass

    @staticmethod
    def _apply(track: Track, properties: dict[str, Any]) -> None:
        status = properties.get("PlaybackStatus")
        if status is not None:
            track.playing = str(status) == "Playing"

        metadata = properties.get("Metadata")
        if not isinstance(metadata, dict):
            return
        track.title = str(metadata.get("xesam:title", "") or "")
        track.album = str(metadata.get("xesam:album", "") or "")
        track.art_url = str(metadata.get("mpris:artUrl", "") or "")

        artists = metadata.get("xesam:artist") or metadata.get("xesam:albumArtist")
        if isinstance(artists, (list, tuple)):
            track.artist = ", ".join(str(name) for name in artists if name)
        else:
            track.artist = str(artists or "")

    # -- what the buttons do -----------------------------------------------
    def command(self, bus_name: str, member: str) -> None:
        """Previous, PlayPause or Next, straight at the player."""
        if self._bus is None:
            return
        try:
            self._bus.call_blocking(bus_name, PATH, PLAYER, member, "", (), timeout=2)
        except dbus.DBusException as error:
            print(f"[dynamic-island] {member} failed: {error}")
