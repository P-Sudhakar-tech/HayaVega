"""Fast CI performance-regression gate for Hayavega's own throughput.

Runs the `json_echo` and `db_list` scenarios against the Hayavega (WSGI)
target only, at a CI-appropriate duration (short and lower-fidelity than
Phase A's full statistical methodology -- this needs to be fast on every
PR, not maximally precise), and compares each scenario's requests_per_sec
against the committed baseline in benchmarks/results/perf_baseline.json.
Both scenarios are always measured and printed, but only `json_echo`
actually fails the job on a regression -- see BLOCKING_SCENARIOS below for
why db_list is informational-only on shared CI runners.

Usage:
    python benchmarks/perf_gate.py                  # check against baseline
    python benchmarks/perf_gate.py --refresh-baseline  # re-measure and overwrite baseline
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmarks.run import SCENARIOS, aggregate, benchmark_target, build_targets  # noqa: E402

BASELINE_PATH = Path(__file__).resolve().parent / "results" / "perf_baseline.json"

DURATION = "5s"
CONNECTIONS = 50
WARMUP_REQUESTS = 50
REGRESSION_THRESHOLD = -0.15
# A single 5s sample is noisy enough on shared CI runners (observed ~2x
# run-to-run swings on db_list with zero code changes) to blow past the
# 15% regression threshold on noise alone. runs=3 + the median (not the
# full suite's runs=5 -- this gate needs to stay fast on every push/PR)
# smooths the worst of that without materially slowing the gate down.
RUNS = 3

GATE_SCENARIOS = [s for s in SCENARIOS if s[0] in {"json_echo", "db_list"}]

# json_echo only. Three real CI runs on GitHub's shared ubuntu-latest runners
# measured db_list medians (already runs=3 + statistics.median, see RUNS above)
# of 75, 465, and 838 req/s with *zero code changes between them* -- a ~10x
# spread. That's between-VM noise: each CI job gets a freshly provisioned
# runner, and db_list's absolute throughput (disk-I/O-bound, via sqlite) is
# extremely sensitive to whatever disk/CPU characteristics that particular VM
# happens to have. Raising RUNS further (even to 10) cannot fix this -- more
# repeats only smooth noise *within* one VM's run, not the variance that only
# shows up *across* VMs. json_echo (pure CPU dispatch overhead, no disk I/O)
# doesn't share this problem and has stayed comparably stable across the same
# CI runs. So db_list is measured and printed on every run (never hidden --
# this project discloses rather than silently drops inconvenient numbers) but
# no longer fails the gate on its own; only json_echo blocks the job.
BLOCKING_SCENARIOS = {"json_echo"}


def measure() -> dict[str, float]:
    target = next(t for t in build_targets(connections=CONNECTIONS) if t.name == "Hayavega")
    result = benchmark_target(
        target,
        duration=DURATION,
        connections=CONNECTIONS,
        warmup_requests=WARMUP_REQUESTS,
        scenarios=GATE_SCENARIOS,
        runs=RUNS,
    )
    if result.error:
        print(f"error running Hayavega target: {result.error}", file=sys.stderr)
        sys.exit(1)
    return {name: aggregate(result.scenarios[name]).requests_per_sec_median for name, _method, _path in GATE_SCENARIOS}


def load_baseline() -> dict[str, dict[str, float]]:
    with BASELINE_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_baseline(measured: dict[str, float]) -> None:
    data = {name: {"requests_per_sec": measured[name]} for name, _method, _path in GATE_SCENARIOS}
    with BASELINE_PATH.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=False)
        f.write("\n")


def check(measured: dict[str, float], baseline: dict[str, dict[str, float]]) -> bool:
    failed = False
    for name, _method, _path in GATE_SCENARIOS:
        measured_value = measured[name]
        baseline_value = baseline[name]["requests_per_sec"]
        pct_change = (measured_value - baseline_value) / baseline_value
        print(f"{name}: {measured_value:.0f} req/s (baseline {baseline_value:.0f} req/s, {pct_change:+.1%})")
        if pct_change < REGRESSION_THRESHOLD:
            if name not in BLOCKING_SCENARIOS:
                print(f"{name}: {measured_value:.0f} req/s vs baseline {baseline_value:.0f} req/s ({pct_change:.1%}) -- informational only, not gated (see BLOCKING_SCENARIOS)")
                continue
            print(f"{name}: {measured_value:.0f} req/s vs baseline {baseline_value:.0f} req/s ({pct_change:.1%})")
            failed = True
    return failed


def refresh(measured: dict[str, float]) -> None:
    old_baseline = load_baseline() if BASELINE_PATH.exists() else {}
    for name, _method, _path in GATE_SCENARIOS:
        old_value = old_baseline.get(name, {}).get("requests_per_sec")
        new_value = measured[name]
        if old_value is None:
            print(f"{name}: (none) -> {new_value:.0f} req/s")
        else:
            print(f"{name}: {old_value:.0f} -> {new_value:.0f} req/s")
    write_baseline(measured)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh-baseline", action="store_true", help="re-measure and overwrite the committed baseline instead of checking against it")
    args = parser.parse_args()

    measured = measure()

    if args.refresh_baseline:
        refresh(measured)
        sys.exit(0)

    baseline = load_baseline()
    failed = check(measured, baseline)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
