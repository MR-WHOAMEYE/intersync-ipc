"""
dining_philosophers.py
InterSync — Dining Philosophers scenario.

Classic deadlock-demonstrating scenario.  N philosophers sit around a table,
each needing two forks (mutexes) to eat.  The naive implementation produces
a deadlock; this scenario demonstrates both the deadlock and its resolution
via a resource-ordering fix.

Runnable:    python3 benchmarks/scenarios/dining_philosophers.py
Importable:  from benchmarks.scenarios.dining_philosophers import run
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import random

DEFAULT_PARAMS = {
    "num_philosophers": 5,
    "think_ms":         20,
    "eat_ms":           15,
    "simulation_s":     5,
    "detect_deadlock":  True,   # use resource-ordering to avoid deadlock
}


class Fork:
    def __init__(self, index: int):
        self.index = index
        self._lock = threading.Lock()
        self.holder: int = -1

    def acquire(self, philosopher_id: int, timeout: float = 0.5) -> bool:
        acquired = self._lock.acquire(timeout=timeout)
        if acquired:
            self.holder = philosopher_id
        return acquired

    def release(self) -> None:
        self.holder = -1
        self._lock.release()


def run(container_name: str = "local", params: dict | None = None) -> dict:
    p = {**DEFAULT_PARAMS, **(params or {})}
    env_params = os.environ.get("INTERYNC_PARAMS")
    if env_params:
        try:
            p.update(json.loads(env_params))
        except json.JSONDecodeError:
            pass

    n = p["num_philosophers"]
    forks = [Fork(i) for i in range(n)]

    meals_eaten    = [0] * n
    starved_count  = [0] * n
    deadlock_events: list[str] = []
    stop_event     = threading.Event()

    def philosopher(pid: int) -> None:
        if p["detect_deadlock"]:
            # Resource ordering: always pick lower-index fork first
            left  = min(pid, (pid + 1) % n)
            right = max(pid, (pid + 1) % n)
        else:
            # Naive (deadlock-prone): pick left then right
            left  = pid
            right = (pid + 1) % n

        while not stop_event.is_set():
            # Think
            time.sleep(p["think_ms"] / 1000.0 * random.uniform(0.5, 1.5))

            # Pick up first fork
            if not forks[left].acquire(pid, timeout=0.5):
                starved_count[pid] += 1
                continue

            # Pick up second fork
            if not forks[right].acquire(pid, timeout=0.5):
                forks[left].release()
                starved_count[pid] += 1
                with threading.Lock():
                    deadlock_events.append(
                        f"t={time.time():.3f}: P{pid} timed out on fork {right}"
                    )
                continue

            # Eat
            meals_eaten[pid] += 1
            time.sleep(p["eat_ms"] / 1000.0)

            forks[right].release()
            forks[left].release()

    threads = [threading.Thread(target=philosopher, args=(i,), daemon=True)
               for i in range(n)]

    t_start = time.perf_counter()
    for t in threads:
        t.start()

    time.sleep(p["simulation_s"])
    stop_event.set()

    for t in threads:
        t.join(timeout=2.0)

    t_total = time.perf_counter() - t_start

    total_meals  = sum(meals_eaten)
    total_starve = sum(starved_count)
    fairness     = (min(meals_eaten) / max(meals_eaten)
                    if max(meals_eaten) > 0 else 0.0)

    result = {
        "scenario":      "dining_philosophers",
        "container":     container_name,
        "params":        p,
        "latency": [],
        "throughput": [],
        "sync_overhead": [
            {"primitive": "MUTEX",
             "acquire_us": round(p["eat_ms"] * 1000 / max(total_meals, 1), 3),
             "release_us": 0.0},
        ],
        "summary": {
            "num_philosophers":  n,
            "total_meals":       total_meals,
            "meals_per_phil":    meals_eaten,
            "total_starvations": total_starve,
            "fairness_ratio":    round(fairness, 4),
            "deadlock_events":   len(deadlock_events),
            "deadlock_sample":   deadlock_events[:5],
            "duration_s":        round(t_total, 4),
            "deadlock_avoidance": p["detect_deadlock"],
        },
    }
    return result


if __name__ == "__main__":
    result = run()
    summary = result["summary"]
    print(f"[dining_philosophers] {summary['num_philosophers']} philosophers, "
          f"{summary['total_meals']} meals, "
          f"fairness={summary['fairness_ratio']:.2f}, "
          f"deadlock_events={summary['deadlock_events']}",
          file=sys.stderr)
    print(json.dumps(result))
