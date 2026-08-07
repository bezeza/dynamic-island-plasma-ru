#!/usr/bin/env bash
# Run Dynamic Island straight from the source tree, no install required.
set -euo pipefail

# readlink resolves the launcher symlink in ~/.local/bin; without it the source
# tree would be looked for next to the symlink instead of next to this script.
here="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"

if ! python3 -c "import PySide6" 2>/dev/null; then
    echo "PySide6 is missing. It has to come from your distribution:" >&2
    echo "    Fedora    sudo dnf install python3-pyside6 python3-dbus layer-shell-qt" >&2
    echo "    Arch      sudo pacman -S pyside6 python-dbus layer-shell-qt" >&2
    echo "    openSUSE  sudo zypper install python3-pyside6 python3-dbus layer-shell-qt" >&2
    exit 1
fi

export PYTHONPATH="$here/src${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m dynamicisland "$@"
