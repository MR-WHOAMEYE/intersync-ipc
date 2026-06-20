# InterSync API Reference

## C IPC Library (`libinterync-ipc.so`)

Include: `#include "lib/ipc/libinterync_ipc.h"`
Link:    `-linterync-ipc -lrt -pthread`

---

### `ipc_create`
```c
ipc_channel_t* ipc_create(ipc_type_t type, const char* name);
```
Allocates and initialises a new IPC channel.

| Type | `name` requirement |
|------|--------------------|
| `IPC_PIPE` | Ignored (pass anything or NULL) |
| `IPC_QUEUE` | Must start with `/` (e.g. `/my-queue`) |
| `IPC_SOCKET` | Basename only; socket created at `/tmp/<name>.sock` |
| `IPC_SHM` | Must start with `/` (e.g. `/my-shm`) |

**Returns:** non-NULL on success; NULL on failure (check `errno`).

---

### `ipc_send`
```c
int ipc_send(ipc_channel_t* ch, const void* data, size_t len);
```
Sends `len` bytes. Blocks if the channel is full.

**Returns:** 0 on success; negative errno on failure.

---

### `ipc_receive`
```c
int ipc_receive(ipc_channel_t* ch, void* buffer, size_t len);
```
Reads up to `len` bytes. Blocks until data is available.

**Returns:** bytes actually read (≥ 0); negative errno on failure; 0 on EOF.

---

### `ipc_destroy`
```c
void ipc_destroy(ipc_channel_t* ch);
```
Closes all file descriptors, unlinks shared resources, and frees memory.
Safe to call with NULL.

---

## C Sync Library (`libinterync-sync.so`)

Include: `#include "lib/sync/libinterync_sync.h"`
Link:    `-linterync-sync -pthread`

---

### `sync_create`
```c
sync_lock_t* sync_create(sync_type_t type);
```
Creates a new synchronisation primitive.

| Type | Underlying implementation |
|------|--------------------------|
| `SYNC_MUTEX` | pthread mutex + `PTHREAD_PRIO_INHERIT` |
| `SYNC_SEMAPHORE` | POSIX unnamed semaphore, value=1 |
| `SYNC_CONDVAR` | pthread condvar + priority-inherit mutex |
| `SYNC_RWLOCK` | pthread rwlock, prefer-writer policy |

---

### `sync_lock` / `sync_lock_read`
```c
int sync_lock(sync_lock_t* lock);       // exclusive / write lock
int sync_lock_read(sync_lock_t* lock);  // shared / read lock (RWLOCK only)
```
Both return 0 on success; negative errno on failure.

---

### `sync_unlock`
```c
int sync_unlock(sync_lock_t* lock);
```

---

### Condition variable operations
```c
int sync_wait(sync_lock_t* lock);        // atomic unlock + wait
int sync_signal(sync_lock_t* lock);      // wake one waiter
int sync_broadcast(sync_lock_t* lock);   // wake all waiters
```
Only valid for `SYNC_CONDVAR`; returns `-EINVAL` for other types.

---

### `sync_destroy`
```c
void sync_destroy(sync_lock_t* lock);
```

---

### Lock trace log

Every `sync_lock()` / `sync_unlock()` call appends one line to:
```
/tmp/interync_lock_trace.log
```

Format:
```
<unix_ns>,<pid>,<lock_ptr_hex>,<ACQUIRE|RELEASE|WAIT>
```

Example:
```
1718000000123456789,12345,0x7f3a10b0,WAIT
1718000000124000000,12345,0x7f3a10b0,ACQUIRE
1718000000125000000,12345,0x7f3a10b0,RELEASE
```

---

## Python Backend API

### `ContainerManager`
```python
from dashboard.backend.container_manager import ContainerManager

mgr = ContainerManager()
mgr.ensure_running("interync-lab-1")
result = mgr.exec("interync-lab-1", ["ls", "/tmp"])
print(result.stdout, result.exit_code)
mgr.push_file("interync-lab-1", "build/libinterync-ipc.so",
              "/opt/interync/lib/libinterync-ipc.so")
data = mgr.pull_file("interync-lab-1", "/tmp/interync_lock_trace.log")
```

### `MetricsCollector`
```python
from dashboard.backend.metrics_collector import MetricsCollector

m = MetricsCollector(mgr)
snap = m.collect("interync-lab-1")
print(snap.cpu_percent, snap.mem_used_kb, snap.mem_total_kb)
```

### `DeadlockDetector`
```python
from dashboard.backend.deadlock_detector import DeadlockDetector

dd = DeadlockDetector(mgr)
result = dd.check("interync-lab-1")
if result.detected:
    print("Deadlock! Cycle:", result.cycle)

graph = dd.build_wait_for_graph("interync-lab-1")  # networkx DiGraph
```

### `BenchmarkRunner`
```python
from dashboard.backend.benchmark_runner import BenchmarkRunner

runner = BenchmarkRunner(mgr)
runner.deploy_libs("interync-lab-1")
result = runner.run_scenario("interync-lab-1",
                              "producer_consumer.py",
                              params={"num_messages": 5000})
runner.save_results(result)
```

---

## Scenario Contract

Each file in `benchmarks/scenarios/` must satisfy:

```python
def run(container_name: str = "local",
        params: dict | None = None) -> dict:
    """
    Returns a dict with keys:
      scenario      : str
      container     : str
      params        : dict
      latency       : list[{"mechanism": str, "latency_us": float}]
      throughput    : list[{"mechanism": str, "throughput_mbs": float}]
      sync_overhead : list[{"primitive": str, "acquire_us": float, "release_us": float}]
      summary       : dict (scenario-specific)
    """
```

The last line printed to stdout **must** be valid JSON of the returned dict.
