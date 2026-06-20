/*
 * bench_spsc.c
 * InterSync — High-Performance C Benchmark Runner for SPSC/MPMC
 *
 * Runs native multi-threaded benchmarks and outputs JSON for the Python
 * orchestrator to parse, bypassing the Python GIL.
 */

#include "libinterync_spsc.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <pthread.h>
#include <time.h>
#include <stdatomic.h>

/* Helper for nanosecond timing */
static inline uint64_t bench_now_ns(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ULL + (uint64_t)ts.tv_nsec;
}

/* =========================================================================
 * SPSC Latency / Throughput Benchmark
 * ========================================================================= */
typedef struct {
    spsc_ring_buffer_t* rb;
    uint64_t count;
    uint64_t start_ns;
    uint64_t end_ns;
} spsc_bench_arg_t;

static void* spsc_producer_worker(void* arg) {
    spsc_bench_arg_t* a = (spsc_bench_arg_t*)arg;
    uint64_t val = 0;
    a->start_ns = bench_now_ns();
    for (uint64_t i = 0; i < a->count; i++) {
        val = i;
        while (spsc_push_blocking(a->rb, &val, sizeof(val)) != 0) {}
    }
    a->end_ns = bench_now_ns();
    return NULL;
}

static void* spsc_consumer_worker(void* arg) {
    spsc_bench_arg_t* a = (spsc_bench_arg_t*)arg;
    uint64_t val = 0;
    size_t sz;
    a->start_ns = bench_now_ns();
    for (uint64_t i = 0; i < a->count; i++) {
        while (spsc_pop_blocking(a->rb, &val, &sz) < 0) {}
    }
    a->end_ns = bench_now_ns();
    return NULL;
}

static void run_spsc_benchmark(uint64_t count, uint64_t capacity) {
    spsc_ring_buffer_t* rb = spsc_create("bench_spsc", capacity, 16, 0);
    if (!rb) {
        printf("{\"error\": \"failed to create spsc\"}\n");
        return;
    }

    spsc_bench_arg_t p_arg = {rb, count, 0, 0};
    spsc_bench_arg_t c_arg = {rb, count, 0, 0};

    pthread_t pt, ct;
    pthread_create(&pt, NULL, spsc_producer_worker, &p_arg);
    pthread_create(&ct, NULL, spsc_consumer_worker, &c_arg);

    pthread_join(pt, NULL);
    pthread_join(ct, NULL);

    uint64_t p_diff = p_arg.end_ns - p_arg.start_ns;
    uint64_t c_diff = c_arg.end_ns - c_arg.start_ns;
    
    /* Calculate average latency in ns */
    double avg_push_ns = (double)p_diff / count;
    double avg_pop_ns  = (double)c_diff / count;
    
    /* Throughput in msg/sec (based on consumer end time - producer start time) */
    uint64_t total_time = c_arg.end_ns - p_arg.start_ns;
    double throughput = ((double)count / total_time) * 1000000000.0;

    printf("{\n");
    printf("  \"mechanism\": \"spsc_ring_buffer\",\n");
    printf("  \"count\": %llu,\n", (unsigned long long)count);
    printf("  \"capacity\": %llu,\n", (unsigned long long)capacity);
    printf("  \"avg_push_ns\": %.2f,\n", avg_push_ns);
    printf("  \"avg_pop_ns\": %.2f,\n", avg_pop_ns);
    printf("  \"throughput_msgs_sec\": %.2f\n", throughput);
    printf("}\n");

    spsc_destroy(rb);
}

/* =========================================================================
 * MPMC Benchmark
 * ========================================================================= */
typedef struct {
    mpmc_queue_t* q;
    uint64_t count;
    uint64_t start_ns;
    uint64_t end_ns;
} mpmc_bench_arg_t;

static void* mpmc_producer_worker(void* arg) {
    mpmc_bench_arg_t* a = (mpmc_bench_arg_t*)arg;
    uint64_t val = 0;
    a->start_ns = bench_now_ns();
    for (uint64_t i = 0; i < a->count; i++) {
        val = i;
        while (mpmc_enqueue_blocking(a->q, &val, sizeof(val)) != 0) {}
    }
    a->end_ns = bench_now_ns();
    return NULL;
}

static void* mpmc_consumer_worker(void* arg) {
    mpmc_bench_arg_t* a = (mpmc_bench_arg_t*)arg;
    uint64_t val = 0;
    size_t sz;
    a->start_ns = bench_now_ns();
    for (uint64_t i = 0; i < a->count; i++) {
        while (mpmc_dequeue_blocking(a->q, &val, &sz) < 0) {}
    }
    a->end_ns = bench_now_ns();
    return NULL;
}

static void run_mpmc_benchmark(uint64_t total_count, uint64_t capacity, int num_p, int num_c) {
    mpmc_queue_t* q = mpmc_create("bench_mpmc", capacity, 16, 0);
    if (!q) {
        printf("{\"error\": \"failed to create mpmc\"}\n");
        return;
    }

    pthread_t* pts = malloc(num_p * sizeof(pthread_t));
    pthread_t* cts = malloc(num_c * sizeof(pthread_t));
    mpmc_bench_arg_t* p_args = malloc(num_p * sizeof(mpmc_bench_arg_t));
    mpmc_bench_arg_t* c_args = malloc(num_c * sizeof(mpmc_bench_arg_t));

    uint64_t count_per_p = total_count / num_p;
    uint64_t count_per_c = total_count / num_c;

    uint64_t global_start = bench_now_ns();

    for (int i = 0; i < num_p; i++) {
        p_args[i].q = q;
        p_args[i].count = count_per_p;
        pthread_create(&pts[i], NULL, mpmc_producer_worker, &p_args[i]);
    }
    for (int i = 0; i < num_c; i++) {
        c_args[i].q = q;
        c_args[i].count = count_per_c;
        pthread_create(&cts[i], NULL, mpmc_consumer_worker, &c_args[i]);
    }

    for (int i = 0; i < num_p; i++) pthread_join(pts[i], NULL);
    for (int i = 0; i < num_c; i++) pthread_join(cts[i], NULL);

    uint64_t global_end = bench_now_ns();
    uint64_t total_time = global_end - global_start;
    double throughput = ((double)total_count / total_time) * 1000000000.0;
    double avg_latency_ns = (double)total_time / total_count;

    printf("{\n");
    printf("  \"mechanism\": \"mpmc_queue\",\n");
    printf("  \"total_count\": %llu,\n", (unsigned long long)total_count);
    printf("  \"capacity\": %llu,\n", (unsigned long long)capacity);
    printf("  \"producers\": %d,\n", num_p);
    printf("  \"consumers\": %d,\n", num_c);
    printf("  \"avg_latency_ns\": %.2f,\n", avg_latency_ns);
    printf("  \"throughput_msgs_sec\": %.2f\n", throughput);
    printf("}\n");

    free(pts); free(cts); free(p_args); free(c_args);
    mpmc_destroy(q);
}

int main(int argc, char** argv) {
    if (argc < 2) {
        fprintf(stderr, "Usage: %s <mode> [args...]\n", argv[0]);
        fprintf(stderr, "Modes: spsc <count> <capacity>\n");
        fprintf(stderr, "       mpmc <count> <capacity> <producers> <consumers>\n");
        return 1;
    }

    const char* mode = argv[1];

    if (strcmp(mode, "spsc") == 0) {
        if (argc != 4) return 1;
        uint64_t count = strtoull(argv[2], NULL, 10);
        uint64_t cap   = strtoull(argv[3], NULL, 10);
        run_spsc_benchmark(count, cap);
    } 
    else if (strcmp(mode, "mpmc") == 0) {
        if (argc != 6) return 1;
        uint64_t count = strtoull(argv[2], NULL, 10);
        uint64_t cap   = strtoull(argv[3], NULL, 10);
        int p = atoi(argv[4]);
        int c = atoi(argv[5]);
        run_mpmc_benchmark(count, cap, p, c);
    }
    else {
        fprintf(stderr, "Unknown mode: %s\n", mode);
        return 1;
    }

    return 0;
}
