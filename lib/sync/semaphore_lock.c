/*
 * semaphore_lock.c
 * InterSync — SYNC_SEMAPHORE implementation (vtable model)
 *
 * Exposes:  sync_semaphore_open()
 * POSIX unnamed semaphore, value=1 (binary).
 */

#include "libinterync_sync.h"

#include <errno.h>
#include <semaphore.h>
#include <stdlib.h>

typedef struct {
    sync_lock_t base;
    sem_t       sem;
} semaphore_lock_t;

static int  sem_lock_fn    (sync_lock_t* lock);
static int  sem_lock_read  (sync_lock_t* lock);
static int  sem_unlock_fn  (sync_lock_t* lock);
static int  sem_wait_fn    (sync_lock_t* lock);
static int  sem_signal_fn  (sync_lock_t* lock);
static int  sem_broadcast_fn(sync_lock_t* lock);
static void sem_destroy_fn (sync_lock_t* lock);

static const sync_ops_t semaphore_ops = {
    .lock      = sem_lock_fn,
    .lock_read = sem_lock_read,
    .unlock    = sem_unlock_fn,
    .wait      = sem_wait_fn,
    .signal    = sem_signal_fn,
    .broadcast = sem_broadcast_fn,
    .destroy   = sem_destroy_fn,
};

sync_lock_t* sync_semaphore_open(void)
{
    semaphore_lock_t* sl = (semaphore_lock_t*)calloc(1, sizeof(*sl));
    if (!sl) return NULL;

    sl->base.type = SYNC_SEMAPHORE;
    sl->base.ops  = &semaphore_ops;

    if (sem_init(&sl->sem, 0, 1) != 0) {
        int saved = errno;
        free(sl);
        errno = saved;
        return NULL;
    }
    return &sl->base;
}

static int sem_lock_fn(sync_lock_t* lock)
{
    semaphore_lock_t* sl = (semaphore_lock_t*)lock;
    sync_trace_log(lock, "WAIT");
    int ret;
    do { ret = sem_wait(&sl->sem); } while (ret < 0 && errno == EINTR);
    if (ret < 0) return -errno;
    sync_trace_log(lock, "ACQUIRE");
    return 0;
}

static int sem_lock_read(sync_lock_t* lock) { return sem_lock_fn(lock); }

static int sem_unlock_fn(sync_lock_t* lock)
{
    semaphore_lock_t* sl = (semaphore_lock_t*)lock;
    if (sem_post(&sl->sem) < 0) return -errno;
    sync_trace_log(lock, "RELEASE");
    return 0;
}

static int  sem_wait_fn    (sync_lock_t* l) { (void)l; return -EINVAL; }
static int  sem_signal_fn  (sync_lock_t* l) { (void)l; return -EINVAL; }
static int  sem_broadcast_fn(sync_lock_t* l) { (void)l; return -EINVAL; }

static void sem_destroy_fn(sync_lock_t* lock)
{
    semaphore_lock_t* sl = (semaphore_lock_t*)lock;
    sem_destroy(&sl->sem);
    free(sl);
}
