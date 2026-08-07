#!/usr/bin/env python3
"""Collect the applet's translatable strings and keep the catalogues in step.

This is xgettext's and msgmerge's job, but the applet builds with nothing but
Python and the two things needed here are small: find every ``i18n("…")`` in
the QML, and add whatever is new to ``template.pot`` and to each ``.po``
without touching a translation that is already there.

    ./tools/update-translations.py          # report and update
    ./tools/update-translations.py --check  # report only, non-zero if stale

Strings that no longer appear anywhere are left in the catalogues rather than
deleted: a wording often comes back, and losing its translation to a rename is
worse than carrying a few dead entries.
"""

from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APPLET = os.path.join(ROOT, "applet", "org.dynamicisland.history")
SOURCES = [
    os.path.join(APPLET, "contents", "ui"),
    os.path.join(APPLET, "contents", "config"),
]
TRANSLATE = os.path.join(APPLET, "translate")

#: i18n("one") and i18np("one", "many", count) — the two the applet uses.
_SINGLE = re.compile(r'\bi18n\(\s*"((?:[^"\\]|\\.)*)"')
_PLURAL = re.compile(r'\bi18np\(\s*"((?:[^"\\]|\\.)*)"\s*,\s*"((?:[^"\\]|\\.)*)"')


def strings() -> tuple[list[str], list[tuple[str, str]]]:
    """Every translatable string in the applet, in the order they are found."""
    single: list[str] = []
    plural: list[tuple[str, str]] = []
    for folder in SOURCES:
        for name in sorted(os.listdir(folder)):
            if not name.endswith(".qml"):
                continue
            with open(os.path.join(folder, name), encoding="utf-8") as handle:
                text = handle.read()
            for match in _PLURAL.finditer(text):
                pair = (match.group(1), match.group(2))
                if pair not in plural:
                    plural.append(pair)
            plural_ids = {one for one, _ in plural}
            for match in _SINGLE.finditer(text):
                value = match.group(1)
                if value not in single and value not in plural_ids:
                    single.append(value)
    return single, plural


def known(path: str) -> set[str]:
    """The msgids a catalogue already carries."""
    found: set[str] = set()
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("msgid "):
                    found.add(line[6:].strip().strip('"'))
    except OSError:
        pass
    return found


def append(path: str, single: list[str], plural: list[tuple[str, str]],
           translated: bool) -> int:
    """Add what is missing. Returns how many entries were added."""
    have = known(path)
    lines: list[str] = []

    for value in single:
        if value in have:
            continue
        lines.append(f'\nmsgid "{value}"\nmsgstr ""\n')
    for one, many in plural:
        if one in have:
            continue
        lines.append(
            f'\nmsgid "{one}"\nmsgid_plural "{many}"\n'
            'msgstr[0] ""\nmsgstr[1] ""\n'
        )

    if not lines:
        return 0
    with open(path, "a", encoding="utf-8") as handle:
        handle.writelines(lines)
    return len(lines)


def main(argv: list[str]) -> int:
    single, plural = strings()
    print(f"{len(single)} strings and {len(plural)} plural forms in the applet")

    catalogues = [os.path.join(TRANSLATE, "template.pot")]
    catalogues += [
        os.path.join(TRANSLATE, name)
        for name in sorted(os.listdir(TRANSLATE))
        if name.endswith(".po")
    ]

    stale = 0
    for path in catalogues:
        missing = len(
            [value for value in single if value not in known(path)]
            + [one for one, _ in plural if one not in known(path)]
        )
        name = os.path.basename(path)
        if not missing:
            print(f"  {name}: complete")
            continue
        stale += missing
        if "--check" in argv:
            print(f"  {name}: {missing} missing")
        else:
            append(path, single, plural, translated=not path.endswith(".pot"))
            print(f"  {name}: {missing} added, they need translating")

    if "--check" in argv and stale:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
