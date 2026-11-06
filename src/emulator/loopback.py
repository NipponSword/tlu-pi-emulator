"""Pi-only self test, no TLU needed.

Jumper GPIO17 (pin 11) -> GPIO27 (pin 13). Sends N pulses on PIN_PMT and
counts the rising edges seen on PIN_TRG with the same edge reader that
fake_altai uses. Checks that no edge is lost and shows the wake-up time
(edge -> Python sees it), which is what limits the fake ALTAI.

    python3 -m emulator.loopback --n 1000 --width-us 1
"""
import argparse
import gc
import sys
import time

from . import gpiochar
from .pins import PIN_PMT, PIN_TRG
from .timing import Stats, try_realtime, wait_until


# --- send pulses on one pin, count edges on the other, report wake-up time
def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--n", type=int, default=1000)
    p.add_argument("--width-us", type=float, default=2.0)
    p.add_argument("--gap-us", type=float, default=2000.0, help="time between pulses [us]")
    p.add_argument("--out-pin", type=int, default=PIN_PMT)
    p.add_argument("--in-pin", type=int, default=PIN_TRG)
    p.add_argument("--chip")
    p.add_argument("--rt", action="store_true")
    a = p.parse_args(argv)
    chip = a.chip or gpiochar.find_header_chip()
    note = try_realtime() if a.rt else "normal priority"
    print("loopback: %s GPIO%d -> GPIO%d, %d pulses, width %.1f us (%s)"
          % (chip, a.out_pin, a.in_pin, a.n, a.width_us, note))

    wake = Stats()
    seen = 0
    width_ns, gap_ns = int(a.width_us * 1e3), int(a.gap_us * 1e3)
    with gpiochar.output(chip, a.out_pin, 0) as out, \
            gpiochar.edge_input(chip, a.in_pin) as inp:
        gc.disable()
        try:
            for _ in range(a.n):
                # one pulse on the output pin
                out.set(1)
                wait_until(time.monotonic_ns() + width_ns)
                out.set(0)
                # wait up to one gap for the edge on the input pin
                t_end = time.monotonic_ns() + gap_ns
                # read whatever arrived, with the timeout of one gap
                while time.monotonic_ns() < t_end:
                    if inp.wait_event(max(0.0, (t_end - time.monotonic_ns()) * 1e-9)):
                        t_seen = time.monotonic_ns()
                        for ts, ev_id, *_ in inp.read_events():
                            if ev_id == gpiochar.EVENT_RISING_EDGE:
                                seen += 1
                                wake.add(t_seen - ts)
                        break
                wait_until(t_end)
        finally:
            out.set(0)
            gc.enable()

    # result: every pulse must have been seen once
    print("sent %d, seen %d  -> %s" % (a.n, seen, "OK" if seen == a.n else "MISMATCH"))
    print("edge -> Python : %s" % wake.summary())
    return 0 if seen == a.n else 1


if __name__ == "__main__":
    sys.exit(main())
