#!/usr/bin/env python3
"""Compile the applet's .po catalogues into the .mo files Plasma reads.

This is msgfmt's job, but gettext is not installed everywhere and the applet
should build with nothing but Python.  The format is small and stable: a
header, two string tables, and the strings themselves; libintl looks entries up
with a binary search, which is why the table has to come out sorted.

    ./tools/build-translations.py

Catalogues land in
``applet/<id>/contents/locale/<language>/LC_MESSAGES/plasma_applet_<id>.mo``,
which is where a KPackage-installed widget looks for them.
"""

from __future__ import annotations

import os
import re
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APPLET_DIR = os.path.join(ROOT, "applet")

MAGIC = 0x950412DE

_LINE = re.compile(
    r"""^\s*
    (?P<keyword>msgid_plural|msgid|msgctxt|msgstr(?:\[(?P<index>\d+)\])?)
    \s+"(?P<text>.*)"\s*$""",
    re.VERBOSE,
)
_CONTINUATION = re.compile(r'^\s*"(?P<text>.*)"\s*$')

_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\"}


def unescape(text: str) -> str:
    out: list[str] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char == "\\" and index + 1 < len(text):
            index += 1
            out.append(_ESCAPES.get(text[index], text[index]))
        else:
            out.append(char)
        index += 1
    return "".join(out)


def parse(path: str) -> dict[str, str]:
    """Return the catalogue as libintl wants it: joined keys, joined values.

    A plural entry is keyed ``msgid\\0msgid_plural`` and its value is every
    ``msgstr[n]`` joined the same way — that is literally the on-disk shape.
    """
    entries: dict[str, str] = {}
    context = singular = plural = None
    translations: dict[int, str] = {}
    field: str | int | None = None  # what a bare "…" line continues

    def flush() -> None:
        nonlocal context, singular, plural, translations
        if singular is not None:
            key = singular if context is None else context + "\x04" + singular
            if plural is None:
                value = translations.get(0, "")
            else:
                key += "\x00" + plural
                value = "\x00".join(translations[i] for i in sorted(translations))
            # An untranslated entry must not shadow the English original.
            if value or singular == "":
                entries[key] = value
        context = singular = plural = None
        translations = {}

    with open(path, "r", encoding="utf-8") as handle:
        for raw in handle:
            line = raw.rstrip("\n")
            if not line.strip() or line.lstrip().startswith("#"):
                field = None
                continue

            match = _LINE.match(line)
            if match:
                keyword = match.group("keyword")
                text = unescape(match.group("text"))
                if keyword == "msgctxt":
                    flush()
                    context, field = text, "msgctxt"
                elif keyword == "msgid":
                    if singular is not None:
                        flush()
                    singular, field = text, "msgid"
                elif keyword == "msgid_plural":
                    plural, field = text, "msgid_plural"
                else:
                    index = int(match.group("index") or 0)
                    translations[index] = text
                    field = index
                continue

            continued = _CONTINUATION.match(line)
            if continued is None or field is None:
                continue
            text = unescape(continued.group("text"))
            if field == "msgctxt":
                context = (context or "") + text
            elif field == "msgid":
                singular = (singular or "") + text
            elif field == "msgid_plural":
                plural = (plural or "") + text
            else:
                translations[field] = translations.get(field, "") + text

    flush()
    return entries


def write_mo(entries: dict[str, str], path: str) -> None:
    # libintl binary-searches the originals, so they must be sorted by bytes.
    items = sorted(
        ((key.encode("utf-8"), value.encode("utf-8")) for key, value in entries.items()),
        key=lambda pair: pair[0],
    )
    count = len(items)
    keys_offset = 7 * 4
    values_offset = keys_offset + count * 8
    strings_offset = values_offset + count * 8

    key_table = bytearray()
    value_table = bytearray()
    blob = bytearray()

    for key, value in items:
        key_table += struct.pack("<II", len(key), strings_offset + len(blob))
        blob += key + b"\x00"
    for key, value in items:
        value_table += struct.pack("<II", len(value), strings_offset + len(blob))
        blob += value + b"\x00"

    header = struct.pack(
        "<IIIIIII", MAGIC, 0, count, keys_offset, values_offset, 0, strings_offset
    )

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(header)
        handle.write(key_table)
        handle.write(value_table)
        handle.write(blob)


def build(applet: str) -> int:
    source = os.path.join(APPLET_DIR, applet, "translate")
    if not os.path.isdir(source):
        return 0
    built = 0
    for name in sorted(os.listdir(source)):
        if not name.endswith(".po"):
            continue
        language = name[:-3]
        entries = parse(os.path.join(source, name))
        target = os.path.join(
            APPLET_DIR, applet, "contents", "locale", language, "LC_MESSAGES",
            f"plasma_applet_{applet}.mo",
        )
        write_mo(entries, target)
        print(f"  {language}: {len(entries)} messages -> {os.path.relpath(target, ROOT)}")
        built += 1
    return built


def main() -> int:
    if not os.path.isdir(APPLET_DIR):
        print("no applet directory", file=sys.stderr)
        return 1
    total = 0
    for applet in sorted(os.listdir(APPLET_DIR)):
        if os.path.isdir(os.path.join(APPLET_DIR, applet)):
            print(applet)
            total += build(applet)
    if total == 0:
        print("nothing to compile", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
