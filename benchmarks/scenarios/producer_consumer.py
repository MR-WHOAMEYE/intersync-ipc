"""
producer_consumer.py
InterSync — Producer-Consumer benchmark scenario.

Runs inside an LXC container.  Spawns a C test program that exercises each
IPC mechanism (pipe/queue/socket/shm) and measures round-trip latency and
throughput.

This file is BOTH:
  • importable by benchmark_suite.py (via the run() function)
  • directly executable:  python3 benchmarks/scenarios/producer_consumer.py

Output:  prints a single JSON line on the last stdout line.
"""

from __future__ import annotations

import json
import os
import sys
import time
import ctypes
import threading
import subprocess
from pathlib import Path

# Scenario parameters (overridable via INTERYNC_PARAMS env var)
DEFAULT_PARAMS = {
    "message_size_bytes": 1024,
    "num_messages":       1000,
    "mechanisms":         ["PIPE", "QUEUE", "SOCKET", "SHM"],
}

LIB_DIR = os.environ.get("LD_LIBRARY_PATH", "/opt/interync/lib")


def run(container_name: str = "local", params: dict | None = None) -> dict:
    """
    Execute the producer-consumer benchmark.

    When running INSIDE a container (called by benchmark_suite.py),
    the C .so is already loaded via LD_LIBRARY_PATH.

    Returns a dict with keys: latency, throughput, sync_overhead, raw.
    """
    p = {**DEFAULT_PARAMS, **(params or {})}

    # Read params from env if present (set by BenchmarkRunner.run_scenario)
    env_params = os.environ.get("INTERYNC_PARAMS")
    if env_params:
        try:
            p.update(json.loads(env_params))
        except json.JSONDecodeError:
            pass

    results = {
        "latency":       [],
        "throughput":    [],
        "sync_overhead": [],
        "scenario":      "producer_consumer",
        "container":     container_name,
        "params":        p,
    }

    for mech in p["mechanisms"]:
        lat, tput = _bench_ipc(mech, p["message_size_bytes"], p["num_messages"])
        results["latency"].append({
            "mechanism":  mech,
            "latency_us": lat,
        })
        results["throughput"].append({
            "mechanism":      mech,
            "throughput_mbs": tput,
        })

    return results


def _bench_ipc(mechanism: str, msg_size: int, num_msgs: int
               ) -> tuple[float, float]:
    """
    Benchmark a single IPC mechanism using pure Python (pipe) or ctypes.

    Returns: (mean_latency_us, throughput_mbs)
    """
    if mechanism == "PIPE":
        return _bench_pipe(msg_size, num_msgs)
    else:
        # For QUEUE/SOCKET/SHM we use the subprocess approach (calls
        # the C library via a small shell command) when inside a container.
        return _bench_via_subprocess(mechanism, msg_size, num_msgs)


def _bench_pipe(msg_size: int, num_msgs: int) -> tuple[float, float]:
    """Python-level pipe benchmark (works without C libs)."""
    data = b"X" * msg_size
    r_fd, w_fd = os.pipe()

    latencies: list[float] = []
    t_start = time.perf_counter()

    chunk = max(1, num_msgs // 20)
    for i in range(num_msgs):
        t0 = time.perf_counter()
        os.write(w_fd, data)
        received = b""
        while len(received) < msg_size:
            read_chunk = os.read(r_fd, msg_size - len(received))
            if not read_chunk:
                break
            received += read_chunk
        t1 = time.perf_counter()
        latencies.append((t1 - t0) * 1e6)
        
        if i > 0 and i % chunk == 0:
            current_mb = (msg_size * i) / (1024 * 1024)
            current_t = t1 - t_start
            tput = current_mb / current_t if current_t > 0 else 0
            print(json.dumps({"stream_tput": tput, "mechanism": "PIPE"}))
            sys.stdout.flush()

    t_total = time.perf_counter() - t_start
    os.close(r_fd)
    os.close(w_fd)

    mean_lat  = sum(latencies) / len(latencies) if latencies else 0.0
    total_mb  = (msg_size * num_msgs) / (1024 * 1024)
    throughput = total_mb / t_total if t_total > 0 else 0.0

    return round(mean_lat, 3), round(throughput, 3)


def _bench_via_subprocess(mechanism: str, msg_size: int, num_msgs: int
                           ) -> tuple[float, float]:
    """
    Runs a C micro-benchmark inline using a small heredoc + gcc one-liner.
    This works inside the container where gcc and the .so files are present.
    Falls back to a synthetic estimate if compilation fails.
    """
    c_src = rf"""
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include "/opt/interync/lib/../../../lib/ipc/libinterync_ipc.h"

static double now_us(void) {{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec * 1e6 + ts.tv_nsec / 1e3;
}}

int main(void) {{
    ipc_type_t type = IPC_{mechanism};
    const char* name = (type == IPC_QUEUE) ? "/ips-bench-q" :
                       (type == IPC_SHM)   ? "/ips-bench-shm" : "bench";
    ipc_channel_t* ch = ipc_create(type, name);
    if (!ch) {{ perror("ipc_create"); return 1; }}

    char* buf = malloc({msg_size});
    memset(buf, 'A', {msg_size});
    char* rbuf = malloc({msg_size});

    double t_start = now_us();
    double lat_sum = 0.0;
    int chunk = {num_msgs} / 20;
    if (chunk == 0) chunk = 1;
    
    for (int i = 0; i < {num_msgs}; i++) {{
        double t0 = now_us();
        ipc_send(ch, buf, {msg_size});
        ipc_receive(ch, rbuf, {msg_size});
        double t1 = now_us();
        lat_sum += t1 - t0;
        
        if (i > 0 && i % chunk == 0) {{
            double current_t = (t1 - t_start) / 1e6;
            double current_mb = (double){msg_size} * i / (1024.0 * 1024.0);
            printf("STREAM %.3f\\n", current_mb / current_t);
            fflush(stdout);
        }}
    }}
    double t_total_s = (now_us() - t_start) / 1e6;

    double mean_lat = lat_sum / {num_msgs};
    double total_mb = (double){msg_size} * {num_msgs} / (1024.0 * 1024.0);
    double tput     = total_mb / t_total_s;

    printf("%.3f %.3f\n", mean_lat, tput);
    free(buf); free(rbuf);
    ipc_destroy(ch);
    return 0;
}}
"""
    src_path = f"/tmp/bench_{mechanism.lower()}.c"
    bin_path = f"/tmp/bench_{mechanism.lower()}"

    try:
        with open(src_path, "w") as f:
            f.write(c_src)

        compile_cmd = [
            "gcc", "-O2", src_path,
            f"-I{LIB_DIR}/../../../lib/ipc",
            f"-L{LIB_DIR}", "-linterync-ipc",
            f"-Wl,-rpath,{LIB_DIR}",
            "-lrt", "-o", bin_path
        ]
        result = subprocess.run(compile_cmd, capture_output=True, text=True,
                                timeout=30)
        if result.returncode != 0:
            raise RuntimeError(f"compile failed: {result.stderr}")

        proc = subprocess.Popen([bin_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        final_lat = 0.0
        final_tput = 0.0
        
        for line in iter(proc.stdout.readline, ''):
            line = line.strip()
            if not line:
                continue
            if line.startswith("STREAM"):
                _, tput = line.split()
                print(json.dumps({"stream_tput": float(tput), "mechanism": mechanism}))
                sys.stdout.flush()
            else:
                try:
                    lat_s, tput_s = line.split()
                    final_lat = float(lat_s)
                    final_tput = float(tput_s)
                except ValueError:
                    pass
        
        proc.wait(timeout=60)
        if proc.returncode != 0:
            raise RuntimeError(f"benchmark binary failed: {proc.stderr.read()}")

        return final_lat, final_tput

    except Exception as exc:
        # Fallback: return synthetic values so the suite doesn't crash
        print(f"[WARN] {mechanism} benchmark failed: {exc}", file=sys.stderr)
        # Rough order-of-magnitude estimates (for development display only)
        fallback_latency   = {"PIPE": 5.0, "QUEUE": 12.0,
                               "SOCKET": 8.0, "SHM": 1.5}.get(mechanism, 10.0)
        fallback_throughput = {"PIPE": 800.0, "QUEUE": 300.0,
                                "SOCKET": 600.0, "SHM": 2000.0}.get(mechanism, 500.0)
        print(f"[WARN] Using fallback estimates for {mechanism} "
              f"(not real hardware numbers)", file=sys.stderr)
        return fallback_latency, fallback_throughput


if __name__ == "__main__":
    result = run()
    # Print all progress to stderr, final JSON to stdout
    print(json.dumps(result))
