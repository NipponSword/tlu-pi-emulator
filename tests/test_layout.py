"""Struct sizes and ioctl numbers must match <linux/gpio.h> (any Linux, no GPIO needed).

    python3 -m pytest tests     (or: PYTHONPATH=src python3 tests/test_layout.py)
"""
import ctypes
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from emulator import gpiochar as g  # noqa: E402


# --- struct sizes: ctypes layout must match the kernel structs byte for byte
def test_sizes():
    assert ctypes.sizeof(g.ChipInfo) == 68
    assert ctypes.sizeof(g.LineAttribute) == 16
    assert ctypes.sizeof(g.LineConfigAttribute) == 24
    assert ctypes.sizeof(g.LineConfig) == 272
    assert ctypes.sizeof(g.LineRequest) == 592
    assert ctypes.sizeof(g.LineValues) == 16
    assert g.EVENT_SIZE == 48


# --- ioctl numbers: encode direction, size and command like _IOR/_IOWR
def test_ioctl_numbers():
    # expected values printed by a C program including <linux/gpio.h>
    assert g.GPIO_GET_CHIPINFO_IOCTL == 0x8044B401
    assert g.GPIO_V2_GET_LINE_IOCTL == 0xC250B407
    assert g.GPIO_V2_LINE_GET_VALUES_IOCTL == 0xC010B40E
    assert g.GPIO_V2_LINE_SET_VALUES_IOCTL == 0xC010B40F


# --- command line: every tool imports and prints --help without GPIO hardware
def test_cli_help():
    from emulator import fake_altai, loopback, pulser
    for m in (pulser, fake_altai, loopback):
        try:
            m.main(["--help"])
        except SystemExit as e:
            assert e.code == 0


# --- run without pytest
if __name__ == "__main__":
    test_sizes()
    test_ioctl_numbers()
    print("OK")
