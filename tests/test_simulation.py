"""End-to-end simulation tests.

Drives a full short run in-process via ``init.build_and_run`` and checks that:
- a fixed seed reproduces an identical metric summary (determinism / regression guard);
- the headline metrics satisfy basic sanity invariants.

No brittle golden numbers are asserted - only reproducibility and invariants.
"""
import sys
from pathlib import Path

import pytest

# build_and_run lives in the standalone script module src/script/init.py. The src/ root
# is already on sys.path via [tool.pytest.ini_options] pythonpath; add src/script so the
# bare ``import init`` resolves the same way ``uv run src/script/init.py`` does.
SCRIPT_DIR = Path(__file__).resolve().parents[1] / "src" / "script"
sys.path.insert(0, str(SCRIPT_DIR))

from init import build_and_run  # noqa: E402

# A small, fast, but non-trivial run exercising the forwarded multihop path.
BASE_PARAMS = dict(
    mode="dynamic_acab",
    scenario="random",
    duration=20.0,
    seed=12345,
    world_width=500.0,
    world_height=500.0,
    mobile_buoy_count=8,
    fixed_buoy_count=12,
    density=20,
    ideal=False,
    static_interval=1.0,
    min_interval=1.0,
    max_interval=5.0,
    multihop_mode="forwarded",
)


def _run(**overrides):
    metrics, sim_time = build_and_run(**{**BASE_PARAMS, **overrides})
    assert metrics is not None
    return metrics.summary(sim_time=sim_time)


def test_run_is_deterministic_for_fixed_seed():
    # build_and_run reseeds the global RNG internally, so two calls with the same seed
    # must produce byte-identical summaries.
    assert _run() == _run()


@pytest.mark.parametrize("multihop_mode", ["none", "append", "forwarded"])
def test_metric_invariants(multihop_mode):
    s = _run(multihop_mode=multihop_mode)

    for key in ("PDR", "Delivery Ratio", "Collision Rate"):
        assert 0.0 <= s[key] <= 1.0, f"{key} out of range: {s[key]}"

    # Every reception is one of the potential receptions counted at broadcast time.
    assert s["Actually Received"] <= s["Potentially Sent"]

    # Forwards are a subset of all transmissions.
    assert s["Forwards Sent"] <= s["Sent"]

    # A unique delivered beacon must have been originated (sent as an own beacon),
    # so it can never exceed the number of origin-generated beacons.
    originated = s["Sent"] - s["Forwards Sent"]
    assert s["Unique Beacons Received"] <= originated


def test_none_mode_sends_no_forwards():
    s = _run(multihop_mode="none")
    assert s["Forwards Sent"] == 0
