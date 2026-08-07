#!/usr/bin/env bash
# Route the Notification portal to a backend that we can actually see.
#
# Sandboxed apps — Flatpaks, and Teams for Linux among them — do not call
# org.freedesktop.Notifications themselves.  They go through
# xdg-desktop-portal, and Plasma points that portal at its own "plasmanotify"
# backend, which delivers straight into Plasma's notification system and never
# touches the D-Bus name we own.  Those notifications would simply never reach
# the island.
#
# The gtk backend forwards portal notifications to org.freedesktop.Notifications
# like any other client, so we prefer that one — and only that one setting;
# everything else is copied from the system configuration as-is.
#
#   ./tools/portal-notifications.sh enable    # island sees sandboxed apps
#   ./tools/portal-notifications.sh disable   # back to the Plasma default
#   ./tools/portal-notifications.sh status
set -euo pipefail

config_dir="$HOME/.config/xdg-desktop-portal"
# XDG_CURRENT_DESKTOP=KDE makes this file win over plain portals.conf.
config="$config_dir/kde-portals.conf"
backup="$config.dynamic-island-backup"
system="/usr/share/xdg-desktop-portal/kde-portals.conf"

reload() {
    systemctl --user restart xdg-desktop-portal.service 2>/dev/null || true
    sleep 1
}

case "${1:-status}" in
enable)
    if [[ ! -e $system ]]; then
        echo "The system configuration $system is missing — nothing to do." >&2
        exit 1
    fi
    mkdir -p "$config_dir"
    # Keep a user file that existed before us, so disable can put it back.
    if [[ -e $config && ! -e $backup ]]; then
        cp -a "$config" "$backup"
    fi
    sed 's/^org\.freedesktop\.impl\.portal\.Notification=.*/org.freedesktop.impl.portal.Notification=gtk/' \
        "$system" > "$config"
    grep -q '^org\.freedesktop\.impl\.portal\.Notification=' "$config" ||
        printf 'org.freedesktop.impl.portal.Notification=gtk\n' >> "$config"
    reload
    echo "Portal notifications now go through the gtk backend."
    echo "Sandboxed apps (Teams, Flatpaks) reach the island that way."
    ;;
disable)
    if [[ -e $backup ]]; then
        mv -f "$backup" "$config"
        echo "The previous user configuration is back."
    else
        rm -f "$config"
        echo "User configuration removed, the system default applies again."
    fi
    reload
    ;;
status)
    if [[ -e $config ]]; then
        echo "User configuration: $config"
        grep -E '^org\.freedesktop\.impl\.portal\.Notification=' "$config" || echo "  (no Notification entry)"
    else
        echo "No user configuration, this applies:"
        grep -E '^org\.freedesktop\.impl\.portal\.Notification=' "$system" 2>/dev/null || echo "  (no entry)"
    fi
    ;;
*)
    echo "Usage: $0 [enable|disable|status]" >&2
    exit 2
    ;;
esac
