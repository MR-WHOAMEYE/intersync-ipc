/*
 * test_ipc_sync.c
 * InterSync — C library smoke test
 *
 * Exercises all four IPC mechanisms and all four sync primitives through
 * their full create → use → destroy lifecycle.
 *
 * Compile and run inside WSL2 / Linux:
 *
 *   make test-libs
 *
 * or manually:
 *   gcc -Wall -Wextra -o build/test_ipc_sync lib/ipc/test_ipc_sync.c \
 *       -Ilib/ipc -Ilib/sync -Lbuild \
 *       -linterync-ipc -linterync-sync \
 *       -Wl,-rpath,build -lrt -pthread
 *   LD_LIBRARY_PATH=build ./build/test_ipc_sync
 *
 * Expected: all lines prefixed with ✓, then "All tests PASSED."
 */

#include <assert.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <pthread.h>
#include <mqueue.h>
#include <sys/mman.h>

#include "libinterync_ipc.h"
#include "libinterync_sync.h"

/* -------------------------------------------------------------------------
 * Helpers
 * ---------------------------------------------------------------------- */
#define PASS(label)  printf("  \033[32m\xe2\x9c\x93\033[0m %s\n", (label))
#define FAIL(label, err) \
    do { fprintf(stderr, "  \033[31m\xe2\x9c\x97\033[0m %s (err=%d)\n", \
                 (label), (err)); exit(1); } while (0)

static const char* MSG    = "Hello, InterSync!";
static const size_t MSGLEN = 17;

/* -------------------------------------------------------------------------
 * IPC tests
 * ---------------------------------------------------------------------- */
static void test_pipe(void)
{
    ipc_channel_t* ch = ipc_create(IPC_PIPE, "test-pipe");
    if (!ch) FAIL("IPC PIPE create", (int)errno);

    if (ipc_send(ch, MSG, MSGLEN) < 0) FAIL("IPC PIPE send", 0);

    char buf[64] = {0};
    int n = ipc_receive(ch, buf, sizeof(buf));
    if (n < 0) FAIL("IPC PIPE receive", n);
    assert((size_t)n == MSGLEN && memcmp(buf, MSG, MSGLEN) == 0);

    ipc_destroy(ch);
    PASS("IPC PIPE  create / send / receive / destroy");
}

static void test_queue(void)
{
    /* Pre-clean any stale mq from a previous crashed run */
    mq_unlink("/interync-test-q");

    ipc_channel_t* ch = ipc_create(IPC_QUEUE, "/interync-test-q");
    if (!ch) FAIL("IPC QUEUE create", (int)errno);

    if (ipc_send(ch, MSG, MSGLEN) < 0) FAIL("IPC QUEUE send", 0);

    char buf[8192] = {0};   /* mq requires buffer >= mq_msgsize */
    int n = ipc_receive(ch, buf, sizeof(buf));
    if (n < 0) FAIL("IPC QUEUE receive", n);
    assert((size_t)n == MSGLEN && memcmp(buf, MSG, MSGLEN) == 0);

    ipc_destroy(ch);
    PASS("IPC QUEUE create / send / receive / destroy");
}

static void test_socket(void)
{
    ipc_channel_t* ch = ipc_create(IPC_SOCKET, "interync-test");
    if (!ch) FAIL("IPC SOCKET create", (int)errno);

    if (ipc_send(ch, MSG, MSGLEN) < 0) FAIL("IPC SOCKET send", 0);

    char buf[64] = {0};
    int n = ipc_receive(ch, buf, sizeof(buf));
    if (n < 0) FAIL("IPC SOCKET receive", n);
    assert((size_t)n == MSGLEN && memcmp(buf, MSG, MSGLEN) == 0);

    ipc_destroy(ch);
    PASS("IPC SOCKET create / send / receive / destroy");
}

static void test_shm(void)
{
    /* Pre-clean stale shm */
    shm_unlink("/interync-test-shm");

    ipc_channel_t* ch = ipc_create(IPC_SHM, "/interync-test-shm");
    if (!ch) FAIL("IPC SHM create", (int)errno);

    if (ipc_send(ch, MSG, MSGLEN) < 0) FAIL("IPC SHM send", 0);

    char buf[64] = {0};
    int n = ipc_receive(ch, buf, sizeof(buf));
    if (n < 0) FAIL("IPC SHM receive", n);
    assert(memcmp(buf, MSG, MSGLEN) == 0);

    ipc_destroy(ch);
    PASS("IPC SHM   create / send / receive / destroy");
}

/* -------------------------------------------------------------------------
 * Sync tests
 * ---------------------------------------------------------------------- */
static void test_mutex(void)
{
    sync_lock_t* lock = sync_create(SYNC_MUTEX);
    if (!lock) FAIL("SYNC MUTEX create", (int)errno);

    if (sync_lock(lock)   < 0) FAIL("SYNC MUTEX lock",   0);
    if (sync_unlock(lock) < 0) FAIL("SYNC MUTEX unlock", 0);

    sync_destroy(lock);
    PASS("SYNC MUTEX   create / lock / unlock / destroy");
}

static void test_semaphore(void)
{
    sync_lock_t* lock = sync_create(SYNC_SEMAPHORE);
    if (!lock) FAIL("SYNC SEMAPHORE create", (int)errno);

    if (sync_lock(lock)   < 0) FAIL("SYNC SEMAPHORE lock",   0);
    if (sync_unlock(lock) < 0) FAIL("SYNC SEMAPHORE unlock", 0);

    sync_destroy(lock);
    PASS("SYNC SEMAPHORE create / lock / unlock / destroy");
}

/* Condvar: helper thread signals after 10 ms */
typedef struct { sync_lock_t* lock; int* flag; } cv_arg_t;

static void* cv_signaller(void* arg)
{
    cv_arg_t* a = (cv_arg_t*)arg;
    usleep(10000);
    sync_lock(a->lock);
    *(a->flag) = 1;
    sync_signal(a->lock);
    sync_unlock(a->lock);
    return NULL;
}

static void test_condvar(void)
{
    sync_lock_t* lock = sync_create(SYNC_CONDVAR);
    if (!lock) FAIL("SYNC CONDVAR create", (int)errno);

    int flag = 0;
    cv_arg_t arg = { lock, &flag };
    pthread_t tid;
    pthread_create(&tid, NULL, cv_signaller, &arg);

    sync_lock(lock);
    while (!flag) sync_wait(lock);
    sync_unlock(lock);

    pthread_join(tid, NULL);
    assert(flag == 1);

    sync_destroy(lock);
    PASS("SYNC CONDVAR  create / lock / wait / signal / unlock / destroy");
}

static void test_rwlock(void)
{
    sync_lock_t* lock = sync_create(SYNC_RWLOCK);
    if (!lock) FAIL("SYNC RWLOCK create", (int)errno);

    if (sync_lock_read(lock) < 0) FAIL("SYNC RWLOCK rdlock",    0);
    if (sync_unlock(lock)    < 0) FAIL("SYNC RWLOCK unlock(r)", 0);

    if (sync_lock(lock)      < 0) FAIL("SYNC RWLOCK wrlock",    0);
    if (sync_unlock(lock)    < 0) FAIL("SYNC RWLOCK unlock(w)", 0);

    sync_destroy(lock);
    PASS("SYNC RWLOCK   create / rdlock / wrlock / unlock / destroy");
}

/* -------------------------------------------------------------------------
 * main
 * ---------------------------------------------------------------------- */
int main(void)
{
    printf("\n=== InterSync C Library Smoke Test ===\n\n");

    printf("IPC mechanisms:\n");
    test_pipe();
    test_queue();
    test_socket();
    test_shm();

    printf("\nSync primitives:\n");
    test_mutex();
    test_semaphore();
    test_condvar();
    test_rwlock();

    printf("\n\033[32mAll tests PASSED.\033[0m\n\n");

    if (access("/tmp/interync_lock_trace.log", F_OK) == 0)
        printf("Lock trace log: /tmp/interync_lock_trace.log\n");

    return 0;
}
