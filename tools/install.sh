#!/usr/bin/env bash
# Everything in one go: launcher, panel applet, autostart, hand-over.
#
# Pass --no-takeover to skip the last step; the island then starts with the
# next login instead of right now.
set -euo pipefail

here="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"
bin_dir="$HOME/.local/bin"
unit_dir="$HOME/.config/systemd/user"
unit="dynamic-island.service"
applet="org.dynamicisland.history"

takeover=1
[[ "${1:-}" == "--no-takeover" ]] && takeover=0

step() { printf '\n\033[1m%s\033[0m\n' "$1"; }
note() { printf '   %s\n' "$1"; }

# -- dependencies -------------------------------------------------------
missing=()
have_pyside=1
python3 -c "import PySide6" 2>/dev/null || { missing+=("python3-pyside6"); have_pyside=0; }
python3 -c "import dbus" 2>/dev/null || missing+=("python3-dbus")

# Where the QML plugin lands is a per-distribution decision: /usr/lib64 on
# Fedora and openSUSE, /usr/lib on Arch, a triplet directory on Debian. Ask Qt
# where its imports live instead of guessing — and only once PySide6 is known
# to be there, or the answer would be a second complaint about the same thing.
if (( have_pyside )); then
    qml_dir="$(python3 -c '
from PySide6.QtCore import QLibraryInfo
paths = getattr(QLibraryInfo, "Path", QLibraryInfo)
print(QLibraryInfo.path(paths.QmlImportsPath))' 2>/dev/null || true)"
    [[ -n "$qml_dir" && -e "$qml_dir/org/kde/layershell/qmldir" ]] \
        || missing+=("layer-shell-qt")
fi

if (( ${#missing[@]} )); then
    echo "Missing packages. Install them first:" >&2
    if command -v pacman >/dev/null 2>&1; then
        arch=()
        for package in "${missing[@]}"; do
            case "$package" in
                python3-pyside6) arch+=("pyside6") ;;
                python3-dbus)    arch+=("python-dbus") ;;
                *)               arch+=("$package") ;;
            esac
        done
        echo "    sudo pacman -S ${arch[*]}" >&2
    elif command -v zypper >/dev/null 2>&1; then
        echo "    sudo zypper install ${missing[*]}" >&2
    else
        echo "    sudo dnf install ${missing[*]}" >&2
    fi
    exit 1
fi

# -- launcher -----------------------------------------------------------
step "Launcher"
mkdir -p "$bin_dir"
ln -sfn "$here/tools/run.sh" "$bin_dir/dynamic-island"
note "$bin_dir/dynamic-island"

case ":$PATH:" in
    *":$bin_dir:"*) ;;
    *) note "Heads up: $bin_dir is not in \$PATH." ;;
esac

# Portals resolve an application by its desktop file; without one the session
# logs a complaint on every start.
mkdir -p "$HOME/.local/share/applications"
install -m 644 "$here/tools/dynamic-island.desktop" \
    "$HOME/.local/share/applications/dynamic-island.desktop"
note "$HOME/.local/share/applications/dynamic-island.desktop"

# -- translations -------------------------------------------------------
# The .po files are the source; the applet ships the compiled catalogues.
step "Translations"
python3 "$here/tools/build-translations.py" | sed 's/^/   /'

# -- autostart ----------------------------------------------------------
# A plain XDG autostart entry would lose the race: it runs after plasmashell,
# which by then owns the notification service. The unit orders itself ahead.
step "Autostart"
mkdir -p "$unit_dir"
install -m 644 "$here/tools/$unit" "$unit_dir/$unit"
systemctl --user daemon-reload
systemctl --user enable "$unit" >/dev/null
note "$unit_dir/$unit (enabled)"

# Older versions of this script installed a .desktop entry instead.
rm -f "$HOME/.config/autostart/dynamic-island.desktop"

# -- panel applet -------------------------------------------------------
step "Panel applet"
if command -v kpackagetool6 >/dev/null 2>&1; then
    if kpackagetool6 --type Plasma/Applet --upgrade "$here/applet/$applet" >/dev/null 2>&1 \
        || kpackagetool6 --type Plasma/Applet --install "$here/applet/$applet" >/dev/null 2>&1; then
        note "$applet installed"
    else
        note "Failed. Try it by hand to see why:"
        note "kpackagetool6 --type Plasma/Applet --install $here/applet/$applet"
    fi

    # Put it into the panel as well, unless it is already sitting there.
    script='var here = false; panels().forEach(function (panel) { panel.widgetIds.forEach(function (id) { if (panel.widgetById(id).type === "org.dynamicisland.history") { here = true; } }); }); if (here) { print("present"); } else if (panels().length > 0) { panels()[0].addWidget("org.dynamicisland.history"); print("added"); } else { print("no panel"); }'
    if reply="$(gdbus call --session --dest org.kde.plasmashell \
                    --object-path /PlasmaShell \
                    --method org.kde.PlasmaShell.evaluateScript "$script" 2>/dev/null)"; then
        case "$reply" in
            *added*)    note "added to the panel" ;;
            *present*)  note "already in the panel" ;;
            *)          note "no panel found — add it by hand" ;;
        esac
    else
        note "plasmashell is not answering, add it by hand:"
        note "right-click the panel -> Add Widgets -> Dynamic Island"
    fi
else
    note "kpackagetool6 is missing, skipping the applet."
fi

# -- Plasma's own on-screen display -------------------------------------
# Volume and brightness would otherwise be drawn twice: once by plasmashell in
# the middle of the screen, once by the island. uninstall.sh turns it back on.
step "On-screen display"
if command -v kwriteconfig6 >/dev/null 2>&1; then
    kwriteconfig6 --file plasmarc --group OSD --key Enabled false
    qdbus org.kde.plasmashell /PlasmaShell refreshCurrentShell >/dev/null 2>&1 || true
    note "Plasma's OSD switched off, the island shows it instead"
else
    note "kwriteconfig6 is missing; Plasma will keep showing its own OSD"
fi

# -- take the service over ----------------------------------------------
if (( takeover )); then
    step "Taking the notification service over"
    note "The panel goes away for a second; windows are untouched."
    # Whatever happens below, the session must not be left without its shell.
    trap 'systemctl --user start plasma-plasmashell.service >/dev/null 2>&1 || true' EXIT
    # Stopping the shell first is the deterministic version of --takeover: the
    # name is free at the moment our unit asks for it.
    systemctl --user stop plasma-plasmashell.service
    systemctl --user start "$unit" || true
    systemctl --user start plasma-plasmashell.service
    trap - EXIT

    sleep 2
    if systemctl --user is-active --quiet "$unit"; then
        note "running"
    else
        note "start failed, see:  journalctl --user -u $unit -e"
    fi
else
    step "Notification service"
    note "Skipped. The island takes over at your next login."
fi

step "Done"
note "Try it:    tools/demo-notifications.sh"
note "Undo it:   tools/uninstall.sh"
