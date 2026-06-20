# InterSync — Lock-Free SPSC Ring Buffer & MPMC Queue: Novelty Specification

> **Status:** Revised Specification (v2)
> **Target:** Lock-free SPSC ring buffer (Module 2) with a true CAS-based lock-free MPMC queue, benchmarked against the existing IPC/Sync libraries (Module 1).
> **Timeline:** Spec-driven implementation over multiple sessions.

---

## Table of Contents

1. [Concept & Motivation](#1-concept--motivation)
2. [Module 2: SPSC Ring Buffer](#2-module-2-spsc-ring-buffer)
3. [Module 2: True Lock-Free MPMC Queue](#3-module-2-true-lock-free-mpmc-queue)
4. [Module 3: Benchmark Comparison](#4-module-3-benchmark-comparison)
5. [Dashboard Visualisation](#5-dashboard-visualisation)
6. [io_uring Async Notification](#6-io_uring-async-notification)
7. [File Layout](#7-file-layout)
8. [API Reference](#8-api-reference)
9. [Implementation Roadmap](#9-implementation-roadmap)

---

## 1. Concept & Motivation

### 1.1 What Makes This Novel

The existing InterSync system (Module 1) implements **traditional kernel-mediated IPC mechanisms** (pipes, POSIX message queues, UNIX domain sockets, POSIX shared memory) and **blocking synchronisation primitives** (pthread mutex, semaphore, condvar, rwlock).

Module 2 introduces a **lock-free SPSC ring buffer and a true CAS-based lock-free MPMC queue** — a fundamentally different approach:

| Axis | Module 1 (Traditional) | Module 2 (Novel) |
|------|------------------------|-------------------|
| **Kernel involvement** | Syscalls for most operations (pipe/socket/queue) | **Pure userspace** — no syscalls on hot path |
| **Progress guarantee** | Blocking — can context-switch (mutex, pipe full) | **Lock-free** — system-wide progress guaranteed; producer is **wait-free** (bounded steps, no spinning) |
| **Data movement** | Copies data between kernel and user buffers | **Zero-copy** — consumer reads data directly from shared memory |
| **Memory model** | Kernel-managed buffer (pipe), kernel queue (mq) | **Cache-line optimised** — padding prevents false sharing; prefetch instructions on hot path |
| **Synchronisation** | OS scheduler + kernel locks | **Atomics + memory barriers** — CAS, load/store with ordering |
| **Scope** | Multi-producer, multi-consumer, cross-process | **In-process (threads) only** — within same address space for true lock-free guarantees |

### 1.2 Key Design Principles

- **Producer wait-free**: `push()` runs in a bounded number of steps regardless of consumer progress. Returns `-EAGAIN` if full — never spins or blocks.
- **Lock-free overall**: System-wide progress guaranteed. Consumer `pop()` uses CAS on slot state; may retry on contention but some thread always makes progress.
- **Cache-line awareness**: Read/write cursors in separate cache lines. Prefetch instructions (`PREFETCHT0`, `PREFETCHW`) on the hot path.
- **Zero-copy for fixed-size messages**: Consumer reads data directly from the ring buffer slot — no copy for messages ≤ `slot_size`.
- **In-process only**: Both SPSC and MPMC operate on memory shared within a single process/address space. Cross-process sync is not supported by Module 2.
- **Honest progress claims**: Producer is wait-free (bounded steps, no loops). Consumer is lock-free (may CAS-retry on contention). Full system is lock-free.

---

## 2. Module 2: SPSC Ring Buffer

### 2.1 Architecture

```
                  ┌──────────────────────────────────────┐
                  │         Ring Buffer (circular)        │
                  │                                      │
   Producer ──────►┌──┬──┬──┬──┬──┬──┬──┬──┬──┬──┬──┐─────► Consumer
        │         │  │  │  │  │  │  │  │  │  │  │  │     │
        │         │s0│s1│s2│s3│s4│s5│s6│...│sN│  │     │
        │         └──┴──┴──┴──┴──┴──┴──┴──┴──┴──┴──┘     │
        │              ▲           ▲                      │
        │              │           │                      │
   write_cursor   ────┘           │                      │
   (producer only)                │                      │
   ───── cache line ──            │                      │
                            read_cursor                  │
                            (consumer only)              │
                            ───── cache line ──          │
                  ┌──────────────────────────────┐
                  │  spsc_ring_buffer_t           │
                  │  ┌─────────────────────────┐  │
                  │  │ pad[64]                 │  │  ←─ avoids false-sharing
                  │  │ write_cursor (uint64_t)  │  │       between producer's
                  │  │ pad[64]                 │  │       write_cursor and
                  │  │ read_cursor (uint64_t)   │  │       consumer's read_cursor
                  │  │ pad[64]                 │  │
                  │  │ mask, slot_size, flags   │  │
                  │  │ slots[] (aligned)        │  │
                  │  └─────────────────────────┘  │
                  └──────────────────────────────┘
```

### 2.2 Slot State Machine (CAS-based Sentinel Design)

Each slot has a `state` field managed by **CAS** (not sequence counters):

```
          ┌──────────────────────────────────────────────┐
          │                                              │
     ┌────▼────┐   CAS(EMPTY→WRITING)   ┌───────────┐   │
     │  EMPTY  │ ───────────────────────►│  FILLED   │   │
     └─────────┘                        └─────┬─────┘   │
          ▲                                   │         │
          │    CAS(READING→EMPTY)             │         │
          └────────────────────────────────────┘         │
          │                                              │
          │                   CAS(FILLED→READING)        │
          │                   ┌───────────┐              │
          └───────────────────│  READING  │◄─────────────┘
                              └───────────┘
```

**Transitions:**
- `EMPTY → WRITING` → `WRITING → FILLED` (producer, two-step: claim then publish)
- `FILLED → READING` → `READING → EMPTY` (consumer, two-step: claim then release)

**Why CAS over sequence counters?**
- No wrapping/overflow concerns with monotonic sequences
- Simpler correctness reasoning: each state transition is one CAS
- Naturally extends to MPMC (multiple producers CAS-claim slots)

### 2.3 Data Structures

```c
#define SPSC_CACHE_LINE  64
#define SPSC_SLOT_DEFAULT 128

typedef enum __attribute__((packed)) {
    SPSC_SLOT_EMPTY   = 0,
    SPSC_SLOT_WRITING = 1,   // producer claimed, writing data
    SPSC_SLOT_FILLED  = 2,   // data ready for consumer
    SPSC_SLOT_READING = 3,   // consumer claimed, reading data
} spsc_slot_state_t;

typedef struct {
    spsc_slot_state_t  state;            // CAS-managed state
    uint8_t            pad[SPSC_CACHE_LINE - sizeof(spsc_slot_state_t)];
    uint8_t            data[SPSC_SLOT_DEFAULT];  // inline payload
    uint32_t           size;             // actual payload bytes
    uint32_t           flags;            // reserved
} spsc_slot_t;

typedef struct __attribute__((aligned(SPSC_CACHE_LINE * 3))) {
    // ── Cache line 0: producer-owned ──
    char               pad_w[SPSC_CACHE_LINE - sizeof(uint64_t)];
    uint64_t           write_cursor;     // incremented on each push (monotonic)

    // ── Cache line 1: consumer-owned ──
    char               pad_r[SPSC_CACHE_LINE - sizeof(uint64_t)];
    uint64_t           read_cursor;      // incremented on each pop (monotonic)

    // ── Cache line 2: shared (read-only after init) ──
    char               pad_s[SPSC_CACHE_LINE - 3 * sizeof(uint64_t)];
    uint64_t           capacity;         // number of slots (power of 2)
    uint64_t           mask;             // capacity - 1
    uint32_t           slot_size;        // fixed slot byte size
    bool               trace_enabled;    // opt-in trace logging
    char               name[48];         // debug label

    // Slots follow in aligned memory
    spsc_slot_t        slots[];          // flexible array member
} spsc_ring_buffer_t;
```

### 2.4 Core Operations

All atomic operations use **C11 `stdatomic.h` explicit memory ordering** for portability across x86, ARM, and RISC-V.

#### `spsc_push` — Non-blocking (returns `-EAGAIN` if full)

The producer **never reads the slot's state before CAS**. It simply CAS-attempts to claim the slot. This avoids TOCTOU races and keeps the wait-free property: if the CAS fails, return immediately — no retry.

```c
int spsc_push(spsc_ring_buffer_t* rb, const void* data, size_t size) {
    // 1. Compute slot index (relaxed: single-writer on write_cursor)
    uint64_t idx = atomic_load_explicit(&rb->write_cursor, memory_order_relaxed) & rb->mask;
    spsc_slot_t* slot = &rb->slots[idx];

    // 2. CAS-claim the slot (only producer does EMPTY→WRITING)
    spsc_slot_state_t expected = SPSC_SLOT_EMPTY;
    if (!atomic_compare_exchange_strong_explicit(
            &slot->state, &expected, SPSC_SLOT_WRITING,
            memory_order_acquire,   // success: acquire sees consumer's RELEASE(EMPTY)
            memory_order_relaxed)) { // failure: don't care
        return -EAGAIN;  // producer wait-free: bounded steps, no retry
    }

    // 3. Write data (nobody else touches this slot until we release it)
    memcpy(slot->data, data, size);
    atomic_store_explicit(&slot->size, size, memory_order_relaxed);

    // 4. Publish slot (release: consumer's load_acquire sees this write)
    atomic_store_explicit(&slot->state, SPSC_SLOT_FILLED, memory_order_release);

    // 5. Advance write cursor (relaxed: only this producer writes it)
    atomic_store_explicit(&rb->write_cursor, idx + 1, memory_order_relaxed);

    // 6. (opt) Enqueue trace event
    return 0;
}
```

**Wait-free:** Bounded instructions, single CAS, no retry. ✓

#### `spsc_push_blocking` — Blocking (spins with `PAUSE` until slot free)

```c
int spsc_push_blocking(spsc_ring_buffer_t* rb, const void* data, size_t size) {
    for (int retries = 0; ; retries++) {
        uint64_t idx = atomic_load_explicit(&rb->write_cursor, memory_order_relaxed) & rb->mask;
        spsc_slot_t* slot = &rb->slots[idx];

        spsc_slot_state_t expected = SPSC_SLOT_EMPTY;
        if (atomic_compare_exchange_strong_explicit(
                &slot->state, &expected, SPSC_SLOT_WRITING,
                memory_order_acquire, memory_order_relaxed)) {
            memcpy(slot->data, data, size);
            atomic_store_explicit(&slot->size, size, memory_order_relaxed);
            atomic_store_explicit(&slot->state, SPSC_SLOT_FILLED, memory_order_release);
            atomic_store_explicit(&rb->write_cursor, idx + 1, memory_order_relaxed);
            return 0;
        }
        if (retries > 1000) sched_yield(); else _mm_pause();
    }
}
```

#### `spsc_pop` — Non-blocking (returns `-EAGAIN` if empty)

```c
int spsc_pop(spsc_ring_buffer_t* rb, void* buffer, size_t* size) {
    uint64_t idx = atomic_load_explicit(&rb->read_cursor, memory_order_relaxed) & rb->mask;
    spsc_slot_t* slot = &rb->slots[idx];

    spsc_slot_state_t state = atomic_load_explicit(&slot->state, memory_order_acquire);
    if (state != SPSC_SLOT_FILLED) {
        return -EAGAIN;  // empty
    }

    // CAS-claim (in SPSC this always succeeds; in MPMC it's contested)
    spsc_slot_state_t expected = SPSC_SLOT_FILLED;
    if (!atomic_compare_exchange_strong_explicit(
            &slot->state, &expected, SPSC_SLOT_READING,
            memory_order_acquire, memory_order_relaxed)) {
        return -EAGAIN;
    }

    uint32_t sz = atomic_load_explicit(&slot->size, memory_order_relaxed);
    memcpy(buffer, slot->data, sz);
    *size = sz;

    atomic_store_explicit(&slot->state, SPSC_SLOT_EMPTY, memory_order_release);
    atomic_store_explicit(&rb->read_cursor, idx + 1, memory_order_relaxed);
    return (int)sz;
}
```

**Lock-free:** If CAS fails, another consumer progressed. System-wide progress guaranteed.

#### `spsc_pop_blocking`

Same as `spsc_pop` but loops with `_mm_pause()` / `sched_yield()` on `-EAGAIN`.

---

### 2.4a Overflow Pool Design

The overflow pool is a pre-allocated array of fixed-size buffers managed via CAS.

```c
#define SPSC_OVERFLOW_POOL_RATIO  4   // pool_size = capacity / 4

typedef struct {
    void*    buffer;            // aligned_alloc'd chunk
    size_t   buffer_capacity;   // max payload this entry can hold
    uint32_t pool_state;        // CAS: 0=FREE, 1=CLAIMED
} spsc_overflow_entry_t;

typedef struct {
    uint32_t               overflow_count;     // entries in pool
    spsc_overflow_entry_t* entries;            // pre-allocated at create()
    atomic_uint32_t        overflow_claim_idx; // monotonically increasing index
} spsc_overflow_pool_t;
```

**Producer path (overflow):**
1. `idx = atomic_fetch_add(&pool->overflow_claim_idx, 1) % pool->overflow_count`
2. CAS `pool->entries[idx].pool_state`: EXPECT FREE, SET CLAIMED
   - If CAS fails (another producer arrived first): increment idx again and retry (lock-free retry, not wait-free)
3. Copy data into `entries[idx].buffer`
4. Write `OVERFLOW_SENTINEL | idx` into the ring buffer slot
5. Consumer reads sentinel, copies from `pool->entries[idx].buffer`, CAS-marks pool slot FREE

**Crash semantics:** If a producer crashes after claiming a pool slot but before writing data, the pool slot remains CLAIMED until `spsc_destroy()`. Documented:
> "If a producer thread terminates abnormally while holding an overflow pool slot, that slot is leaked. The pool is reclaimed in full on `spsc_destroy()`. For normal operation this does not occur."

**Overflow pool exhaustion:** If all overflow slots are CLAIMED, `spsc_push()` returns `-EMSGSIZE`. Callers either:
- Refuse large messages (fails fast, clean)
- Or use `SPSC_FLAG_OVERFLOW_UNBOUNDED` (allocates on demand via `aligned_alloc`, freed by consumer) — slower but unbounded

### 2.5 Memory Scope

**In-process (threads) only.** The ring buffer is allocated via `aligned_alloc` in the creating thread's heap. Threads share the pointer via:

```c
spsc_ring_buffer_t* rb = spsc_create("my-ring", 1024, 128, 0);
// Pass `rb` to producer and consumer threads via pthread_create arg.
```

No `shm_open`, no `mmap`, no cross-process support. This avoids:
- The need for kernel-mediated synchronization (futex, file-based)
- Pointer validity issues in different address spaces
- Out-of-band handshake protocols for shared memory naming

### 2.6 Data Model

| Message Size | Path | Notes |
|-------------|------|-------|
| `size ≤ slot_size` | **Fixed (fast path)** | Stored inline in `slot.data`. Zero-copy for consumer. |
| `size > slot_size` | **Overflow (slow path)** | Stored in a **pre-allocated overflow pool** at init time. Each overflow slot is a fixed-size buffer. Consumer copies out and marks pool slot free. |

**Overflow pool design:**
```c
typedef struct {
    void*  buffer;           // pre-allocated via aligned_alloc
    size_t capacity;         // max payload for this pool entry
    bool   in_use;           // false = available; CAS-claimed by producer
} spsc_overflow_slot_t;

// Pool allocated at spsc_create time:
rb->overflow_pool = calloc(overflow_count, sizeof(spsc_overflow_slot_t));
for (i...overflow_count) rb->overflow_pool[i].buffer = aligned_alloc(64, max_overflow_size);
```

When an overflow message is pushed:
1. Producer CAS-claims an overflow slot from the pool
2. Copies data into it
3. Writes a special token (`SPSC_OVERFLOW_TOKEN`) into the ring buffer slot that points to the pool entry index
4. Consumer reads the token, copies out data from the pool slot, marks pool slot free

**If overflow pool is exhausted:** push returns `-EMSGSIZE`.

### 2.7 Dynamic Growth

`SPSC_FLAG_GROWABLE` enables dynamic resizing via **double-buffer migration**:

1. Allocate a new 2× capacity ring buffer
2. CAS the old buffer's `migrating` flag to signal migration
3. Consumer drains remaining FILLED slots from old buffer
4. Both producer and consumer swap to new buffer atomically (CAS on a migration pointer)
5. Old buffer is freed

**Rarely invoked.** Growth is an O(n) copy operation that briefly stalls producers.

### 2.8 Inline Assembly Optimisations

| Location | Instruction | Purpose |
|----------|-------------|---------|
| Push completion | `mfence` (or `xchg` as barrier) | Ensure data visible before state change |
| Pop claim | `cmpxchg` (CAS) | Atomic slot state transition |
| Cache prefetch (next slot) | `PREFETCHT0` (consumer) / `PREFETCHW` (producer) | Reduce cache miss latency |
| Spin-wait hint | `PAUSE` | Consumer polling loop hint for hyperthreading |
| Non-temporal store (overflow) | `MOVNTI` (optional) | Large data streaming — bypasses cache |

### 2.9 Trace Logging

**Design: in-memory ring buffer, not file I/O.**

```c
#define SPSC_TRACE_EVENTS 4096

typedef struct {
    uint64_t timestamp_ns;
    uint64_t cursor;
    uint8_t  event;    // PUSH=1, POP=2, OVERFLOW=3, MIGRATE=4
    uint32_t size;
    int      slot_idx;
} spsc_trace_event_t;

spsc_trace_event_t trace_ring[SPSC_TRACE_EVENTS];
atomic_uint64_t    trace_head;  // written by producer/consumer
```

- Push/pop operations append to the in-memory ring buffer (no syscall)
- A background thread or timer flushes to `/tmp/interync_spsc_trace.log` periodically
- **Disabled by default.** Enabled via `spsc_set_trace(rb, true)` for debugging sessions
- The dashboard polls the in-memory trace ring for visualization (no file parsing needed)

### 2.10 Summary: What SPSC Does and Does Not Claim

| Claim | True? |
|-------|-------|
| "Lock-free overall" | ✓ Yes — system-wide progress guaranteed |
| "Producer wait-free" | ✓ Yes — `push()` is bounded, no loops, no blocking |
| "Zero-copy for fixed msgs" | ✓ Yes — consumer reads inline data directly |
| "Pure userspace" | ✓ Yes — no syscalls on hot path |
| "Cache-line optimised" | ✓ Yes — padding, prefetch, alignment |
| "Works cross-process" | ✗ No — in-process threads only |
| "Consumer wait-free" | ⚠ No — consumer may CAS-retry or spin on empty |
| "No notification overhead" | ⚠ Consumer polls — notification via io_uring is optional |

---

## 3. Module 2: True Lock-Free MPMC Queue

### 3.1 Design: CAS-based Bounded MPMC

The MPMC queue uses a **shared circular array of slot descriptors**, where each slot has a state machine managed by CAS. Multiple producers CAS-compete to claim slots; multiple consumers CAS-compete to claim filled slots.

```
           ┌──── CAS ────┐
  Producers├─► Producer 1├──┐
           │             │  │   ┌──────────────────────────────────────┐
           ├─► Producer 2├──┼──►│  Slot Array                         │
           │             │  │   │                                      │
           └─────────────┘  │   │  ┌──┬──┬──┬──┬──┬──┬──┬──┬──┐     │
                            └──►│  │s0│s1│s2│s3│s4│s5│s6│...│sN│     │
                            ┌──►│  └──┴──┴──┴──┴──┴──┴──┴──┴──┘     │
           ┌──── CAS ────┐  │   └──────────────────────────────────────┘
  Consumers├─►Consumer 1├──┘
           │             │
           ├─►Consumer 2├─── CAS-claim FILLED slots
           └─────────────┘
```

Unlike the SPSC combiner approach (which the review correctly identifies as a bottleneck), this is a **true single-array lock-free MPMC queue**. All producers and consumers operate on the same slot array via CAS on slot state and shared enqueue/dequeue indices.

### 3.2 Slot State Machine (MPMC variant)

```
          ┌──────────────────────────────────────────────────────┐
          │                                                      │
          │   fetch_add(enqueue_idx) + CAS(EMPTY→WRITING)       │
          │      (multiple producers compete via fetch_add)      │
     ┌────▼────┐                  ┌───────────┐                 │
     │  EMPTY  │ ─────────────────►│  FILLED   │                 │
     └─────────┘    (data copied) └─────┬─────┘                 │
          ▲                             │                       │
          │   fetch_add(dequeue_idx) + CAS(FILLED→READING)      │
          │       (multiple consumers compete via fetch_add)     │
          │                             │                       │
          │                             ▼                       │
          │                      ┌───────────┐                  │
          └──────────────────────│  EMPTY     │◄─────────────────┘
            (CAS mark free)      └───────────┘
                                  (data copied out)
```

**Key difference from SPSC:**
- Both EMPTY→WRITING and FILLED→READING transitions are contested via `atomic_fetch_add` on enqueue/dequeue indices
- Multiple producers `fetch_add` to get unique slot indices, then CAS to claim
- **Hand-over-hand fairness:** When a producer finds its slot busy, it `fetch_add`s again to advance the global index. This helps other producers avoid the busy slot.

**Fairness property:**
> "The MPMC queue is **not strictly fair** — a fast producer could in theory overtake a slow one. However, the hand-over-hand advancement ensures that under realistic contention, no producer is permanently starved. For fair-enough scheduling with bounded waiting, this design is sufficient."

### 3.3 Data Structures

```c
#define MPMC_CACHE_LINE 64
#define MPMC_SLOT_DEFAULT 128

typedef enum __attribute__((packed)) {
    MPMC_EMPTY   = 0,
    MPMC_WRITING = 1,
    MPMC_FILLED  = 2,
    MPMC_READING = 3,
} mpmc_slot_state_t;

typedef struct {
    mpmc_slot_state_t  state;                // CAS-managed across ALL producers/consumers
    uint8_t            pad[MPMC_CACHE_LINE - sizeof(mpmc_slot_state_t)];
    uint8_t            data[MPMC_SLOT_DEFAULT];
    uint32_t           size;
    uint32_t           producer_id;          // which producer wrote this (debug)
} mpmc_slot_t;

typedef struct {
    // ── Cache line: producer contention point ──
    char               pad_enq[MPMC_CACHE_LINE - sizeof(uint64_t)];
    atomic_uint64_t    enqueue_idx;           // CAS-claimed by producers

    // ── Cache line: consumer contention point ──
    char               pad_deq[MPMC_CACHE_LINE - sizeof(uint64_t)];
    atomic_uint64_t    dequeue_idx;           // CAS-claimed by consumers

    // ── Cache line: shared read-only ──
    char               pad_s[MPMC_CACHE_LINE - 3 * sizeof(uint64_t)];
    uint64_t           capacity;              // power of 2
    uint64_t           mask;
    uint32_t           slot_size;
    char               name[48];

    mpmc_slot_t        slots[];               // flexible array
} mpmc_queue_t;
```

### 3.4 Core Operations

**Key MPMC design:** Producers use `atomic_fetch_add` on `enqueue_idx` to distribute across slots (each producer gets a unique index). Then they CAS-claim the slot. Consumers use the same pattern on `dequeue_idx`.

#### `mpmc_enqueue` — Non-blocking

```c
int mpmc_enqueue(mpmc_queue_t* q, const void* data, size_t size) {
    // 1. Atomically claim an index (all producers compete here)
    uint64_t idx = atomic_fetch_add_explicit(&q->enqueue_idx, 1, memory_order_relaxed);
    uint64_t slot_idx = idx & q->mask;
    mpmc_slot_t* slot = &q->slots[slot_idx];

    // 2. CAS-claim the slot. If full, retry up to capacity times before returning -EAGAIN.
    //    (Fetch-add ensures no two producers get the same idx, but they may get a slot
    //     that's still READING from a prior consumer. Retry is needed.)
    uint64_t attempts = q->capacity;  // at most one full wrap
    while (attempts--) {
        mpmc_slot_state_t expected = MPMC_EMPTY;
        if (atomic_compare_exchange_strong_explicit(
                &slot->state, &expected, MPMC_WRITING,
                memory_order_acquire, memory_order_relaxed)) {
            goto claimed;
        }
        // Slot busy — try the next one (hand-over-hand: advancement helps others)
        idx = atomic_fetch_add_explicit(&q->enqueue_idx, 1, memory_order_relaxed);
        slot_idx = idx & q->mask;
        slot = &q->slots[slot_idx];
    }
    return -EAGAIN;  // all slots checked — queue full

claimed:
    memcpy(slot->data, data, size);
    atomic_store_explicit(&slot->size, size, memory_order_relaxed);
    atomic_store_explicit(&slot->state, MPMC_FILLED, memory_order_release);
    return 0;
}
```

**Lock-free & fair (hand-over-hand):**
- `fetch_add` distributes producers across different slots (reduces contention on same slot)
- If the CAS fails (slot not EMPTY), the producer `fetch_add`s again to advance to the *next* slot
- This advancing helps other producers: advancing `enqueue_idx` past a busy slot means other producers won't pile up on it either
- Under high contention, all producers work together to advance the queue

#### `mpmc_enqueue_blocking`

Same as above but loops indefinitely (no `-EAGAIN` return) with `_mm_pause()` / `sched_yield()`.

#### `mpmc_dequeue` — Non-blocking

```c
int mpmc_dequeue(mpmc_queue_t* q, void* buffer, size_t* size) {
    uint64_t idx = atomic_fetch_add_explicit(&q->dequeue_idx, 1, memory_order_relaxed);
    uint64_t slot_idx = idx & q->mask;
    mpmc_slot_t* slot = &q->slots[slot_idx];

    uint64_t attempts = q->capacity;
    while (attempts--) {
        mpmc_slot_state_t state = atomic_load_explicit(&slot->state, memory_order_acquire);
        if (state != MPMC_FILLED) {
            // Not ready yet — advance dequeue_idx hand-over-hand
            idx = atomic_fetch_add_explicit(&q->dequeue_idx, 1, memory_order_relaxed);
            slot_idx = idx & q->mask;
            slot = &q->slots[slot_idx];
            continue;
        }

        mpmc_slot_state_t expected = MPMC_FILLED;
        if (atomic_compare_exchange_strong_explicit(
                &slot->state, &expected, MPMC_READING,
                memory_order_acquire, memory_order_relaxed)) {
            goto claimed;
        }
        // CAS failed (another consumer claimed it) — advance
        idx = atomic_fetch_add_explicit(&q->dequeue_idx, 1, memory_order_relaxed);
        slot_idx = idx & q->mask;
        slot = &q->slots[slot_idx];
    }
    return -EAGAIN;

claimed:
    uint32_t sz = atomic_load_explicit(&slot->size, memory_order_relaxed);
    memcpy(buffer, slot->data, sz);
    *size = sz;
    atomic_store_explicit(&slot->state, MPMC_EMPTY, memory_order_release);
    return (int)sz;
}
```

#### `mpmc_dequeue_blocking`

Same but loops indefinitely with `_mm_pause()` / `sched_yield()` on empty/CAS-fail.

### 3.5 Overflow Handling

MPMC uses the same pre-allocated overflow pool design as SPSC (§2.4a). The overflow pool is shared among all producers; each producer CAS-claims a pool slot via `atomic_fetch_add` on `overflow_claim_idx`.

### 3.6 MPMC vs SPSC Combiner: Why This Is Correct

The review correctly identifies the combiner approach as a bottleneck:

```
❌ OLD (Multi-SPSC Combiner)        ✓ NEW (True CAS-based MPMC)
   Producer1 ──► SPSC_A ──┐           Producer1 ──┐
   Producer2 ──► SPSC_B ──┼──►Comb.──►Consumer     Producer2 ──┼──►Slot Array ──► Consumer1
   Producer3 ──► SPSC_C ──┘                              Producer3 ──┘               │
   ⇢ Combiner is O(P) bottleneck                    ⇢ All producers CAS on           ├──► Consumer2
   ⇢ Extra copy: push→combiner→pop                      shared enqueue_idx           └──► Consumer3
   ⇢ Not truly lock-free                           ⇢ True lock-free progress
```

### 3.7 MPMC API

```c
// Lifecycle
mpmc_queue_t*    mpmc_create(const char* name, uint64_t capacity,
                              uint32_t slot_size, uint32_t flags);
void             mpmc_destroy(mpmc_queue_t* q);

// Non-blocking (returns -EAGAIN if full/empty)
int              mpmc_enqueue(mpmc_queue_t* q, const void* data, size_t size);
int              mpmc_dequeue(mpmc_queue_t* q, void* buffer, size_t* size);

// Blocking (spins with PAUSE + sched_yield until success)
int              mpmc_enqueue_blocking(mpmc_queue_t* q, const void* data, size_t size);
int              mpmc_dequeue_blocking(mpmc_queue_t* q, void* buffer, size_t* size);

// Batch operations (optimistic multi-slot CAS)
int              mpmc_enqueue_batch(mpmc_queue_t* q, const void** buffers,
                                    size_t* sizes, int count);
int              mpmc_dequeue_batch(mpmc_queue_t* q, void** buffers,
                                    size_t* sizes, int max_count);

// Utility
uint64_t         mpmc_capacity(mpmc_queue_t* q);
uint64_t         mpmc_available(mpmc_queue_t* q);  // approximate
void             mpmc_set_trace(mpmc_queue_t* q, bool enabled);
```

---

## 4. Module 3: Benchmark Comparison

### 4.1 Benchmark Comparison Matrix

Only directly comparable mechanisms are benchmarked side-by-side. Each row documents applicability, fairness, and key metrics.

| Benchmark | M2 Mechanism | M1 Comparison | Comparable? | Key Metrics |
|-----------|-------------|---------------|-------------|-------------|
| `spsc_latency` | SPSC (single msg) | — (M2 only) | N/A — characterisation | Push/pop latency (p50, p99, p99.9) |
| `spsc_throughput` | SPSC (saturated) | — (M2 only) | N/A — characterisation | Msg/s at capacity, cache misses |
| `spsc_contention` | SPSC (cache effects) | — (M2 only) | N/A — characterisation | Cache misses vs alignment strategy |
| `spsc_vs_shm` | SPSC | **SHM + semaphore** | **YES** — both SPSC, memory-based | Latency, throughput, CPU cycles/op |
| `spsc_vs_pipe` | SPSC | Pipe (1 writer, 1 reader) | ⚠ Context only — pipe is kernel-based, multi-consumer capable | Latency, throughput (annotated) |
| `mpmc_contention` | MPMC (N prod, M cons) | — (M2 only) | N/A — characterisation | Enq/deq latency, throughput at scale |
| `mpmc_vs_mutex` | MPMC | **Mutex + shared queue** | **YES** — both N-to-M | Tail latency under 1–16 threads, context switches |
| `mpmc_vs_queue` | MPMC | **POSIX message queue** | ⚠ Partial — msg queue is kernel-based | Throughput, latency (apples-to-oranges) |

**M1-only scenarios** (unchanged, not adapted for M2):
- `producer_consumer.py` — PIPE, QUEUE, SOCKET, SHM
- `readers_writers.py` — RWLOCK
- `dining_philosophers.py` — mutex deadlock demo
- `lock_contention.py` — MUTEX, SEMAPHORE, CONDVAR, RWLOCK

### 4.2 Key Metrics

| Metric | How Measured |
|--------|-------------|
| **Push latency** | `clock_gettime(CLOCK_MONOTONIC)` around `spsc_push()` / `mpmc_enqueue()` — 100k samples |
| **Pop latency** | Same around `spsc_pop()` / `mpmc_dequeue()` — 100k samples |
| **Tail latency** | P50, P99, P99.9 from all samples |
| **Throughput** | Messages per second at saturation (ring buffer at capacity) |
| **CPU cycles/op** | `clock_gettime(CLOCK_THREAD_CPUTIME_ID)` — user CPU time only |
| **Cache misses** | `perf stat -e cache-misses,cache-references` — best-effort, may require `perf_event_paranoid` adjustment |
| **Context switches** | `perf stat -e context-switches` — only meaningful for M1 (blocking) vs M2 (lock-free) |

### 4.3 Benchmark Runner Output

```json
{
  "generated_at": "2026-06-18",
  "comparison_version": 3,
  "comparability": {
    "fair": ["spsc_vs_shm", "mpmc_vs_mutex"],
    "context_only": ["spsc_vs_pipe", "mpmc_vs_queue"],
    "m2_characterisation": ["spsc_latency", "spsc_throughput", "spsc_contention", "mpmc_contention"]
  },
  "results": [ ... ]
}
```

---

## 5. Dashboard Visualisation

### 5.1 New Tab: SPSC Ring Buffer Visualizer

A new Qt tab (`spsc_visualizer.py`) with two panels:

```
┌──────────────────────────────────────────────────────────────┐
│  [⇄ IPC]  [⊗ Sync]  [🍽 Philo]  [◉ SPSC]  [⏱ Bench]  [≡ Log] │
├──────────────┬───────────────────────────────────────────────┤
│  CONTROL      │  VISUALIZATION                                │
│               │                                               │
│  • Mode:      │  ┌───────────────────────────────────────┐   │
│    SPSC/MPMC  │  │  Ring Buffer State                     │   │
│  • Slot Size  │  │  ┌──┬──┬──┬──┬──┬──┬──┬──┐            │   │
│  • Push/Test  │  │  │██│  │██│██│  │  │▓▓│  │            │   │
│  • Pop/Test   │  │  └──┴──┴──┴──┴──┴──┴──┴──┘            │   │
│  • Burst Mode │  │  write=5 ▸  ▸ read=2  cap=8           │   │
│  • Trace Log  │  └───────────────────────────────────────┘   │
│               │                                               │
│  ── MPMC ──   │  ┌───────────────────────────────────────┐   │
│  • Producers  │  │  Latency Histogram                     │   │
│  • Consumers  │  │  ████                                 │   │
│  • Run Test   │  │  ████████                             │   │
│               │  │  ████████████                         │   │
│               │  │  0  100  200  300  400  500 ns         │   │
│               │  │  avg=87ns  p50=65ns  p99=210ns        │   │
│               │  └───────────────────────────────────────┘   │
│               │  ┌───────────────────────────────────────┐   │
│               │  │  Throughput (msg/s)                    │   │
│               │  │  ░░░░░░░░░▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓░░░░░░░░  │   │
│               │  └───────────────────────────────────────┘   │
└──────────────┴───────────────────────────────────────────────┘
```

### 5.2 Ring Buffer State Panel (QPainter)

- Row of rectangular cells, one per slot
- Colors: EMPTY (dark/transparent), FILLED (teal/green gradient), WRITING (yellow border), READING (blue pulse)
- Animated cursor positions: write cursor pulses teal, read cursor pulses purple
- Overflow slots shown with a "⤴" symbol
- Capacity and fill % displayed at bottom

### 5.3 Latency Histogram (pyqtgraph)

- Live histogram of push/pop latencies
- Bins: 0–50 ns, 50–100 ns, 100–200 ns, 200–500 ns, 500+ ns
- Separate overlaid distributions for fixed (fast) vs overflow (slow) paths
- Summary stats: min, max, avg, p50, p99, p99.9
- Mode toggle: SPSC vs MPMC latency

### 5.4 MPMC Control Panel

- `num_producers` slider (1–16) — spawns producer threads
- `num_consumers` slider (1–16) — spawns consumer threads
- `burst_count` spinbox (100–100000)
- Action buttons: [START MPMC], [STOP], [RUN BENCH]
- Live stats: enqueue latency, dequeue latency, throughput (msg/s)

### 5.5 EventBus Integration

```python
spsc_pushed = pyqtSignal(dict)
# Keys: ring_name, slot_idx, size, overflow, latency_ns

spsc_popped = pyqtSignal(dict)
# Keys: ring_name, slot_idx, size, overflow, latency_ns

mpmc_enqueued = pyqtSignal(dict)
mpmc_dequeued = pyqtSignal(dict)

spsc_trace_event = pyqtSignal(dict)
# Keys: event_type (PUSH/POP/OVERFLOW), cursor, size, timestamp
```

No cache-line heatmap signal — removed from scope.

---

## 6. io_uring Async Notification

### 6.1 Motivation

The SPSC ring buffer is polling-based by default. For scenarios where the consumer should not busy-wait, `io_uring` provides an async notification mechanism without `epoll` overhead.

### 6.2 Integration

```c
typedef struct {
    spsc_ring_buffer_t* ring;
    struct io_uring*    uring;        // io_uring instance
    bool                registered;   // io_uring attached?
} spsc_async_t;

int spsc_async_init(spsc_async_t* ah, spsc_ring_buffer_t* rb);
// Registers the ring buffer with an io_uring instance.
// Producer: on push, submits an SQE completion event.
// Consumer: calls io_uring_wait_cqe() instead of busy-polling.

int spsc_push_async(spsc_async_t* ah, const void* data, size_t size);
// push() + io_uring SQE submission to notify consumer.

int spsc_wait_pop(spsc_async_t* ah, void* buffer, size_t* size,
                  struct timespec* timeout);
// io_uring_wait_cqe() + spsc_try_pop().
```

### 6.3 Polling Comparison

A dedicated benchmark compares consumer-side notification strategies:

| Strategy | Latency | CPU Usage | Notes |
|----------|---------|-----------|-------|
| Spin-wait (`while(!spsc_try_pop()) {}`) | Lowest | 100% one core | Max throughput |
| Pause-wait (`_mm_pause()` in loop) | +5–10 ns | ~70% | Best for dedicated consumer thread |
| **io_uring notify** | +200–500 ns | ~0% while waiting | Best for idle/scenario with sporadic messages |
| Eventfd + epoll | +500–1000 ns | ~0% while waiting | Traditional baseline |

---

## 7. File Layout

### 7.1 New Files

```
lib/spsc/
├── libinterync_spsc.h         # Public API header (SPSC + MPMC symbols)
├── spsc_ring_buffer.c         # Core SPSC ring buffer CAS-based implementation
├── spsc_inline_asm.h          # Inline assembly helpers (CAS, barriers, prefetch, PAUSE)
├── spsc_overflow.c            # Pre-allocated overflow pool management
├── spsс_grow.c                # Dynamic buffer growth (double-buffer migration)
├── spsc_trace.c               # In-memory trace ring buffer + async flush
├── spsc_io_uring.c            # io_uring async notification wrapper
├── mpmc_queue.c               # True CAS-based lock-free MPMC queue
└── test_spsc.c                # Unit tests and smoke tests

benchmarks/scenarios/
├── spsc_latency.py            # SPSC latency microbenchmark
├── spsc_throughput.py         # SPSC throughput saturation
├── spsc_contention.py         # Cache-line contention comparison
├── spsc_vs_shm.py             # SPSC vs SHM+semaphore (fair comparison)
├── spsc_vs_pipe.py            # SPSC vs pipe (context only — annotated)
├── mpmc_contention.py         # MPMC with N producers, M consumers
├── mpmc_vs_mutex.py           # MPMC vs mutex-guarded queue
├── polling_comparison.py      # Spin vs pause vs io_uring vs eventfd
└── m2_bench_suite.py          # Orchestrator for all M2 scenarios

dashboard/
├── ui/
│   ├── spsc_visualizer.py     # NEW: ring buffer state + latency histogram + throughput
│   └── dashboard_window.py    # MODIFIED: add SPSC tab
├── interactive/
│   ├── spsc_controls.py       # NEW: control panel for SPSC/MPMC tab
│   └── __init__.py            # MODIFIED
├── backend/
│   ├── interactive_backend.py # MODIFIED: add SPSC/MPMC operations
│   └── event_bus.py           # MODIFIED: add SPSC/MPMC signals

results/
└── comparison_v2.json         # Side-by-side M1 vs M2 report (fair comparisons only)
```

### 7.2 Modified Files

| File | Changes |
|------|---------|
| `Makefile` | Add `build-spsc` target, `deploy-spsc` target, `bench-spsc` target, `test-spsc` target |
| `dashboard/ui/dashboard_window.py` | Add SPSC tab to SplitView, register spsc_controls + spsc_visualizer |
| `dashboard/backend/event_bus.py` | Add `spsc_pushed`, `spsc_popped`, `mpmc_*`, `spsc_trace_event` signals |
| `dashboard/backend/interactive_backend.py` | Add `push_spsc()`, `pop_spsc()`, `run_mpmc()` methods |
| `requirements.txt` | No new dependencies |

---

## 8. API Reference

### 8.1 SPSC Library (`libinterync-spsc.so`)

**Include:** `#include "lib/spsc/libinterync_spsc.h"`
**Link:** `-linterync-spsc -pthread`

#### `spsc_create`

```c
spsc_ring_buffer_t* spsc_create(const char* name, uint64_t capacity,
                                uint32_t slot_size, uint32_t flags);
```

| Parameter | Description |
|-----------|-------------|
| `name` | Debug label (copied, used in trace log) |
| `capacity` | Number of slots (rounded up to nearest power of 2) |
| `slot_size` | Fixed slot size (default 128). Must be ≥ 8. |
| `flags` | Bitmask: `SPSC_FLAG_GROWABLE`, `SPSC_FLAG_OVERFLOW_POOL` |

**Returns:** non-NULL on success; NULL on failure (errno set).

#### `spsc_destroy`

```c
void spsc_destroy(spsc_ring_buffer_t* rb);
```

Frees all memory, including overflow pool. Safe with NULL.

#### `spsc_push` / `spsc_push_blocking` / `spsc_try_push`

```c
int spsc_push(spsc_ring_buffer_t* rb, const void* data, size_t size);           // non-blocking, -EAGAIN if full
int spsc_push_blocking(spsc_ring_buffer_t* rb, const void* data, size_t size);  // spins until success
int spsc_try_push(spsc_ring_buffer_t* rb, const void* data, size_t size);       // alias for spsc_push
```

**Returns:** 0 on success; `-EAGAIN` if full (producer wait-free); `-EMSGSIZE` if overflow pool exhausted.

#### `spsc_pop` / `spsc_pop_blocking` / `spsc_try_pop`

```c
int spsc_pop(spsc_ring_buffer_t* rb, void* buffer, size_t* size);           // non-blocking, -EAGAIN if empty
int spsc_pop_blocking(spsc_ring_buffer_t* rb, void* buffer, size_t* size);  // spins until success
int spsc_try_pop(spsc_ring_buffer_t* rb, void* buffer, size_t* size);       // alias for spsc_pop
```

**Returns:** bytes read (≥ 0) on success; `-EAGAIN` if empty.

#### `spsc_capacity` / `spsc_available`

```c
uint64_t spsc_capacity(spsc_ring_buffer_t* rb);
uint64_t spsc_available(spsc_ring_buffer_t* rb);  // approximate
```

#### `spsc_resize`

```c
int spsc_resize(spsc_ring_buffer_t* rb, uint64_t new_capacity);
```

Grow the ring buffer. Requires `SPSC_FLAG_GROWABLE`. Returns 0 on success.

#### `spsc_set_trace`

```c
void spsc_set_trace(spsc_ring_buffer_t* rb, bool enabled);
```

Enable/disable in-memory trace logging (default: off).

#### `spsc_async_init` / `spsc_push_async` / `spsc_wait_pop`

```c
int spsc_async_init(spsc_async_t* ah, spsc_ring_buffer_t* rb);
int spsc_push_async(spsc_async_t* ah, const void* data, size_t size);
int spsc_wait_pop(spsc_async_t* ah, void* buffer, size_t* size,
                  struct timespec* timeout);
void spsc_async_destroy(spsc_async_t* ah);
```

io_uring-backed async notification. See §6 for details.

### 8.2 MPMC Library (`libinterync-mpmc.so`)

**Include:** `#include "lib/spsc/libinterync_spsc.h"`
**Link:** `-linterync-mpmc -pthread`

#### `mpmc_create` / `mpmc_destroy`

```c
mpmc_queue_t* mpmc_create(const char* name, uint64_t capacity,
                           uint32_t slot_size, uint32_t flags);
void          mpmc_destroy(mpmc_queue_t* q);
```

#### `mpmc_enqueue` / `mpmc_enqueue_blocking` / `mpmc_dequeue` / `mpmc_dequeue_blocking`

```c
int mpmc_enqueue(mpmc_queue_t* q, const void* data, size_t size);
int mpmc_enqueue_blocking(mpmc_queue_t* q, const void* data, size_t size);
int mpmc_dequeue(mpmc_queue_t* q, void* buffer, size_t* size);
int mpmc_dequeue_blocking(mpmc_queue_t* q, void* buffer, size_t* size);
```

**Returns:** 0 on success; `-EAGAIN` if full/empty (non-blocking variants); negative errno on failure.

#### Batch operations

```c
int mpmc_enqueue_batch(mpmc_queue_t* q, const void** buffers,
                       size_t* sizes, int count);
int mpmc_dequeue_batch(mpmc_queue_t* q, void** buffers,
                       size_t* sizes, int max_count);
```

#### Utility

```c
uint64_t mpmc_capacity(mpmc_queue_t* q);
uint64_t mpmc_available(mpmc_queue_t* q);  // approximate
void     mpmc_set_trace(mpmc_queue_t* q, bool enabled);
```

---

## 9. Implementation Roadmap

### Phase 1 — SPSC Core (Session 1)
1. Create `libinterync_spsc.h` — data structures, SPSC public API
2. Implement `spsc_ring_buffer.c` — CAS-based state machine push/pop
3. Implement `spsc_inline_asm.h` — `cmpxchg`, `mfence`, prefetch, `PAUSE`
4. Implement slot state machine: EMPTY → WRITING → FILLED → READING → EMPTY
5. Create `test_spsc.c` — single-thread, multi-thread, stress tests
6. Add `build-spsc` target to `Makefile`
7. Compile and run tests

### Phase 2 — SPSC Advanced (Session 2)
1. Implement `spsc_overflow.c` — pre-allocated overflow pool
2. Implement `spsc_grow.c` — double-buffer migration
3. Implement `spsc_trace.c` — in-memory ring buffer + async flush
4. Implement `spsc_io_uring.c` — async notification
5. Full stress test with ThreadSanitizer

### Phase 3 — MPMC Queue (Session 3)
1. Implement `mpmc_queue.c` — shared enqueue_idx/dequeue_idx with CAS
2. MPMC slot state machine (same as SPSC but contested)
3. Implement batch operations (optimistic multi-slot CAS)
4. Add integration tests: N producers × M consumers
5. Add `build-mpmc` target to `Makefile`
6. Stress test with 16 producers, 16 consumers

### Phase 4 — Dashboard Integration (Session 4)
1. Create `spsc_visualizer.py` — ring buffer state + latency histogram + throughput
2. Create `spsc_controls.py` — SPSC push/pop controls
3. Wire SPSC control panel → InteractiveBackend → LXD → canvas
4. Wire MPMC control panel → InteractiveBackend → LXD → canvas
5. Modify `event_bus.py` — add SPSC/MPMC signals
6. Modify `dashboard_window.py` — add SPSC tab

### Phase 5 — Benchmarks (Session 5)
1. Create `spsc_latency.py`, `spsc_throughput.py`, `spsc_contention.py`
2. Create `spsc_vs_shm.py`, `spsc_vs_pipe.py` (fair comparison benchmarks)
3. Create `mpmc_contention.py`, `mpmc_vs_mutex.py`
4. Create `polling_comparison.py` (spin vs pause vs io_uring vs eventfd)
5. Create `m2_bench_suite.py` — orchestrator
6. Run all benchmarks, collect `comparison_v2.json`

### Phase 6 — Polish & Documentation (Session 6)
1. Performance optimization — prefetch tuning, CAS retry threshold
2. io_uring integration testing
3. Documentation: API.md update, ARCHITECTURE.md update
4. Scenario JSON files for SPSC-specific guided scenarios
5. Final testing

---

## Appendix A: C11 Atomics & Memory Ordering Reference

### A.1 Per-Operation Memory Orders

| Operation | Function | Order (success) | Order (failure) | Rationale |
|-----------|----------|-----------------|-----------------|-----------|
| Producer claims slot (SPSC) | `CAS(EMPTY→WRITING)` | `memory_order_acquire` | `memory_order_relaxed` | Acquire pairs with consumer's release(EMPTY) |
| Producer claims slot (MPMC) | `CAS(EMPTY→WRITING)` | `memory_order_acquire` | `memory_order_relaxed` | Same; contested across producers |
| Producer writes data | `store(size)` | `memory_order_relaxed` | — | Single-writer, no ordering needed |
| Producer publishes slot | `store(FILLED)` | `memory_order_release` | — | Paired with consumer's acquire load |
| Producer advance cursor | `store(write_cursor)` | `memory_order_relaxed` | — | Single-writer to this cache line |
| Consumer peek state | `load(state)` | `memory_order_acquire` | — | Must see producer's release(FILLED) |
| Consumer claims slot | `CAS(FILLED→READING)` | `memory_order_acquire` | `memory_order_relaxed` | Acquire pairs with producer's release(FILLED) |
| Consumer releases slot | `store(EMPTY)` | `memory_order_release` | — | Paired with producer's acquire CAS |
| Consumer advance cursor | `store(read_cursor)` | `memory_order_relaxed` | — | Single-writer to this cache line |
| MPMC enqueue_idx | `fetch_add(enqueue_idx)` | `memory_order_relaxed` | — | Only used for distribution; ordering from slot CAS |
| MPMC dequeue_idx | `fetch_add(dequeue_idx)` | `memory_order_relaxed` | — | Same rationale |

### A.2 ARM / RISC-V Considerations

On ARM, `memory_order_acquire` maps to `ldar` (load-acquire) and `memory_order_release` maps to `stlr` (store-release). On RISC-V, acquire maps to `fence r, rw` and release maps to `fence rw, w`. The `_explicit` C11 variants generate correct assembly for all architectures.

**No `memory_order_seq_cst` is used anywhere.** Sequential consistency is unnecessarily expensive on ARM (generates `dmb ish` full barriers) and provides no correctness benefit for the slot state machine.

### A.3 Cache-Line Isolation

```
┌─ Cache line 0 (producer) ──────────────────────────────────┐
│ char pad[64 - 8];  uint64_t write_cursor;                 │
│ ── Only the single producer writes to this cache line ──   │
└─────────────────────────────────────────────────────────────┘
┌─ Cache line 1 (consumer) ──────────────────────────────────┐
│ char pad[64 - 8];  uint64_t read_cursor;                  │
│ ── Only the single consumer writes to this cache line ──   │
└─────────────────────────────────────────────────────────────┘
┌─ Cache line 2 (shared read-only) ──────────────────────────┐
│ uint64_t capacity, mask;  uint32_t slot_size, flags;       │
│ ── Set once at init, never written again ──                │
└─────────────────────────────────────────────────────────────┘
┌─ Each slot occupies one cache line ────────────────────────┐
│ spsc_slot_state_t state;  uint8_t pad[63];                │
│ ── State + data share a line — intentional:               │
│    Producer writes state(EMPTY→WRITING) → invalidates line │
│    Consumer's acquire load fetches the entire line         │
│    ⇒ data is in cache for the consumer immediately ──      │
└─────────────────────────────────────────────────────────────┘
```

---

## Appendix B: Changes from v2 (v3 Fixes)

| Issue | v2 Issue | v3 Fix |
|-------|----------|--------|
| #1 MPMC enqueue_idx | Underspecified — only `load` shown | Uses `atomic_fetch_add` for distribution + hand-over-hand advancement |
| #2 Memory ordering | Hand-wavy "acquire/release" | Precise C11 `memory_order_*` in every pseudocode example |
| #3 Redundant state check | Check state before CAS on same slot | Producer: just CAS directly (no pre-check). MPMC: retry loop with fetch_add |
| #4 CAC fairness | Not discussed | Hand-over-hand CAS advancement documented; fairness bound stated |
| #5 Overflow pool crash | Pool exhausted → -EMSGSIZE, no crash story | Bounded pool (capacity/4), crash-leak documented, UNBOUNDED flag for safety |
| #6 Blocking variants | Missing | `*_blocking()` variants for all push/pop/enqueue/dequeue |
| #7 Benchmark matrix | Vague applicability table | Full matrix with "fair" / "context-only" / "characterisation" categories |
| #8 Overflow details | `bool in_use` field (not CAS-safe) | `uint32_t pool_state` managed via CAS |
