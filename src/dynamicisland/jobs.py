"""File operations, so the island really does show everything.

Copying, moving and deleting do not travel over ``org.freedesktop.Notifications``
at all.  KDE tracks them with a second protocol: an application asks
``org.kde.JobViewServer`` for a view, gets back an object path, and then reports
progress on that object while the server sends ``cancelRequested``,
``suspendRequested`` and ``resumeRequested`` back the other way.  That is where
the pause and cancel buttons in Plasma's progress popup come from.

Taking the name over means those buttons have to keep working, so every method
of the protocol is implemented and the three signals go back to the sender
untouched.  Both interface versions are served: KF6's KIO speaks
``org.kde.JobViewServerV2``, older clients speak the original.

If the names cannot be had — because plasmashell got there first — nothing
breaks; the jobs simply stay with Plasma, which is also what
``showJobs: false`` does.
"""

from __future__ import annotations

import os
from typing import Any

import dbus
import dbus.service

from PySide6.QtCore import QObject, Signal

from .server import plain
from .strings import language

SERVER_PATH = "/JobViewServer"
SERVER_V1 = "org.kde.JobViewServer"
SERVER_V2 = "org.kde.JobViewServerV2"
KUISERVER = "org.kde.kuiserver"

JOB_V1 = "org.kde.JobView"
JOB_V2 = "org.kde.JobViewV2"
#: What KDE Frameworks 6 actually speaks: one property bag per update instead
#: of a setter per value. Everything else here exists for older senders.
JOB_V3 = "org.kde.JobViewV3"
JOB_PATH = "/org/dynamicisland/jobs/JobView_%d"

#: KJob::Capability
KILLABLE = 0x01
SUSPENDABLE = 0x02

#: The JobView protocol. KDE defines it identically on both interface
#: versions, so the handlers below are shared and only the interface name
#: differs — see _mirror().
JOB_METHODS = {
    "terminate": "s",
    "setSuspended": "b",
    "setTotalAmount": "ts",
    "setProcessedAmount": "ts",
    "setPercent": "u",
    "setSpeed": "t",
    "setInfoMessage": "s",
    "setDescriptionField": "uss",
    "clearDescriptionField": "u",
    "setDestUrl": "v",
    "setError": "u",
}

JOB_SIGNALS = ("cancelRequested", "suspendRequested", "resumeRequested")


#: Where a desktop entry may be found, in the order XDG looks.
_DATA_DIRS = [
    os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"),
    *(os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":"),
]


def display_name(desktop_entry: str) -> str:
    """``org.kde.dolphin`` -> ``Dolphin``.

    A job identifies its sender by desktop entry, which is a fine key and a
    poor label.  The desktop file has the name a person would recognise, in
    their language; failing that, the last part of the entry will do.
    """
    entry = str(desktop_entry or "").removesuffix(".desktop")
    if not entry:
        return ""

    preferred = f"Name[{language()}]="
    for directory in _DATA_DIRS:
        path = os.path.join(directory, "applications", entry + ".desktop")
        try:
            with open(path, "r", encoding="utf-8") as handle:
                names: dict[str, str] = {}
                for line in handle:
                    if line.startswith(preferred):
                        return line.split("=", 1)[1].strip()
                    if line.startswith("Name="):
                        names.setdefault("plain", line.split("=", 1)[1].strip())
                    elif line.startswith("["):
                        # A later group's Name= belongs to an action, not the app.
                        if names:
                            break
                if names:
                    return names["plain"]
        except OSError:
            continue

    return entry.rsplit(".", 1)[-1].capitalize()



def _handler(name: str, count: int):
    """A method of the right arity that forwards to ``_<name>``."""
    if count == 1:
        def handler(self, first):
            return getattr(self, "_" + name)(plain(first))
    elif count == 2:
        def handler(self, first, second):
            return getattr(self, "_" + name)(plain(first), plain(second))
    elif count == 3:
        def handler(self, first, second, third):
            return getattr(self, "_" + name)(
                plain(first), plain(second), plain(third)
            )
    else:
        raise ValueError(f"no wrapper for {count} arguments")
    handler.__name__ = name
    return handler


def _mirror(interface: str) -> type:
    """A mixin that exposes the protocol under *interface*.

    dbus-python dispatches by attribute name and walks the MRO until the
    ``_dbus_interface`` matches, so the same member name can live on two
    mixins — which is exactly what serving V1 and V2 from one object needs.
    Every generated method forwards to ``_<name>`` on the job itself.
    """
    members: dict[str, Any] = {}

    for name, signature in JOB_METHODS.items():
        # dbus-python insists that the signature and the argument count match,
        # so the wrapper has to be written out per arity. Every type in the
        # protocol is a single character, which makes len() the count.
        handler = _handler(name, len(signature))
        members[name] = dbus.service.method(
            interface, in_signature=signature, out_signature=""
        )(handler)

    for name in JOB_SIGNALS:
        def emitter(self, _name=name):
            pass

        emitter.__name__ = name
        members[name] = dbus.service.signal(interface, signature="")(emitter)

    # Built through InterfaceType, not plain type(): the metaclass is what
    # registers the members for introspection, and clients decide from the
    # introspection whether the interface exists at all.
    return dbus.service.InterfaceType(
        "_JobView" + interface.rsplit(".", 1)[-1],
        (dbus.service.Interface,),
        members,
    )


_JobViewV1Interface = _mirror(JOB_V1)
_JobViewV2Interface = _mirror(JOB_V2)


class _JobViewV3Interface(dbus.service.Interface):
    """The current protocol.

    A KF6 file operation reports everything through ``update`` with keys like
    ``title``, ``percent``, ``processedBytes`` and ``descriptionValue1``; the
    same keys already come along in the hints of ``requestView``.
    """

    @dbus.service.method(JOB_V3, in_signature="a{sv}", out_signature="")
    def update(self, properties):
        self.apply(plain(properties))

    @dbus.service.method(JOB_V3, in_signature="usa{sv}", out_signature="")
    def terminate(self, error_code, error_message, hints):
        self._terminate(str(error_message))

    @dbus.service.signal(JOB_V3, signature="")
    def cancelRequested(self):
        pass

    @dbus.service.signal(JOB_V3, signature="")
    def suspendRequested(self):
        pass

    @dbus.service.signal(JOB_V3, signature="")
    def resumeRequested(self):
        pass


class JobView(dbus.service.Object, _JobViewV3Interface,
              _JobViewV1Interface, _JobViewV2Interface):
    """One running file operation."""

    def __init__(self, service: "JobService", path: str, index: int,
                 desktop_entry: str, icon_name: str, app_name: str,
                 capabilities: int) -> None:
        super().__init__(service.bus, path)
        self._service = service
        self.path = path
        self.index = index
        self.desktop_entry = desktop_entry
        self.icon_name = icon_name
        self.app_name = app_name or display_name(desktop_entry)
        self.capabilities = int(capabilities)

        self.info_message = ""
        self.title = ""
        self.descriptions: dict[int, tuple[str, str]] = {}
        self.percent = 0
        self.speed = 0
        self.amounts: dict[str, int] = {}
        self.totals: dict[str, int] = {}
        self.suspended = False
        self.error = 0
        self.finished = False

    # -- what the island asks of the job ---------------------------------
    @property
    def killable(self) -> bool:
        return bool(self.capabilities & KILLABLE)

    @property
    def suspendable(self) -> bool:
        return bool(self.capabilities & SUSPENDABLE)

    def _emit(self, name: str) -> None:
        """Signal on every version we serve — a sender listens only on its own."""
        for interface in (
            _JobViewV3Interface, _JobViewV1Interface, _JobViewV2Interface
        ):
            getattr(interface, name)(self)

    def request_cancel(self) -> None:
        self._emit("cancelRequested")

    def request_suspend(self) -> None:
        # The sender confirms by reporting itself suspended; do not guess here.
        self._emit("suspendRequested")

    def request_resume(self) -> None:
        self._emit("resumeRequested")

    # -- what the job tells us -------------------------------------------
    def apply(self, properties: dict) -> None:
        """Take in one V3 property bag."""
        labels: dict[int, str] = {}
        values: dict[int, str] = {}

        for key, value in properties.items():
            if key == "title":
                self.title = str(value)
            elif key == "infoMessage":
                self.info_message = str(value)
            elif key == "percent":
                self.percent = max(0, min(100, int(value)))
            elif key == "speed":
                self.speed = int(value)
            elif key == "suspended":
                self.suspended = bool(value)
            elif key in ("error", "errorCode"):
                self.error = int(value)
            elif key.endswith("Bytes") or key.endswith("Files") or key.endswith("Dirs"):
                unit = key.removeprefix("processed").removeprefix("total").lower()
                target = self.amounts if key.startswith("processed") else self.totals
                target[unit] = int(value)
            elif key.startswith("descriptionLabel"):
                labels[int(key[len("descriptionLabel"):] or 0)] = str(value)
            elif key.startswith("descriptionValue"):
                values[int(key[len("descriptionValue"):] or 0)] = str(value)

        for number in set(labels) | set(values):
            existing = self.descriptions.get(number, ("", ""))
            self.descriptions[number] = (
                labels.get(number, existing[0]),
                values.get(number, existing[1]),
            )

        # Some senders report amounts but no percentage; the bar should still
        # move for them.
        if "percent" not in properties:
            total = self.totals.get("bytes") or self.totals.get("files") or 0
            done = self.amounts.get("bytes") or self.amounts.get("files") or 0
            if total > 0:
                self.percent = max(0, min(100, round(done * 100 / total)))

        self._changed()

    def _terminate(self, error_message: str = "") -> None:
        if self.finished:
            return
        self.finished = True
        self._service.forget(self)

    def _setSuspended(self, suspended: bool) -> None:
        self.suspended = bool(suspended)
        self._changed()

    def _setTotalAmount(self, amount: int, unit: str) -> None:
        self.totals[str(unit)] = int(amount)
        self._changed()

    def _setProcessedAmount(self, amount: int, unit: str) -> None:
        self.amounts[str(unit)] = int(amount)
        self._changed()

    def _setPercent(self, percent: int) -> None:
        self.percent = max(0, min(100, int(percent)))
        self._changed()

    def _setSpeed(self, bytes_per_second: int) -> None:
        self.speed = int(bytes_per_second)
        self._changed()

    def _setInfoMessage(self, message: str) -> None:
        self.info_message = str(message)
        self._changed()

    def _setDescriptionField(self, number: int, label: str, value: str) -> None:
        self.descriptions[int(number)] = (str(label), str(value))
        self._changed()

    def _clearDescriptionField(self, number: int) -> None:
        self.descriptions.pop(int(number), None)
        self._changed()

    def _setDestUrl(self, url: Any) -> None:
        self.descriptions.setdefault(1, ("", str(url)))
        self._changed()

    def _setError(self, code: int) -> None:
        self.error = int(code)
        self._changed()

    def _changed(self) -> None:
        if not self.finished:
            self._service.changed.emit(self)


class _JobServerV1(dbus.service.Interface):
    @dbus.service.method(SERVER_V1, in_signature="ssi", out_signature="o")
    def requestView(self, app_name, app_icon_name, capabilities):
        # The first version identifies the sender by icon name and label; there
        # is no desktop entry to look anything else up with.
        return self._open("", str(app_icon_name), str(app_name), int(capabilities))


class _JobServerV2(dbus.service.Interface):
    @dbus.service.method(SERVER_V2, in_signature="sia{sv}", out_signature="o")
    def requestView(self, desktop_entry, capabilities, hints):
        # Never let a surprise in the hints cost the sender its progress view.
        converted = plain(hints)
        readable = ""
        if isinstance(converted, dict):
            readable = str(converted.get("application-display-name", "") or "")
        path = self._open(str(desktop_entry), "", readable, int(capabilities))
        # The hints are a first update: title and description are in there.
        if isinstance(converted, dict):
            self._service.job(str(path)).apply(converted)
        return path


class _KUiServer(dbus.service.Interface):
    """The bookkeeping half of the protocol.

    Applications ask here whether it is worth reporting progress to us at all,
    so these answers decide whether a copy ever reaches the island.
    """

    @dbus.service.method(KUISERVER, in_signature="ss", out_signature="")
    def registerService(self, service, object_path):
        pass

    @dbus.service.method(KUISERVER, in_signature="", out_signature="b")
    def requiresJobTracker(self):
        """False means "we are showing it, keep your own window to yourself".

        The name reads backwards.  KDynamicJobTracker asks this and, on a
        ``true``, builds a KWidgetJobTracker — the application's own progress
        dialog — instead of reporting the job to us.  Plasma answers ``false``
        here, and so must we.
        """
        return False

    @dbus.service.method(KUISERVER, in_signature="", out_signature="as")
    def registeredJobContacts(self):
        return dbus.Array([], signature="s")

    @dbus.service.method(KUISERVER, in_signature="", out_signature="")
    def emitJobUrlsChanged(self):
        pass

    @dbus.service.signal(KUISERVER, signature="as")
    def jobUrlsChanged(self, urls):
        pass

    @dbus.service.signal(KUISERVER, signature="b")
    def requiresJobTrackerChanged(self, required):
        pass


class _JobServerObject(dbus.service.Object, _JobServerV1, _JobServerV2, _KUiServer):
    def __init__(self, service: "JobService") -> None:
        super().__init__(service.bus, SERVER_PATH)
        self._service = service

    def _open(self, desktop_entry: str, icon_name: str, app_name: str,
              capabilities: int):
        return dbus.ObjectPath(
            self._service.open(desktop_entry, icon_name, app_name, capabilities)
        )


class JobService(QObject):
    """Qt-facing side: one signal per job change, one when it is over."""

    #: A job appeared or moved on; carries the JobView.
    changed = Signal(object)
    #: A job is done, cancelled or gone; carries the JobView.
    finished = Signal(object)

    def __init__(self, parent: QObject | None = None, claim: bool = True) -> None:
        super().__init__(parent)
        self.bus = None
        self._names: list[str] = []
        self._object = None
        self._jobs: dict[str, JobView] = {}
        self._next = 1

        if not claim:
            return

        self.bus = dbus.SessionBus()
        # Both names sit on the same object; kuiserver is the one applications
        # look for before they start reporting.  No ALLOW_REPLACEMENT, for the
        # same reason as the notification name: a restarting plasmashell asks
        # with REPLACE_EXISTING and would take them straight back.
        for name in (SERVER_V1, KUISERVER):
            try:
                reply = self.bus.request_name(name, dbus.bus.NAME_FLAG_REPLACE_EXISTING)
            except dbus.DBusException as error:
                print(f"[dynamic-island] cannot ask for {name}: {error}")
                continue
            if reply == dbus.bus.REQUEST_NAME_REPLY_PRIMARY_OWNER:
                self._names.append(name)
        # Exported either way: if we are only queued, the object starts serving
        # the moment plasmashell lets the name go.
        self._object = _JobServerObject(self)

    @property
    def owns_names(self) -> bool:
        return len(self._names) == 2

    def open(self, desktop_entry: str, icon_name: str, app_name: str,
             capabilities: int) -> str:
        path = JOB_PATH % self._next
        self._next += 1
        job = JobView(
            self, path, self._next, desktop_entry, icon_name, app_name, capabilities
        )
        self._jobs[path] = job
        self.changed.emit(job)
        return path

    def forget(self, job: JobView) -> None:
        self._jobs.pop(job.path, None)
        self.finished.emit(job)
        try:
            job.remove_from_connection()
        except LookupError:
            pass

    def job(self, path: str) -> JobView:
        return self._jobs[path]

    def jobs(self) -> list[JobView]:
        return list(self._jobs.values())
