/*
 * libinterync_spsc.h
 * InterSync — Lock-Free SPSC Ring Buffer & MPMC Queue (Module 2)
 *
 * Public API header.  Include this in consumer code:
 *   #include "lib/spsc/libinterync_spsc.h"
 * Link with:
 *   -linterync-spsc -pthread
 *
 * Design notes
 * ============
 * SPSC (Single-Producer Single-Consumer):
 *   - Producer is WAIT-FREE: spsc_push() runs in bounded steps, no loops.
 *   - Consumer is LOCK-FREE: spsc_pop() may CAS-retry on an empty slot but
 *     system-wide progress is guaranteed.
 *   - In-process threads only (no cross-process / shared-memory support).
 *
 * MPMC (Multi-Producer Multi-Consumer):
 *   - Uses Dmitry Vyukov's sequence-number algorithm (NOT fetch_add-in-retry).
 *   - Each slot carries a monotone sequence counter.  Producers and consumers
 *     check the counter to determine slot ownership — no global index racing.
 *   - Lock-free: at least one thread always makes progress.
 *
 * Memory model
 * ============
 * All atomics use C11 <stdatomic.h> explicit orderings.  No seq_cst is used.
 * See Appendix A of docs/spsc-mpmc-spec.md for the per-operation table.
 *
 * Portability: x86-64, ARM64, RISC-V (via C11 acquire/release mappings).
 */

#pragma once
#ifndef LIBINTERYNC_SPSC_H
#define LIBINTERYNC_SPSC_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <errno.h>
#include <time.h>

/* =========================================================================
 * Compile-time constants
 * ========================================================================= */

#define SPSC_CACHE_LINE     64      /* bytes per cache line                  */
#define SPSC_SLOT_DEFAULT   128     /* default inline payload bytes per slot  */
#define SPSC_NAME_MAX       48      /* max debug label length (incl. NUL)     */

/* Flags for spsc_create() / mpmc_create() */
#define SPSC_FLAG_GROWABLE          (1u << 0)  /* enable spsc_resize()       */
#define SPSC_FLAG_OVERFLOW_POOL     (1u << 1)  /* pre-alloc overflow pool    */
#define SPSC_FLAG_OVERFLOW_UNBOUNDED (1u << 2) /* alloc on demand (slow)     */

/* Overflow sentinel written into a slot's size field */
#define SPSC_OVERFLOW_TOKEN         (UINT32_MAX)

/* =========================================================================
 * SPSC slot state machine
 * =========================================================================
 *
 *   EMPTY ──CAS(→WRITING)──► WRITING ──store(→FILLED)──► FILLED
 *     ▲                                                      │
 *     │                                               CAS(→READING)
 *     │                                                      │
 *     └─────── store(→EMPTY) ◄────── READING ◄──────────────┘
 *
 * Only the producer transitions EMPTY→WRITING→FILLED.
 * Only the consumer transitions FILLED→READING→EMPTY.
 * In MPMC, multiple threads compete on both transitions via CAS.
 */
typedef enum __attribute__((packed)) {
    SPSC_SLOT_EMPTY   = 0,  /* slot is free for producer to claim            */
    SPSC_SLOT_WRITING = 1,  /* producer claimed; writing payload              */
    SPSC_SLOT_FILLED  = 2,  /* payload ready; consumer may claim              */
    SPSC_SLOT_READING = 3,  /* consumer claimed; reading payload              */
} spsc_slot_state_t;

/* =========================================================================
 * SPSC slot
 * =========================================================================
 * The state and data intentionally share a cache line:
 *   Producer's store(WRITING) invalidates the line; consumer's acquire-load
 *   fetches the whole line so the payload is already in cache.  No extra
 *   prefetch needed for the inline fast path.
 *
 * Overflow: when size == SPSC_OVERFLOW_TOKEN, data[0..3] holds the uint32_t
 *   overflow pool index.  Consumer copies from the pool and marks it free.
 */
typedef struct {
    _Atomic(spsc_slot_state_t) state;                      /* CAS-managed    */
    uint8_t                    _pad[SPSC_CACHE_LINE        /* pad to one CL  */
                                   - sizeof(_Atomic(spsc_slot_state_t))];
    uint8_t                    data[SPSC_SLOT_DEFAULT];    /* inline payload  */
    _Atomic(uint32_t)          size;                       /* payload bytes   */
    uint32_t                   flags;                      /* reserved        */
} spsc_slot_t;

/* =========================================================================
 * Overflow pool entry
 * ========================================================================= */
typedef struct {
    void*               buffer;         /* aligned_alloc'd chunk              */
    size_t              buf_capacity;   /* max payload this entry can hold    */
    _Atomic(uint32_t)   pool_state;     /* CAS: 0=FREE, 1=CLAIMED            */
} spsc_overflow_entry_t;

typedef struct {
    uint32_t               count;           /* number of entries              */
    spsc_overflow_entry_t* entries;         /* array allocated at create()    */
    _Atomic(uint32_t)      claim_idx;       /* monotone counter for claiming  */
} spsc_overflow_pool_t;

/* =========================================================================
 * SPSC ring buffer
 * =========================================================================
 *
 * Cache-line layout (3 × 64 bytes before the slot array):
 *
 *   [CL 0] write_cursor  — written only by producer
 *   [CL 1] read_cursor   — written only by consumer
 *   [CL 2] capacity, mask, slot_size, flags, name (read-only after init)
 *
 * NOTE: write_cursor and read_cursor are *monotonically increasing* raw
 *       counters, NOT masked indices.  Apply (cursor & mask) to get the
 *       slot index.  This preserves the full-vs-empty distinction and
 *       keeps the wait-free property of push() — see spsc_push() docs.
 *
 * Growth: the `migrating` pointer is set non-NULL when a resize is in
 *         progress.  It points to the new (larger) ring buffer.  See
 *         spsc_resize() for the double-buffer migration protocol.
 */
typedef struct spsc_ring_buffer spsc_ring_buffer_t;

struct spsc_ring_buffer {
    /* ── Cache line 0: producer-owned ─────────────────────────────────── */
    _Atomic(uint64_t) write_cursor;                    /* monotone; producer */
    char _pad_w[SPSC_CACHE_LINE - sizeof(_Atomic(uint64_t))];

    /* ── Cache line 1: consumer-owned ─────────────────────────────────── */
    _Atomic(uint64_t) read_cursor;                     /* monotone; consumer */
    char _pad_r[SPSC_CACHE_LINE - sizeof(_Atomic(uint64_t))];

    /* ── Cache line 2: shared read-only after init ─────────────────────── */
    uint64_t              capacity;    /* number of slots (power of 2)       */
    uint64_t              mask;        /* capacity - 1                       */
    uint32_t              slot_size;   /* inline payload limit (bytes)       */
    uint32_t              create_flags;
    bool                  trace_enabled;
    char                  name[SPSC_NAME_MAX];

    /* ── Growth (null when not resizing) ───────────────────────────────── */
    _Atomic(spsc_ring_buffer_t*) migrating; /* non-NULL during resize        */

    /* ── Overflow pool (null when SPSC_FLAG_OVERFLOW_POOL not set) ──────── */
    spsc_overflow_pool_t* overflow;

    /* ── Slots follow in contiguous aligned memory ─────────────────────── */
    spsc_slot_t slots[];   /* flexible array — do NOT add fields after this  */
};

/* =========================================================================
 * io_uring async handle (optional; §6 of spec)
 * ========================================================================= */
struct io_uring; /* forward-declare; caller includes <liburing.h> if needed  */

typedef struct {
    spsc_ring_buffer_t* ring;
    struct io_uring*    uring;
    bool                registered;
} spsc_async_t;

/* =========================================================================
 * SPSC public API
 * ========================================================================= */

#ifdef __cplusplus
extern "C" {
#endif

/* Lifecycle */
spsc_ring_buffer_t* spsc_create(const char* name, uint64_t capacity,
                                uint32_t slot_size, uint32_t flags);
void                spsc_destroy(spsc_ring_buffer_t* rb);

/* Non-blocking push/pop (-EAGAIN if full/empty; -EMSGSIZE if overflow pool
 * exhausted; 0 on success for push; bytes_read on success for pop) */
int spsc_push(spsc_ring_buffer_t* rb, const void* data, size_t size);
int spsc_pop (spsc_ring_buffer_t* rb, void* buffer,     size_t* out_size);

/* Aliases */
static inline int spsc_try_push(spsc_ring_buffer_t* rb,
                                const void* data, size_t size)
    { return spsc_push(rb, data, size); }

static inline int spsc_try_pop(spsc_ring_buffer_t* rb,
                               void* buffer, size_t* out_size)
    { return spsc_pop(rb, buffer, out_size); }

/* Blocking variants — spin with PAUSE / sched_yield until success */
int spsc_push_blocking(spsc_ring_buffer_t* rb, const void* data, size_t size);
int spsc_pop_blocking (spsc_ring_buffer_t* rb, void* buffer,     size_t* out_size);

/* Utility */
uint64_t spsc_capacity (spsc_ring_buffer_t* rb);
uint64_t spsc_available(spsc_ring_buffer_t* rb);   /* approximate */
void     spsc_set_trace(spsc_ring_buffer_t* rb, bool enabled);

/* Growth (requires SPSC_FLAG_GROWABLE) */
int spsc_resize(spsc_ring_buffer_t* rb, uint64_t new_capacity);

/* io_uring async notification (§6) */
int  spsc_async_init   (spsc_async_t* ah, spsc_ring_buffer_t* rb);
int  spsc_push_async   (spsc_async_t* ah, const void* data, size_t size);
int  spsc_wait_pop     (spsc_async_t* ah, void* buffer, size_t* out_size,
                        struct timespec* timeout);
void spsc_async_destroy(spsc_async_t* ah);

/* =========================================================================
 * MPMC queue  (Dmitry Vyukov sequence-number design)
 * =========================================================================
 *
 * Each slot has a `sequence` counter (not a state enum).  The sequence
 * counter encodes slot ownership without any global index racing:
 *
 *   - After init:   sequence[i] = i   (slot i is ready for producer at
 *                                      enqueue_pos == i)
 *   - After enqueue: sequence[i] = pos + 1   (ready for dequeue)
 *   - After dequeue: sequence[i] = pos + capacity (ready for next enqueue
 *                                                    at pos + capacity)
 *
 * Producers load enqueue_pos (fetch_add), read sequence[slot], and CAS
 * only when sequence == pos (their turn).  If sequence < pos, the queue
 * is full; if sequence > pos, another producer already claimed the slot.
 * No retry-fetch_add is needed — each producer keeps its own claimed pos.
 *
 * This eliminates the bug in the original spec where fetch_add inside
 * the retry loop advanced the global index on every CAS failure.
 */

#define MPMC_CACHE_LINE     64
#define MPMC_SLOT_DEFAULT   128
#define MPMC_NAME_MAX       48

typedef struct {
    _Atomic(uint64_t) sequence;              /* Vyukov sequence counter       */
    char              _pad[MPMC_CACHE_LINE - sizeof(_Atomic(uint64_t))];
    uint8_t           data[MPMC_SLOT_DEFAULT];
    _Atomic(uint32_t) size;
    uint32_t          producer_id;           /* debug: which producer wrote  */
} mpmc_slot_t;

typedef struct {
    /* ── Cache line: producer contention point ─────────────────────────── */
    _Atomic(uint64_t) enqueue_pos;
    char _pad_enq[MPMC_CACHE_LINE - sizeof(_Atomic(uint64_t))];

    /* ── Cache line: consumer contention point ─────────────────────────── */
    _Atomic(uint64_t) dequeue_pos;
    char _pad_deq[MPMC_CACHE_LINE - sizeof(_Atomic(uint64_t))];

    /* ── Cache line: shared read-only after init ─────────────────────────── */
    uint64_t capacity;
    uint64_t mask;
    uint32_t slot_size;
    char     name[MPMC_NAME_MAX];

    mpmc_slot_t slots[];   /* flexible array                                  */
} mpmc_queue_t;

/* Lifecycle */
mpmc_queue_t* mpmc_create (const char* name, uint64_t capacity,
                            uint32_t slot_size, uint32_t flags);
void          mpmc_destroy(mpmc_queue_t* q);

/* Non-blocking */
int mpmc_enqueue(mpmc_queue_t* q, const void* data, size_t size);
int mpmc_dequeue(mpmc_queue_t* q, void* buffer,     size_t* out_size);

/* Blocking */
int mpmc_enqueue_blocking(mpmc_queue_t* q, const void* data, size_t size);
int mpmc_dequeue_blocking(mpmc_queue_t* q, void* buffer,     size_t* out_size);

/* Batch */
int mpmc_enqueue_batch(mpmc_queue_t* q, const void** buffers,
                       const size_t* sizes, int count);
int mpmc_dequeue_batch(mpmc_queue_t* q, void** buffers,
                       size_t* sizes, int max_count);

/* Utility */
uint64_t mpmc_capacity (mpmc_queue_t* q);
uint64_t mpmc_available(mpmc_queue_t* q);   /* approximate */
void     mpmc_set_trace(mpmc_queue_t* q, bool enabled);

#ifdef __cplusplus
}
#endif

#endif /* LIBINTERYNC_SPSC_H */
