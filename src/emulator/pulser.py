"""Fake PMT: send exactly N pulses on PIN_PMT (-> X4-7 trg_i[0]).

    python3 -m emulator.pulser --n 1000 --rate 100

Prints the number of pulses really sent (also after Ctrl-C), so it can be
compared with N_ACCEPT / the number of events from tlu_run.py.
"""
import argparse
import gc
import sys
import time

from . import gpiochar
from .pins import PIN_PMT
from .timing import try_realtime, wait_until


def run(chip, pin, n, rate, width_us, start_delay, rt):
    period_ns = int(1e9 / rate)
    width_ns = int(width_us * 1e3)
    if width_ns >= period_ns:
        raise SystemExit("width %.1f us >= period %.1f us" % (width_us, period_ns * 1e-3))

    note = try_realtime() if rt else "normal priority"
    print("pulser: %s pin GPIO%d, %d pulses, %.1f Hz, width %.1f us (%s)"
          % (chip, pin, n, rate, width_us, note))
    sent = 0
    with gpiochar.output(chip, pin, 0) as out:
        if start_delay > 0:
            print("start in %.1f s ..." % start_delay)
            time.sleep(start_delay)
        gc.disable()
        t0 = time.monotonic_ns() + 1_000_000
        late = 0
        t_last = t0
        try:
            for k in range(n):
                t_on = t0 + k * period_ns
                if time.monotonic_ns() > t_on + period_ns // 2:
                    late += 1           # fell behind (rate too high for Python)
                wait_until(t_on)
                out.set(1)
                t_last = time.monotonic_ns()
                wait_until(time.monotonic_ns() + width_ns)
                out.set(0)
                sent += 1
        except KeyboardInterrupt:
            print("\ninterrupted")
        finally:
            out.set(0)
            gc.enable()
        dt = (t_last - t0) * 1e-9          # first to last pulse
    rate_real = (sent - 1) / dt if sent > 1 and dt > 0 else 0.0
    print("sent %d pulses in %.3f s (%.1f Hz)" % (sent, dt, rate_real))
    if late:
        print("WARNING %d pulses more than half a period late: lower --rate" % late)
    print("N_PULSES %d" % sent)
    return sent


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--n", type=int, default=100, help="number of pulses")
    p.add_argument("--rate", type=float, default=100.0, help="pulses per second [Hz]")
    p.add_argument("--width-us", type=float, default=10.0, help="pulse width [us]")
    p.add_argument("--start-delay", type=float, default=0.0, help="wait before the first pulse [s]")
    p.add_argument("--pin", type=int, default=PIN_PMT, help="BCM GPIO (default %d)" % PIN_PMT)
    p.add_argument("--chip", help="gpiochip path (default: auto, pinctrl-rp1)")
    p.add_argument("--rt", action="store_true", help="try SCHED_FIFO (needs sudo)")
    a = p.parse_args(argv)
    chip = a.chip or gpiochar.find_header_chip()
    run(chip, a.pin, a.n, a.rate, a.width_us, a.start_delay, a.rt)
    return 0


if __name__ == "__main__":
    sys.exit(main())
