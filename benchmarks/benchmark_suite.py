"""
benchmark_suite.py
InterSync — top-level benchmark orchestrator.

Connects to each LXC container, runs all scenarios, collects results,
and writes:
  results/latency.csv
  results/throughput.csv
  results/report.json

Run via:
    make benchmark
    # or directly:
    python3 benchmarks/benchmark_suite.py
"""

from __future__ import annotations

import json
import logging
import sys
import os
import time
from pathlib import Path

# Ensure project root is on path
_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("benchmark_suite")

CONTAINER_NAMES = ["interync-lab-1", "interync-lab-2", "interync-lab-3"]


def run_all() -> None:
    """Run all scenarios across all containers and save results."""
    from dashboard.backend.container_manager import ContainerManager, ContainerError
    from dashboard.backend.benchmark_runner  import BenchmarkRunner

    try:
        mgr = ContainerManager()
    except ContainerError as exc:
        log.error("Cannot connect to LXD: %s", exc)
        log.error("Is lxd running?  Try: sudo lxd start")
        sys.exit(1)

    runner = BenchmarkRunner(mgr)

    # Ensure libs are built
    build_dir = _ROOT / "build"
    if not (build_dir / "libinterync-ipc.so").exists():
        log.error(
            "libinterync-ipc.so not found in build/.  "
            "Run 'make build-libs' first."
        )
        sys.exit(1)

    report: dict = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "note": (
            "WSL2 benchmark numbers are NOT representative of real hardware. "
            "Run on a dedicated Linux VM for final results."
        ),
        "latency":       [],
        "throughput":    [],
        "sync_overhead": [],
        "scenarios":     {},
    }

    scenarios = [
        ("producer_consumer.py",    "producer_consumer"),
        ("readers_writers.py",       "readers_writers"),
        ("dining_philosophers.py",   "dining_philosophers"),
        ("lock_contention.py",       "lock_contention"),
    ]

    for container in CONTAINER_NAMES:
        log.info("=== Container: %s ===", container)
        try:
            mgr.ensure_running(container)
        except ContainerError as exc:
            log.error("Cannot reach %s: %s — skipping", container, exc)
            continue

        for script, key in scenarios:
            log.info("  Running scenario: %s", script)
            try:
                result = runner.run_scenario(container, script)
                # Accumulate latency / throughput rows
                for row in result.get("latency", []):
                    row["container"] = container
                    report["latency"].append(row)
                for row in result.get("throughput", []):
                    row["container"] = container
                    report["throughput"].append(row)
                for row in result.get("sync_overhead", []):
                    row["container"] = container
                    report["sync_overhead"].append(row)

                report["scenarios"].setdefault(key, {})[container] = result
                log.info("    ✓ %s completed", key)
            except ContainerError as exc:
                log.error("    ✗ %s failed: %s", key, exc)
                report["scenarios"].setdefault(key, {})[container] = {
                    "error": str(exc)
                }

    runner.save_results(report)
    log.info("Results written to ./results/")
    _print_summary(report)


def _print_summary(report: dict) -> None:
    print("\n" + "=" * 60)
    print("  InterSync Benchmark Summary")
    print("=" * 60)
    for row in report.get("latency", []):
        print(f"  {row.get('mechanism','?'):12s}  "
              f"latency={row.get('latency_us',0):.2f} µs  "
              f"[{row.get('container','')}]")
    for row in report.get("throughput", []):
        print(f"  {row.get('mechanism','?'):12s}  "
              f"throughput={row.get('throughput_mbs',0):.2f} MB/s  "
              f"[{row.get('container','')}]")
    print("=" * 60)
    print(f"  Full report: {_ROOT / 'results' / 'report.json'}")
    print()


if __name__ == "__main__":
    run_all()
