#!/usr/bin/env python3
"""Make the pictures and the animation in the README, the same way every time.

The island photographs itself — see ``dynamicisland/capture.py`` — so this
starts one with the recorder on, plays a script of notifications at it, and
turns the frames into the files ``docs/`` holds:

    ./tools/make-media.py                      # everything, on a dark ground
    ./tools/make-media.py --backdrop desktop   # on a still of this desktop
    ./tools/make-media.py hero                 # one scene, to iterate on it

What ``grabWindow`` returns is the surface itself: the real pill, the real
spring animation, and an alpha channel where the desktop shows through. That
is put onto a background here — a plain dark one by default, or a still of the
actual desktop with ``--backdrop desktop`` — because a card floating on
transparency does not read as what it is, a pill hanging off the top bezel of
a screen. The frames are never redrawn or touched up: what is in the GIF is
what the compositor was handed.

Nothing here is needed to run the island. It is here so that the next person to
change how a card looks can regenerate every picture in one command instead of
staging screenshots by hand.
"""

from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(ROOT, "docs")
SHOTS = os.path.join(DOCS, "screenshots")

#: The strip along the top edge that every frame is cut to.
BOX = (760, 300)
FPS = 20


class Scene:
    """One recording: what is sent, and what is kept from the result.

    *steps* are ``(wait, arguments)`` pairs handed to ``notify-send``; the wait
    happens first, so the opening one is how long the island has to be up and
    idle before anything arrives.

    *stills* names frames to save as pictures, counted from the first frame
    that has anything on it.  *clip* is the range that becomes the GIF.

    Every step is sent as critical, and the island being recorded is started
    with do-not-disturb on: the session keeps sending real notifications while
    this runs, and a chat message landing in front of the card being
    photographed is how you end up with somebody's private message in a
    README. Critical is the one thing do-not-disturb lets through, so the
    scene gets the island to itself.
    """

    def __init__(self, name, steps=(), stills=None, clip=None, tail=3.0,
                 trigger=None, sources=(), quiet=True, bus=True,
                 environment=None):
        self.name = name
        self.steps = list(steps)
        self.stills = stills or {}
        self.clip = clip
        self.tail = tail
        #: Called instead of notify-send for the things that do not come over
        #: the notification bus at all: the volume display, what is playing,
        #: a feed, a webhook.
        self.trigger = trigger
        #: Sources the recorded island should be configured with.
        self.sources = list(sources)
        #: Do-not-disturb keeps the session out, but it also drops the volume
        #: display and the track banner, which are low urgency by nature and
        #: come from inside the island rather than over the bus. Those two
        #: scenes take their chances and are simply re-run if a message lands
        #: in them.
        self.quiet = quiet
        #: A scene that needs no notification bus does not take it, and then
        #: nothing the session sends can land in the middle of the shot. Only
        #: the notify-send scenes need it.
        self.bus = bus
        #: Extra environment for the island of this scene.
        self.environment = dict(environment or {})


def run(*command: str) -> str:
    done = subprocess.run(command, capture_output=True, text=True, check=False)
    return done.stdout


# -- the things that are not notifications ------------------------------
def volume(percent: int = 42) -> None:
    """Ask for the on-screen display Plasma would have drawn."""
    run("gdbus", "call", "--session", "--dest", "org.kde.plasmashell",
        "--object-path", "/org/kde/osdService",
        "--method", "org.kde.osdService.volumeChanged", str(percent))


def nudge_player() -> None:
    """Make whatever is playing say so, so the track card comes up."""
    names = run("gdbus", "call", "--session", "--dest", "org.freedesktop.DBus",
                "--object-path", "/org/freedesktop/DBus",
                "--method", "org.freedesktop.DBus.ListNames")
    for part in names.replace("'", " ").split():
        if part.startswith("org.mpris.MediaPlayer2."):
            for _ in range(2):  # pause and play again: two state changes
                run("gdbus", "call", "--session", "--dest", part,
                    "--object-path", "/org/mpris/MediaPlayer2",
                    "--method", "org.mpris.MediaPlayer2.Player.PlayPause")
                time.sleep(1.2)
            return
    print("  (no media player is running, skipping)")


def webhook_card(port: int = 8791) -> None:
    """Knock on the island's own webhook, the way a machine on the LAN would."""
    import urllib.request

    url = (f"http://127.0.0.1:{port}/notify/backup"
           "?summary=Backup%20finished&body=42%20GB%20in%2011%20minutes"
           "&icon=drive-harddisk&progress=100&timeout=8000&urgency=critical")
    try:
        urllib.request.urlopen(url, timeout=4).read()
    except OSError as error:
        print(f"  (the webhook did not answer: {error})")


SCENES = [
    Scene(
        "hero",
        [
            (2.0, ["-a", "Firefox", "-i", "firefox", "-t", "7000",
                   "Download finished", "fedora-workstation.iso has been saved.",
                   "-A", "open=Open", "-A", "folder=Show folder"]),
            (3.5, ["-a", "Element", "-i", "im-message-new", "-t", "7000",
                   "Message from Anna", "Are you still coming tonight?",
                   "-A", "inline-reply=Reply"]),
        ],
        stills={"island-actions": 22, "island-reply": 95},
        clip=(0, 150),
        tail=4.0,
    ),
    Scene(
        "critical",
        [(2.0, ["-a", "System", "-i", "battery-caution", "-u", "critical", "-t", "6000",
                "Battery almost empty", "4 % left — please plug in."])],
        stills={"island-critical": 25},
        clip=(0, 60),
    ),
    Scene(
        "volume",
        quiet=False,
        bus=False,
        trigger=volume,
        stills={"island-volume": 28},
        clip=(0, 70),
        tail=3.5,
    ),
    Scene(
        "media",
        quiet=False,
        bus=False,
        trigger=nudge_player,
        stills={"island-media": 55},
        clip=(0, 90),
        tail=4.0,
    ),
    Scene(
        "copy",
        # Copy progress arrives over KDE's job protocol, and only a program
        # with a progress dialog registers there — one from the command line
        # finishes silently. So the island is asked to walk a job through its
        # own code instead; see capture.DemoJob.
        environment={"DYNAMIC_ISLAND_DEMO_JOB": "1"},
        # The card is a normal-urgency one, so do-not-disturb would drop it;
        # nothing here needs the bus either, which keeps the session out.
        quiet=False,
        bus=False,
        stills={"island-copy": 45},
        clip=(0, 80),
        tail=14.0,
    ),
    Scene(
        "webhook",
        trigger=webhook_card,
        sources=[{"type": "webhook", "name": "shot", "bind": "127.0.0.1",
                  "port": 8791, "token": "", "allowCommands": False}],
        stills={"island-webhook": 25},
        clip=(0, 70),
        tail=3.5,
    ),
    Scene(
        "feed",
        sources=[{"type": "rss", "name": "heise", "appName": "heise online",
                  "url": "https://www.heise.de/rss/heise-top-atom.xml",
                  "interval": 900, "maxItems": 1, "announceFirst": True,
                  "trustDates": False, "timeout": 8000,
                  "urgency": "critical"}],
        stills={"island-feed": 25},
        clip=(0, 80),
        tail=8.0,
    ),
    Scene(
        "stack",
        [(2.0, ["-a", "Mail", "-i", "mail-unread", "-t", "9000",
                "New message", "Subject of the first one"]),
         (0.6, ["-a", "Mail", "-i", "mail-unread", "-t", "9000",
                "New message", "Subject of the second"]),
         (0.6, ["-a", "Mail", "-i", "mail-unread", "-t", "9000",
                "New message", "Subject of the third"]),
         (0.6, ["-a", "Mail", "-i", "mail-unread", "-t", "9000",
                "New message", "And the fourth"])],
        stills={"island-stack": 40},
        clip=(0, 90),
        tail=4.0,
    ),
]


def kwin_script(source: str) -> None:
    """Run a line of KWin script — the only way to clear the desktop."""
    handle, path = tempfile.mkstemp(suffix=".js")
    with os.fdopen(handle, "w") as script:
        script.write(source)
    name = os.path.basename(path)
    reply = run("gdbus", "call", "--session", "--dest", "org.kde.KWin",
                "--object-path", "/Scripting", "--method",
                "org.kde.kwin.Scripting.loadScript", path, name)
    identifier = "".join(character for character in reply if character.isdigit())
    if identifier:
        run("gdbus", "call", "--session", "--dest", "org.kde.KWin",
            "--object-path", f"/Scripting/Script{identifier}",
            "--method", "org.kde.kwin.Script.run")
    run("gdbus", "call", "--session", "--dest", "org.kde.KWin",
        "--object-path", "/Scripting", "--method",
        "org.kde.kwin.Scripting.unloadScript", name)
    os.unlink(path)


def clear_desktop() -> None:
    kwin_script(
        "for (const window of workspace.windowList()) {"
        "  if (window.normalWindow) { window.minimized = true; }"
        "}"
    )
    time.sleep(1.5)


def dark_backdrop(folder: str) -> str:
    """A ground of our own: near-black, with the faintest fall-off.

    Flat #000 would swallow the pill, which is nearly black itself; this keeps
    the shape readable without inventing a desktop that is not there.
    """
    import random

    from PIL import Image

    width, height = BOX
    image = Image.new("RGB", (width, height))
    pixels = image.load()
    # A pixel of noise per channel. The glow behind a card is a wide, very
    # shallow gradient, and eight bits over a near-black ground turn that into
    # visible rings; noise this small is invisible and the rings are not.
    grain = random.Random(7)
    for y in range(height):
        # 0x14 at the top edge down to 0x08: enough to see the pill against.
        value = int(20 - 12 * (y / height))
        for x in range(width):
            jitter = grain.randint(-1, 1)
            pixels[x, y] = (value + jitter, value + jitter, value + 2 + jitter)
    path = os.path.join(folder, "backdrop.png")
    image.save(path)
    return path


def backdrop(folder: str) -> str:
    """A still of the empty desktop, cut to the same strip as the frames."""
    path = os.path.join(folder, "desktop.png")
    run("spectacle", "-b", "-n", "-f", "-o", path)
    for _ in range(20):
        time.sleep(0.5)
        if os.path.exists(path) and os.path.getsize(path) > 0:
            break

    from PIL import Image

    full = Image.open(path).convert("RGB")
    left = (full.width - BOX[0]) // 2
    cut = full.crop((left, 0, left + BOX[0], BOX[1]))
    cut.save(os.path.join(folder, "backdrop.png"))
    return os.path.join(folder, "backdrop.png")


def quiet_config(folder: str, sources=(), quiet: bool = True) -> str:
    """A configuration of its own for the island being recorded.

    Nothing from the session gets in, no source of the user's fires mid-take,
    and a card still expires when the scene says it should.
    """
    home = os.path.join(folder, "config")
    os.makedirs(os.path.join(home, "dynamic-island"), exist_ok=True)
    with open(os.path.join(home, "dynamic-island", "config.json"), "w") as handle:
        json.dump(
            {
                "doNotDisturb": quiet,
                "criticalNeverExpires": False,
                "sources": list(sources),
                "language": "en",
            },
            handle,
        )
    return home


def record(scene: Scene, folder: str) -> list[str]:
    """Play the scene at a fresh island and return its composited frames."""
    frames = os.path.join(folder, scene.name)
    os.makedirs(frames, exist_ok=True)

    environment = dict(os.environ)
    environment["XDG_CONFIG_HOME"] = quiet_config(
        folder, scene.sources, scene.quiet)
    environment["DYNAMIC_ISLAND_CAPTURE"] = os.path.join(frames, "f-%04d.png")
    environment["DYNAMIC_ISLAND_CAPTURE_BOX"] = f"{BOX[0]}x{BOX[1]}"
    environment["PYTHONPATH"] = os.path.join(ROOT, "src")
    environment["PYTHONUNBUFFERED"] = "1"
    environment.update(scene.environment)

    subprocess.run(["pkill", "-9", "-f", "^python3 -m dynamicisland"], check=False)
    time.sleep(1.0)
    command = [sys.executable, "-m", "dynamicisland"]
    if not scene.bus:
        command.append("--no-bus")
    island = subprocess.Popen(
        command,
        env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    time.sleep(4.0)  # the surface has to be up before anything is sent

    if scene.trigger is not None:
        scene.trigger()

    for wait, arguments in scene.steps:
        time.sleep(wait)
        if "-u" not in arguments:
            arguments = ["-u", "critical", *arguments]
        subprocess.Popen(["notify-send", *arguments],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(scene.tail)

    island.terminate()
    try:
        island.wait(timeout=5)
    except subprocess.TimeoutExpired:
        island.kill()

    return sorted(glob.glob(os.path.join(frames, "*.png")))


def composite(frames: list[str], backdrop_path: str, folder: str,
              name: str) -> list[str]:
    """Put the frames on the desktop and drop the empty lead-in."""
    from PIL import Image

    desktop = Image.open(backdrop_path).convert("RGBA")
    out = os.path.join(folder, name + "-comp")
    os.makedirs(out, exist_ok=True)

    first = None
    written: list[str] = []
    for path in frames:
        frame = Image.open(path).convert("RGBA")
        if first is None:
            # The alpha channel says whether anything is on the surface yet.
            if frame.getchannel("A").getextrema()[1] < 8:
                continue
            first = path
        target = os.path.join(out, f"c-{len(written):04d}.png")
        Image.alpha_composite(desktop, frame).convert("RGB").save(target)
        written.append(target)
    return written


def gif(frames: list[str], target: str, clip) -> None:
    start, end = clip
    chosen = frames[start:end]
    if not chosen:
        return
    folder = os.path.dirname(chosen[0])
    listing = os.path.join(folder, "clip.txt")
    with open(listing, "w") as handle:
        for path in chosen:
            handle.write(f"file '{path}'\nduration {1 / FPS}\n")
        handle.write(f"file '{chosen[-1]}'\n")

    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
         "-i", listing, "-vf",
         # 560 wide and 96 colours. The grain that keeps the glow from
         # banding also defeats palette compression, so the budget has to come
         # from somewhere; at this size a card is still perfectly readable and
         # a README with eight of these stays under three megabytes.
         "scale=560:-1:flags=lanczos,split[a][b];"
         "[a]palettegen=max_colors=96[p];[b][p]paletteuse=dither=bayer:bayer_scale=4",
         target],
        check=False,
    )
    print(f"  {os.path.relpath(target, ROOT)} "
          f"({os.path.getsize(target) // 1024} KB, {len(chosen)} frames)")


def themes(folder: str) -> None:
    """The same card in all four palettes, side by side.

    On a ground of their own each would look right and none would be
    comparable, so they share one mid-grey — the only background on which a
    black pill and a white one are both visible.
    """
    from PIL import Image

    modes = ["amoled", "dark", "light", "system"]
    ground = Image.new("RGBA", BOX, (108, 108, 116, 255))
    sheet = Image.new("RGB", (BOX[0] * 2, BOX[1] * 2))

    for index, mode in enumerate(modes):
        home = os.path.join(folder, "theme-" + mode)
        os.makedirs(os.path.join(home, "dynamic-island"), exist_ok=True)
        with open(os.path.join(home, "dynamic-island", "config.json"), "w") as handle:
            json.dump({"theme": mode, "sources": [], "language": "en"}, handle)

        frames = os.path.join(folder, "frames-" + mode)
        os.makedirs(frames, exist_ok=True)
        environment = dict(os.environ)
        environment["XDG_CONFIG_HOME"] = home
        environment["DYNAMIC_ISLAND_CAPTURE"] = os.path.join(frames, "f-%04d.png")
        environment["DYNAMIC_ISLAND_CAPTURE_BOX"] = f"{BOX[0]}x{BOX[1]}"
        environment["PYTHONPATH"] = os.path.join(ROOT, "src")

        subprocess.run(["pkill", "-9", "-f", "^python3 -m dynamicisland"], check=False)
        time.sleep(1.0)
        # --demo feeds the island from inside, so --no-bus keeps the session's
        # own notifications out of the picture entirely.
        island = subprocess.Popen(
            [sys.executable, "-m", "dynamicisland", "--no-bus", "--demo"],
            env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        time.sleep(9.0)
        island.terminate()
        try:
            island.wait(timeout=5)
        except subprocess.TimeoutExpired:
            island.kill()

        shots = sorted(glob.glob(os.path.join(frames, "*.png")))
        # Far enough in that the second card of the demo — the one with
        # buttons — has arrived and settled.
        card = Image.open(shots[105]).convert("RGBA")
        sheet.paste(Image.alpha_composite(ground, card).convert("RGB"),
                    ((index % 2) * BOX[0], (index // 2) * BOX[1]))

    target = os.path.join(SHOTS, "themes.png")
    sheet.save(target)
    print(f"  {os.path.relpath(target, ROOT)}")


def main(argv: list[str]) -> int:
    arguments = [value for value in argv[1:] if not value.startswith("--")]
    on_desktop = "--backdrop" in argv[1:] and "desktop" in argv[1:]
    wanted = [value for value in arguments if value != "desktop"] or (
        [scene.name for scene in SCENES] + ["themes"]
    )
    os.makedirs(SHOTS, exist_ok=True)

    folder = tempfile.mkdtemp(prefix="island-media-")
    print(f"working in {folder}")
    if on_desktop:
        clear_desktop()
        desktop = backdrop(folder)
    else:
        desktop = dark_backdrop(folder)

    if "themes" in wanted:
        print("themes:")
        themes(folder)

    for scene in SCENES:
        if scene.name not in wanted:
            continue
        print(f"{scene.name}:")
        frames = composite(record(scene, folder), desktop, folder, scene.name)
        print(f"  {len(frames)} frames")

        from PIL import Image

        for name, index in scene.stills.items():
            if index < len(frames):
                target = os.path.join(SHOTS, name + ".png")
                Image.open(frames[index]).save(target)
                print(f"  {os.path.relpath(target, ROOT)}")
        if scene.clip:
            gif(frames, os.path.join(DOCS, scene.name + ".gif"), scene.clip)

    shutil.rmtree(folder, ignore_errors=True)
    print("done — the island the session was using has to be started again:")
    print("  systemctl --user start dynamic-island.service")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
