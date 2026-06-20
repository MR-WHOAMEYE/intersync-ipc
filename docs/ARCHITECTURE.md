# InterSync Architecture

## Overview

InterSync is a capstone project demonstrating **four IPC mechanisms** and **four synchronisation primitives** running inside isolated LXC containers, visualised by a PyQt6 desktop dashboard.

```
┌─────────────────────────────────────────────────────────────┐
│                   Host (WSL2 / Linux VM)                     │
│                                                              │
│  ┌──────────────────────┐  ┌──────────────────────────────┐ │
│  │  PyQt6 Dashboard      │  │  Python Benchmark Suite       │ │
│  │  dashboard/main.py    │  │  benchmarks/benchmark_suite.py│ │
│  │                       │  │                              │ │
│  │  ┌─────────────────┐  │  │  ┌──────────────────────┐   │ │
│  │  │ ContainerManager│◄─┼──┼──│  BenchmarkRunner      │   │ │
│  │  │ (pylxd only)    │  │  │  └──────────────────────┘   │ │
│  │  └────────┬────────┘  │  └──────────────┬───────────────┘ │
│  └───────────┼───────────┘                 │               │
│              │  LXD REST API               │               │
└──────────────┼─────────────────────────────┼───────────────┘
               │                             │
        ┌──────▼─────────────────────────────▼──────┐
        │          LXC Containers (Ubuntu 22.04)      │
        │                                             │
        │  interync-lab-1   interync-lab-2   -lab-3   │
        │  ┌─────────────┐                            │
        │  │ C Test       │  IPC mechanisms:           │
        │  │ Harness      │   pipe / queue /           │
        │  │ + .so libs   │   socket / shm             │
        │  └─────────────┘                            │
        │  /tmp/interync_lock_trace.log  ←── C libs   │
        └─────────────────────────────────────────────┘
```

## Module Breakdown

### Module 1 — IPC Library (`lib/ipc/`)

| File | Mechanism | Key System Calls |
|------|-----------|-----------------|
| `pipe_channel.c` | Anonymous pipe | `pipe()`, `read()`, `write()` |
| `queue_channel.c` | POSIX message queue | `mq_open()`, `mq_send()`, `mq_receive()` |
| `socket_channel.c` | UNIX domain socket | `socket()`, `bind()`, `connect()`, `accept()` |
| `shm_channel.c` | Shared memory + semaphore | `shm_open()`, `mmap()`, `sem_open()` |

All four expose the **same four-function API**:
```c
ipc_channel_t* ipc_create(ipc_type_t type, const char* name);
int  ipc_send(ipc_channel_t* ch, const void* data, size_t len);
int  ipc_receive(ipc_channel_t* ch, void* buffer, size_t len);
void ipc_destroy(ipc_channel_t* ch);
```

### Module 2 — Sync Library (`lib/sync/`)

| File | Primitive | Notes |
|------|-----------|-------|
| `mutex_lock.c` | pthread mutex | `PTHREAD_PRIO_INHERIT`, `PTHREAD_MUTEX_ERRORCHECK` |
| `semaphore_lock.c` | POSIX unnamed semaphore | `sem_init`, binary (value=1) |
| `condvar_lock.c` | Condition variable | Paired mutex + `pthread_cond_t`, `CLOCK_MONOTONIC` |
| `rwlock.c` | Read-write lock | `PTHREAD_RWLOCK_PREFER_WRITER_NONRECURSIVE_NP` |

**Lock trace log** at `/tmp/interync_lock_trace.log`:
```
<timestamp_ns>,<pid>,<lock_ptr_hex>,<ACQUIRE|RELEASE|WAIT>
```

### Module 3 — Benchmarks (`benchmarks/`)

```
benchmarks/
├── benchmark_suite.py          ← orchestrator (pylxd → containers)
└── scenarios/
    ├── producer_consumer.py    ← IPC latency + throughput
    ├── readers_writers.py      ← rwlock contention + fairness
    ├── dining_philosophers.py  ← deadlock demo + avoidance
    └── lock_contention.py      ← all 4 primitives under N-thread contention
```

Each scenario:
- Has `run(container_name, params) -> dict`
- Is standalone-runnable (`python3 scenarios/foo.py`)
- Prints JSON as last stdout line (consumed by the runner)

### Module 4 — Dashboard (`dashboard/`)

```
dashboard/
├── main.py                     ← entry point
├── ui/
│   ├── theme.py                ← QSS + colour constants
│   ├── dashboard_window.py     ← 5-tab main window
│   ├── ipc_visualizer.py       ← animated QPainter IPC flow
│   ├── sync_visualizer.py      ← lock state + wait-for graph
│   └── charts.py               ← matplotlib bar/line charts
└── backend/
    ├── container_manager.py    ← ONLY pylxd importer
    ├── metrics_collector.py    ← /proc/stat + /proc/meminfo reader
    ├── benchmark_runner.py     ← lib deployment + scenario execution
    └── deadlock_detector.py    ← trace log → wait-for graph → cycle detection
```

## Data Flow

```
C lib (container)
  └─> /tmp/interync_lock_trace.log
        └─> deadlock_detector.py (pulls via lxd exec + cat)
              └─> networkx DiGraph
                    └─> sync_visualizer.py (renders wait-for graph)

benchmarks/scenarios/*.py (inside container)
  └─> JSON stdout
        └─> BenchmarkRunner.run_scenario()
              └─> results/report.json
                    └─> ChartsWidget (matplotlib)
```

## ⚠ WSL2 Note

Benchmark numbers collected on WSL2 are **not representative of real hardware**.
WSL2 virtualises the Linux kernel and shares host CPU scheduling.

**Final benchmark results MUST be collected on a bare-metal Linux VM.**
Use `make vm-ready` to print the deployment checklist.

## Build System

Single entry point: `make` (see `make help` for all targets).

Key additions over the original Makefile skeleton:
- `build-libs`: compiles each `.c` to its own `.o` (avoids ODR violations)
- `test-libs`: compiles + runs `lib/ipc/test_ipc_sync.c` smoke test
- Shared libraries: `build/libinterync-ipc.so`, `build/libinterync-sync.so`
