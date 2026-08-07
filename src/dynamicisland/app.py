"""Process entry point: claim the bus name, put the surface on screen, run."""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys

# The shell integration is picked when the platform plugin loads, so this has to
# happen before anything drags in QGuiApplication.
from .window import WindowTools, enable_layer_shell

enable_layer_shell()

from dbus.mainloop.glib import DBusGMainLoop  # noqa: E402

from PySide6.QtCore import QTimer, QUrl  # noqa: E402
from PySide6.QtGui import QFont, QGuiApplication  # noqa: E402
from PySide6.QtQml import QQmlApplicationEngine  # noqa: E402

from . import __version__, glibpump  # noqa: E402
from .artwork import ArtworkProvider, ThemeIconProvider  # noqa: E402
from .config import Config  # noqa: E402
from .controller import IslandController  # noqa: E402
from .media import MediaWatcher  # noqa: E402
from .model import NotificationModel  # noqa: E402
from .osd import OsdWatcher  # noqa: E402
from .jobs import JobService  # noqa: E402
from .server import NameLost, NotificationService  # noqa: E402
from .sources import SourceManager  # noqa: E402
from .strings import Strings  # noqa: E402

QML_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "qml")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="dynamic-island",
        description="A Dynamic Island notification surface for KDE Plasma on Wayland.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument(
        "--demo",
        action="store_true",
        help="show sample notifications a second after startup",
    )
    parser.add_argument(
        "--takeover",
        action="store_true",
        help="restart plasmashell so it releases the notification service to us",
    )
    parser.add_argument(
        "--no-bus",
        action="store_true",
        help="do not take over org.freedesktop.Notifications — for working on "
        "the looks without cutting the session off from its notifications",
    )
    return parser.parse_args(argv[1:])


def _install_signal_handlers(application: QGuiApplication) -> None:
    for signal_number in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signal_number, lambda *_: application.quit())
    # Qt blocks in C++, so give the interpreter a chance to run its handlers.
    heartbeat = QTimer(application)
    heartbeat.setInterval(300)
    heartbeat.timeout.connect(lambda: None)
    heartbeat.start()


#: (delay in ms, app name, icon, summary, body, actions, hints, timeout)
_DEMO_SCRIPT = [
    (
        1000,
        "Dynamic Island",
        "preferences-desktop-notification",
        "It works",
        "Click runs the default action, swipe up dismisses.",
        [],
        {"urgency": 1},
        9000,
    ),
    (
        3500,
        "Firefox",
        "firefox",
        "Download finished",
        "fedora-workstation.iso has been saved.",
        ["open", "Open", "folder", "Show folder"],
        {"urgency": 1, "desktop-entry": "firefox"},
        9000,
    ),
    (
        6000,
        "Element",
        "im-message-new",
        "Message from Anna",
        "Are you still coming tonight?",
        ["inline-reply", "Reply"],
        {"urgency": 1, "x-kde-reply-placeholder-text": "Write a reply…"},
        12000,
    ),
    (
        8000,
        "System",
        "battery-caution",
        "Battery almost empty",
        "4 % left — please plug in.",
        [],
        {"urgency": 2},
        0,
    ),
]


def _restart_plasmashell() -> None:
    """Bounce the shell so the bus can pass the notification service to us."""
    print("[dynamic-island] restarting plasmashell to take the service over…")
    try:
        subprocess.Popen(
            ["systemctl", "--user", "restart", "plasma-plasmashell.service"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as error:
        print(f"[dynamic-island] could not restart plasmashell: {error}", file=sys.stderr)


def _demo(service: NotificationService) -> None:
    for delay, app_name, icon, summary, body, actions, hints, timeout in _DEMO_SCRIPT:
        QTimer.singleShot(
            delay,
            lambda a=app_name, i=icon, s=summary, b=body, ac=actions, h=hints, t=timeout:
            service.handle_notify(a, 0, i, s, b, ac, h, t),
        )


def main(argv: list[str] | None = None) -> int:
    arguments = _parse_args(argv if argv is not None else sys.argv)

    DBusGMainLoop(set_as_default=True)

    application = QGuiApplication(sys.argv)
    application.setApplicationName("Dynamic Island")
    application.setApplicationVersion(__version__)
    application.setOrganizationName("dynamicisland")
    application.setDesktopFileName("dynamic-island")
    application.setQuitOnLastWindowClosed(False)

    glibpump.install(application)
    _install_signal_handlers(application)

    config = Config()
    font_family = str(config.get("fontFamily") or "")
    if font_family:
        application.setFont(QFont(font_family))

    try:
        service = NotificationService(application, claim=not arguments.no_bus)
    except NameLost as error:
        print(f"[dynamic-island] {error}", file=sys.stderr)
        return 1
    except Exception as error:  # noqa: BLE001 - a dead bus is worth reporting plainly
        print(f"[dynamic-island] cannot reach the session bus: {error}", file=sys.stderr)
        return 1

    # File operations travel over their own protocol; without this they would
    # stay with plasmashell and never reach the island.
    jobs = JobService(
        application, claim=bool(config.get("showJobs")) and not arguments.no_bus
    )

    # Volume and brightness are drawn by plasmashell; we listen in on the bus.
    # Neither this nor the media watcher takes a name away from anybody — they
    # only listen — so --no-bus leaves them on. The flag means "do not take the
    # notification service over", not "be a dead shell": an island started that
    # way still shows what is playing, which is what makes it useful for
    # looking at the thing while the session keeps its own notifications.
    osd = OsdWatcher(application, claim=bool(config.get("showOsd")))
    media = MediaWatcher(application, claim=bool(config.get("showMedia")))

    # The settings live in the applet's own configuration dialog, which is
    # inside plasmashell — these two are how it reads and writes ours.
    service.config_handler = config.as_json
    service.set_config_handler = config.merge

    model = NotificationModel(application)
    artwork_provider = ArtworkProvider()
    controller = IslandController(config, model, service, artwork_provider, application)
    controller.attach_jobs(jobs)
    controller.attach_osd(osd)
    controller.attach_media(media)

    # Feeds, endpoints and the webhook server. They are started after the
    # controller is wired up so the first card has somewhere to land, and
    # never when the island is only being looked at.
    # Sources are the island's own business too: a feed and a webhook take
    # nothing from the session, and a second island started with --no-bus for
    # a screenshot only finds its port already in use, which it says plainly.
    sources = SourceManager(config, application)
    controller.attach_sources(sources)
    sources.start()
    config.changed.connect(sources.reload)

    tools = WindowTools(application)

    engine = QQmlApplicationEngine(application)
    engine.addImportPath(QML_ROOT)
    engine.addImageProvider("island", artwork_provider)
    engine.addImageProvider("icon", ThemeIconProvider())

    context = engine.rootContext()
    context.setContextProperty("Cfg", config)
    context.setContextProperty("Notifications", model)
    context.setContextProperty("Controller", controller)
    context.setContextProperty("WindowTools", tools)
    strings = Strings(application, str(config.get("language") or ""))
    config.changed.connect(
        lambda: strings.follow(str(config.get("language") or ""))
    )
    context.setContextProperty("Strings", strings)

    engine.load(QUrl.fromLocalFile(os.path.join(QML_ROOT, "main.qml")))
    if not engine.rootObjects():
        print("[dynamic-island] the QML scene failed to load", file=sys.stderr)
        return 1

    root_window = engine.rootObjects()[0]
    tools.attach(root_window)

    # Both off unless somebody is making pictures for the README — capture.py.
    if os.environ.get("DYNAMIC_ISLAND_DEMO_JOB"):
        from .capture import DemoJob

        DemoJob(controller, application)

    if os.environ.get("DYNAMIC_ISLAND_CAPTURE"):
        from .capture import Recorder

        Recorder(
            root_window,
            os.environ["DYNAMIC_ISLAND_CAPTURE"],
            os.environ.get("DYNAMIC_ISLAND_CAPTURE_BOX", ""),
            application,
        )
    tools.moveToScreen(str(config.get("screen") or "active"))
    config.changed.connect(
        lambda: tools.moveToScreen(str(config.get("screen") or "active"))
    )

    if arguments.no_bus:
        print(f"[dynamic-island] {__version__}, standalone — the session keeps "
              "its own notification server")
    elif service.owns_name:
        print(f"[dynamic-island] serving org.freedesktop.Notifications ({__version__})")
        if config.get("showOsd"):
            print("[dynamic-island] on-screen displays: "
                  + ("watching" if osd.watching else "not available"))
        if config.get("showJobs"):
            print("[dynamic-island] file operations: "
                  + ("serving org.kde.JobViewServer" if jobs.owns_names
                     else "left with plasmashell, the names were taken"))
    else:
        print("[dynamic-island] queued behind the running notification server.")
        if arguments.takeover:
            QTimer.singleShot(500, _restart_plasmashell)
        else:
            print("    Start with --takeover, or hand it over yourself:")
            print("    systemctl --user restart plasma-plasmashell.service")

    if arguments.demo:
        _demo(service)

    exit_code = application.exec()

    # Leave without letting Python finalise Qt for us.  PySide tears the
    # QGuiApplication down from an atexit hook, which deletes the QML engine
    # and then blocks forever joining its pixmap reader thread — systemd waits
    # out the stop timeout and SIGABRTs us, coredump and all.  There is nothing
    # to flush: the config and the history are written as they change.
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(exit_code)
