#!/usr/bin/env python3
"""A virtual absolute pointer, so the island can be tested without a hand.

The island only gets the clicks that fall inside the input region it hands to
the compositor, so the only honest way to test that region is to move a real
pointer over it and press.  This registers a uinput device that libinput picks
up like any other absolute pointing device.

Needs write access to /dev/uinput — on Fedora the seat owner gets it by ACL.

    ./tools/synthetic_pointer.py click 2560 60
    ./tools/synthetic_pointer.py swipe 2560 60 2560 10
"""

from __future__ import annotations

import sys
import time

from evdev import AbsInfo, UInput
from evdev import ecodes as e

ABS_MAX = 32767


class Pointer:
    """A uinput absolute pointer. Keep one alive for a whole test run."""

    def __init__(self, width: int, height: int, settle: float = 1.3) -> None:
        self.width = width
        self.height = height
        self._device = UInput(
            {
                e.EV_KEY: [e.BTN_LEFT, e.BTN_MIDDLE, e.BTN_RIGHT],
                e.EV_ABS: [
                    (e.ABS_X, AbsInfo(0, 0, ABS_MAX, 0, 0, 0)),
                    (e.ABS_Y, AbsInfo(0, 0, ABS_MAX, 0, 0, 0)),
                ],
            },
            name="dynamic-island-test-pointer",
            version=1,
        )
        # libinput needs a moment to notice the device and add it to the seat.
        time.sleep(settle)

    def close(self) -> None:
        self._device.close()

    def __enter__(self) -> "Pointer":
        return self

    def __exit__(self, *_) -> None:
        self.close()

    # -- primitives -----------------------------------------------------
    def move(self, x: float, y: float, settle: float = 0.22) -> None:
        self._device.write(e.EV_ABS, e.ABS_X, round(x / self.width * ABS_MAX))
        self._device.write(e.EV_ABS, e.ABS_Y, round(y / self.height * ABS_MAX))
        self._device.syn()
        time.sleep(settle)

    def click(self, x: float, y: float, button: int = e.BTN_LEFT) -> None:
        self.move(x, y)
        self._device.write(e.EV_KEY, button, 1)
        self._device.syn()
        time.sleep(0.09)
        self._device.write(e.EV_KEY, button, 0)
        self._device.syn()
        time.sleep(0.3)

    def swipe(self, x1: float, y1: float, x2: float, y2: float, steps: int = 14) -> None:
        self.move(x1, y1)
        self._device.write(e.EV_KEY, e.BTN_LEFT, 1)
        self._device.syn()
        time.sleep(0.05)
        for step in range(1, steps + 1):
            self.move(
                x1 + (x2 - x1) * step / steps,
                y1 + (y2 - y1) * step / steps,
                settle=0.02,
            )
        self._device.write(e.EV_KEY, e.BTN_LEFT, 0)
        self._device.syn()
        time.sleep(0.3)

    def park(self) -> None:
        """Get out of the way so a stray hover does not hold a timer open."""
        self.move(self.width // 2, self.height - 4)


def main(argv: list[str]) -> int:
    if len(argv) < 4:
        print(__doc__)
        return 2

    command = argv[1]
    with Pointer(5120, 1440) as pointer:
        if command == "move":
            pointer.move(int(argv[2]), int(argv[3]))
        elif command == "click":
            pointer.click(int(argv[2]), int(argv[3]))
        elif command == "middleclick":
            pointer.click(int(argv[2]), int(argv[3]), e.BTN_MIDDLE)
        elif command == "swipe":
            pointer.swipe(*(int(value) for value in argv[2:6]))
        else:
            print(f"unknown command {command}")
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
