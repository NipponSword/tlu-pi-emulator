"""Fake PMT: send pulses on PIN_PMT (-> X4-7 trg_i[0]).

    python3 -m emulator.pulser --n 1000 --rate 100                 # 1000 pulses, every 10 ms
    python3 -m emulator.pulser --rate 500 --poisson --duration 300 # beam-like: random, 500 Hz mean, 5 min

--poisson draws the time to the next pulse from an exponential distribution
(mean 1/rate), like particles arriving at random. Gaps shorter than
--min-gap-us (default: width + 5 us, so the pulse can go low again) are
stretched to that value; their number is printed ("short gaps").

Prints the number of pulses really sent (also after Ctrl-C), so it can be
compared with N_ACCEPT / the number of events from tlu_run.py.
"""
import argparse
import gc
import random
import sys
import time

from . import gpiochar
from .pins import PIN_PMT
from .timing import try_realtime, wait_until


# --- pulse start times: fixed period or random (Poisson) gaps
def schedule(t0, rate, poisson, min_gap_ns, rng):
    period_ns = int(1e9 / rate)
    k = 0
    t = t0
    while True:
        if poisson:
            gap = int(rng.expovariate(rate) * 1e9)
            short = gap < min_gap_ns
            yield t, short
            t += max(gap, min_gap_ns)
        else:
            yield t0 + k * period_ns, False     # t0 + k * period: no drift
            k += 1


# --- send pulses until n are sent or duration_s has passed
def run(chip, pin, n, rate, width_us, start_delay, rt, poisson=False, duration_s=0.0,
        min_gap_us=None, seed=None):
    period_ns = int(1e9 / rate)
    width_ns = int(width_us * 1e3)
    if min_gap_us is None:
        min_gap_us = width_us + 5.0
    min_gap_ns = int(min_gap_us * 1e3)
    if not poisson and width_ns >= period_ns:
        raise SystemExit("width %.1f us >= period %.1f us" % (width_us, period_ns * 1e-3))
    if min_gap_ns <= width_ns:
        raise SystemExit("--min-gap-us must be longer than --width-us")
    if n <= 0 and duration_s <= 0:
        raise SystemExit("give --n or --duration")

    note = try_realtime() if rt else "normal priority"
    what = "%d pulses" % n if n > 0 else "%.1f s" % duration_s
    if n > 0 and duration_s > 0:
        what = "%d pulses or %.1f s" % (n, duration_s)
    print("pulser: %s pin GPIO%d, %s, %s %.1f Hz, width %.1f us (%s)"
          % (chip, pin, what, "random (Poisson) mean" if poisson else "fixed", rate, width_us, note))
    rng = random.Random(seed)
    sent = 0
    short = 0
    late = 0
    late_limit = period_ns // 2 if not poisson else 100_000   # 100 us behind = late
    with gpiochar.output(chip, pin, 0) as out:
        if start_delay > 0:
            print("start in %.1f s ..." % start_delay)
            time.sleep(start_delay)
        # no garbage collection pauses while pulsing
        gc.disable()
        t0 = time.monotonic_ns() + 1_000_000
        t_end = t0 + int(duration_s * 1e9) if duration_s > 0 else None
        t_last = t0
        try:
            for t_on, was_short in schedule(t0, rate, poisson, min_gap_ns, rng):
                if (n > 0 and sent >= n) or (t_end is not None and t_on >= t_end):
                    break
                if time.monotonic_ns() > t_on + late_limit:
                    late += 1           # fell behind (rate too high for Python)
                # high for width_ns, then low
                wait_until(t_on)
                out.set(1)
                t_last = time.monotonic_ns()
                wait_until(t_last + width_ns)
                out.set(0)
                sent += 1
                short += was_short
        except KeyboardInterrupt:
            print("\ninterrupted")
        finally:
            out.set(0)
            gc.enable()
        # measured rate from first to last pulse
        dt = (t_last - t0) * 1e-9
    rate_real = (sent - 1) / dt if sent > 1 and dt > 0 else 0.0
    print("sent %d pulses in %.3f s (%.1f Hz)" % (sent, dt, rate_real))
    if poisson:
        print("short gaps stretched to %.1f us: %d" % (min_gap_us, short))
    if late:
        print("WARNING %d pulses late: lower --rate (or use --rt)" % late)
    print("N_PULSES %d" % sent)
    return sent


# --- command line options
def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--n", type=int, default=None,
                   help="number of pulses (default 100, or no limit with --duration)")
    p.add_argument("--duration", type=float, default=0.0, help="stop after this many s")
    p.add_argument("--rate", type=float, default=100.0, help="(mean) pulses per second [Hz]")
    p.add_argument("--poisson", action="store_true", help="random gaps (Poisson), mean = 1/rate")
    p.add_argument("--min-gap-us", type=float, help="shortest gap in --poisson (default width + 5 us)")
    p.add_argument("--seed", type=int, help="random seed for --poisson (repeatable runs)")
    p.add_argument("--width-us", type=float, default=10.0, help="pulse width [us]")
    p.add_argument("--start-delay", type=float, default=0.0, help="wait before the first pulse [s]")
    p.add_argument("--pin", type=int, default=PIN_PMT, help="BCM GPIO (default %d)" % PIN_PMT)
    p.add_argument("--chip", help="gpiochip path (default: auto, pinctrl-rp1)")
    p.add_argument("--rt", action="store_true", help="try SCHED_FIFO (needs sudo)")
    a = p.parse_args(argv)
    n = a.n if a.n is not None else (0 if a.duration > 0 else 100)
    chip = a.chip or gpiochar.find_header_chip()
    run(chip, a.pin, n, a.rate, a.width_us, a.start_delay, a.rt,
        a.poisson, a.duration, a.min_gap_us, a.seed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
