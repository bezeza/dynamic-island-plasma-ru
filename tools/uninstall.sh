#!/usr/bin/env bash
# Undo everything install.sh did and leave Plasma with its own notifications.
set -euo pipefail

here="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"
unit="dynamic-island.service"
applet="org.dynamicisland.history"

step() { printf '\n\033[1m%s\033[0m\n' "$1"; }
note() { printf '   %s\n' "$1"; }

# -- autostart ----------------------------------------------------------
step "Autostart"
systemctl --user disable --now "$unit" >/dev/null 2>&1 || true
rm -f "$HOME/.config/systemd/user/$unit"
rm -f "$HOME/.config/autostart/dynamic-island.desktop"  # pre-systemd versions
systemctl --user daemon-reload
note "removed"

# Anything started by hand rather than by the unit.
pkill -f "python3 -m dynamicisland" 2>/dev/null || true

# -- panel applet -------------------------------------------------------
step "Panel applet"
script='var gone = 0; panels().forEach(function (panel) { panel.widgetIds.forEach(function (id) { var widget = panel.widgetById(id); if (widget.type === "org.dynamicisland.history") { widget.remove(); gone += 1; } }); }); print(gone);'
gdbus call --session --dest org.kde.plasmashell --object-path /PlasmaShell \
    --method org.kde.PlasmaShell.evaluateScript "$script" >/dev/null 2>&1 || true

if command -v kpackagetool6 >/dev/null 2>&1; then
    kpackagetool6 --type Plasma/Applet --remove "$applet" >/dev/null 2>&1 || true
fi
rm -rf "$HOME/.local/share/plasma/plasmoids/$applet"
note "taken out of the panel and uninstalled"

# -- launcher -----------------------------------------------------------
step "Launcher"
rm -f "$HOME/.local/bin/dynamic-island"
rm -f "$HOME/.local/share/applications/dynamic-island.desktop"
rm -f "$HOME/.local/share/applications/dynamic-island-settings.desktop"
note "removed"

# Configuration and history stay: they are the user's, and reinstalling is
# nicer when the settings survived. Say where they are instead of deleting.
note "settings stay in ~/.config/dynamic-island/"
note "history stays in ~/.local/share/dynamic-island/"

# -- Plasma's own on-screen display -------------------------------------
step "On-screen display"
if command -v kwriteconfig6 >/dev/null 2>&1; then
    kwriteconfig6 --file plasmarc --group OSD --key Enabled true
    note "Plasma draws volume and brightness again"
fi

# -- give the service back ----------------------------------------------
step "Giving notifications back to Plasma"
exec "$here/tools/restore-plasma-notifications.sh"
