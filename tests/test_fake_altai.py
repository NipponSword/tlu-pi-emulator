"""FakeAltai logic with fake GPIO lines (no hardware).

    python3 -m pytest tests     (or: PYTHONPATH=src python3 tests/test_fake_altai.py)
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from emulator import gpiochar  # noqa: E402
from emulator.fake_altai import FakeAltai  # noqa: E402

BUSY_US = 200


# --- fake output line: remembers every level written
class FakeOut:
    def __init__(self):
        self.levels = []

    def set(self, value, index=0):
        self.levels.append(value)

    @property
    def level(self):
        return self.levels[-1] if self.levels else None


# --- fake edge input: hands out prepared batches of rising edges
class FakeIn:
    def __init__(self):
        self.batches = []
        self.seq = 0

    def push(self, *ts_list):
        batch = []
        for ts in ts_list:
            self.seq += 1
            batch.append((ts, gpiochar.EVENT_RISING_EDGE, 27, self.seq, self.seq))
        self.batches.append(batch)

    def wait_event(self, timeout):
        return bool(self.batches)

    def read_events(self, max_events=64):
        return self.batches.pop(0) if self.batches else []


def make(count_during_busy=False, first_id=0, count_only=False, log_path=None):
    trg, busy = FakeIn(), FakeOut()
    dev = FakeAltai(trg, busy, BUSY_US, count_only, count_during_busy, first_id, 409.6, log_path)
    return dev, trg, busy


# --- busy is high before the run and after it, low while armed
def test_busy_high_outside_run():
    dev, trg, busy = make()
    assert busy.level == 1
    dev.arm()
    assert busy.level == 0
    dev.disarm()
    assert busy.level == 1


# --- edges before arming are ignored and do not use IDs
def test_edges_before_arm_ignored():
    dev, trg, busy = make()
    trg.push(time.monotonic_ns())
    dev.handle_events()
    trg.push(time.monotonic_ns())       # still queued when arm() drains
    dev.arm()
    assert dev.ignored == 2
    assert dev.stats.accepted == 0 and dev.stats.next_id == 0


# --- separated triggers are all accepted, IDs count from first_id, one busy pulse each
def test_accept_and_ids():
    dev, trg, busy = make(first_id=1)
    dev.arm()
    for _ in range(3):
        trg.push(time.monotonic_ns())
        dev.handle_events()
    s = dev.stats
    assert s.accepted == 3 and s.cancelled == 0
    assert (s.first_id, s.next_id - 1) == (1, 3)
    assert busy.levels[-6:] == [1, 0, 1, 0, 1, 0]


# --- a trigger that came while busy was high is cancelled (no ID, no busy pulse)
def test_cancel_during_busy():
    dev, trg, busy = make()
    dev.arm()
    t0 = time.monotonic_ns()
    trg.push(t0, t0 + 10_000)            # second edge 10 us later, inside the busy
    dev.handle_events()
    s = dev.stats
    assert s.accepted == 1 and s.cancelled == 1 and s.next_id == 1
    assert busy.levels[-2:] == [1, 0]


# --- replica option: the trigger during busy still takes an ID
def test_count_during_busy():
    dev, trg, busy = make(count_during_busy=True)
    dev.arm()
    t0 = time.monotonic_ns()
    trg.push(t0, t0 + 10_000)
    dev.handle_events()
    s = dev.stats
    assert s.accepted == 1 and s.cancelled == 1 and s.next_id == 2


# --- busy length is about busy_us
def test_busy_length():
    dev, trg, busy = make()
    dev.arm()
    t0 = time.monotonic_ns()
    trg.push(t0)
    dev.handle_events()
    held = dev.busy_until - t0
    assert BUSY_US * 1e3 <= held < (BUSY_US + 5000) * 1e3


# --- every arm starts a new run with IDs from first_id again
def test_rearm_resets_ids():
    dev, trg, busy = make()
    dev.arm()
    trg.push(time.monotonic_ns())
    dev.handle_events()
    dev.disarm()
    dev.arm()
    assert dev.run_no == 2 and dev.stats.next_id == 0 and dev.stats.accepted == 0


# --- --count-only never drives busy
def test_count_only():
    dev, trg, busy = make(count_only=True)
    dev.arm()
    trg.push(time.monotonic_ns())
    dev.handle_events()
    dev.disarm()
    assert busy.levels == [] and dev.stats.accepted == 1


# --- --log writes one CSV line per edge (accepted and cancelled), one file per run
def test_log_csv():
    import tempfile
    d = tempfile.mkdtemp()
    path = os.path.join(d, "react.csv")
    dev, trg, busy = make(log_path=path)
    dev.arm()
    t0 = time.monotonic_ns()
    trg.push(t0, t0 + 10_000)
    dev.handle_events()
    dev.disarm()
    lines = open(path).read().splitlines()
    assert lines[0] == "kind,id,edge_ns,react_ns"
    assert lines[1].startswith("accept,0,%d," % t0)
    assert lines[2] == "cancel,-1,%d," % (t0 + 10_000)
    dev.arm()
    dev.disarm()
    assert os.path.exists(os.path.join(d, "react_run2.csv"))


# --- run without pytest
if __name__ == "__main__":
    for name, f in list(globals().items()):
        if name.startswith("test_"):
            f()
    print("OK")
