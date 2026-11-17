"""pulser schedule and run loop with a fake output line (no hardware).

    python3 -m pytest tests     (or: PYTHONPATH=src python3 tests/test_pulser.py)
"""
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from emulator import gpiochar, pulser  # noqa: E402


# --- fixed rate: exact period, no drift
def test_fixed_schedule():
    g = pulser.schedule(0, 1000, False, 15_000, random.Random(1))
    ts = [next(g)[0] for _ in range(5)]
    assert ts == [0, 1_000_000, 2_000_000, 3_000_000, 4_000_000]


# --- Poisson: mean gap ~ 1/rate, never shorter than min_gap
def test_poisson_schedule():
    rate, min_gap = 500.0, 15_000
    g = pulser.schedule(0, rate, True, min_gap, random.Random(42))
    ts = [next(g)[0] for _ in range(20001)]
    gaps = [b - a for a, b in zip(ts, ts[1:])]
    assert min(gaps) >= min_gap
    mean_ms = sum(gaps) / len(gaps) * 1e-6
    assert 1.95 < mean_ms < 2.05            # 1/500 Hz = 2 ms


# --- fake output line: counts rising edges
class FakeOut:
    def __init__(self):
        self.rises = 0

    def set(self, v, index=0):
        self.rises += v

    def __enter__(self):
        return self

    def __exit__(self, *e):
        pass


# --- duration mode stops on time, n mode stops on count
def test_run_duration_and_n():
    out = FakeOut()
    gpiochar.output = lambda *a, **k: out
    sent = pulser.run("/dev/x", 17, 0, 2000, 10, 0, False, poisson=True, duration_s=0.2, seed=1)
    assert sent == out.rises and 300 < sent < 500     # ~400 expected
    out.rises = 0
    assert pulser.run("/dev/x", 17, 50, 2000, 10, 0, False) == 50 == out.rises


# --- run without pytest
if __name__ == "__main__":
    for name, f in list(globals().items()):
        if name.startswith("test_"):
            f()
    print("OK")
