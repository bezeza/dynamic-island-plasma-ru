"""What happens when a button on a source's card is pressed.

Two things can happen, and they are deliberately the only two: open a url, or
run a command.  The island receives — it is nobody's HTTP client — so there is
no button that calls an endpoint back.

The command case is the dangerous one: a webhook whose buttons may run commands
is a shell for whoever can reach the port.  It only exists when the
configuration says so, and by the time an :class:`Action` gets here
:func:`payload.parse_action` has already thrown it away if it does not.
"""

from __future__ import annotations

import subprocess

from PySide6.QtCore import QObject, QUrl
from PySide6.QtGui import QDesktopServices

from .. import debug
from .payload import Action


class ActionRunner(QObject):
    """Carries out one :class:`Action`."""

    def run(self, action: Action | None) -> bool:
        if action is None:
            return False
        debug(f"action {action.kind} {action.key!r}")
        if action.kind == "url":
            return self._open(action.url)
        if action.kind == "command":
            return self._command(action.command)
        return action.kind == "dismiss"  # nothing to do but close the card

    # -- the two kinds ----------------------------------------------------
    @staticmethod
    def _open(url: str) -> bool:
        if not url:
            return False
        # Qt hands this to the portal, which is what gets a browser open from
        # a process that owns no window on Wayland.
        if QDesktopServices.openUrl(QUrl(url)):
            return True
        try:
            subprocess.Popen(
                ["xdg-open", url],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as error:
            print(f"[dynamic-island] could not open {url!r}: {error}")
            return False
        return True

    @staticmethod
    def _command(command: list[str]) -> bool:
        if not command:
            return False
        try:
            subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as error:
            print(f"[dynamic-island] could not run {command!r}: {error}")
            return False
        return True
