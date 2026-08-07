#!/usr/bin/env bash
# Fire a handful of notifications that exercise every part of the island.
set -euo pipefail

step() { echo "  · $1"; sleep "${2:-3}"; }

echo "Sending test notifications…"

step "plain" 0
notify-send -a "Spotify" "Now Playing" "Boards of Canada — Roygbiv"
sleep 4

step "with actions" 0
notify-send -a "Firefox" -i firefox "Download finished" \
    "fedora-workstation.iso has been saved." \
    -A "open=Open" -A "folder=Show folder"
sleep 5

step "critical (never expires)" 0
notify-send -a "System" -u critical -i battery-caution \
    "Battery almost empty" "4 % left — please plug in."
sleep 5

step "stack" 0
for index in 1 2 3 4; do
    notify-send -a "Mail" -i mail-unread "New message $index" \
        "Subject of message number $index"
    sleep 0.4
done
sleep 5

step "progress" 0
id=$(notify-send -a "Copying" -p -h int:value:10 "Copying files" "10 %")
for value in 35 60 85 100; do
    sleep 1
    notify-send -a "Copying" -r "$id" -h "int:value:$value" \
        "Copying files" "$value %"
done

# The webhook is off unless it was configured, so this is a probe rather than
# a demonstration: it only says something when there is something to say.
port="$(python3 - <<'PY' 2>/dev/null || true
import json, os
path = os.path.join(
    os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
    "dynamic-island", "config.json",
)
try:
    with open(path, encoding="utf-8") as handle:
        for source in json.load(handle).get("sources") or []:
            if source.get("type") == "webhook" and source.get("enabled") is not False \
                    and not source.get("token"):
                print(source.get("port") or 8757)
                break
except (OSError, ValueError):
    pass
PY
)"

if [[ -n $port ]]; then
    step "webhook" 0
    curl -s -o /dev/null "http://127.0.0.1:$port/notify/demo?summary=Over%20HTTP&body=Anything%20that%20can%20fetch%20a%20URL%20can%20do%20this" || true
    sleep 4
fi

echo "Done."
