/*
 * sync_interactive.c
 * InterSync — interactive Sync helper binary
 *
 * Thin CLI wrapper over libinterync-sync.so.
 * All results are written as JSON to stdout.
 * Errors are written as JSON to stderr with a non-zero exit code.
 *
 * Usage:
 *   sync_interactive acquire      MUTEX|SEMAPHORE|CONDVAR|RWLOCK <lock_name>
 *   sync_interactive acquire_read RWLOCK <lock_name>
 *   sync_interactive release      <lock_handle_hex>
 *   sync_interactive stress       MUTEX|SEMAPHORE|RWLOCK <threads> <iterations>
 *   sync_interactive deadlock     MUTEX|SEMAPHORE <num_threads>
 *   sync_interactive kill         <pid>
 *
 * JSON output (acquire):
 *   {"op":"acquire","primitive":"MUTEX","lock_name":"fork-1",
 *    "lock_handle":"0x7f3a10","holder_pid":1234,
 *    "acquire_time_us":45.2,"error":null}
 *
 * JSON output (stress):
 *   {"op":"stress","primitive":"MUTEX","threads":8,"iterations":100,
 *    "total_time_us":98000.0,"contention_events":321,"error":null}
 *
 * Build:
 *   gcc -Wall -O2 -o build/sync_interactive lib/sync/sync_interactive.c \
 *       -Ilib/sync -Lbuild -linterync-sync -pthread \
 *       -Wl,-rpath,/opt/interync/lib
 */

#define _GNU_SOURCE
#include "libinterync_sync.h"

#include <errno.h>
#include <pthread.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

/* --------------------------------------------------------------------------
 * Timing helpers
 * -------------------------------------------------------------------------- */
static inline double now_us(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (double)ts.tv_sec * 1e6 + (double)ts.tv_nsec / 1e3;
}

/* --------------------------------------------------------------------------
 * Primitive name → enum
 * -------------------------------------------------------------------------- */
static sync_type_t parse_primitive(const char *name)
{
    if (strcmp(name, "MUTEX")     == 0) return SYNC_MUTEX;
    if (strcmp(name, "SEMAPHORE") == 0) return SYNC_SEMAPHORE;
    if (strcmp(name, "CONDVAR")   == 0) return SYNC_CONDVAR;
    if (strcmp(name, "RWLOCK")    == 0) return SYNC_RWLOCK;
    fprintf(stderr,
            "{\"error\":\"Unknown primitive '%s'. Use MUTEX|SEMAPHORE|CONDVAR|RWLOCK\"}\n",
            name);
    exit(2);
}

/* --------------------------------------------------------------------------
 * acquire
 * -------------------------------------------------------------------------- */
static int cmd_acquire(sync_type_t type, const char *prim, const char *lock_name)
{
    sync_lock_t *lock = sync_create(type);
    if (!lock) {
        char buf[128];
        snprintf(buf, sizeof(buf), "sync_create failed: %s", strerror(errno));
        fprintf(stderr, "{\"op\":\"acquire\",\"error\":\"%s\"}\n", buf);
        return 1;
    }

    double t0 = now_us();
    int rc = sync_lock(lock);
    double acq_us = now_us() - t0;

    if (rc != 0) {
        sync_destroy(lock);
        fprintf(stderr,
                "{\"op\":\"acquire\",\"error\":\"sync_lock returned %d\"}\n", rc);
        return 1;
    }

    printf("{\"op\":\"acquire\",\"primitive\":\"%s\",\"lock_name\":\"%s\","
           "\"lock_handle\":\"%p\",\"holder_pid\":%d,"
           "\"acquire_time_us\":%.2f,\"error\":null}\n",
           prim, lock_name, (void *)lock, (int)getpid(), acq_us);

    /* Keep the lock open — the calling Python code will run release next.
     * We persist the handle by printing it; Python parses it and calls release. */
    /* Note: for a real persistent session, use a daemon. Here we hold
     * the lock until the process exits, then output and flush. */
    fflush(stdout);
    /* Block until SIGTERM so the dashboard can release it cleanly. */
    pause();
    sync_unlock(lock);
    sync_destroy(lock);
    return 0;
}

/* --------------------------------------------------------------------------
 * acquire_read (RWLOCK shared read)
 * -------------------------------------------------------------------------- */
static int cmd_acquire_read(const char *lock_name)
{
    sync_lock_t *lock = sync_create(SYNC_RWLOCK);
    if (!lock) {
        fprintf(stderr,
                "{\"op\":\"acquire_read\",\"error\":\"sync_create RWLOCK failed: %s\"}\n",
                strerror(errno));
        return 1;
    }

    double t0 = now_us();
    int rc = sync_lock_read(lock);
    double acq_us = now_us() - t0;

    if (rc != 0) {
        sync_destroy(lock);
        fprintf(stderr,
                "{\"op\":\"acquire_read\",\"error\":\"sync_lock_read returned %d\"}\n", rc);
        return 1;
    }

    printf("{\"op\":\"acquire_read\",\"primitive\":\"RWLOCK\",\"lock_name\":\"%s\","
           "\"lock_handle\":\"%p\",\"holder_pid\":%d,"
           "\"acquire_time_us\":%.2f,\"error\":null}\n",
           lock_name, (void *)lock, (int)getpid(), acq_us);
    fflush(stdout);
    pause();
    sync_unlock(lock);
    sync_destroy(lock);
    return 0;
}

/* --------------------------------------------------------------------------
 * release — sends SIGTERM to a running acquire process
 * -------------------------------------------------------------------------- */
static int cmd_release(const char *pid_str)
{
    pid_t pid = (pid_t)atol(pid_str);
    if (pid <= 0) {
        fprintf(stderr, "{\"op\":\"release\",\"error\":\"Invalid PID '%s'\"}\n", pid_str);
        return 1;
    }
    if (kill(pid, SIGTERM) != 0) {
        fprintf(stderr,
                "{\"op\":\"release\",\"pid\":%d,\"error\":\"%s\"}\n",
                pid, strerror(errno));
        return 1;
    }
    printf("{\"op\":\"release\",\"pid\":%d,\"error\":null}\n", pid);
    return 0;
}

/* --------------------------------------------------------------------------
 * stress — N threads each acquire/release M times, measuring contention
 * -------------------------------------------------------------------------- */
typedef struct {
    sync_lock_t *lock;
    int          iterations;
    long         contention_events;
} stress_arg_t;

static void *stress_worker(void *arg)
{
    stress_arg_t *a = (stress_arg_t *)arg;
    for (int i = 0; i < a->iterations; i++) {
        double t0 = now_us();
        sync_lock(a->lock);
        double dt = now_us() - t0;
        if (dt > 100.0) /* >100µs means we waited */
            a->contention_events++;
        /* brief critical section */
        volatile int x = 0;
        for (int j = 0; j < 1000; j++) x++;
        (void)x;
        sync_unlock(a->lock);
    }
    return NULL;
}

static int cmd_stress(sync_type_t type, const char *prim,
                      int num_threads, int iterations)
{
    sync_lock_t *lock = sync_create(type);
    if (!lock) {
        fprintf(stderr,
                "{\"op\":\"stress\",\"error\":\"sync_create failed: %s\"}\n",
                strerror(errno));
        return 1;
    }

    stress_arg_t *args = (stress_arg_t *)calloc(num_threads, sizeof(*args));
    pthread_t    *tids = (pthread_t *)malloc(num_threads * sizeof(pthread_t));
    if (!args || !tids) {
        fprintf(stderr, "{\"op\":\"stress\",\"error\":\"malloc failed\"}\n");
        sync_destroy(lock); free(args); free(tids); return 1;
    }

    double t0 = now_us();
    for (int i = 0; i < num_threads; i++) {
        args[i].lock       = lock;
        args[i].iterations = iterations;
        args[i].contention_events = 0;
        pthread_create(&tids[i], NULL, stress_worker, &args[i]);
    }
    long total_contention = 0;
    for (int i = 0; i < num_threads; i++) {
        pthread_join(tids[i], NULL);
        total_contention += args[i].contention_events;
    }
    double total_us = now_us() - t0;

    free(args); free(tids);
    sync_destroy(lock);

    printf("{\"op\":\"stress\",\"primitive\":\"%s\",\"threads\":%d,"
           "\"iterations\":%d,\"total_time_us\":%.2f,"
           "\"contention_events\":%ld,\"error\":null}\n",
           prim, num_threads, iterations, total_us, total_contention);
    return 0;
}

/* --------------------------------------------------------------------------
 * deadlock — two threads acquire two locks in opposite order
 * -------------------------------------------------------------------------- */
typedef struct {
    sync_lock_t *lock_a;
    sync_lock_t *lock_b;
    int          reverse;   /* if 1: acquire B then A (opposite order) */
} deadlock_arg_t;

static void *deadlock_worker(void *arg)
{
    deadlock_arg_t *a = (deadlock_arg_t *)arg;
    if (!a->reverse) {
        sync_lock(a->lock_a);
        usleep(10000); /* ensure the other thread gets lock_b first */
        sync_lock(a->lock_b); /* will block if other holds it */
        sync_unlock(a->lock_b);
        sync_unlock(a->lock_a);
    } else {
        sync_lock(a->lock_b);
        usleep(10000);
        sync_lock(a->lock_a); /* circular wait → deadlock */
        sync_unlock(a->lock_a);
        sync_unlock(a->lock_b);
    }
    return NULL;
}

static int cmd_deadlock(sync_type_t type, const char *prim, int num_threads)
{
    sync_lock_t *lock_a = sync_create(type);
    sync_lock_t *lock_b = sync_create(type);
    if (!lock_a || !lock_b) {
        fprintf(stderr,
                "{\"op\":\"deadlock\",\"error\":\"sync_create failed\"}\n");
        if (lock_a) sync_destroy(lock_a);
        if (lock_b) sync_destroy(lock_b);
        return 1;
    }

    deadlock_arg_t args[2];
    pthread_t tids[2];
    args[0].lock_a = lock_a; args[0].lock_b = lock_b; args[0].reverse = 0;
    args[1].lock_a = lock_a; args[1].lock_b = lock_b; args[1].reverse = 1;

    printf("{\"op\":\"deadlock\",\"primitive\":\"%s\",\"threads\":%d,"
           "\"pid\":%d,\"status\":\"injecting\",\"error\":null}\n",
           prim, num_threads, (int)getpid());
    fflush(stdout);

    for (int i = 0; i < 2 && i < num_threads; i++)
        pthread_create(&tids[i], NULL, deadlock_worker, &args[i]);

    /* Wait a moment then report cycle — threads will be blocked */
    usleep(200000); /* 200ms — both threads should now be deadlocked */
    printf("{\"op\":\"deadlock\",\"status\":\"deadlocked\","
           "\"cycle_pids\":[%d]}\n", (int)getpid());
    fflush(stdout);

    /* Block indefinitely — dashboard resolves via kill command */
    pause();
    return 0;
}

/* --------------------------------------------------------------------------
 * kill — signals a process to terminate (unlock + exit)
 * -------------------------------------------------------------------------- */
static int cmd_kill(const char *pid_str)
{
    pid_t pid = (pid_t)atol(pid_str);
    if (pid <= 0) {
        fprintf(stderr,
                "{\"op\":\"kill\",\"error\":\"Invalid PID '%s'\"}\n", pid_str);
        return 1;
    }
    if (kill(pid, SIGKILL) != 0) {
        fprintf(stderr,
                "{\"op\":\"kill\",\"pid\":%d,\"error\":\"%s\"}\n",
                pid, strerror(errno));
        return 1;
    }
    printf("{\"op\":\"kill\",\"pid\":%d,\"error\":null}\n", pid);
    return 0;
}

/* --------------------------------------------------------------------------
 * main
 * -------------------------------------------------------------------------- */
int main(int argc, char *argv[])
{
    if (argc < 3) {
        fprintf(stderr,
                "Usage:\n"
                "  sync_interactive acquire      PRIM LOCK_NAME\n"
                "  sync_interactive acquire_read RWLOCK LOCK_NAME\n"
                "  sync_interactive release      PID\n"
                "  sync_interactive stress       PRIM THREADS ITERS\n"
                "  sync_interactive deadlock     PRIM NUM_THREADS\n"
                "  sync_interactive kill         PID\n");
        return 2;
    }

    const char *op = argv[1];

    if (strcmp(op, "acquire") == 0) {
        if (argc < 4) { fprintf(stderr, "acquire requires PRIM LOCK_NAME\n"); return 2; }
        sync_type_t type = parse_primitive(argv[2]);
        return cmd_acquire(type, argv[2], argv[3]);

    } else if (strcmp(op, "acquire_read") == 0) {
        if (argc < 4) { fprintf(stderr, "acquire_read requires RWLOCK LOCK_NAME\n"); return 2; }
        return cmd_acquire_read(argv[3]);

    } else if (strcmp(op, "release") == 0) {
        return cmd_release(argv[2]);

    } else if (strcmp(op, "stress") == 0) {
        if (argc < 5) { fprintf(stderr, "stress requires PRIM THREADS ITERS\n"); return 2; }
        sync_type_t type = parse_primitive(argv[2]);
        return cmd_stress(type, argv[2], atoi(argv[3]), atoi(argv[4]));

    } else if (strcmp(op, "deadlock") == 0) {
        if (argc < 4) { fprintf(stderr, "deadlock requires PRIM NUM_THREADS\n"); return 2; }
        sync_type_t type = parse_primitive(argv[2]);
        return cmd_deadlock(type, argv[2], atoi(argv[3]));

    } else if (strcmp(op, "kill") == 0) {
        return cmd_kill(argv[2]);

    } else {
        fprintf(stderr,
                "{\"error\":\"Unknown op '%s'\"}\n", op);
        return 2;
    }
}
