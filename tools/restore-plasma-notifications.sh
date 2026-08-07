#!/usr/bin/env bash
# Hand org.freedesktop.Notifications back to plasmashell.
#
# Plasma only claims the service while it is starting up, so once Dynamic
# Island has taken it over the shell has to be restarted to get it back.
set -euo pipefail

if systemctl --user is-active --quiet plasma-plasmashell.service; then
    echo "Restarting plasmashell…"
    systemctl --user restart plasma-plasmashell.service
else
    echo "Restarting plasmashell (without systemd)…"
    kquitapp6 plasmashell 2>/dev/null || true
    sleep 1
    (setsid plasmashell >/dev/null 2>&1 &)
fi

sleep 2
owner="$(busctl --user status org.freedesktop.Notifications 2>/dev/null \
         | sed -n 's/^Comm=//p' | head -1)"
echo "The notification service now belongs to: ${owner:-unknown}"
