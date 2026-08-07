"""An HTTP endpoint other machines can push notifications to.

This is the one part of the island that listens to the network, so it is also
the one part that has to be unfriendly by default:

* it binds to localhost unless told otherwise,
* it refuses to start on any other address without a token,
* and a button may only run a command when the configuration says so — a
  webhook that runs commands for whoever can reach it is a remote shell.

The server itself is stock :mod:`http.server` on a thread of its own.  What
crosses back into Qt is a plain dictionary through one signal, which is the
narrowest bridge there is: no Qt objects are touched from the server thread.
"""

from __future__ import annotations

import hmac
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from PySide6.QtCore import QObject, Signal

from .. import __version__, debug
from .base import Source
from .payload import Item, parse_item

#: Anything larger than this is somebody else's problem.
MAX_BODY = 256 * 1024

_LOOPBACK = ("127.0.0.1", "::1", "localhost")

_HELP = """Dynamic Island webhook — it receives, nothing else.

GET  /notify?summary=Hello&body=World
GET  /notify/<name>?summary=…        <name> is what the card says it is from
POST /notify        JSON body, see below
POST /notify/<name> the same
GET  /health        "ok" while the island is up

Everything the query string can do the JSON body can do too:

{
  "summary":  "Backup finished",
  "body":     "42 GB in 11 minutes",
  "appName":  "borg",
  "icon":     "drive-harddisk",
  "urgency":  "normal",            // low | normal | critical
  "id":       "backup",            // same id replaces the card
  "timeout":  8000,                // ms, 0 stays until dismissed
  "progress": 80,                  // 0..100, omit for no bar
  "url":      "https://…",         // opened when the card is clicked
  "actions": [
    {"label": "Open",  "type": "url", "url": "https://…"},
    {"label": "Retry", "type": "command", "command": ["systemctl", "--user",
                                                      "start", "backup"]}
  ]
}

Authorisation: Bearer <token> — or ?token=<token> when a header is awkward.
"""


class _Handler(BaseHTTPRequestHandler):
    """One request. The server instance carries the settings."""

    server_version = f"DynamicIsland/{__version__}"
    protocol_version = "HTTP/1.1"

    # -- plumbing ---------------------------------------------------------
    def log_message(self, format: str, *args: Any) -> None:
        debug(f"webhook {self.address_string()} {format % args}")

    def _send(self, status: int, body: str, kind: str = "text/plain") -> None:
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", f"{kind}; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _authorised(self, query: dict[str, list[str]]) -> bool:
        token = self.server.token  # type: ignore[attr-defined]
        if not token:
            return True
        header = self.headers.get("Authorization", "")
        offered = (
            header[7:].strip() if header.startswith("Bearer ")
            else query.get("token", [""])[0]
        )
        # compare_digest rather than ==: the obvious comparison stops at the
        # first wrong character and how long it took says how much was right.
        # It is given bytes, not str: the str form refuses anything outside
        # ASCII, so a single non-ASCII character in either the offered token or
        # the configured one would raise instead of answering 401.
        return hmac.compare_digest(offered.encode("utf-8"), token.encode("utf-8"))

    @staticmethod
    def _endpoint(path: str) -> str | None:
        """``/notify`` or ``/notify/<name>``; None when it is neither.

        The name in the path is what the card says it came from, so one island
        can take one endpoint per service without a line of configuration:
        ``/notify/backup``, ``/notify/grafana``, ``/notify/doorbell``.
        """
        path = path.rstrip("/")
        if path in ("/notify", ""):
            return ""
        if not path.startswith("/notify/"):
            return None

        name = unquote(path[len("/notify/"):]).strip()
        # The name does not stay a label: it becomes the card's app name, and
        # from there it is looked up as a desktop entry and written into the
        # history. A name that can hold a separator or a leading dot can name a
        # file instead of an application, so those are not endpoints at all.
        if not name or len(name) > 64:
            return None
        if name.startswith(".") or any(bad in name for bad in ("/", "\\")):
            return None
        if any(character < " " or character == "\x7f" for character in name):
            return None
        return name

    # -- the two verbs ----------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802 - the base class names it
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)

        if parsed.path.rstrip("/") in ("/health", "/healthz"):
            self._send(200, "ok")
            return
        if not self._authorised(query):
            self._send(401, "unauthorised")
            return
        if parsed.path in ("/", "/help"):
            self._send(200, _HELP)
            return

        name = self._endpoint(parsed.path)
        if name is None:
            self._send(404, "no such endpoint")
            return

        # A query string is the whole payload here, and it is the form most
        # things can manage: a router, a shell script, a browser bookmark.
        raw = {key: values[0] for key, values in query.items() if values}
        raw.pop("token", None)
        self._deliver(raw, name)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if not self._authorised(query):
            self._send(401, "unauthorised")
            return
        name = self._endpoint(parsed.path)
        if name is None:
            self._send(404, "no such endpoint")
            return

        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_BODY:
            self._send(413, "a body between 1 byte and 256 KB, please")
            return

        body = self.rfile.read(length)
        try:
            raw = json.loads(body.decode("utf-8", errors="replace"))
        except ValueError as error:
            self._send(400, f"that is not JSON: {error}")
            return
        if not isinstance(raw, dict):
            self._send(400, "the body has to be a JSON object")
            return
        self._deliver(raw, name)

    def _deliver(self, raw: dict[str, Any], endpoint: str = "") -> None:
        item = parse_item(
            raw,
            allow_commands=self.server.allow_commands,  # type: ignore[attr-defined]
            # The payload still wins; the path only names the sender for
            # everything that does not bother to.
            app_name=endpoint or self.server.app_name,  # type: ignore[attr-defined]
            icon=self.server.icon,  # type: ignore[attr-defined]
        )
        if item is None:
            self._send(400, "nothing to show: summary or body is required")
            return
        self.server.deliver(item)  # type: ignore[attr-defined]
        self._send(200, json.dumps({"ok": True, "id": item.key}), "application/json")


class _Server(ThreadingHTTPServer):
    """The settings a handler needs, hung off the server it belongs to."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, token, allow_commands, app_name, icon, deliver):
        super().__init__(address, _Handler)
        self.token = token
        self.allow_commands = allow_commands
        self.app_name = app_name
        self.icon = icon
        self.deliver = deliver


class WebhookSource(Source):
    """The webhook server, as a source like any other."""

    #: Emitted from the server thread; Qt queues it into the main one.
    arrived = Signal(object)

    def __init__(self, config: dict[str, Any], parent: QObject | None = None) -> None:
        super().__init__(config, parent)
        self.name = str(config.get("name") or "webhook")
        self.app_name = str(config.get("appName") or "Webhook")
        self.bind = str(config.get("bind") or "127.0.0.1")
        self.port = int(config.get("port") or 8757)
        self.token = self._secret(config)
        self.allow_commands = bool(config.get("allowCommands"))
        self._server: _Server | None = None
        self._thread: threading.Thread | None = None
        self.arrived.connect(self._forward)

    # -- the token ---------------------------------------------------------
    @staticmethod
    def _secret(config: dict[str, Any]) -> str:
        """The token, from wherever the configuration says it lives.

        ``token`` in the file is the convenient way and the one everybody
        reaches for first, so the file is written for its owner only — see
        :meth:`dynamicisland.config.Config.save`.  It is still a secret in a
        file that gets copied into dotfile repositories and pasted into bug
        reports, which is what the other two are for:

        * ``tokenFile`` points at a file that holds nothing else, so it can be
          kept out of the way and given whatever permissions you like,
        * ``tokenEnv`` names an environment variable, for a token that is
          handed to the session by something else entirely.
        """
        name = str(config.get("tokenEnv") or "")
        if name:
            value = os.environ.get(name, "")
            if value:
                return value.strip()
            print(f"[dynamic-island] {name} is not set; the webhook has no token")

        path = str(config.get("tokenFile") or "")
        if path:
            path = os.path.expanduser(path)
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    value = handle.read().strip()
            except OSError as error:
                print(f"[dynamic-island] cannot read the token file: {error}")
                return ""
            mode = os.stat(path).st_mode & 0o077
            if mode:
                # Saying so is the whole point of keeping it in its own file.
                print(
                    f"[dynamic-island] {path} can be read by others; "
                    "chmod 600 it"
                )
            return value

        return str(config.get("token") or "")

    # -- lifetime ---------------------------------------------------------
    def start(self) -> None:
        if self.bind not in _LOOPBACK and not self.token:
            print(
                f"[dynamic-island] the webhook would listen on {self.bind} "
                "without a token — refusing. Set a token, or bind to 127.0.0.1."
            )
            return
        if self.allow_commands and not self.token:
            # Loopback is not a boundary here: allowCommands turns a card into
            # a button that runs a program as this user, and every account on
            # the machine can reach 127.0.0.1.
            print(
                "[dynamic-island] the webhook would accept command buttons "
                "without a token — refusing. Set a token, or turn "
                "allowCommands off."
            )
            return

        try:
            self._server = _Server(
                (self.bind, self.port),
                self.token,
                self.allow_commands,
                self.app_name,
                self.icon,
                self.arrived.emit,
            )
        except OSError as error:
            print(f"[dynamic-island] webhook cannot listen on "
                  f"{self.bind}:{self.port}: {error}")
            self._server = None
            return

        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="dynamic-island-webhook",
            daemon=True,
        )
        self._thread.start()
        print(
            f"[dynamic-island] webhook on http://{self.bind}:{self.port}/notify"
            + ("" if self.token else "  (no token — localhost only)")
            + ("  commands allowed" if self.allow_commands else "")
        )

    def stop(self) -> None:
        if self._server is None:
            return
        self._server.shutdown()
        self._server.server_close()
        self._server = None

    def _forward(self, item: Item) -> None:
        """Back on the main thread, where the island lives."""
        self.produced.emit([item])

    def describe(self) -> str:
        return f"{self.name} (http://{self.bind}:{self.port})"
