/*
 * test_spsc.c
 * InterSync — SPSC ring buffer & MPMC queue unit/stress tests (Phase 1).
 *
 * Tests:
 *   [SPSC-01] Single-thread push/pop round-trip
 *   [SPSC-02] Ring full returns -EAGAIN
 *   [SPSC-03] Ring empty returns -EAGAIN
 *   [SPSC-04] write_cursor monotone (BUG-1 fix verification)
 *   [SPSC-05] Multi-thread producer + consumer (1P:1C, 100k messages)
 *   [SPSC-06] Multi-thread stress with capacity wrap-around
 *   [SPSC-07] Blocking push/pop
 *   [MPMC-01] Single-thread enqueue/dequeue round-trip
 *   [MPMC-02] Multi-thread N producers × M consumers (correctness)
 *   [MPMC-03] Sequence counter correctness (Vyukov invariant)
 *   [MPMC-04] No duplicate or lost messages under contention
 *
 * Build & run (from project root):
 *   make test-spsc
 * Or manually:
 *   gcc -Wall -Wextra -O2 -pthread -fsanitize=thread \
 *       lib/spsc/spsc_ring_buffer.c lib/spsc/mpmc_queue.c \
 *       lib/spsc/test_spsc.c \
 *       -Ilib/spsc -o build/test_spsc && ./build/test_spsc
 */

#include "libinterync_spsc.h"

#include <assert.h>
#include <errno.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

/* =========================================================================
 * Test framework
 * ========================================================================= */

static int  g_pass = 0;
static int  g_fail = 0;

#define TEST(name) \
    do { \
        printf("  %-45s", name); \
        fflush(stdout); \
    } while (0)

#define PASS() \
    do { \
        printf("PASS\n"); \
        g_pass++; \
    } while (0)

#define FAIL(msg) \
    do { \
        printf("FAIL  (%s)\n", msg); \
        g_fail++; \
    } while (0)

#define ASSERT(cond, msg) \
    do { \
        if (!(cond)) { FAIL(msg); return; } \
    } while (0)

static uint64_t now_ns(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ULL + (uint64_t)ts.tv_nsec;
}

/* =========================================================================
 * SPSC tests
 * ========================================================================= */

static void test_spsc_roundtrip(void)
{
    TEST("[SPSC-01] Single-thread push/pop round-trip");

    spsc_ring_buffer_t* rb = spsc_create("test-01", 8, 64, 0);
    ASSERT(rb != NULL, "spsc_create returned NULL");

    const char* msg = "hello-interync";
    size_t      msglen = strlen(msg) + 1;

    int rc = spsc_push(rb, msg, msglen);
    ASSERT(rc == 0, "push failed");

    char   buf[64] = {0};
    size_t sz = 0;
    rc = spsc_pop(rb, buf, &sz);
    ASSERT(rc > 0, "pop returned non-positive");
    ASSERT(sz == msglen, "size mismatch");
    ASSERT(strcmp(buf, msg) == 0, "content mismatch");

    spsc_destroy(rb);
    PASS();
}

static void test_spsc_full(void)
{
    TEST("[SPSC-02] Ring full returns -EAGAIN");

    spsc_ring_buffer_t* rb = spsc_create("test-02", 4, 16, 0);
    ASSERT(rb != NULL, "spsc_create returned NULL");

    uint32_t val = 0;
    int pushed = 0;
    while (spsc_push(rb, &val, sizeof(val)) == 0) {
        val++;
        pushed++;
        if (pushed > 100) break; /* safety guard */
    }
    /* Ring of 4 slots: should push exactly 4 before -EAGAIN */
    ASSERT(pushed == 4, "expected 4 pushes before full");

    int rc = spsc_push(rb, &val, sizeof(val));
    ASSERT(rc == -EAGAIN, "expected -EAGAIN on full ring");

    spsc_destroy(rb);
    PASS();
}

static void test_spsc_empty(void)
{
    TEST("[SPSC-03] Ring empty returns -EAGAIN");

    spsc_ring_buffer_t* rb = spsc_create("test-03", 4, 16, 0);
    ASSERT(rb != NULL, "spsc_create returned NULL");

    char   buf[16];
    size_t sz = 0;
    int rc = spsc_pop(rb, buf, &sz);
    ASSERT(rc == -EAGAIN, "expected -EAGAIN on empty ring");

    spsc_destroy(rb);
    PASS();
}

static void test_spsc_cursor_monotone(void)
{
    TEST("[SPSC-04] write_cursor is monotone (BUG-1 fix)");

    /* With capacity=4 (mask=3), after 5 pushes/pops the write_cursor
     * must be 5 — NOT 1 (which would happen with the buggy idx+1 store). */
    spsc_ring_buffer_t* rb = spsc_create("test-04", 4, 16, 0);
    ASSERT(rb != NULL, "spsc_create returned NULL");

    uint32_t val;
    char     buf[16];
    size_t   sz;

    for (int i = 0; i < 5; i++) {
        val = (uint32_t)i;
        while (spsc_push(rb, &val, sizeof(val)) == -EAGAIN) {
            /* drain one slot */
            (void)spsc_pop(rb, buf, &sz);
        }
    }

    /* Drain remaining */
    while (spsc_pop(rb, buf, &sz) >= 0) {}

    /* write_cursor must be >= 5 (monotone) */
    uint64_t wc = atomic_load_explicit(&rb->write_cursor, memory_order_relaxed);
    ASSERT(wc >= 5, "write_cursor not monotone — BUG-1 regression");

    spsc_destroy(rb);
    PASS();
}

/* ── Multi-thread test helpers ── */

#define MT_MESSAGES   100000
#define MT_CAPACITY   1024

typedef struct {
    spsc_ring_buffer_t* rb;
    uint64_t            count;
    uint64_t            checksum;
} thread_arg_t;

static void* producer_thread(void* arg)
{
    thread_arg_t* a = arg;
    for (uint64_t i = 0; i < a->count; i++) {
        while (spsc_push_blocking(a->rb, &i, sizeof(i)) != 0) {}
        a->checksum ^= i;
    }
    return NULL;
}

static void* consumer_thread(void* arg)
{
    thread_arg_t* a = arg;
    size_t sz;
    for (uint64_t i = 0; i < a->count; i++) {
        uint64_t val = 0;
        while (spsc_pop_blocking(a->rb, &val, &sz) < 0) {}
        a->checksum ^= val;
    }
    return NULL;
}

static void test_spsc_multithread(void)
{
    TEST("[SPSC-05] Multi-thread 1P:1C 100k messages (no loss)");

    spsc_ring_buffer_t* rb = spsc_create("test-05", MT_CAPACITY, 16, 0);
    ASSERT(rb != NULL, "spsc_create returned NULL");

    thread_arg_t prod = { rb, MT_MESSAGES, 0 };
    thread_arg_t cons = { rb, MT_MESSAGES, 0 };

    pthread_t pt, ct;
    pthread_create(&pt, NULL, producer_thread, &prod);
    pthread_create(&ct, NULL, consumer_thread, &cons);
    pthread_join(pt, NULL);
    pthread_join(ct, NULL);

    /* XOR checksums must match: producer XOR'd i for each i,
     * consumer XOR'd each received value — should be equal.             */
    ASSERT(prod.checksum == cons.checksum, "checksum mismatch — messages lost/corrupted");

    spsc_destroy(rb);
    PASS();
}

static void test_spsc_stress(void)
{
    TEST("[SPSC-06] Stress: wrap-around 500k messages, capacity=16");

    spsc_ring_buffer_t* rb = spsc_create("test-06", 16, 16, 0);
    ASSERT(rb != NULL, "spsc_create returned NULL");

    const uint64_t N = 500000;
    thread_arg_t   prod = { rb, N, 0 };
    thread_arg_t   cons = { rb, N, 0 };

    pthread_t pt, ct;
    pthread_create(&pt, NULL, producer_thread, &prod);
    pthread_create(&ct, NULL, consumer_thread, &cons);
    pthread_join(pt, NULL);
    pthread_join(ct, NULL);

    ASSERT(prod.checksum == cons.checksum, "checksum mismatch in stress test");
    spsc_destroy(rb);
    PASS();
}

static void test_spsc_blocking(void)
{
    TEST("[SPSC-07] Blocking push/pop wake up correctly");

    spsc_ring_buffer_t* rb = spsc_create("test-07", 4, 16, 0);
    ASSERT(rb != NULL, "spsc_create returned NULL");

    uint64_t val = 0xDEADBEEF;
    int rc = spsc_push_blocking(rb, &val, sizeof(val));
    ASSERT(rc == 0, "push_blocking failed");

    uint64_t out = 0;
    size_t   sz  = 0;
    rc = spsc_pop_blocking(rb, &out, &sz);
    ASSERT(rc > 0, "pop_blocking failed");
    ASSERT(out == 0xDEADBEEF, "value corrupted");

    spsc_destroy(rb);
    PASS();
}

/* =========================================================================
 * SPSC Phase 2 Tests (Overflow, Grow, Trace)
 * ========================================================================= */

static void test_spsc_overflow(void)
{
    TEST("[SPSC-08] Overflow pool (large messages)");

    spsc_ring_buffer_t* rb = spsc_create("test-08", 4, 16, SPSC_FLAG_OVERFLOW_POOL);
    ASSERT(rb != NULL, "spsc_create returned NULL");

    char large_msg[128];
    memset(large_msg, 'A', sizeof(large_msg));
    large_msg[127] = '\0';

    int rc = spsc_push(rb, large_msg, sizeof(large_msg));
    ASSERT(rc == 0, "push failed for overflow message");

    char buf[128] = {0};
    size_t sz = 0;
    rc = spsc_pop(rb, buf, &sz);
    ASSERT(rc >= 0, "pop failed for overflow message");
    ASSERT(sz == sizeof(large_msg), "size mismatch");
    ASSERT(strcmp(buf, large_msg) == 0, "content mismatch");

    spsc_destroy(rb);
    PASS();
}

static void test_spsc_resize(void)
{
    TEST("[SPSC-09] Dynamic growth (SPSC_FLAG_GROWABLE)");

    spsc_ring_buffer_t* rb = spsc_create("test-09", 4, 16, SPSC_FLAG_GROWABLE);
    ASSERT(rb != NULL, "spsc_create returned NULL");

    uint32_t val1 = 111, val2 = 222;
    spsc_push(rb, &val1, sizeof(val1));
    spsc_push(rb, &val2, sizeof(val2));

    int rc = spsc_resize(rb, 16);
    ASSERT(rc == 0, "spsc_resize failed");

    spsc_ring_buffer_t* new_rb = atomic_load_explicit(&rb->migrating, memory_order_relaxed);
    ASSERT(new_rb != NULL, "migrating pointer not set");
    ASSERT(new_rb->capacity == 16, "new capacity incorrect");

    /* Drain new rb */
    uint32_t out;
    size_t sz;
    rc = spsc_pop(new_rb, &out, &sz);
    ASSERT(rc > 0 && out == 111, "first value missing or corrupted");
    rc = spsc_pop(new_rb, &out, &sz);
    ASSERT(rc > 0 && out == 222, "second value missing or corrupted");

    /* Free both (destroying old frees the migrating new one too) */
    spsc_destroy(rb);
    PASS();
}

/* =========================================================================
 * MPMC tests
 * ========================================================================= */

static void test_mpmc_roundtrip(void)
{
    TEST("[MPMC-01] Single-thread enqueue/dequeue round-trip");

    mpmc_queue_t* q = mpmc_create("mpmc-01", 8, 64, 0);
    ASSERT(q != NULL, "mpmc_create returned NULL");

    uint64_t val = 0xCAFEBABE;
    int rc = mpmc_enqueue(q, &val, sizeof(val));
    ASSERT(rc == 0, "enqueue failed");

    uint64_t out = 0;
    size_t   sz  = 0;
    rc = mpmc_dequeue(q, &out, &sz);
    ASSERT(rc > 0, "dequeue failed");
    ASSERT(out == 0xCAFEBABE, "value corrupted");

    mpmc_destroy(q);
    PASS();
}

/* ── MPMC multi-thread structures ── */

#define MPMC_PRODUCERS  4
#define MPMC_CONSUMERS  4
#define MPMC_PER_PROD   25000   /* each producer sends this many */
#define MPMC_TOTAL      (MPMC_PRODUCERS * MPMC_PER_PROD)

typedef struct {
    mpmc_queue_t*     q;
    uint32_t          id;
    uint64_t          count;
    _Atomic(uint64_t) checksum;
} mpmc_arg_t;

/* Global received-message bitmap for duplicate/loss detection */
static uint8_t g_received[MPMC_TOTAL];
static _Atomic(int) g_receive_count;

static void* mpmc_producer(void* arg)
{
    mpmc_arg_t* a = arg;
    /* Each producer sends values in range [id*PER_PROD, (id+1)*PER_PROD) */
    uint64_t start = (uint64_t)a->id * MPMC_PER_PROD;
    for (uint64_t i = start; i < start + a->count; i++) {
        while (mpmc_enqueue_blocking(a->q, &i, sizeof(i)) != 0) {}
        atomic_fetch_xor_explicit(&a->checksum, i, memory_order_relaxed);
    }
    return NULL;
}

static _Atomic(uint64_t) g_consumer_checksum;

static void* mpmc_consumer(void* arg)
{
    mpmc_arg_t* a = arg;
    uint64_t total_recv = 0;

    /* Each consumer receives MPMC_TOTAL / CONSUMERS messages */
    uint64_t my_share = MPMC_TOTAL / MPMC_CONSUMERS;

    for (uint64_t i = 0; i < my_share; i++) {
        uint64_t val = 0;
        size_t   sz  = 0;
        while (mpmc_dequeue_blocking(a->q, &val, &sz) < 0) {}
        atomic_fetch_xor_explicit(&g_consumer_checksum, val, memory_order_relaxed);
        if (val < MPMC_TOTAL) {
            g_received[val]++;
        }
        total_recv++;
    }
    (void)total_recv;
    return NULL;
}

static void test_mpmc_multithread(void)
{
    TEST("[MPMC-02] Multi-thread 4P:4C 100k messages (no loss/dup)");

    mpmc_queue_t* q = mpmc_create("mpmc-02", 256, 64, 0);
    ASSERT(q != NULL, "mpmc_create returned NULL");

    memset(g_received, 0, sizeof(g_received));
    atomic_store(&g_receive_count, 0);
    atomic_store(&g_consumer_checksum, 0);

    mpmc_arg_t prod_args[MPMC_PRODUCERS];
    mpmc_arg_t cons_args[MPMC_CONSUMERS];
    pthread_t  pts[MPMC_PRODUCERS];
    pthread_t  cts[MPMC_CONSUMERS];

    uint64_t producer_checksum = 0;

    for (int i = 0; i < MPMC_PRODUCERS; i++) {
        prod_args[i].q        = q;
        prod_args[i].id       = (uint32_t)i;
        prod_args[i].count    = MPMC_PER_PROD;
        atomic_store(&prod_args[i].checksum, 0);
        pthread_create(&pts[i], NULL, mpmc_producer, &prod_args[i]);
    }
    for (int i = 0; i < MPMC_CONSUMERS; i++) {
        cons_args[i].q     = q;
        cons_args[i].id    = (uint32_t)i;
        cons_args[i].count = MPMC_TOTAL / MPMC_CONSUMERS;
        atomic_store(&cons_args[i].checksum, 0);
        pthread_create(&cts[i], NULL, mpmc_consumer, &cons_args[i]);
    }

    for (int i = 0; i < MPMC_PRODUCERS; i++) {
        pthread_join(pts[i], NULL);
        producer_checksum ^= atomic_load(&prod_args[i].checksum);
    }
    for (int i = 0; i < MPMC_CONSUMERS; i++) {
        pthread_join(cts[i], NULL);
    }

    uint64_t consumer_checksum = atomic_load(&g_consumer_checksum);

    /* Check for duplicates or losses */
    int errors = 0;
    for (int i = 0; i < MPMC_TOTAL; i++) {
        if (g_received[i] != 1) errors++;
    }

    ASSERT(errors == 0, "messages lost or duplicated");
    ASSERT(producer_checksum == consumer_checksum,
           "checksum mismatch — data corrupted");

    mpmc_destroy(q);
    PASS();
}

static void test_mpmc_sequence_invariant(void)
{
    TEST("[MPMC-03] Sequence counter Vyukov invariant after init");

    mpmc_queue_t* q = mpmc_create("mpmc-03", 8, 16, 0);
    ASSERT(q != NULL, "mpmc_create returned NULL");

    /* After init: seq[i] must equal i */
    int ok = 1;
    for (uint64_t i = 0; i < q->capacity; i++) {
        uint64_t seq = atomic_load_explicit(&q->slots[i].sequence,
                                            memory_order_relaxed);
        if (seq != i) { ok = 0; break; }
    }
    ASSERT(ok, "Vyukov invariant violated at init");

    /* After one enqueue+dequeue cycle: seq[0] must equal capacity */
    uint64_t val = 42;
    size_t   sz  = 0;
    mpmc_enqueue(q, &val, sizeof(val));
    uint64_t out;
    mpmc_dequeue(q, &out, &sz);

    uint64_t seq0 = atomic_load_explicit(&q->slots[0].sequence,
                                         memory_order_relaxed);
    ASSERT(seq0 == q->capacity,
           "seq[0] after 1 cycle != capacity (Vyukov invariant broken)");

    mpmc_destroy(q);
    PASS();
}

static void test_mpmc_no_fetch_add_bug(void)
{
    TEST("[MPMC-04] enqueue_pos advances exactly N times for N enqueues");

    /* If the old fetch_add-in-retry bug were present, enqueue_pos would
     * advance MORE than N times under contention.  We verify it is exactly
     * N in the single-thread case.                                          */
    mpmc_queue_t* q = mpmc_create("mpmc-04", 16, 16, 0);
    ASSERT(q != NULL, "mpmc_create returned NULL");

    const int N = 8;
    uint64_t  val = 0;
    for (int i = 0; i < N; i++) {
        mpmc_enqueue(q, &val, sizeof(val));
        val++;
    }

    uint64_t ep = atomic_load_explicit(&q->enqueue_pos, memory_order_relaxed);
    ASSERT(ep == (uint64_t)N,
           "enqueue_pos != N — fetch_add-in-retry bug present");

    mpmc_destroy(q);
    PASS();
}

/* =========================================================================
 * main
 * ========================================================================= */

int main(void)
{
    printf("\n");
    printf("=================================================================\n");
    printf("  InterSync SPSC/MPMC Test Suite  (Phase 1)\n");
    printf("=================================================================\n\n");

    printf("SPSC Ring Buffer\n");
    printf("----------------\n");
    test_spsc_roundtrip();
    test_spsc_full();
    test_spsc_empty();
    test_spsc_cursor_monotone();

    uint64_t t0 = now_ns();
    test_spsc_multithread();
    uint64_t t1 = now_ns();
    printf("         [1P:1C 100k messages: %.1f ms]\n",
           (t1 - t0) / 1e6);

    t0 = now_ns();
    test_spsc_stress();
    t1 = now_ns();
    printf("         [1P:1C 500k wrap-around: %.1f ms]\n",
           (t1 - t0) / 1e6);

    test_spsc_blocking();
    test_spsc_overflow();
    test_spsc_resize();

    printf("\nMPMC Queue (Vyukov algorithm)\n");
    printf("-----------------------------\n");
    test_mpmc_roundtrip();

    t0 = now_ns();
    test_mpmc_multithread();
    t1 = now_ns();
    printf("         [4P:4C 100k messages: %.1f ms]\n",
           (t1 - t0) / 1e6);

    test_mpmc_sequence_invariant();
    test_mpmc_no_fetch_add_bug();

    printf("\n=================================================================\n");
    printf("  Results: %d passed, %d failed\n", g_pass, g_fail);
    printf("=================================================================\n\n");

    return g_fail > 0 ? 1 : 0;
}
