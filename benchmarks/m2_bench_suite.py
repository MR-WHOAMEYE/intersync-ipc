import subprocess
import json
import os
import sys
import datetime

BENCH_SPSC_BIN = os.path.join(os.path.dirname(__file__), "..", "build", "bench_spsc")
RESULTS_DIR = os.path.join(os.path.dirname(__file__), "..", "results")

def run_spsc(count, capacity):
    print(f"Running SPSC (count={count}, cap={capacity})...")
    res = subprocess.run([BENCH_SPSC_BIN, "spsc", str(count), str(capacity)], capture_output=True, text=True)
    if res.returncode != 0:
        print(f"Error running bench_spsc: {res.stderr}")
        return None
    return json.loads(res.stdout)

def run_mpmc(count, capacity, p, c):
    print(f"Running MPMC (count={count}, cap={capacity}, p={p}, c={c})...")
    res = subprocess.run([BENCH_SPSC_BIN, "mpmc", str(count), str(capacity), str(p), str(c)], capture_output=True, text=True)
    if res.returncode != 0:
        print(f"Error running bench_spsc: {res.stderr}")
        return None
    return json.loads(res.stdout)

def main():
    if not os.path.exists(BENCH_SPSC_BIN):
        print(f"Error: {BENCH_SPSC_BIN} not found. Please run 'make bench-spsc'.")
        sys.exit(1)
        
    os.makedirs(RESULTS_DIR, exist_ok=True)
    
    results = []
    
    # M2 Characterisation Benchmarks
    results.append(run_spsc(count=1000000, capacity=1024))
    results.append(run_spsc(count=5000000, capacity=65536))
    
    # MPMC Contention
    results.append(run_mpmc(count=1000000, capacity=1024, p=2, c=2))
    results.append(run_mpmc(count=1000000, capacity=1024, p=4, c=4))
    results.append(run_mpmc(count=1000000, capacity=1024, p=8, c=8))
    
    # We would normally also run the Module 1 Python scripts here using subprocess
    # and gather their latency/throughput to compare SPSC vs SHM and MPMC vs Mutex
    # For now, we will aggregate the true C performance of our lock-free queues.
    
    report = {
        "generated_at": datetime.datetime.now().isoformat(),
        "comparison_version": 3,
        "comparability": {
            "fair": ["spsc_vs_shm", "mpmc_vs_mutex"],
            "context_only": ["spsc_vs_pipe", "mpmc_vs_queue"],
            "m2_characterisation": ["spsc_latency", "spsc_throughput", "spsc_contention", "mpmc_contention"]
        },
        "results": results
    }
    
    report_path = os.path.join(RESULTS_DIR, "comparison_v2.json")
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)
        
    print(f"Benchmark suite complete. Results saved to {report_path}")

if __name__ == "__main__":
    main()
