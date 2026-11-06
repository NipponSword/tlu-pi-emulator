"""Minimal Linux GPIO character-device (uAPI v2) access, no extra packages.

Why not lgpio for the fake ALTAI: lgpio holds every edge alert back by
500 us before it calls the Python callback (to sort events in time), so
busy would always rise too late for the TLU busy_timeout (max 0xFFFF clk
= 410 us at 160 MHz). Here we read the kernel edge events ourselves:
interrupt driven, every edge is buffered by the kernel (none missed even
for a 100 ns pulse), and the wake-up latency is only tens of us.

Layout follows <linux/gpio.h> (GPIO_V2_*), checked by tests/test_layout.py.
"""
import ctypes
import fcntl
import os
import select
import struct

# ---- <linux/gpio.h> constants
GPIO_MAX_NAME_SIZE = 32
GPIO_V2_LINES_MAX = 64
GPIO_V2_LINE_NUM_ATTRS_MAX = 10

FLAG_USED = 1 << 0
FLAG_ACTIVE_LOW = 1 << 1
FLAG_INPUT = 1 << 2
FLAG_OUTPUT = 1 << 3
FLAG_EDGE_RISING = 1 << 4
FLAG_EDGE_FALLING = 1 << 5
FLAG_BIAS_PULL_UP = 1 << 8
FLAG_BIAS_PULL_DOWN = 1 << 9
FLAG_BIAS_DISABLED = 1 << 10

ATTR_ID_FLAGS = 1
ATTR_ID_OUTPUT_VALUES = 2
ATTR_ID_DEBOUNCE = 3

EVENT_RISING_EDGE = 1
EVENT_FALLING_EDGE = 2


class ChipInfo(ctypes.Structure):
    _fields_ = [("name", ctypes.c_char * GPIO_MAX_NAME_SIZE),
                ("label", ctypes.c_char * GPIO_MAX_NAME_SIZE),
                ("lines", ctypes.c_uint32)]


class LineAttribute(ctypes.Structure):
    # union { u64 flags; u64 values; u32 debounce_period_us; } -> one u64
    _fields_ = [("id", ctypes.c_uint32),
                ("padding", ctypes.c_uint32),
                ("value", ctypes.c_uint64)]


class LineConfigAttribute(ctypes.Structure):
    _fields_ = [("attr", LineAttribute),
                ("mask", ctypes.c_uint64)]


class LineConfig(ctypes.Structure):
    _fields_ = [("flags", ctypes.c_uint64),
                ("num_attrs", ctypes.c_uint32),
                ("padding", ctypes.c_uint32 * 5),
                ("attrs", LineConfigAttribute * GPIO_V2_LINE_NUM_ATTRS_MAX)]


class LineRequest(ctypes.Structure):
    _fields_ = [("offsets", ctypes.c_uint32 * GPIO_V2_LINES_MAX),
                ("consumer", ctypes.c_char * GPIO_MAX_NAME_SIZE),
                ("config", LineConfig),
                ("num_lines", ctypes.c_uint32),
                ("event_buffer_size", ctypes.c_uint32),
                ("padding", ctypes.c_uint32 * 5),
                ("fd", ctypes.c_int32)]


class LineValues(ctypes.Structure):
    _fields_ = [("bits", ctypes.c_uint64),
                ("mask", ctypes.c_uint64)]


# struct gpio_v2_line_event: u64 timestamp_ns; u32 id, offset, seqno, line_seqno; u32 padding[6]
EVENT_FMT = "=QIIII6I"
EVENT_SIZE = struct.calcsize(EVENT_FMT)   # 48


def _ioc(direction, nr, size):
    return (direction << 30) | (size << 16) | (0xB4 << 8) | nr


_IOR, _IOWR = 2, 3
GPIO_GET_CHIPINFO_IOCTL = _ioc(_IOR, 0x01, ctypes.sizeof(ChipInfo))
GPIO_V2_GET_LINE_IOCTL = _ioc(_IOWR, 0x07, ctypes.sizeof(LineRequest))
GPIO_V2_LINE_GET_VALUES_IOCTL = _ioc(_IOWR, 0x0E, ctypes.sizeof(LineValues))
GPIO_V2_LINE_SET_VALUES_IOCTL = _ioc(_IOWR, 0x0F, ctypes.sizeof(LineValues))


def chip_info(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        info = ChipInfo()
        fcntl.ioctl(fd, GPIO_GET_CHIPINFO_IOCTL, info)
        return info.name.decode(), info.label.decode(), info.lines
    finally:
        os.close(fd)


def find_header_chip():
    """Path of the gpiochip that drives the 40-pin header.

    Pi 5: RP1 ("pinctrl-rp1"), gpiochip0 on new kernels, gpiochip4 on old ones.
    Older Pis: "pinctrl-bcm2711" / "pinctrl-bcm2835".
    """
    found = []
    for name in sorted(os.listdir("/dev")):
        if not name.startswith("gpiochip"):
            continue
        path = "/dev/" + name
        try:
            _, label, _ = chip_info(path)
        except OSError:
            continue
        found.append((path, label))
        if label.startswith("pinctrl-rp1") or label.startswith("pinctrl-bcm"):
            return path
    raise RuntimeError("no 40-pin header gpiochip found (seen: %s)" % found)


class Lines:
    """One line request: a set of GPIO lines with the same settings."""

    def __init__(self, chip, offsets, flags, consumer="tlu-pi-emulator",
                 values=None, event_buffer_size=0):
        self.offsets = list(offsets)
        req = LineRequest()
        for k, off in enumerate(self.offsets):
            req.offsets[k] = off
        req.num_lines = len(self.offsets)
        req.consumer = consumer.encode()[:GPIO_MAX_NAME_SIZE - 1]
        req.config.flags = flags
        if values is not None and flags & FLAG_OUTPUT:
            bits = sum((1 << k) for k, v in enumerate(values) if v)
            a = req.config.attrs[0]
            a.attr.id = ATTR_ID_OUTPUT_VALUES
            a.attr.value = bits
            a.mask = (1 << len(self.offsets)) - 1
            req.config.num_attrs = 1
        req.event_buffer_size = event_buffer_size
        chip_fd = os.open(chip, os.O_RDONLY)
        try:
            fcntl.ioctl(chip_fd, GPIO_V2_GET_LINE_IOCTL, req)
        finally:
            os.close(chip_fd)
        self.fd = req.fd
        self._vals = LineValues()

    # -- outputs
    def set(self, value, index=0):
        self._vals.mask = 1 << index
        self._vals.bits = (1 << index) if value else 0
        fcntl.ioctl(self.fd, GPIO_V2_LINE_SET_VALUES_IOCTL, self._vals)

    # -- inputs
    def get(self, index=0):
        v = LineValues()
        v.mask = 1 << index
        fcntl.ioctl(self.fd, GPIO_V2_LINE_GET_VALUES_IOCTL, v)
        return (v.bits >> index) & 1

    def wait_event(self, timeout):
        """True if an edge event is ready within timeout [s]."""
        r, _, _ = select.select([self.fd], [], [], timeout)
        return bool(r)

    def read_events(self, max_events=64):
        """List of (timestamp_ns, event_id, offset, seqno, line_seqno)."""
        buf = os.read(self.fd, EVENT_SIZE * max_events)
        out = []
        for k in range(len(buf) // EVENT_SIZE):
            f = struct.unpack_from(EVENT_FMT, buf, k * EVENT_SIZE)
            out.append(f[:5])
        return out

    def close(self):
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def output(chip, offset, value=0):
    return Lines(chip, [offset], FLAG_OUTPUT, values=[value])


def edge_input(chip, offset, rising=True, falling=False, pull_down=True, buffer=1024):
    flags = FLAG_INPUT
    if rising:
        flags |= FLAG_EDGE_RISING
    if falling:
        flags |= FLAG_EDGE_FALLING
    if pull_down:
        flags |= FLAG_BIAS_PULL_DOWN
    return Lines(chip, [offset], flags, event_buffer_size=buffer)
