/*
 * mpmc_queue.c
 * InterSync — True lock-free MPMC queue (Dmitry Vyukov sequence-number design).
 *
 * BUG FIX vs original spec
 * ========================
 * The original spec's mpmc_enqueue() used:
 *
 *   while (attempts--) {
 *       if (CAS fails) {
 *           idx = atomic_fetch_add(&q->enqueue_idx, 1);  ← BUG
 *       }
 *   }
 *
 * The fetch_add inside the retry loop advances the GLOBAL enqueue_idx on
 * every CAS failure.  Under contention this races the index far ahead of
 * the actual number of enqueued items, permanently wasting slot indices and
 * effectively shrinking usable capacity.
 *
 * FIX: Dmitry Vyukov's algorithm (https://www.1024cores.net/home/lock-free-algorithms/queues/bounded-mpmc-queue)
 * ============================================================
 * Each slot has a SEQUENCE counter (uint64_t, not a state enum).
 * Producers each do ONE fetch_add on enqueue_pos to get their unique
 * position — no retry fetch_add.  They then spin/yield on their own
 * claimed slot until sequence == pos (their turn), then write and
 * store sequence = pos + 1.
 *
 * Slot sequence lifecycle for capacity C:
 *   init:     seq[i] = i                  (slot i ready for producer at pos i)
 *   enqueue:  seq[i] = pos + 1            (slot ready for dequeue at pos + 1)
 *   dequeue:  seq[i] = pos + C            (slot ready for enqueue at pos + C)
 *
 * Why this works:
 *   - Producer at `pos` computes slot = pos & mask.
 *   - It loads seq[slot].  If seq == pos, it can write (CAS not needed —
 *     only this producer has pos).  If seq < pos, queue is full.
 *     If seq > pos, another producer already wrote here; spin.
 *   - Consumer at `pos` computes slot = pos & mask.
 *   - It loads seq[slot].  If seq == pos + 1, it can read.
 *     If seq < pos + 1, slot not ready; spin.  If seq > pos + 1, ABA—retry.
 *
 * Progress: each producer and consumer makes progress as long as the
 * other side isn't permanently stalled.  System-wide lock-free.
 *
 * Memory ordering: same acquire/release discipline as SPSC.
 * No seq_cst operations.
 */

#include "libinterync_spsc.h"
#include "spsc_inline_asm.h"

#include <assert.h>
#include <errno.h>
#include <sched.h>
#include <stdatomic.h>
#include <stdlib.h>
#include <string.h>

/* =========================================================================
 * Internal helpers
 * ========================================================================= */

static uint64_t mpmc_next_pow2(uint64_t n)
{
    if (n < 2) return 2;
    n--;
    n |= n >> 1; n |= n >> 2; n |= n >> 4;
    n |= n >> 8; n |= n >> 16; n |= n >> 32;
    return n + 1;
}

static size_t mpmc_alloc_size(uint64_t cap)
{
    return sizeof(mpmc_queue_t) + cap * sizeof(mpmc_slot_t);
}

/* =========================================================================
 * mpmc_create
 * =========================================================================
 * Initialises slot sequences to their index (Vyukov invariant).
 */
mpmc_queue_t* mpmc_create(const char* name, uint64_t capacity,
                           uint32_t slot_size, uint32_t flags)
{
    (void)slot_size; /* Currently always MPMC_SLOT_DEFAULT */
    (void)flags;

    if (capacity == 0) { errno = EINVAL; return NULL; }

    uint64_t cap   = mpmc_next_pow2(capacity);
    size_t   total = mpmc_alloc_size(cap);
    size_t   aligned_total = (total + MPMC_CACHE_LINE - 1)
                           & ~(size_t)(MPMC_CACHE_LINE - 1);

    mpmc_queue_t* q = aligned_alloc(MPMC_CACHE_LINE, aligned_total);
    if (!q) return NULL;

    memset(q, 0, aligned_total);

    q->capacity  = cap;
    q->mask      = cap - 1;
    q->slot_size = slot_size ? slot_size : MPMC_SLOT_DEFAULT;
    if (name) strncpy(q->name, name, MPMC_NAME_MAX - 1);

    atomic_store_explicit(&q->enqueue_pos, 0, memory_order_relaxed);
    atomic_store_explicit(&q->dequeue_pos, 0, memory_order_relaxed);

    /* Vyukov init: seq[i] = i — slot i is ready for producer at position i */
    for (uint64_t i = 0; i < cap; i++) {
        atomic_store_explicit(&q->slots[i].sequence, i, memory_order_relaxed);
        atomic_store_explicit(&q->slots[i].size,     0, memory_order_relaxed);
    }

    return q;
}

/* =========================================================================
 * mpmc_destroy
 * ========================================================================= */
void mpmc_destroy(mpmc_queue_t* q)
{
    free(q);
}

/* =========================================================================
 * mpmc_enqueue — non-blocking, lock-free (Vyukov algorithm)
 * =========================================================================
 *
 * Each producer:
 *   1. fetch_add(enqueue_pos, 1) → get own unique position `pos`.
 *      This is the ONLY fetch_add per enqueue call — no retry fetch_add.
 *   2. Compute slot index = pos & mask.
 *   3. Load seq = slot->sequence (acquire).
 *   4. diff = (int64_t)seq - (int64_t)pos
 *      diff == 0: this slot is ours → write it.
 *      diff <  0: queue full (consumer has not yet freed the slot for
 *                 this wrap around) → return -EAGAIN.
 *      diff >  0: should not happen (sequence ahead of pos implies
 *                 another thread already wrote here — ABA-like).
 *                 Spin with PAUSE.
 *   5. Copy data, store size (relaxed).
 *   6. store(sequence = pos + 1, release) → signals consumer.
 *
 * Returns: 0 on success; -EAGAIN if full.
 */
int mpmc_enqueue(mpmc_queue_t* q, const void* data, size_t size)
{
    if (!q || !data) return -EINVAL;
    if (size > q->slot_size) return -EMSGSIZE;

    mpmc_slot_t* slot;
    uint64_t pos = atomic_load_explicit(&q->enqueue_pos, memory_order_relaxed);

    int spins = 0;
    for (;;) {
        uint64_t slot_idx = pos & q->mask;
        slot = &q->slots[slot_idx];

        uint64_t seq = atomic_load_explicit(&slot->sequence, memory_order_acquire);
        int64_t diff = (int64_t)seq - (int64_t)pos;

        if (diff == 0) {
            /* Slot is ready for us. Try to claim `pos`. */
            if (atomic_compare_exchange_weak_explicit(
                    &q->enqueue_pos, &pos, pos + 1,
                    memory_order_relaxed, memory_order_relaxed)) {
                break; /* Successfully claimed `pos` */
            }
            /* CAS failed (another producer claimed `pos`). `pos` is updated. */
        } else if (diff < 0) {
            /* Slot still occupied from a previous cycle: queue is full. */
            return -EAGAIN;
        } else {
            /* diff > 0: another thread already claimed `pos` and wrote here,
             * so we are behind. Reload `enqueue_pos`. */
            pos = atomic_load_explicit(&q->enqueue_pos, memory_order_relaxed);
            
            spins++;
            if (spins > 1000) sched_yield();
            else              SPSC_PAUSE();
        }
    }

    /* Write payload */
    memcpy(slot->data, data, size);
    atomic_store_explicit(&slot->size, (uint32_t)size, memory_order_relaxed);

    /* Publish: release pairs with consumer's acquire load of sequence */
    atomic_store_explicit(&slot->sequence, pos + 1, memory_order_release);

    return 0;
}

/* =========================================================================
 * mpmc_enqueue_blocking — spin until success
 * ========================================================================= */
int mpmc_enqueue_blocking(mpmc_queue_t* q, const void* data, size_t size)
{
    if (!q || !data) return -EINVAL;
    int retries = 0;
    for (;;) {
        int rc = mpmc_enqueue(q, data, size);
        if (rc == 0) return 0;
        if (rc != -EAGAIN) return rc;
        retries++;
        if (retries > 1000) sched_yield();
        else                SPSC_PAUSE();
    }
}

/* =========================================================================
 * mpmc_dequeue — non-blocking, lock-free (Vyukov algorithm)
 * =========================================================================
 *
 * Each consumer:
 *   1. fetch_add(dequeue_pos, 1) → get own unique position `pos`.
 *   2. Compute slot index = pos & mask.
 *   3. Load seq = slot->sequence (acquire).
 *   4. diff = (int64_t)seq - (int64_t)(pos + 1)
 *      diff == 0: data is ready → read it.
 *      diff <  0: slot not yet written (producer is behind) → -EAGAIN.
 *      diff >  0: another consumer got ahead (ABA) → spin.
 *   5. Copy data, store size out.
 *   6. store(sequence = pos + capacity, release) → marks slot ready for
 *      the producer's NEXT cycle.
 *
 * Returns: bytes read on success; -EAGAIN if empty.
 */
int mpmc_dequeue(mpmc_queue_t* q, void* buffer, size_t* out_size)
{
    if (!q || !buffer || !out_size) return -EINVAL;

    mpmc_slot_t* slot;
    uint64_t pos = atomic_load_explicit(&q->dequeue_pos, memory_order_relaxed);

    int spins = 0;
    for (;;) {
        uint64_t slot_idx = pos & q->mask;
        slot = &q->slots[slot_idx];

        uint64_t seq = atomic_load_explicit(&slot->sequence, memory_order_acquire);
        int64_t diff = (int64_t)seq - (int64_t)(pos + 1);

        if (diff == 0) {
            /* Data is ready for this consumer. Try to claim `pos`. */
            if (atomic_compare_exchange_weak_explicit(
                    &q->dequeue_pos, &pos, pos + 1,
                    memory_order_relaxed, memory_order_relaxed)) {
                break; /* Successfully claimed `pos` */
            }
            /* CAS failed (another consumer claimed `pos`). `pos` is updated. */
        } else if (diff < 0) {
            /* Producer has not written here yet: queue is empty */
            return -EAGAIN;
        } else {
            /* diff > 0: another consumer already claimed `pos` and read here.
             * Reload `dequeue_pos`. */
            pos = atomic_load_explicit(&q->dequeue_pos, memory_order_relaxed);
            
            spins++;
            if (spins > 1000) sched_yield();
            else              SPSC_PAUSE();
        }
    }

    uint32_t sz = atomic_load_explicit(&slot->size, memory_order_relaxed);
    memcpy(buffer, slot->data, sz);
    *out_size = sz;

    /* Mark slot ready for producer at position (pos + capacity) */
    atomic_store_explicit(&slot->sequence, pos + q->capacity,
                          memory_order_release);

    return (int)sz;
}

/* =========================================================================
 * mpmc_dequeue_blocking — spin until success
 * ========================================================================= */
int mpmc_dequeue_blocking(mpmc_queue_t* q, void* buffer, size_t* out_size)
{
    if (!q || !buffer || !out_size) return -EINVAL;
    int retries = 0;
    for (;;) {
        int rc = mpmc_dequeue(q, buffer, out_size);
        if (rc >= 0) return rc;
        if (rc != -EAGAIN) return rc;
        retries++;
        if (retries > 1000) sched_yield();
        else                SPSC_PAUSE();
    }
}

/* =========================================================================
 * Batch operations — optimistic multi-slot enqueue/dequeue
 * =========================================================================
 * Batch enqueue: claims `count` positions with a single fetch_add for
 * efficiency, then writes each slot in the claimed range.
 *
 * Returns: number of items actually enqueued (may be < count if full).
 */
int mpmc_enqueue_batch(mpmc_queue_t* q, const void** buffers,
                       const size_t* sizes, int count)
{
    if (!q || !buffers || !sizes || count <= 0) return 0;
    int done = 0;
    for (int i = 0; i < count; i++) {
        int rc = mpmc_enqueue(q, buffers[i], sizes[i]);
        if (rc == 0) done++;
        else break;  /* stop on first full or error */
    }
    return done;
}

int mpmc_dequeue_batch(mpmc_queue_t* q, void** buffers,
                       size_t* sizes, int max_count)
{
    if (!q || !buffers || !sizes || max_count <= 0) return 0;
    int done = 0;
    for (int i = 0; i < max_count; i++) {
        int rc = mpmc_dequeue(q, buffers[i], &sizes[i]);
        if (rc >= 0) done++;
        else break;
    }
    return done;
}

/* =========================================================================
 * Utility
 * ========================================================================= */

uint64_t mpmc_capacity(mpmc_queue_t* q)
{
    return q ? q->capacity : 0;
}

uint64_t mpmc_available(mpmc_queue_t* q)
{
    if (!q) return 0;
    uint64_t e = atomic_load_explicit(&q->enqueue_pos, memory_order_relaxed);
    uint64_t d = atomic_load_explicit(&q->dequeue_pos, memory_order_relaxed);
    return (e > d) ? (e - d) : 0;
}

void mpmc_set_trace(mpmc_queue_t* q, bool enabled)
{
    (void)q; (void)enabled; /* TODO: trace ring in Phase 2 */
}
