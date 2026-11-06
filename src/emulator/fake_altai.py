"""Fake ALTAI (EUDET-like DUT): for each trg_o rising edge on PIN_TRG raise
busy on PIN_BUSY for --busy-us, then drop it.

    python3 -m emulator.fake_altai --busy-us 1000          # Ctrl-C to stop
    python3 -m emulator.fake_altai --count-only            # just count trg_o

The TLU waits at most busy_timeout (0xFFFF clk = 410 us) after trg_o for busy
to go high, otherwise it counts N_TIMEOUT. The reaction time of this script
(edge -> busy high) is printed at the end; "late" counts reactions slower
than --timeout-us, i.e. the triggers expected to show up as N_TIMEOUT.

At the end prints N_PI <count>: pass it to tlu_run.py --n-pi.
"""
import argparse
import gc
import sys
import time

from . import gpiochar
from .pins import PIN_BUSY, PIN_TRG
from .timing import Stats, try_realtime, wait_until


# --- main loop: wait for trg_o, raise busy, hold it, drop it, collect stats
def run(chip, trg_pin, busy_pin, busy_us, duration, count_only, timeout_us, rt, cpu):
    note = try_realtime(cpu=cpu) if rt else "normal priority"
    mode = "count only" if count_only else "busy %.1f us" % busy_us
    print("fake_altai: %s trg_o GPIO%d -> busy GPIO%d, %s (%s)"
          % (chip, trg_pin, busy_pin, mode, note))
    busy_ns = int(busy_us * 1e3)
    timeout_ns = int(timeout_us * 1e3)

    # counters and statistics
    n = 0
    late = 0
    lost = 0
    queued = 0                  # edges that arrived while we were still busy
    react = Stats()
    last_seq = None
    t_stop = time.monotonic_ns() + int(duration * 1e9) if duration > 0 else None
    t_print = time.monotonic_ns() + 1_000_000_000

    with gpiochar.edge_input(chip, trg_pin) as trg, \
            gpiochar.output(chip, busy_pin, 0) as busy:
        print("waiting for trg_o ... (Ctrl-C to stop)")
        gc.disable()
        try:
            while t_stop is None or time.monotonic_ns() < t_stop:
                # idle: print the count once per second
                if not trg.wait_event(0.1):
                    now = time.monotonic_ns()
                    if now > t_print:
                        t_print = now + 1_000_000_000
                        print("  %d triggers" % n, flush=True)
                    continue
                # one or more trg_o edges are queued: answer each in turn
                events = trg.read_events()
                for k, (ts, ev_id, _off, _seq, line_seq) in enumerate(events):
                    if ev_id != gpiochar.EVENT_RISING_EDGE:
                        continue
                    if not count_only:
                        busy.set(1)            # answer as fast as possible
                    t_hi = time.monotonic_ns()
                    n += 1
                    if k:
                        queued += 1
                    # gap in the kernel sequence number = edges lost
                    if last_seq is not None and line_seq > last_seq + 1:
                        lost += line_seq - last_seq - 1
                    last_seq = line_seq
                    # reaction time: kernel edge timestamp -> busy high
                    dt = t_hi - ts
                    react.add(dt)
                    if dt > timeout_ns:
                        late += 1
                    # hold busy for busy_us, then release
                    if not count_only:
                        wait_until(t_hi + busy_ns)
                        busy.set(0)
        except KeyboardInterrupt:
            print()
        finally:
            busy.set(0)
            gc.enable()

    # summary
    print("triggers seen  : %d" % n)
    print("edge -> busy   : %s" % react.summary())
    if not count_only:
        print("late (> %.0f us): %d  -> expect N_TIMEOUT about this" % (timeout_us, late))
    if queued:
        print("WARNING %d trg_o arrived while busy was still high "
              "(TLU should not do that, or busy is not reaching X4-8)" % queued)
    if lost:
        print("WARNING kernel event buffer overflowed, %d edges lost" % lost)
    print("N_PI %d" % n)
    return n


# --- command line options
def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--busy-us", type=float, default=1000.0, help="busy high time [us]")
    p.add_argument("--time", type=float, default=0.0, help="stop after this many s (0: Ctrl-C)")
    p.add_argument("--count-only", action="store_true", help="do not drive busy, only count trg_o")
    p.add_argument("--timeout-us", type=float, default=0xFFFF / 160.0,
                   help="TLU busy_timeout to compare with [us] (default 0xFFFF clk = 409.6)")
    p.add_argument("--trg-pin", type=int, default=PIN_TRG)
    p.add_argument("--busy-pin", type=int, default=PIN_BUSY)
    p.add_argument("--chip", help="gpiochip path (default: auto, pinctrl-rp1)")
    p.add_argument("--rt", action="store_true", help="SCHED_FIFO + one CPU (needs sudo)")
    p.add_argument("--cpu", type=int, default=3, help="CPU for --rt (default 3)")
    a = p.parse_args(argv)
    chip = a.chip or gpiochar.find_header_chip()
    run(chip, a.trg_pin, a.busy_pin, a.busy_us, a.time, a.count_only, a.timeout_us, a.rt, a.cpu)
    return 0


if __name__ == "__main__":
    sys.exit(main())
