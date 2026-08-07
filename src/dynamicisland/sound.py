"""Playing whatever sound an application asked us to play.

The notification spec lets a sender pick the sound three ways:

* ``suppress-sound`` — stay quiet, whatever else the hints say,
* ``sound-file`` — an absolute path to play,
* ``sound-name`` — an event id from the XDG sound naming spec, resolved
  against the user's sound theme.

Taking the notification service over means taking this job over too: nothing
else in the session will play these for us any more.

libcanberra does the theme lookup and the PulseAudio/PipeWire plumbing, so we
bind to it directly and only fall back to spawning a helper if it is missing.
"""

from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
from typing import Any

from . import APP_ID

CA_SUCCESS = 0


class _Canberra:
    """Thin ctypes binding to the bits of libcanberra we need."""

    def __init__(self) -> None:
        self._lib = ctypes.CDLL("libcanberra.so.0")
        self._lib.ca_context_create.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
        self._lib.ca_context_create.restype = ctypes.c_int
        self._lib.ca_context_play.restype = ctypes.c_int
        self._lib.ca_context_change_props.restype = ctypes.c_int

        context = ctypes.c_void_p()
        if self._lib.ca_context_create(ctypes.byref(context)) != CA_SUCCESS:
            raise OSError("ca_context_create failed")
        self._context = context

        self._call(
            self._lib.ca_context_change_props,
            b"application.name",
            b"Dynamic Island",
            b"application.id",
            APP_ID.encode(),
        )

    def _call(self, function, *properties: bytes) -> int:
        # Both entry points take NULL-terminated key/value pairs of char*.
        arguments: list[Any] = [self._context]
        if function is self._lib.ca_context_play:
            arguments.append(ctypes.c_uint32(0))
        arguments += [ctypes.c_char_p(item) for item in properties]
        arguments.append(None)
        return function(*arguments)

    def play(self, *properties: bytes) -> bool:
        return self._call(self._lib.ca_context_play, *properties) == CA_SUCCESS


_canberra: _Canberra | None = None
_canberra_tried = False


def _backend() -> _Canberra | None:
    global _canberra, _canberra_tried
    if not _canberra_tried:
        _canberra_tried = True
        try:
            _canberra = _Canberra()
        except (OSError, AttributeError) as error:
            print(f"[dynamic-island] no libcanberra ({error}), falling back")
            _canberra = None
    return _canberra


def _spawn(command: list[str]) -> None:
    try:
        subprocess.Popen(
            command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
    except OSError as error:
        print(f"[dynamic-island] could not play a sound: {error}")


def play_file(path: str) -> None:
    if not os.path.isfile(path):
        return
    backend = _backend()
    if backend is not None and backend.play(
        b"media.filename", path.encode(), b"media.role", b"event"
    ):
        return
    player = shutil.which("paplay") or shutil.which("pw-play")
    if player:
        _spawn([player, path])


def play_event(name: str) -> None:
    """Play an XDG sound-theme event id such as ``message`` or ``bell``."""
    if not name:
        return
    backend = _backend()
    if backend is not None and backend.play(
        b"event.id", name.encode(), b"media.role", b"event"
    ):
        return
    player = shutil.which("canberra-gtk-play")
    if player:
        _spawn([player, "-i", name])


def play_for(hints: dict[str, Any], default_event: str = "") -> None:
    """Play whatever *hints* asked for, or *default_event* when they asked for nothing."""
    if hints.get("suppress-sound"):
        return

    path = hints.get("sound-file") or hints.get("sound_file")
    if isinstance(path, str) and path:
        play_file(path)
        return

    name = hints.get("sound-name") or hints.get("sound_name")
    if isinstance(name, str) and name:
        play_event(name)
        return

    play_event(default_event)
