"""Fake ALTAI (EUDET-like DUT), behaving like the ALPIDE DAQ board.

Outside a run busy is held HIGH (the board is "stopped", forced busy). While
armed (in a run) busy is LOW; each trg_o rising edge on PIN_TRG is accepted,
gets the next trigger ID, and busy on PIN_BUSY goes high for --busy-us.
Triggers arriving while busy is high are cancelled (not counted, no ID),
like the real board as primary. --count-during-busy counts them instead
(the replica behaviour is not known yet).

    python3 -m emulator.fake_altai --busy-us 1000               # arm now, Ctrl-C to stop
    python3 -m emulator.fake_altai --arm-after 5 --run-time 30  # one 30 s run
    python3 -m emulator.fake_altai --manual                     # Enter = arm/stop, q = quit
    python3 -m emulator.fake_altai --count-only                 # never drive busy, just count

The TLU waits at most busy_timeout (0xFFFF clk = 410 us) after trg_o for busy
to go high, otherwise it counts N_TIMEOUT. "late" counts reactions slower
than --timeout-us, i.e. the triggers expected to show up as N_TIMEOUT.

At the end of each run prints N_PI <accepted>: pass it to tlu_run.py --n-pi.
When the program exits the busy pin is released (Versa input pull-down -> low).
"""
import argparse
import gc
import select
import sys
import time

from . import gpiochar
from .pins import PIN_BUSY, PIN_TRG
from .timing import Stats, try_realtime, wait_until


# --- per-run counters (reset at every arm)
class RunStats:
    def __init__(self, first_id):
        self.next_id = first_id     # ID given to the next accepted trigger
        self.first_id = first_id
        self.accepted = 0           # triggers that got an ID (and a busy pulse)
        self.cancelled = 0          # triggers that came while busy was high
        self.late = 0               # reactions slower than the TLU busy_timeout
        self.react = Stats()        # kernel edge timestamp -> busy high

    # --- print the end-of-run summary
    def report(self, run_no, count_only, timeout_us, count_during_busy):
        print("run %d summary" % run_no)
        print("  accepted       : %d" % self.accepted)
        if self.accepted:
            print("  trigger IDs    : %d .. %d" % (self.first_id, self.next_id - 1))
        label = "counted (replica mode)" if count_during_busy else "cancelled"
        print("  during busy    : %d %s" % (self.cancelled, label))
        print("  edge -> busy   : %s" % self.react.summary())
        if not count_only:
            print("  late (> %.0f us): %d  -> expect N_TIMEOUT about this" % (timeout_us, self.late))
        print("N_PI %d" % self.accepted, flush=True)


# --- the fake DAQ board: busy line + arm state + trigger handling
class FakeAltai:
    def __init__(self, trg, busy, busy_us, count_only, count_during_busy,
                 first_id, timeout_us):
        self.trg = trg
        self.busy = busy
        self.busy_ns = int(busy_us * 1e3)
        self.count_only = count_only
        self.count_during_busy = count_during_busy
        self.first_id = first_id
        self.timeout_us = timeout_us
        self.timeout_ns = int(timeout_us * 1e3)
        self.armed = False
        self.run_no = 0
        self.stats = None
        self.busy_until = 0         # monotonic ns when the last busy pulse ended
        self.ignored = 0            # edges while not armed (busy already high)
        self.lost = 0               # edges lost in the kernel buffer
        self.last_seq = None
        self._drive(1)              # start "stopped": forced busy

    # --- set the busy pin (never driven in --count-only)
    def _drive(self, level):
        if not self.count_only:
            self.busy.set(level)

    # --- start a run: reset IDs and counters, release the forced busy
    def arm(self):
        self._drain()               # edges from before the run do not count
        self.run_no += 1
        self.stats = RunStats(self.first_id)
        self.armed = True
        self._drive(0)
        print("run %d armed (busy low), first ID %d" % (self.run_no, self.first_id), flush=True)

    # --- stop a run: forced busy back on, print the summary
    def disarm(self):
        if not self.armed:
            return
        self._drive(1)
        self.armed = False
        self.stats.report(self.run_no, self.count_only, self.timeout_us, self.count_during_busy)

    # --- throw away queued edges (counted as ignored)
    def _drain(self):
        while self.trg.wait_event(0):
            self.ignored += len(self.trg.read_events())

    # --- handle every queued edge
    def handle_events(self):
        for ts, ev_id, _off, _seq, line_seq in self.trg.read_events():
            if ev_id != gpiochar.EVENT_RISING_EDGE:
                continue
            # gap in the kernel sequence number = edges lost
            if self.last_seq is not None and line_seq > self.last_seq + 1:
                self.lost += line_seq - self.last_seq - 1
            self.last_seq = line_seq

            if not self.armed:
                self.ignored += 1           # board stopped: busy is high anyway
                continue
            s = self.stats
            if ts < self.busy_until:
                # came while busy was high: cancelled (primary) or counted (replica?)
                s.cancelled += 1
                if self.count_during_busy:
                    s.next_id += 1
                continue

            # accept: busy up as fast as possible, then book-keeping
            self._drive(1)
            t_hi = time.monotonic_ns()
            s.accepted += 1
            s.next_id += 1
            dt = t_hi - ts
            s.react.add(dt)
            if dt > self.timeout_ns:
                s.late += 1
            # hold busy for busy_us, then release
            wait_until(t_hi + self.busy_ns)
            self._drive(0)
            self.busy_until = time.monotonic_ns()


# --- wait for an edge or (in --manual) a line on stdin; returns "trg", "key" or None
def wait_input(trg, manual, timeout):
    fds = [trg.fd] + ([sys.stdin] if manual else [])
    r, _, _ = select.select(fds, [], [], timeout)
    if trg.fd in r:
        return "trg"
    if r:
        return "key"
    return None


# --- main loop: arm / run / disarm according to the options
def run(chip, trg_pin, busy_pin, busy_us, arm_after, run_time, manual, count_only,
        count_during_busy, first_id, timeout_us, rt, cpu):
    note = try_realtime(cpu=cpu) if rt else "normal priority"
    mode = "count only" if count_only else "busy %.1f us" % busy_us
    print("fake_altai: %s trg_o GPIO%d -> busy GPIO%d, %s (%s)"
          % (chip, trg_pin, busy_pin, mode, note))

    with gpiochar.edge_input(chip, trg_pin) as trg, \
            gpiochar.output(chip, busy_pin, 0 if count_only else 1) as busy:
        dev = FakeAltai(trg, busy, busy_us, count_only, count_during_busy, first_id, timeout_us)
        now = time.monotonic_ns()
        t_arm = None if manual else now + int(arm_after * 1e9)
        t_stop = None
        if manual:
            print("stopped (busy high). Enter = arm / stop, q + Enter = quit")
        elif arm_after > 0:
            print("stopped (busy high), arming in %.1f s ..." % arm_after)
        t_print = now + 1_000_000_000
        gc.disable()
        try:
            while True:
                now = time.monotonic_ns()
                # timed arm / stop
                if t_arm is not None and now >= t_arm:
                    t_arm = None
                    dev.arm()
                    if run_time > 0:
                        t_stop = time.monotonic_ns() + int(run_time * 1e9)
                if t_stop is not None and now >= t_stop:
                    dev.disarm()
                    break
                # wait for a trigger edge (or a key in --manual)
                got = wait_input(trg, manual, 0.1)
                if got == "trg":
                    dev.handle_events()
                elif got == "key":
                    line = sys.stdin.readline().strip().lower()
                    if line == "q":
                        break
                    if dev.armed:
                        dev.disarm()
                    else:
                        dev.arm()
                elif dev.armed and time.monotonic_ns() > t_print:
                    # idle: show the count once per second
                    t_print = time.monotonic_ns() + 1_000_000_000
                    print("  %d triggers" % dev.stats.accepted, flush=True)
        except KeyboardInterrupt:
            print()
        finally:
            dev.disarm()
            gc.enable()
        if dev.ignored:
            print("edges while stopped (ignored): %d" % dev.ignored)
        if dev.lost:
            print("WARNING kernel event buffer overflowed, %d edges lost" % dev.lost)
    return dev


# --- command line options
def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--busy-us", type=float, default=1000.0, help="busy high time per trigger [us]")
    p.add_argument("--arm-after", type=float, default=0.0,
                   help="hold busy high this many s, then arm (default 0: arm at once)")
    p.add_argument("--run-time", type=float, default=0.0,
                   help="stop the run after this many s (0: until Ctrl-C)")
    p.add_argument("--manual", action="store_true",
                   help="arm / stop with Enter, quit with q (ignores --arm-after/--run-time)")
    p.add_argument("--first-id", type=int, default=0, help="ID of the first trigger in a run")
    p.add_argument("--count-during-busy", action="store_true",
                   help="give IDs to triggers that come while busy (replica?); default: cancel them")
    p.add_argument("--count-only", action="store_true", help="never drive busy, only count trg_o")
    p.add_argument("--timeout-us", type=float, default=0xFFFF / 160.0,
                   help="TLU busy_timeout to compare with [us] (default 0xFFFF clk = 409.6)")
    p.add_argument("--trg-pin", type=int, default=PIN_TRG)
    p.add_argument("--busy-pin", type=int, default=PIN_BUSY)
    p.add_argument("--chip", help="gpiochip path (default: auto, pinctrl-rp1)")
    p.add_argument("--rt", action="store_true", help="SCHED_FIFO + one CPU (needs sudo)")
    p.add_argument("--cpu", type=int, default=3, help="CPU for --rt (default 3)")
    p.add_argument("--time", type=float, help=argparse.SUPPRESS)   # old name of --run-time
    a = p.parse_args(argv)
    if a.time is not None:
        a.run_time = a.time
    chip = a.chip or gpiochar.find_header_chip()
    run(chip, a.trg_pin, a.busy_pin, a.busy_us, a.arm_after, a.run_time, a.manual,
        a.count_only, a.count_during_busy, a.first_id, a.timeout_us, a.rt, a.cpu)
    return 0


if __name__ == "__main__":
    sys.exit(main())
