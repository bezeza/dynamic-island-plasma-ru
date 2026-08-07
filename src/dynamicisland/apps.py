"""Finding the application behind a notification, and putting it in front.

A notification names its sender loosely: a desktop entry if we are lucky, an
app name if not, and the two rarely match a file name exactly — "Firefox"
against org.mozilla.firefox.desktop, "DNF App Center" against
org.dnf.AppCenter.desktop.  So the desktop files are indexed by everything they
can be recognised by: their id, their ``Name``, and their ``StartupWMClass``.

Showing the application is the other half.  Starting it again is not the same
as showing it: a running Electron app may do nothing at all, and a minimised
window stays minimised.  KWin can bring the window back, but only from a
script — so one gets written, run and thrown away again, and only when it finds
no window at all does the application get started for real.  The script reports
that back over the bus, which is why this module needs nothing from the caller
but the two names the notification arrived with.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile

from . import debug

#: Where a desktop file may live, in the order XDG looks. The flatpak exports
#: are spelled out because a service started by systemd does not always inherit
#: the session's XDG_DATA_DIRS.
DATA_DIRS = [
    os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"),
    *(os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":"),
    os.path.expanduser("~/.local/share/flatpak/exports/share"),
    "/var/lib/flatpak/exports/share",
]

#: Bus address of our own service — the raise script calls back into it.
_BUS_NAME = "org.freedesktop.Notifications"
_OBJECT_PATH = "/org/freedesktop/Notifications"
_ISLAND_INTERFACE = "org.dynamicisland.Island"

#: A needle shorter than this matches half the session; "vlc" is the shortest
#: application name worth keeping.
_MIN_NEEDLE = 3


# -- the index ----------------------------------------------------------
class _Entry:
    """One desktop file, with everything it answers to."""

    __slots__ = ("path", "stem", "name", "wmclass")

    def __init__(self, path: str, stem: str, name: str, wmclass: str) -> None:
        self.path = path
        self.stem = stem
        self.name = name
        self.wmclass = wmclass


#: Built on demand and rebuilt when a directory changes underneath us.
_index: list[_Entry] = []
_index_stamp: tuple = ()


def _folders() -> list[str]:
    seen: list[str] = []
    for directory in DATA_DIRS:
        folder = os.path.join(directory, "applications")
        if folder not in seen and os.path.isdir(folder):
            seen.append(folder)
    return seen


def _stamp() -> tuple:
    """What has to change before the index is worth rebuilding."""
    marks = []
    for folder in _folders():
        try:
            marks.append((folder, os.stat(folder).st_mtime_ns))
        except OSError:
            continue
    return tuple(marks)


def _read_entry(path: str) -> _Entry:
    """Pull the three recognisable fields out of a desktop file.

    Only the first ``[Desktop Entry]`` group counts: actions further down carry
    their own ``Name`` and would otherwise overwrite the application's.
    """
    name = wmclass = ""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            in_group = False
            for line in handle:
                line = line.strip()
                if line.startswith("["):
                    if in_group:
                        break  # past the main group, nothing left to read
                    in_group = line == "[Desktop Entry]"
                    continue
                if not in_group:
                    continue
                if line.startswith("Name=") and not name:
                    name = line[5:].strip()
                elif line.startswith("StartupWMClass="):
                    wmclass = line[15:].strip()
    except OSError:
        pass
    stem = os.path.basename(path)[: -len(".desktop")]
    return _Entry(path, stem, name, wmclass)


def index() -> list[_Entry]:
    """Every desktop file on the system, cached until one of them moves."""
    global _index, _index_stamp
    stamp = _stamp()
    if stamp == _index_stamp and _index:
        return _index

    entries: list[_Entry] = []
    seen: set[str] = set()
    for folder in _folders():
        try:
            names = sorted(os.listdir(folder))
        except OSError:
            continue
        for name in names:
            if not name.endswith(".desktop") or name in seen:
                continue
            seen.add(name)  # the first directory in XDG order wins
            entries.append(_read_entry(os.path.join(folder, name)))

    _index = entries
    _index_stamp = stamp
    return _index


# -- matching -----------------------------------------------------------
def fold(text: str) -> str:
    """Lower case, ASCII letters and digits only.

    The same folding runs inside the KWin script, where it is a regular
    expression over ``[a-z0-9]`` — an umlaut has to disappear on both sides or
    the two never agree on a name.
    """
    return "".join(
        character
        for character in str(text or "").lower()
        if character.isascii() and character.isalnum()
    )


def desktop_file(entry: str) -> str | None:
    """The desktop file for a desktop entry, an app name, or a path."""
    entry = str(entry or "").strip()
    if not entry:
        return None

    # The history hands out full paths; take them at face value.
    if entry.endswith(".desktop") and os.path.isabs(entry) and os.path.exists(entry):
        return entry

    entry = entry.removesuffix(".desktop")

    # An exact id is the only match worth trusting blindly.
    for directory in DATA_DIRS:
        path = os.path.join(directory, "applications", entry + ".desktop")
        if os.path.exists(path):
            return path

    folded = fold(entry)
    if not folded:
        return None
    # "org.mozilla.firefox" is as likely to be spelled "firefox".
    tail = fold(entry.rsplit(".", 1)[-1])

    loose: str | None = None
    for record in index():
        stem = fold(record.stem)
        if stem == folded or stem == tail:
            return record.path
        if fold(record.stem.rsplit(".", 1)[-1]) in (folded, tail):
            return record.path
        if record.name and fold(record.name) == folded:
            return record.path
        if record.wmclass and fold(record.wmclass) == folded:
            return record.path
        # Last resort: spotify against spotify-launcher.desktop. Kept until
        # the whole index has been searched — an exact hit further down wins.
        if loose is None and len(folded) > _MIN_NEEDLE and folded in stem:
            loose = record.path
    return loose


def needles(entry: str, app_name: str = "") -> list[str]:
    """Everything a window of this application might call itself.

    KWin knows a window by its desktop file name and by the two X11-style
    class strings; all three are matched against this list.

    Once a desktop file has been found, it is the *only* thing the list is
    built from.  Throwing the application's own name in as well is what makes
    a click land on the wrong window: a notification saying it comes from
    "Mail" would otherwise match anything with mail in its class, and the
    panel applet — which hands over the resolved file and nothing else — has
    been getting this right all along while the island guessed.
    """
    candidates: list[str] = []
    path = desktop_file(entry) or desktop_file(app_name)
    if path is not None:
        stem = os.path.basename(path)[: -len(".desktop")]
        candidates += [stem, stem.rsplit(".", 1)[-1]]
        record = _read_entry(path)
        if record.wmclass:
            candidates.append(record.wmclass)
    else:
        # Nothing on disk to go by, so the names as they arrived are all there
        # is — a running application may still answer to one of them.
        for source in (entry, app_name):
            source = str(source or "").strip()
            if not source:
                continue
            if source.endswith(".desktop"):
                source = os.path.basename(source)[: -len(".desktop")]
            candidates += [source, source.rsplit(".", 1)[-1]]

    needles: list[str] = []
    for candidate in candidates:
        folded = fold(candidate)
        if len(folded) >= _MIN_NEEDLE and folded not in needles:
            needles.append(folded)
    return needles


# -- showing ------------------------------------------------------------
#: Finds the window and brings it back, minimised or not.  KWin scripts take no
#: arguments, so both the needles and the fallback are written into the script.
#:
#: Nothing here may throw: a script that dies half way leaves the window where
#: it was and never reaches the callback, so the click would do nothing at all.
_RAISE_SCRIPT = """
const needles = %(needles)s;
const fallback = %(fallback)s;

function folded(text) {
    return String(text || "").toLowerCase().replace(/[^a-z0-9]/g, "");
}

function names(window) {
    return [folded(window.desktopFileName),
            folded(window.resourceClass),
            folded(window.resourceName)];
}

let exact = null;
let loose = null;

for (const window of workspace.windowList()) {
    if (!window.normalWindow || window.skipTaskbar) {
        continue;
    }
    const candidates = names(window);
    for (const needle of needles) {
        if (candidates.indexOf(needle) >= 0) {
            exact = exact || window;
        } else if (needle.length >= 5 && candidates.some(function (name) {
            /* Only this direction: a window whose class contains the needle is
             * plausibly the application. The other way round — the needle
             * containing the class — matches on two shared letters and lands
             * the click on whatever happened to be open. */
            return name.indexOf(needle) >= 0;
        })) {
            loose = loose || window;
        }
    }
    if (exact) {
        break;
    }
}

const target = exact || loose;
if (target) {
    target.minimized = false;
    try {
        // A window on another virtual desktop would be raised out of sight.
        if (!target.onAllDesktops && target.desktops && target.desktops.length) {
            workspace.currentDesktop = target.desktops[0];
        }
    } catch (error) {
        // Older KWin, different desktop API — activating still works.
    }
    workspace.activeWindow = target;
    // Say which window it was. Guessing wrong is the failure mode here, and
    // without this the log cannot tell a wrong guess from no guess at all.
    callDBus("%(bus)s", "%(path)s", "%(interface)s", "Raised",
             String(target.resourceClass) + " / " + String(target.desktopFileName));
} else if (fallback) {
    // Nothing is running: hand the desktop file back for a real start.
    callDBus("%(bus)s", "%(path)s", "%(interface)s", "Start", fallback);
}
"""


def show(entry: str, app_name: str = "") -> bool:
    """Put an application in front: raise its window, or start it.

    Returns False only when we could neither name the application nor find a
    window for it — the one case where a click genuinely has nothing to do.
    """
    wanted = needles(entry, app_name)
    fallback = desktop_file(entry) or desktop_file(app_name) or ""
    # Printed rather than only traced: when a click opens the wrong thing this
    # line and the one the KWin script sends back are the whole story.
    print(
        f"[dynamic-island] open {entry or app_name!r}: looking for "
        f"{wanted}, would start {os.path.basename(fallback) or 'nothing'}"
    )

    if not wanted:
        return False

    if _raise_window(wanted, fallback):
        return True

    # KWin is not answering (or is not KWin at all); starting is all that is
    # left, and it is better than the click doing nothing.
    return start(fallback) if fallback else False


def _raise_window(needles: list[str], fallback: str) -> bool:
    """Run the raise script. False means KWin never got to run it."""
    source = _RAISE_SCRIPT % {
        "needles": json.dumps(needles),
        "fallback": json.dumps(fallback),
        "bus": _BUS_NAME,
        "path": _OBJECT_PATH,
        "interface": _ISLAND_INTERFACE,
    }
    return run_script(source)


def run_script(source: str, name: str = "", keep: bool = False) -> bool:
    """Hand a script to KWin and run it. False means KWin never ran it.

    A one-shot needs no ``name``: the temp file's basename serves as one, which
    keeps two scripts run at once apart, and the script is unloaded again the
    moment it ran.  ``keep=True`` is for a watcher that stays resident and
    keeps calling home.  Such a script gets a fixed name — a leftover under
    that name from a crashed run is replaced, not joined — and its file is not
    deleted, because nothing says when the compositor is done reading it.  It
    still comes from mkstemp: a fixed, guessable path in a shared /tmp is an
    invitation to plant a link there and have KWin run somebody else's script.
    """
    folder = None
    if keep:
        # Kept files go to the runtime directory: per user, wiped at logout,
        # so restarts cannot pile anything up anywhere permanent.
        folder = os.environ.get("XDG_RUNTIME_DIR") or None
    handle, path = tempfile.mkstemp(
        prefix=f"{name or 'dynamic-island'}-", suffix=".js", dir=folder
    )
    plugin = name or os.path.basename(path)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as script:
            script.write(source)
        if name:
            unload_script(plugin)
        reply = _kwin("loadScript", path, plugin)
        if reply is None:
            return False
        # loadScript answers with the id the script object is registered under.
        identifier = reply.strip().strip("()").split(",")[0].strip()
        ran = _call(f"/Scripting/Script{identifier}", "org.kde.kwin.Script", "run")
        if not keep:
            unload_script(plugin)
        return ran is not None
    except OSError as error:
        print(f"[dynamic-island] could not run a KWin script: {error}")
        return False
    finally:
        if not keep:
            try:
                os.unlink(path)
            except OSError:
                pass


def unload_script(name: str) -> None:
    """Take a loaded script out of KWin again."""
    _kwin("unloadScript", name)


def _kwin(member: str, *arguments: str) -> str | None:
    return _call("/Scripting", "org.kde.kwin.Scripting", member, *arguments)


def _call(path: str, interface: str, member: str, *arguments: str) -> str | None:
    command = [
        "gdbus", "call", "--session", "--dest", "org.kde.KWin",
        "--object-path", path, "--method", f"{interface}.{member}", *arguments,
    ]
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=4)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def start(entry: str) -> bool:
    """Start an application outright, without looking for a window first.

    This is what the raise script calls back into when it found nothing to
    bring forward, and it takes the desktop file it was handed.
    """
    path = entry if entry.endswith(".desktop") and os.path.isabs(entry) else None
    if path is None or not os.path.exists(path):
        path = desktop_file(entry)
    if path is None:
        return False

    try:
        subprocess.Popen(
            ["gio", "launch", path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as error:
        print(f"[dynamic-island] could not open {entry!r}: {error}")
        return False
    return True
