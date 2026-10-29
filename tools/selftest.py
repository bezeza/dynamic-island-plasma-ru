#!/usr/bin/env python3
"""Drive the island the way a user would and check that it reacts.

Starts a fresh instance with debug logging, then for every interaction sends a
real notification over D-Bus, moves a real pointer onto the pill and clicks.
What the island reports back through the log and what ``notify-send`` prints
when an action fires are the assertions.

    ./tools/selftest.py             # everything
    ./tools/selftest.py --offline   # only what needs no session

Requires write access to /dev/uinput and a Wayland session with layer shell —
except for ``--offline``, which runs the checks that are only code: the feed
parser and the file-operation cards. That is what continuous integration can
do on a machine with no screen.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Only the interactive half drives a pointer, and only that half needs evdev.
# Importing it up here unconditionally is what made --offline die before it ran
# a single check: the machine CI runs on has no input devices and no python
# evdev, so the import failed and took the whole file with it. Annotations are
# strings under the __future__ import above, so a missing Pointer costs nothing
# until something actually asks for one.
try:
    from synthetic_pointer import Pointer  # noqa: E402
except ImportError as failure:  # pragma: no cover - depends on the machine
    Pointer = None
    POINTER_UNAVAILABLE = str(failure)
else:
    POINTER_UNAVAILABLE = ""

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(
    os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "dynamic-island-selftest.log"
)

MASK = re.compile(r"mask=(-?\d+),(-?\d+) (\d+)x(\d+)")

#: The island under test gets a configuration of its own, so the run does not
#: depend on how the machine it runs on happens to be set up — and so there is
#: a webhook to knock on.
CONFIG_HOME = os.path.join(
    os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "dynamic-island-selftest-config"
)
WEBHOOK_PORT = 8759

_island: subprocess.Popen | None = None
_failures: list[str] = []


# -- harness ------------------------------------------------------------
def screen_size() -> tuple[int, int]:
    try:
        output = subprocess.run(
            ["kscreen-doctor", "-o"], capture_output=True, text=True, timeout=10
        ).stdout
        match = re.search(r"(\d+),(\d+) (\d+)x(\d+)", output)
        if match:
            return int(match.group(3)), int(match.group(4))
    except (OSError, subprocess.SubprocessError):
        pass
    return 1920, 1080


def start_island() -> None:
    global _island
    subprocess.run(["pkill", "-9", "-f", "^python3 -m dynamicisland"], check=False)
    time.sleep(1.0)
    write_config()
    environment = dict(os.environ)
    environment["XDG_CONFIG_HOME"] = CONFIG_HOME
    environment["DYNAMIC_ISLAND_DEBUG"] = "1"
    environment["PYTHONUNBUFFERED"] = "1"
    environment["PYTHONPATH"] = os.path.join(ROOT, "src")
    with open(LOG, "w") as handle:
        _island = subprocess.Popen(
            [sys.executable, "-m", "dynamicisland"],
            stdout=handle,
            stderr=subprocess.STDOUT,
            env=environment,
            start_new_session=True,
        )
    for _ in range(40):
        time.sleep(0.25)
        if "serving org.freedesktop.Notifications" in log_text():
            return
        if "queued behind" in log_text():
            stop_island()
            raise SystemExit(
                "plasmashell still owns the notification service — start the "
                "island once with --takeover first."
            )
    stop_island()
    raise SystemExit("the island did not come up, see " + LOG)


def write_config() -> None:
    """The defaults, plus the one source the run wants to talk to."""
    folder = os.path.join(CONFIG_HOME, "dynamic-island")
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "config.json"), "w", encoding="utf-8") as handle:
        json.dump(
            {
                "sources": [
                    {
                        "type": "webhook",
                        "name": "selftest",
                        "appName": "Selftest hook",
                        "bind": "127.0.0.1",
                        "port": WEBHOOK_PORT,
                        "token": "",
                        "allowCommands": False,
                    }
                ]
            },
            handle,
        )


def stop_island() -> None:
    if _island is not None and _island.poll() is None:
        _island.terminate()
        try:
            _island.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _island.kill()


def log_text() -> str:
    try:
        with open(LOG, "r") as handle:
            return handle.read()
    except OSError:
        return ""


def island_rect() -> tuple[int, int, int, int] | None:
    """The pill's current geometry, or None when nothing is on screen."""
    matches = MASK.findall(log_text())
    if not matches:
        return None
    x, y, width, height = (int(value) for value in matches[-1])
    return None if width == 0 or height == 0 else (x, y, width, height)


def notify(*arguments: str) -> subprocess.Popen:
    """Send a notification; the handle carries the action key on stdout."""
    return subprocess.Popen(
        ["notify-send", *arguments],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )


def notify_id(*arguments: str) -> int:
    """Send a notification and get its id back.

    Used by the tests that only care that a card went away: the id is what
    lets them assert on that one card instead of on an empty island, which a
    chat client can spoil at any moment.
    """
    done = subprocess.run(
        ["notify-send", "-p", *arguments],
        capture_output=True, text=True, check=False,
    )
    text = done.stdout.strip()
    return int(text) if text.isdigit() else 0


def wait_closed(notification_id: int, timeout: float = 4.0) -> bool:
    """Whether the island reported closing exactly this notification."""
    needle = f"closed #{notification_id} "
    deadline = time.time() + timeout
    while time.time() < deadline:
        if needle in log_text():
            return True
        time.sleep(0.15)
    return False


def wait_gone(timeout: float = 3.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if island_rect() is None:
            return True
        time.sleep(0.15)
    return False


def settle(timeout: float = 12.0) -> None:
    """Wait for an empty island before putting the next card up.

    The island under test is the session's notification server, so a chat
    client firing while a test runs lands a card of its own in front of the
    one being clicked — and the click then dismisses the wrong thing.  Nothing
    can stop that mid-test, but starting each test on an empty island is what
    keeps a stray message from failing everything that comes after it.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if island_rect() is None:
            return
        time.sleep(0.25)
    print("    (something is still on the island; carrying on anyway)")


def check(name: str, condition: bool, detail: str = "") -> None:
    print(f"  {'PASS' if condition else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))
    if not condition:
        _failures.append(name)


# -- the tests ----------------------------------------------------------
def test_click_dismisses(pointer: Pointer) -> None:
    settle()
    identifier = notify_id("-a", "Selftest", "-i", "firefox", "Click", "closes me", "-t", "0")
    time.sleep(1.6)
    rect = island_rect()
    if rect is None:
        check("click dismisses", False, "the island never appeared")
        return
    x, y, width, height = rect
    pointer.click(x + width / 2, y + 40)
    check("click dismisses", wait_closed(identifier))
    pointer.park()


def test_default_action(pointer: Pointer) -> None:
    settle()
    handle = notify(
        "-a", "Claude Code", "-i", "dialog-information",
        "Default action", "a click should invoke 'default'",
        "-t", "0", "-A", "default=To the terminal",
    )
    time.sleep(1.6)
    rect = island_rect()
    if rect is None:
        check("default action", False, "the island never appeared")
        handle.kill()
        return
    x, y, width, height = rect
    pointer.click(x + width / 2, y + 40)
    time.sleep(1.0)
    try:
        output = handle.communicate(timeout=4)[0].strip()
    except subprocess.TimeoutExpired:
        handle.kill()
        output = ""
    check("default action", output == "default", f"notify-send reported {output!r}")
    pointer.park()


def test_action_button(pointer: Pointer) -> None:
    settle()
    handle = notify(
        "-a", "Firefox", "-i", "firefox", "Actions", "the first button is 'open'",
        "-t", "0", "-A", "open=Open", "-A", "folder=Folder",
    )
    time.sleep(1.6)
    rect = island_rect()
    if rect is None:
        check("action button", False, "the island never appeared")
        handle.kill()
        return
    x, y, _width, height = rect
    # The action row is the last thing in the card: one padding above the
    # bottom edge, and the first button starts at the card's left padding.
    pointer.click(x + 50, y + height - 29)
    time.sleep(1.0)
    try:
        output = handle.communicate(timeout=4)[0].strip()
    except subprocess.TimeoutExpired:
        handle.kill()
        output = ""
    check("action button", output == "open", f"notify-send reported {output!r}")
    pointer.park()


def test_close_button(pointer: Pointer) -> None:
    settle()
    identifier = notify_id("-a", "Selftest", "-i", "firefox", "Close button", "top right", "-t", "0")
    time.sleep(1.6)
    rect = island_rect()
    if rect is None:
        check("close button", False, "the island never appeared")
        return
    x, y, width, _height = rect
    # Hovering is what makes the button appear in the first place.
    pointer.move(x + width / 2, y + 40)
    time.sleep(0.5)
    pointer.click(x + width - 25, y + 25)
    check("close button", wait_closed(identifier))
    pointer.park()


def test_middle_click(pointer: Pointer) -> None:
    settle()
    from evdev import ecodes

    identifier = notify_id("-a", "Selftest", "-i", "firefox", "Middle click", "dismisses", "-t", "0")
    time.sleep(1.6)
    rect = island_rect()
    if rect is None:
        check("middle click dismisses", False, "the island never appeared")
        return
    x, y, width, _height = rect
    pointer.click(x + width / 2, y + 40, ecodes.BTN_MIDDLE)
    check("middle click dismisses", wait_closed(identifier))
    pointer.park()


def test_swipe(pointer: Pointer) -> None:
    settle()
    identifier = notify_id("-a", "Selftest", "-i", "firefox", "Swipe", "upwards dismisses", "-t", "0")
    time.sleep(1.6)
    rect = island_rect()
    if rect is None:
        check("swipe dismisses", False, "the island never appeared")
        return
    x, y, width, _height = rect
    pointer.swipe(x + width / 2, y + 45, x + width / 2, y - 20)
    check("swipe dismisses", wait_closed(identifier))
    pointer.park()


def test_critical_stays(pointer: Pointer) -> None:
    settle()
    handle = notify(
        "-a", "System", "-i", "battery-caution", "-u", "critical",
        "Critical", "must not go away on its own",
    )
    time.sleep(1.6)
    present = island_rect() is not None
    time.sleep(8.0)
    still = island_rect() is not None
    check("critical never expires", present and still)
    rect = island_rect()
    if rect:
        pointer.click(rect[0] + rect[2] / 2, rect[1] + 40)
        wait_gone()
    pointer.park()
    handle.kill()


def test_replace(pointer: Pointer) -> None:
    settle()
    first = subprocess.run(
        ["notify-send", "-a", "Copying", "-p", "-h", "int:value:10", "Progress", "10 %"],
        capture_output=True, text=True,
    ).stdout.strip()
    time.sleep(1.4)
    before = island_rect()
    subprocess.run(
        ["notify-send", "-a", "Copying", "-r", first, "-h", "int:value:80",
         "Progress", "80 %"],
        check=False,
    )
    time.sleep(1.2)
    after = island_rect()
    # A replacement must update the card in place, not stack a second one:
    # a stacked card would widen the input region by its peek.
    check(
        "a replacement does not stack",
        before is not None and after is not None and before[3] == after[3],
        f"{before} -> {after}",
    )
    if after:
        pointer.click(after[0] + after[2] / 2, after[1] + 40)
        wait_gone()
    pointer.park()


def test_history(pointer: Pointer) -> None:
    """What the panel applet gets to read once the pill is done with a card.

    The applet only ever sees this file, so checking the file is checking the
    applet — and emptying it goes back over D-Bus, which is the one thing the
    applet cannot do on its own.  Note that this leaves the history empty.
    """
    settle()
    clear_history()
    handle = notify(
        "-a", "History", "-i", "firefox", "Remember this",
        "it belongs in the panel afterwards", "-t", "0",
    )
    time.sleep(1.6)
    rect = island_rect()
    if rect is None:
        check("the history remembers cards", False, "the island never appeared")
        handle.kill()
        return
    pointer.click(rect[0] + rect[2] / 2, rect[1] + 40)
    wait_gone()
    pointer.park()
    handle.kill()

    time.sleep(0.8)
    entries = history_entries()
    # Not entries[0]: anything the session sends while this runs lands in
    # front of ours, and that is not what is being tested here.
    check(
        "the history remembers cards",
        any(entry.get("summary") == "Remember this" for entry in entries),
        f"{len(entries)} entries",
    )

    clear_history()
    time.sleep(0.8)
    check(
        "the history can be emptied",
        not any(entry.get("summary") == "Remember this"
                for entry in history_entries()),
    )


def history_entries() -> list[dict]:
    path = os.path.join(
        os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"),
        "dynamic-island",
        "history.json",
    )
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return []


def clear_history() -> None:
    """The same call the applet's broom makes."""
    subprocess.run(
        [
            "gdbus", "call", "--session",
            "--dest", "org.freedesktop.Notifications",
            "--object-path", "/org/freedesktop/Notifications",
            "--method", "org.dynamicisland.Island.ClearHistory",
        ],
        capture_output=True,
        check=False,
    )


def test_webhook() -> None:
    """A card that came in over HTTP, with nobody touching the machine.

    This is the whole point of the source subsystem: something on the network
    says a sentence, and it lands on the island like any application would —
    and the last part of the path is what the card says it came from, which is
    what the history records afterwards.
    """
    import urllib.error
    import urllib.request

    settle()
    clear_history()
    url = (
        f"http://127.0.0.1:{WEBHOOK_PORT}/notify/backup"
        "?summary=Webhook%20works&body=sent%20over%20HTTP&timeout=2000"
    )
    try:
        with urllib.request.urlopen(url, timeout=4) as answer:
            status = answer.status
    except (urllib.error.URLError, OSError) as error:
        check("the webhook takes a card", False, str(error))
        return

    time.sleep(1.5)
    check(
        "the webhook takes a card",
        status == 200 and "selftest: 'Webhook works'" in log_text(),
        f"HTTP {status}",
    )

    # Two seconds to expire, then it is the history's problem.
    time.sleep(3.0)
    entries = [
        entry for entry in history_entries()
        if entry.get("summary") == "Webhook works"
    ]
    check(
        "the endpoint names the sender",
        bool(entries) and entries[0].get("appName") == "backup",
        entries[0].get("appName") if entries else "nothing in the history",
    )


def test_file_operations() -> None:
    """A copy job becomes a card, and its buttons reach the copying app.

    This one runs against the classes directly rather than the live island:
    the sender of a job is a KIO application, and there is no command-line way
    to make one report progress.
    """
    sys.path.insert(0, os.path.join(ROOT, "src"))
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtGui import QGuiApplication

    from dynamicisland import artwork
    from dynamicisland.config import Config
    from dynamicisland.controller import IslandController, JOB_ID_BASE
    from dynamicisland.model import NotificationModel
    from dynamicisland.server import NotificationService

    application = QGuiApplication.instance() or QGuiApplication([])

    class FakeJob:
        path = "/fake/JobView_1"
        desktop_entry = "org.kde.dolphin"
        icon_name = ""
        app_name = "Dolphin"
        info_message = "Copying files"
        descriptions = {0: ("Source", "/tmp/a"), 1: ("Destination", "/tmp/b")}
        percent = 40
        suspended = False
        killable = True
        suspendable = True

        def __init__(self):
            self.calls = []

        def request_cancel(self):
            self.calls.append("cancel")

        def request_suspend(self):
            self.calls.append("suspend")

        def request_resume(self):
            self.calls.append("resume")

    model = NotificationModel()
    controller = IslandController(
        Config(), model, NotificationService(claim=False), artwork.ArtworkProvider()
    )

    job = FakeJob()
    controller._on_job_changed(job)
    card = model.get(JOB_ID_BASE)
    check(
        "a copy job becomes a card",
        card is not None
        and card.summary == "Copying files"
        and card.progress == 40
        and [action["key"] for action in card.actions] == ["job-suspend", "job-cancel"],
    )
    check(
        "a copy card never expires",
        card is not None and controller._duration_for(card) == 0,
    )

    controller.invokeAction(JOB_ID_BASE, "job-suspend")
    job.suspended = True
    controller._on_job_changed(job)
    controller.invokeAction(JOB_ID_BASE, "job-suspend")
    controller.invokeAction(JOB_ID_BASE, "job-cancel")
    check(
        "the buttons reach the application",
        job.calls == ["suspend", "resume", "cancel"],
        str(job.calls),
    )
    # Cancelling asks; the application answers by terminating the job.
    check("cancelling leaves the card up", model.get(JOB_ID_BASE) is not None)
    controller._on_job_finished(job)
    check("a finished copy clears the card", model.get(JOB_ID_BASE) is None)
    _ = application


#: A feed that mixes Atom's namespace with RSS's habits, which is what most of
#: them do. Static rather than fetched: a test that needs the network is a test
#: that fails on a train.
_ATOM_FIXTURE = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Fixture</title>
  <icon>https://example.com/logo.png</icon>
  <entry>
    <title>Something happened</title>
    <id>urn:uuid:1</id>
    <updated>2999-01-01T12:00:00Z</updated>
    <link rel="edit" href="https://example.com/edit/1"/>
    <link rel="alternate" href="https://example.com/1"/>
    <summary>&lt;p&gt;With &lt;b&gt;markup&lt;/b&gt; in it.&lt;/p&gt;</summary>
  </entry>
  <entry>
    <title>Long ago</title>
    <id>urn:uuid:2</id>
    <updated>2001-01-01T12:00:00Z</updated>
    <link href="https://example.com/2"/>
    <summary>Old news.</summary>
  </entry>
</feed>
"""


def test_hardened() -> None:
    """The things that were once wrong, so they cannot go wrong again quietly."""
    sys.path.insert(0, os.path.join(ROOT, "src"))
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    import time as _time

    from PySide6.QtGui import QGuiApplication

    from dynamicisland import artwork
    from dynamicisland.sources.feed import FeedSource, when
    from dynamicisland.sources.webhook import _Handler

    application = QGuiApplication.instance() or QGuiApplication([])

    # A rowstride Qt is going to ignore must not be what the length is checked
    # against, or the decoder reads whole rows off the end of the buffer.
    check(
        "a bogus rowstride is refused",
        all(
            artwork.decode_image_data((8, 200, stride, False, 8, 3, b"B" * 24)).isNull()
            for stride in (0, -1, 4)
        ),
    )
    good = artwork.decode_image_data((2, 2, 6, False, 8, 3, b"\xff" * 12))
    check("a sound icon still decodes", not good.isNull(), f"{good.width()}x{good.height()}")

    # A date without a zone is not this machine's local time. Read as local it
    # lands in the past, and the "old news" guard then hides genuinely new
    # entries for as many hours as the offset.
    previous_tz = os.environ.get("TZ")
    os.environ["TZ"] = "Europe/Berlin"
    _time.tzset()
    try:
        naive, zoned = when("2026-08-06T19:12:00"), when("2026-08-06T19:12:00Z")
    finally:
        if previous_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = previous_tz
        _time.tzset()
    check("a date with no zone is read as UTC", naive == zoned, f"{naive - zoned:+.0f}s apart")

    # announceFirst is documented as "do announce what is already there", and
    # the date guard used to swallow all of it — permanently.
    source = FeedSource({
        "name": "fixture", "url": "https://example.com/feed.xml",
        "announceFirst": True,
    })
    announced: list = []
    source.produced.connect(announced.extend)
    source.announce(source.parse(_ATOM_FIXTURE))
    check(
        "announceFirst really announces",
        len(announced) == 2,
        f"{len(announced)} of 2 entries",
    )

    # The endpoint name becomes an app name, is looked up as a desktop entry
    # and is written to the history — so it must not be able to name a file.
    check(
        "the endpoint name cannot be a path",
        all(_Handler._endpoint(path) is None for path in (
            "/notify/../../../../tmp/pwn",
            "/notify//etc/issue.desktop",
            "/notify/.ssh",
            "/notify/" + "x" * 90,
        )),
    )
    check(
        "an ordinary endpoint still works",
        _Handler._endpoint("/notify/backup") == "backup"
        and _Handler._endpoint("/notify") == "",
    )
    _ = application


def test_media_focus() -> None:
    """The track banner stays away while the player itself is in front.

    Watching the video in the browser or sitting in Spotify means the track
    change is already on screen — the card would only repeat it.  The focus
    watcher is driven directly, the way KWin's callback would.
    """
    sys.path.insert(0, os.path.join(ROOT, "src"))
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtGui import QGuiApplication

    from dynamicisland import artwork
    from dynamicisland.config import Config
    from dynamicisland.controller import IslandController, MEDIA_ID
    from dynamicisland.focus import FocusWatcher
    from dynamicisland.media import Track
    from dynamicisland.model import NotificationModel
    from dynamicisland.server import NotificationService

    application = QGuiApplication.instance() or QGuiApplication([])

    model = NotificationModel()
    controller = IslandController(
        Config(), model, NotificationService(claim=False), artwork.ArtworkProvider()
    )
    focus = FocusWatcher(None, claim=False)
    controller.attach_focus(focus)

    def track(title: str) -> Track:
        made = Track("org.mpris.MediaPlayer2.selftest")
        made.identity = "Selftest"
        made.desktop_entry = "firefox"
        made.title = title
        made.artist = "Artist"
        made.playing = True
        return made

    # The browser plays and the browser is in front: no card — but the track
    # is taken on, because the volume display draws whatever plays.
    focus._on_activated("org.mozilla.firefox", "firefox", "Navigator")
    controller._on_track(track("First"))
    check("a focused player gets no card", model.get(MEDIA_ID) is None)
    check(
        "the track is adopted anyway",
        controller._media_payload().get("title") == "First",
    )

    # Same player, another window in front: the card shows, buttons and all.
    focus._on_activated("org.kde.konsole", "konsole", "konsole")
    controller._on_track(track("Second"))
    card = model.get(MEDIA_ID)
    check(
        "an unfocused player gets its card",
        card is not None
        and card.summary == "Second"
        and [action["key"] for action in card.actions]
        == ["media-previous", "media-playpause", "media-next"],
    )

    # Walking over to the player takes the card down again.
    focus._on_activated("firefox", "firefox", "Navigator")
    check("focusing the player retracts the card", model.get(MEDIA_ID) is None)

    # A held-back change also sweeps away a card left by another player:
    # stale, and its buttons would steer the new player now.
    def other(title: str) -> Track:
        made = Track("org.mpris.MediaPlayer2.other")
        made.identity = "Other"
        made.desktop_entry = "spotify"
        made.title = title
        made.playing = True
        return made

    focus._on_activated("org.kde.konsole", "konsole", "konsole")
    controller._on_track(other("Background Song"))
    focus._on_activated("org.mozilla.firefox", "firefox", "Navigator")
    check(
        "another player's card survives the focus move",
        model.get(MEDIA_ID) is not None,
    )
    controller._on_track(track("Video"))
    check(
        "a held-back change sweeps the stale card away",
        model.get(MEDIA_ID) is None,
    )

    # And with the switch off, focus makes no difference.
    controller._config._data["mediaHideWhenFocused"] = False
    controller._on_track(track("Third"))
    check(
        "the switch turns the holding back off",
        model.get(MEDIA_ID) is not None,
    )
    controller.dismiss(MEDIA_ID)
    _ = application


def test_history_link() -> None:
    """A feed entry's link survives into the history, a command never does.

    The buttons of a source card die with the card, but the article behind a
    feed entry is exactly what somebody opens from the applet a day later —
    so the link rides along as a hint and the history writes it out.
    """
    sys.path.insert(0, os.path.join(ROOT, "src"))
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    import tempfile

    from PySide6.QtGui import QGuiApplication

    from dynamicisland import history as history_module
    from dynamicisland.config import Config
    from dynamicisland.model import LINK_HINT, Notification
    from dynamicisland.sources import SourceManager
    from dynamicisland.sources.payload import Action, Item

    application = QGuiApplication.instance() or QGuiApplication([])

    manager = SourceManager(Config())
    sent: list = []
    manager.notify.connect(lambda *arguments: sent.append(arguments))

    class FakeSource:
        name = "fixture"
        app_name = "Fixture"

    manager._show(
        FakeSource(),
        Item(
            key="1",
            summary="Something happened",
            default=Action(
                key="default", label="Open", kind="url",
                url="https://example.com/1",
            ),
        ),
    )
    check(
        "a source card carries its link",
        bool(sent) and sent[0][6].get(LINK_HINT) == "https://example.com/1",
    )

    # The history writes the link out — and refuses anything but http(s):
    # the hint travels over the bus, and a stored click must never become a
    # file or a command.
    folder = tempfile.mkdtemp(prefix="dynamic-island-selftest-")
    history_module.DATA_DIR = folder
    history_module.HISTORY_PATH = os.path.join(folder, "history.json")
    history_module.ICON_DIR = os.path.join(folder, "icons")
    store = history_module.HistoryStore(10)
    store.add(Notification(id=1, app_name="heise", summary="Article",
                           hints={LINK_HINT: "https://example.com/article"}))
    store.add(Notification(id=2, app_name="hook", summary="No link",
                           hints={LINK_HINT: "file:///etc/passwd"}))
    entries = store.entries()
    check(
        "the history keeps the link",
        len(entries) == 2
        and entries[1].get("url") == "https://example.com/article"
        and entries[0].get("url") == "",
    )
    identifiers = [entry.get("entryId") for entry in entries]
    check(
        "history entries have unique stable IDs",
        len(identifiers) == 2
        and all(isinstance(identifier, str) and identifier for identifier in identifiers)
        and len(set(identifiers)) == 2,
    )
    removed_id = entries[0].get("entryId")
    check(
        "a single history entry can be removed",
        bool(removed_id)
        and store.remove(removed_id)
        and len(store.entries()) == 1
        and store.entries()[0].get("summary") == "Article",
    )
    reloaded = history_module.HistoryStore(10)
    check(
        "history entry removal persists to disk",
        len(reloaded.entries()) == 1
        and reloaded.entries()[0].get("summary") == "Article",
    )
    _ = application


def test_atom_feed() -> None:
    """Atom, and the rule that keeps yesterday's headlines out of today."""
    sys.path.insert(0, os.path.join(ROOT, "src"))
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtGui import QGuiApplication

    from dynamicisland.sources.feed import FeedSource

    application = QGuiApplication.instance() or QGuiApplication([])
    source = FeedSource({"name": "fixture", "url": "https://example.com/feed.xml"})
    items = source.parse(_ATOM_FIXTURE)

    check(
        "atom is read",
        len(items) == 2
        and items[0].summary == "Something happened"
        # rel="edit" must not win over the one a reader is meant to follow
        and items[0].default is not None
        and items[0].default.url == "https://example.com/1"
        and items[0].body == "With markup in it.",
        f"{len(items)} entries",
    )

    announced: list = []
    source.produced.connect(announced.extend)
    source._seeded = True  # the silent first round already happened
    source.announce(items)
    check(
        "old entries stay out",
        [item.summary for item in announced] == ["Something happened"],
        str([item.summary for item in announced]),
    )
    check(
        "the feed's own icon is asked for",
        source._icon_source(__import__("xml.etree.ElementTree", fromlist=["x"])
                            .fromstring(_ATOM_FIXTURE))
        == "https://example.com/logo.png",
    )
    _ = application


def test_sound_hints() -> None:
    sys.path.insert(0, os.path.join(ROOT, "src"))
    from dynamicisland import sound

    played: list[str] = []
    original_event, original_file = sound.play_event, sound.play_file
    sound.play_event = lambda name: played.append(f"event:{name}")
    sound.play_file = lambda path: played.append(f"file:{path}")
    try:
        sound.play_for({"sound-name": "message"})
        sound.play_for({"sound-file": "/usr/share/sounds/freedesktop/stereo/bell.oga"})
        sound.play_for({"suppress-sound": True, "sound-name": "message"})
        sound.play_for({}, "bell")
    finally:
        sound.play_event, sound.play_file = original_event, original_file

    check(
        "the sound hints are honoured",
        played == [
            "event:message",
            "file:/usr/share/sounds/freedesktop/stereo/bell.oga",
            "event:bell",
        ],
        str(played),
    )
    check(
        "libcanberra really plays",
        sound.play_event("message") is None and sound._backend() is not None,
    )


def main() -> int:
    if "--offline" in sys.argv:
        print("offline checks (no session needed):")
        test_atom_feed()
        test_file_operations()
        test_media_focus()
        test_history_link()
        test_hardened()
        print()
        if _failures:
            print(f"{len(_failures)} test(s) failed: {', '.join(_failures)}")
            return 1
        print("All green.")
        return 0

    if Pointer is None:
        print(
            "the interactive checks drive a real pointer and need python-evdev "
            f"plus /dev/uinput: {POINTER_UNAVAILABLE}\n"
            "run with --offline for the checks that are only code.",
            file=sys.stderr,
        )
        return 1

    width, height = screen_size()
    print(f"Screen {width}x{height}, log: {LOG}")
    start_island()
    print("the island is up, testing the interactions:")
    try:
        with Pointer(width, height) as pointer:
            test_click_dismisses(pointer)
            test_default_action(pointer)
            test_action_button(pointer)
            test_close_button(pointer)
            test_middle_click(pointer)
            test_swipe(pointer)
            test_replace(pointer)
            test_critical_stays(pointer)
            test_history(pointer)
        test_webhook()
        test_atom_feed()
        test_file_operations()
        test_media_focus()
        test_history_link()
        test_hardened()
        test_sound_hints()
    finally:
        stop_island()

    print()
    if _failures:
        print(f"{len(_failures)} test(s) failed: {', '.join(_failures)}")
        return 1
    print("All green.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
