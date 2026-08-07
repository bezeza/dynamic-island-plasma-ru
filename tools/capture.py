#!/usr/bin/env python3
"""Grab a rectangle of the screen, repeatedly, straight out of the compositor.

Making a picture of the island is harder than it sounds: it lives on a
layer-shell surface above everything else, so a window screenshot cannot see
it, and a screen recorder wants a portal dialog clicked before it starts.

KWin will hand out an area over D-Bus without any of that — ``CaptureArea``
writes raw pixels into a pipe — and doing that in a loop is a screen recorder
good enough for a README: no cursor, no dialog, no compositing artefacts, and
frames that are exactly the region asked for.

    ./tools/capture.py shot  out.png            2200 0 760 260
    ./tools/capture.py video out-%03d.png 4.0   2200 0 760 260

The second form writes numbered frames at ~20 per second for the given number
of seconds; turn them into a GIF with ffmpeg afterwards. Needs PySide6 for the
image assembly and python3-dbus for the call itself.
"""

from __future__ import annotations

import os
import sys
import time

import dbus

from PySide6.QtGui import QGuiApplication, QImage

SERVICE = "org.kde.KWin.ScreenShot2"
PATH = "/org/kde/KWin/ScreenShot2"
INTERFACE = "org.kde.KWin.ScreenShot2"

#: What KWin calls the only format it hands out here (QImage::Format_ARGB32).
_FORMATS = {1: QImage.Format_ARGB32_Premultiplied, 5: QImage.Format_ARGB32}


def capture(bus, x: int, y: int, width: int, height: int) -> QImage:
    """One frame of the given rectangle."""
    read_fd, write_fd = os.pipe()
    try:
        proxy = bus.get_object(SERVICE, PATH)
        results = proxy.CaptureArea(
            dbus.Int32(x), dbus.Int32(y), dbus.UInt32(width), dbus.UInt32(height),
            dbus.Dictionary({"include-cursor": dbus.Boolean(False)}, signature="sv"),
            dbus.types.UnixFd(write_fd),
            dbus_interface=INTERFACE,
        )
        os.close(write_fd)
        write_fd = -1

        stride = int(results["stride"])
        got_width = int(results["width"])
        got_height = int(results["height"])
        fmt = _FORMATS.get(int(results["format"]), QImage.Format_ARGB32)

        wanted = stride * got_height
        chunks: list[bytes] = []
        have = 0
        while have < wanted:
            chunk = os.read(read_fd, wanted - have)
            if not chunk:
                break
            chunks.append(chunk)
            have += len(chunk)
        raw = b"".join(chunks)
        # QImage does not copy the buffer, so the copy is the point.
        return QImage(raw, got_width, got_height, stride, fmt).copy()
    finally:
        os.close(read_fd)
        if write_fd >= 0:
            os.close(write_fd)


def main(argv: list[str]) -> int:
    if len(argv) < 6:
        print(__doc__)
        return 2

    mode, target = argv[1], argv[2]
    if mode == "shot":
        seconds, rect = 0.0, [int(value) for value in argv[3:7]]
    else:
        seconds, rect = float(argv[3]), [int(value) for value in argv[4:8]]

    application = QGuiApplication(sys.argv)  # QImage needs one
    bus = dbus.SessionBus()

    if mode == "shot":
        capture(bus, *rect).save(target)
        print(target)
        return 0

    # 20 a second is smooth enough for a spring animation and slow enough that
    # the round trip keeps up; the timestamps below say what really happened.
    interval = 1.0 / 20
    frame = 0
    started = time.monotonic()
    while time.monotonic() - started < seconds:
        due = time.monotonic() + interval
        capture(bus, *rect).save(target % frame)
        frame += 1
        rest = due - time.monotonic()
        if rest > 0:
            time.sleep(rest)
    elapsed = time.monotonic() - started
    print(f"{frame} frames in {elapsed:.1f}s ({frame / elapsed:.1f}/s)")
    _ = application
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
