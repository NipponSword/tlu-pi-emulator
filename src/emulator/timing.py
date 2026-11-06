"""Busy-wait helpers: time.sleep() alone is too coarse for us-level pulses."""
import time

_SPIN_NS = 300_000      # sleep until 0.3 ms before the target, then spin


def wait_until(t_ns):
    """Return at monotonic time t_ns [ns] (late by a few us at most)."""
    rest = t_ns - time.monotonic_ns()
    if rest > _SPIN_NS:
        time.sleep((rest - _SPIN_NS) * 1e-9)
    while time.monotonic_ns() < t_ns:
        pass


def try_realtime(priority=50, cpu=None):
    """Best effort: SCHED_FIFO + pin to one CPU. Returns a note for the log."""
    notes = []
    if cpu is not None:
        try:
            import os
            os.sched_setaffinity(0, {cpu})
            notes.append("cpu %d" % cpu)
        except OSError as e:
            notes.append("affinity failed (%s)" % e.strerror)
    try:
        import os
        os.sched_setscheduler(0, os.SCHED_FIFO, os.sched_param(priority))
        notes.append("SCHED_FIFO %d" % priority)
    except (OSError, AttributeError) as e:
        notes.append("no realtime (%s; run with sudo for it)" % getattr(e, "strerror", e))
    return ", ".join(notes)


class Stats:
    """min / mean / max / 99 % of a list of values in ns, shown in us."""

    def __init__(self):
        self.v = []

    def add(self, x):
        self.v.append(x)

    def __len__(self):
        return len(self.v)

    def summary(self):
        if not self.v:
            return "no data"
        s = sorted(self.v)
        p99 = s[min(len(s) - 1, int(0.99 * len(s)))]
        return "min %.1f  mean %.1f  99%% %.1f  max %.1f us" % (
            s[0] * 1e-3, sum(s) / len(s) * 1e-3, p99 * 1e-3, s[-1] * 1e-3)
