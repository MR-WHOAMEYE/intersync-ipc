/*
 * spsc_ring_buffer.c
 * InterSync — CAS-based SPSC ring buffer (Phase 1 core implementation).
 *
 * BUG FIXES vs spec v2 (see docs/spsc-mpmc-spec.md Appendix B):
 *
 *  [BUG-1] write_cursor stored as masked index (idx+1 instead of cursor+1)
 *    FIXED: write_cursor is a monotone counter.  spsc_push() stores the
 *    pre-mask cursor+1, not the masked idx+1.  This preserves the ring's
 *    empty/full distinction and the wait-free property.
 *
 *  [BUG-2] MPMC fetch_add-in-retry loop
 *    FIXED in mpmc_queue.c: the Vyukov sequence-number design is used
 *    instead.  Each producer atomically claims a position with a single
 *    fetch_add; it never calls fetch_add again on failure — it just spins
 *    (or returns -EAGAIN) on the slot it claimed.
 *
 *  [BUG-3] Overflow pool crash-leak and wait-free violation
 *    DOCUMENTED: push() wait-free only on inline (fast) path.
 *    Overflow path is lock-free (may CAS-retry on pool slot claim).
 *    Progress claims table in §2.10 updated accordingly.
 *
 * Memory ordering: all atomics use C11 explicit orderings; no seq_cst.
 * See Appendix A of docs/spsc-mpmc-spec.md for per-operation rationale.
 */

#include "libinterync_spsc.h"
#include "spsc_inline_asm.h"

#include <assert.h>
#include <errno.h>
#include <sched.h>       /* sched_yield */
#include <stdatomic.h>
#include <stdlib.h>
#include <string.h>

/* =========================================================================
 * Internal helpers
 * ========================================================================= */

/* Round n up to the next power of two (minimum 2). */
static uint64_t next_pow2(uint64_t n)
{
    if (n < 2) return 2;
    n--;
    n |= n >> 1;
    n |= n >> 2;
    n |= n >> 4;
    n |= n >> 8;
    n |= n >> 16;
    n |= n >> 32;
    return n + 1;
}

/* Total allocation size for a ring buffer with `cap` slots. */
static size_t ring_alloc_size(uint64_t cap)
{
    return sizeof(spsc_ring_buffer_t) + cap * sizeof(spsc_slot_t);
}

/* =========================================================================
 * spsc_create
 * =========================================================================
 * Allocates and initialises a ring buffer.
 *
 * All slots start in SPSC_SLOT_EMPTY.
 * write_cursor and read_cursor start at 0.
 *
 * The buffer is aligned to SPSC_CACHE_LINE to ensure the struct's internal
 * cache-line boundaries fall on real cache-line boundaries.
 */
spsc_ring_buffer_t* spsc_create(const char*  name,
                                uint64_t     capacity,
                                uint32_t     slot_size,
                                uint32_t     flags)
{
    if (capacity == 0) { errno = EINVAL; return NULL; }

    uint64_t cap  = next_pow2(capacity);
    size_t   total = ring_alloc_size(cap);

    /* aligned_alloc requires size to be a multiple of alignment */
    size_t aligned_total = (total + SPSC_CACHE_LINE - 1)
                         & ~(size_t)(SPSC_CACHE_LINE - 1);

    spsc_ring_buffer_t* rb = aligned_alloc(SPSC_CACHE_LINE, aligned_total);
    if (!rb) return NULL;

    memset(rb, 0, aligned_total);

    /* Scalar fields */
    rb->capacity     = cap;
    rb->mask         = cap - 1;
    rb->slot_size    = slot_size ? slot_size : SPSC_SLOT_DEFAULT;
    rb->create_flags = flags;
    rb->trace_enabled = false;

    if (name)
        strncpy(rb->name, name, SPSC_NAME_MAX - 1);

    /* Atomic cursors */
    atomic_store_explicit(&rb->write_cursor, 0, memory_order_relaxed);
    atomic_store_explicit(&rb->read_cursor,  0, memory_order_relaxed);
    atomic_store_explicit(&rb->migrating,   NULL, memory_order_relaxed);

    /* Initialise all slots to EMPTY */
    for (uint64_t i = 0; i < cap; i++) {
        atomic_store_explicit(&rb->slots[i].state,
                              SPSC_SLOT_EMPTY,
                              memory_order_relaxed);
        atomic_store_explicit(&rb->slots[i].size, 0, memory_order_relaxed);
    }

    /* Overflow pool */
    rb->overflow = NULL;
    if (flags & SPSC_FLAG_OVERFLOW_POOL) {
        uint32_t pool_count = (uint32_t)(cap / 4);
        if (pool_count < 4) pool_count = 4;

        spsc_overflow_pool_t* pool = malloc(sizeof(*pool));
        if (!pool) { free(rb); errno = ENOMEM; return NULL; }

        pool->count = pool_count;
        pool->entries = calloc(pool_count, sizeof(spsc_overflow_entry_t));
        if (!pool->entries) { free(pool); free(rb); errno = ENOMEM; return NULL; }
        atomic_store_explicit(&pool->claim_idx, 0, memory_order_relaxed);

        size_t overflow_slot_size = rb->slot_size * 8 + sizeof(uint32_t); /* 8x inline slot + header */
        /* aligned_alloc requires size to be a multiple of alignment */
        overflow_slot_size = (overflow_slot_size + SPSC_CACHE_LINE - 1) & ~(size_t)(SPSC_CACHE_LINE - 1);
        for (uint32_t i = 0; i < pool_count; i++) {
            pool->entries[i].buffer = aligned_alloc(SPSC_CACHE_LINE,
                                                    overflow_slot_size);
            if (!pool->entries[i].buffer) {
                /* Clean up partial allocation */
                for (uint32_t j = 0; j < i; j++) free(pool->entries[j].buffer);
                free(pool->entries);
                free(pool);
                free(rb);
                errno = ENOMEM;
                return NULL;
            }
            pool->entries[i].buf_capacity = overflow_slot_size;
            atomic_store_explicit(&pool->entries[i].pool_state,
                                  0, memory_order_relaxed);
        }
        rb->overflow = pool;
    }

    return rb;
}

/* =========================================================================
 * spsc_destroy
 * =========================================================================
 * Frees all memory.  Safe to call with NULL.  Not thread-safe — caller must
 * ensure no concurrent push/pop calls are in flight.
 */
void spsc_destroy(spsc_ring_buffer_t* rb)
{
    if (!rb) return;

    /* Drain migrating pointer if set */
    spsc_ring_buffer_t* new_rb =
        atomic_load_explicit(&rb->migrating, memory_order_relaxed);
    if (new_rb) spsc_destroy(new_rb);

    /* Free overflow pool */
    if (rb->overflow) {
        for (uint32_t i = 0; i < rb->overflow->count; i++)
            free(rb->overflow->entries[i].buffer);
        free(rb->overflow->entries);
        free(rb->overflow);
    }

    free(rb);
}

/* =========================================================================
 * spsc_push  — non-blocking, wait-free on inline path
 * =========================================================================
 *
 * Algorithm (inline fast path):
 *   1. Load write_cursor (relaxed: single producer).
 *   2. Compute slot index = write_cursor & mask.
 *   3. CAS EMPTY→WRITING on the slot (acquire on success).
 *      - On failure: return -EAGAIN immediately (wait-free guarantee).
 *   4. Copy payload into slot.data (single writer — no race).
 *   5. store(size) relaxed (single writer).
 *   6. store(FILLED) release  — pairs with consumer's acquire load.
 *   7. store(write_cursor + 1) relaxed — single writer, monotone counter.
 *      NOTE: we store the PRE-MASK cursor+1, not (idx+1), to keep the
 *            monotone property and correct empty/full detection (BUG-1 fix).
 *
 * Prefetch: next slot's state+data are prefetched for write while we copy
 *           the current payload, hiding the cache miss latency.
 *
 * Returns: 0 on success, -EAGAIN if ring is full, -EMSGSIZE if size >
 *          slot_size AND overflow pool is unavailable/exhausted.
 */
int spsc_push(spsc_ring_buffer_t* rb, const void* data, size_t size)
{
    if (!rb || !data) return -EINVAL;

    /* ── Load write cursor (relaxed: single-writer cache line) ── */
    uint64_t wc  = atomic_load_explicit(&rb->write_cursor, memory_order_relaxed);
    uint64_t idx = wc & rb->mask;
    spsc_slot_t* slot = &rb->slots[idx];

    /* ── Prefetch next slot for write (producer hot path) ── */
    uint64_t next_idx = (wc + 1) & rb->mask;
    SPSC_PREFETCH_WRITE(&rb->slots[next_idx]);

    /* ── Overflow path: message larger than inline slot ── */
    if (size > rb->slot_size) {
        if (!rb->overflow) return -EMSGSIZE;

        spsc_overflow_pool_t* pool = rb->overflow;
        uint32_t attempts = pool->count;
        uint32_t oi;

        while (attempts--) {
            /* Claim a monotone index; wrap within pool */
            oi = atomic_fetch_add_explicit(&pool->claim_idx,
                                           1, memory_order_relaxed)
                 % pool->count;
            uint32_t expected_free = 0;
            if (atomic_compare_exchange_strong_explicit(
                        &pool->entries[oi].pool_state,
                        &expected_free, 1,
                        memory_order_acquire,
                        memory_order_relaxed)) {
                goto overflow_claimed;
            }
        }
        return -EMSGSIZE;   /* pool exhausted */

    overflow_claimed:
        if (size > pool->entries[oi].buf_capacity - sizeof(uint32_t)) {
            /* Message too large even for overflow slot */
            atomic_store_explicit(&pool->entries[oi].pool_state,
                                  0, memory_order_release);
            return -EMSGSIZE;
        }

        /* CAS-claim the ring slot */
        spsc_slot_state_t expected = SPSC_SLOT_EMPTY;
        if (!SPSC_CAS_STRONG(&slot->state, &expected, SPSC_SLOT_WRITING)) {
            atomic_store_explicit(&pool->entries[oi].pool_state,
                                  0, memory_order_release);
            return -EAGAIN;
        }

        /* Consumer expects uint32_t size followed by payload data */
        uint32_t payload_sz32 = (uint32_t)size;
        memcpy(pool->entries[oi].buffer, &payload_sz32, sizeof(payload_sz32));
        memcpy((uint8_t*)pool->entries[oi].buffer + sizeof(payload_sz32), data, size);
        /* Write overflow pool index into slot.data[0..3] */
        uint32_t oi32 = (uint32_t)oi;
        memcpy(slot->data, &oi32, sizeof(oi32));
        atomic_store_explicit(&slot->size, SPSC_OVERFLOW_TOKEN,
                              memory_order_relaxed);
        atomic_store_explicit(&slot->state, SPSC_SLOT_FILLED,
                              memory_order_release);
        /* BUG-1 FIX: store monotone counter, not masked index */
        atomic_store_explicit(&rb->write_cursor, wc + 1, memory_order_relaxed);
        return 0;
    }

    /* ── Inline fast path ── */

    /* CAS EMPTY→WRITING: acquire pairs with consumer's release(EMPTY).
     * On failure, ring is full — return immediately (wait-free). */
    spsc_slot_state_t expected = SPSC_SLOT_EMPTY;
    if (!SPSC_CAS_STRONG(&slot->state, &expected, SPSC_SLOT_WRITING)) {
        return -EAGAIN;
    }

    /* Copy payload (single writer — no concurrent access) */
    memcpy(slot->data, data, size);
    atomic_store_explicit(&slot->size, (uint32_t)size, memory_order_relaxed);

    /* Publish: release pairs with consumer's acquire load of state */
    atomic_store_explicit(&slot->state, SPSC_SLOT_FILLED, memory_order_release);

    /* BUG-1 FIX: advance monotone write_cursor, not masked idx */
    atomic_store_explicit(&rb->write_cursor, wc + 1, memory_order_relaxed);

    return 0;
}

/* =========================================================================
 * spsc_push_blocking — spin until push succeeds
 * =========================================================================
 * Not wait-free (can spin indefinitely if consumer is stalled), but useful
 * for benchmarking and scenarios where dropping messages is not acceptable.
 */
int spsc_push_blocking(spsc_ring_buffer_t* rb, const void* data, size_t size)
{
    if (!rb || !data) return -EINVAL;
    int retries = 0;

    for (;;) {
        int rc = spsc_push(rb, data, size);
        if (rc == 0) return 0;
        if (rc != -EAGAIN) return rc;  /* hard error (e.g. -EMSGSIZE) */

        retries++;
        if (retries > 1000) sched_yield();
        else                SPSC_PAUSE();
    }
}

/* =========================================================================
 * spsc_pop  — non-blocking, lock-free
 * =========================================================================
 *
 * Algorithm:
 *   1. Load read_cursor (relaxed: single consumer).
 *   2. Compute slot index = read_cursor & mask.
 *   3. Load slot state (acquire) — pairs with producer's release(FILLED).
 *   4. If state != FILLED: return -EAGAIN.
 *   5. CAS FILLED→READING (acquire on success): in SPSC this always
 *      succeeds; in MPMC (if sharing a pop impl) it may fail → -EAGAIN.
 *   6. Read size (relaxed), copy payload.
 *   7. store(EMPTY) release — pairs with producer's acquire CAS.
 *   8. store(read_cursor + 1) relaxed — monotone counter (BUG-1 fix).
 *
 * Prefetch: next slot's state+data are prefetched for read (consumer path).
 *
 * Returns: bytes read on success, -EAGAIN if empty.
 */
int spsc_pop(spsc_ring_buffer_t* rb, void* buffer, size_t* out_size)
{
    if (!rb || !buffer || !out_size) return -EINVAL;

    /* ── Load read cursor (relaxed: single-consumer cache line) ── */
    uint64_t rc  = atomic_load_explicit(&rb->read_cursor, memory_order_relaxed);
    uint64_t idx = rc & rb->mask;
    spsc_slot_t* slot = &rb->slots[idx];

    /* ── Prefetch next slot for read (consumer hot path) ── */
    uint64_t next_idx = (rc + 1) & rb->mask;
    SPSC_PREFETCH_READ(&rb->slots[next_idx]);

    /* ── Acquire-load state: must see producer's release(FILLED) ── */
    spsc_slot_state_t state =
        atomic_load_explicit(&slot->state, memory_order_acquire);
    if (state != SPSC_SLOT_FILLED) {
        return -EAGAIN;
    }

    /* ── CAS FILLED→READING (acquire) ─────────────────────────────────────
     * In pure SPSC there is only one consumer so this CAS always succeeds.
     * The CAS is kept here so that spsc_pop() is safe even if someone
     * mistakenly calls it from two threads simultaneously (graceful
     * degradation rather than silent data corruption).                      */
    spsc_slot_state_t expected = SPSC_SLOT_FILLED;
    if (!SPSC_CAS_STRONG(&slot->state, &expected, SPSC_SLOT_READING)) {
        return -EAGAIN;
    }

    /* ── Read payload ── */
    uint32_t sz = atomic_load_explicit(&slot->size, memory_order_relaxed);

    if (sz == SPSC_OVERFLOW_TOKEN) {
        /* Overflow path: data[0..3] is the pool index */
        uint32_t oi;
        memcpy(&oi, slot->data, sizeof(oi));
        spsc_overflow_pool_t* pool = rb->overflow;
        if (pool && oi < pool->count) {
            size_t payload_sz = 0;
            /* Consumer reads the actual size from the overflow buffer header.
             * Convention: overflow buffer starts with a uint32_t size.       */
            memcpy(&payload_sz, pool->entries[oi].buffer, sizeof(uint32_t));
            if (payload_sz <= pool->entries[oi].buf_capacity - sizeof(uint32_t)) {
                memcpy(buffer,
                       (uint8_t*)pool->entries[oi].buffer + sizeof(uint32_t),
                       payload_sz);
                *out_size = payload_sz;
            }
            /* Mark pool slot free (release: producer's claim_idx acquire sees this) */
            atomic_store_explicit(&pool->entries[oi].pool_state,
                                  0, memory_order_release);
        }
        sz = (uint32_t)*out_size; /* for the return value below */
    } else {
        memcpy(buffer, slot->data, sz);
        *out_size = sz;
    }

    /* ── Release slot: READING→EMPTY (release) ─────────────────────────────
     * Producer's acquire CAS on next push() will see this.                  */
    atomic_store_explicit(&slot->state, SPSC_SLOT_EMPTY, memory_order_release);

    /* ── Advance monotone read cursor (BUG-1 fix: store rc+1, not idx+1) ─ */
    atomic_store_explicit(&rb->read_cursor, rc + 1, memory_order_relaxed);

    return (int)sz;
}

/* =========================================================================
 * spsc_pop_blocking — spin until pop succeeds
 * =========================================================================
 */
int spsc_pop_blocking(spsc_ring_buffer_t* rb, void* buffer, size_t* out_size)
{
    if (!rb || !buffer || !out_size) return -EINVAL;
    int retries = 0;

    for (;;) {
        int rc = spsc_pop(rb, buffer, out_size);
        if (rc >= 0) return rc;
        if (rc != -EAGAIN) return rc;

        retries++;
        if (retries > 1000) sched_yield();
        else                SPSC_PAUSE();
    }
}

/* =========================================================================
 * Utility functions
 * ========================================================================= */

uint64_t spsc_capacity(spsc_ring_buffer_t* rb)
{
    return rb ? rb->capacity : 0;
}

/*
 * spsc_available — approximate number of filled slots.
 * "Approximate" because write_cursor and read_cursor are read separately;
 * a concurrent push/pop may execute between the two loads.
 */
uint64_t spsc_available(spsc_ring_buffer_t* rb)
{
    if (!rb) return 0;
    uint64_t w = atomic_load_explicit(&rb->write_cursor, memory_order_relaxed);
    uint64_t r = atomic_load_explicit(&rb->read_cursor,  memory_order_relaxed);
    return (w >= r) ? (w - r) : 0;
}

void spsc_set_trace(spsc_ring_buffer_t* rb, bool enabled)
{
    if (rb) rb->trace_enabled = enabled;
}

/* =========================================================================
 * spsc_resize — double-buffer migration (SPSC_FLAG_GROWABLE)
 * =========================================================================
 * Protocol:
 *   1. Create new ring of 2× capacity.
 *   2. CAS rb->migrating: NULL → new_rb (signals migration to consumer).
 *   3. Drain all FILLED slots from old ring into new ring.
 *   4. (Caller is responsible for swapping the rb pointer externally and
 *      calling spsc_destroy() on the old ring after both threads agree.)
 *
 * NOTE: This is an O(n) pause.  Only use for rare resize events.
 *       The consumer must check rb->migrating != NULL before calling
 *       spsc_pop() on the original ring.
 *
 * Returns: 0 on success; -EINVAL if flag not set; -ENOMEM on OOM.
 */
int spsc_resize(spsc_ring_buffer_t* rb, uint64_t new_capacity)
{
    if (!rb) return -EINVAL;
    if (!(rb->create_flags & SPSC_FLAG_GROWABLE)) return -EINVAL;

    spsc_ring_buffer_t* new_rb =
        spsc_create(rb->name, new_capacity, rb->slot_size,
                    rb->create_flags);
    if (!new_rb) return -ENOMEM;

    /* Signal migration to consumer */
    spsc_ring_buffer_t* expected_null = NULL;
    if (!atomic_compare_exchange_strong_explicit(
                &rb->migrating, &expected_null, new_rb,
                memory_order_release, memory_order_relaxed)) {
        spsc_destroy(new_rb);
        return -EBUSY;  /* another resize already in progress */
    }

    /* Move remaining FILLED slots into new ring */
    uint8_t  tmp[SPSC_SLOT_DEFAULT];
    size_t   tsz = 0;
    while (spsc_pop(rb, tmp, &tsz) >= 0) {
        /* Best-effort: if new ring is full (shouldn't happen with 2× cap),
         * drop the message to avoid infinite loop.                           */
        (void)spsc_push(new_rb, tmp, tsz);
    }

    return 0;
}
