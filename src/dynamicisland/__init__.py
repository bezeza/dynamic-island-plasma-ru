"""Dynamic Island — a notification surface for KDE Plasma on Wayland."""

import os

__version__ = "0.1.0"

APP_ID = "org.dynamicisland.Island"

#: Set DYNAMIC_ISLAND_DEBUG=1 to trace notifications, masks and pointer events.
DEBUG = bool(os.environ.get("DYNAMIC_ISLAND_DEBUG"))


def debug(message: str) -> None:
    if DEBUG:
        print(f"[dynamic-island] {message}")
