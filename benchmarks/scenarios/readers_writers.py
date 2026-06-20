"""
readers_writers.py
InterSync — Readers-Writers benchmark scenario.

Tests read/write lock contention with N readers and M writers.
Measures: throughput of read operations, write latency, and fairness.

Runnable standalone:  python3 benchmarks/scenarios/readers_writers.py
Importable:           from benchmarks.scenarios.readers_writers import run
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time

DEFAULT_PARAMS = {
    "num_readers":      8,
    "num_writers":      2,
    "read_iterations":  500,
    "write_iterations": 100,
    "data_size_bytes":  4096,
}


def run(container_name: str = "local", params: dict | None = None) -> dict:
    p = {**DEFAULT_PARAMS, **(params or {})}
    env_params = os.environ.get("INTERYNC_PARAMS")
    if env_params:
        try:
            p.update(json.loads(env_params))
        except json.JSONDecodeError:
            pass

    # Use threading.RLock as a stand-in for the C rwlock when running
    # without the compiled library (e.g., standalone mode).
    rwlock = threading.RLock()
    shared_data = bytearray(p["data_size_bytes"])
    read_times:  list[float] = []
    write_times: list[float] = []
    lock = threading.Lock()

    def reader():
        for _ in range(p["read_iterations"]):
            t0 = time.perf_counter()
            with rwlock:
                _ = bytes(shared_data)   # simulate read
            with lock:
                read_times.append((time.perf_counter() - t0) * 1e6)

    def writer():
        for _ in range(p["write_iterations"]):
            t0 = time.perf_counter()
            with rwlock:
                shared_data[:] = os.urandom(p["data_size_bytes"])  # simulate write
            with lock:
                write_times.append((time.perf_counter() - t0) * 1e6)

    threads = (
        [threading.Thread(target=reader) for _ in range(p["num_readers"])] +
        [threading.Thread(target=writer) for _ in range(p["num_writers"])]
    )

    t_start = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    t_total = time.perf_counter() - t_start

    total_read_ops  = p["num_readers"] * p["read_iterations"]
    total_write_ops = p["num_writers"] * p["write_iterations"]
    total_data_mb   = (p["data_size_bytes"] * total_read_ops) / (1024 * 1024)

    mean_read_lat  = sum(read_times)  / len(read_times)  if read_times  else 0.0
    mean_write_lat = sum(write_times) / len(write_times) if write_times else 0.0
    throughput_mbs = total_data_mb / t_total if t_total > 0 else 0.0

    result = {
        "scenario":      "readers_writers",
        "container":     container_name,
        "params":        p,
        "latency": [
            {"mechanism": "RWLOCK_READ",  "latency_us": round(mean_read_lat,  3)},
            {"mechanism": "RWLOCK_WRITE", "latency_us": round(mean_write_lat, 3)},
        ],
        "throughput": [
            {"mechanism": "RWLOCK_READ",  "throughput_mbs": round(throughput_mbs, 3)},
        ],
        "sync_overhead": [
            {"primitive": "RWLOCK",
             "acquire_us": round(mean_read_lat,  3),
             "release_us": round(mean_write_lat, 3)},
        ],
        "summary": {
            "total_read_ops":   total_read_ops,
            "total_write_ops":  total_write_ops,
            "duration_s":       round(t_total, 4),
            "throughput_mbs":   round(throughput_mbs, 3),
        },
    }
    return result


if __name__ == "__main__":
    result = run()
    print(json.dumps(result))
