"""
lock_contention.py
InterSync — Lock Contention benchmark scenario.

Measures how all four sync primitives perform under high contention
with N threads all competing for the same lock.

Runnable:   python3 benchmarks/scenarios/lock_contention.py
Importable: from benchmarks.scenarios.lock_contention import run
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time

DEFAULT_PARAMS = {
    "num_threads":      16,
    "iterations_each":  500,
    "primitives":       ["MUTEX", "SEMAPHORE", "CONDVAR", "RWLOCK"],
}


def _bench_primitive(primitive: str,
                     num_threads: int,
                     iterations: int) -> tuple[float, float]:
    """
    Run a contention benchmark for a single primitive using Python
    threading equivalents.

    Returns: (mean_acquire_us, mean_release_us)
    """
    acquire_times: list[float] = []
    release_times: list[float] = []
    t_lock = threading.Lock()   # guard the result lists

    if primitive in ("MUTEX", "CONDVAR"):
        shared_lock = threading.Lock()
    elif primitive == "SEMAPHORE":
        import threading
        shared_lock = threading.Semaphore(1)
    elif primitive == "RWLOCK":
        shared_lock = threading.RLock()
    else:
        shared_lock = threading.Lock()

    counter = [0]   # shared resource

    def worker() -> None:
        for _ in range(iterations):
            t0 = time.perf_counter()
            shared_lock.acquire()
            acq_us = (time.perf_counter() - t0) * 1e6

            counter[0] += 1   # critical section

            t1 = time.perf_counter()
            shared_lock.release()
            rel_us = (time.perf_counter() - t1) * 1e6

            with t_lock:
                acquire_times.append(acq_us)
                release_times.append(rel_us)

    threads = [threading.Thread(target=worker) for _ in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    mean_acq = sum(acquire_times) / len(acquire_times) if acquire_times else 0.0
    mean_rel = sum(release_times) / len(release_times) if release_times else 0.0
    return round(mean_acq, 3), round(mean_rel, 3)


def run(container_name: str = "local", params: dict | None = None) -> dict:
    p = {**DEFAULT_PARAMS, **(params or {})}
    env_params = os.environ.get("INTERYNC_PARAMS")
    if env_params:
        try:
            p.update(json.loads(env_params))
        except json.JSONDecodeError:
            pass

    sync_overhead: list[dict] = []
    for prim in p["primitives"]:
        acq_us, rel_us = _bench_primitive(
            prim, p["num_threads"], p["iterations_each"]
        )
        sync_overhead.append({
            "primitive":  prim,
            "acquire_us": acq_us,
            "release_us": rel_us,
        })
        print(f"  {prim:12s}  acq={acq_us:.3f} µs  rel={rel_us:.3f} µs",
              file=sys.stderr)

    result = {
        "scenario":      "lock_contention",
        "container":     container_name,
        "params":        p,
        "latency":       [],
        "throughput":    [],
        "sync_overhead": sync_overhead,
        "summary": {
            "num_threads":      p["num_threads"],
            "iterations_each":  p["iterations_each"],
        },
    }
    return result


if __name__ == "__main__":
    result = run()
    print(json.dumps(result))
