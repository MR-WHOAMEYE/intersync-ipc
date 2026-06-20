/*
 * rwlock.c
 * InterSync — SYNC_RWLOCK implementation (vtable model)
 *
 * Exposes:  sync_rwlock_open()
 * PTHREAD_RWLOCK_PREFER_WRITER_NONRECURSIVE_NP to avoid writer starvation.
 */

#include "libinterync_sync.h"

#include <errno.h>
#include <pthread.h>
#include <stdlib.h>

typedef struct {
    sync_lock_t      base;
    pthread_rwlock_t rwlock;
} rwlock_lock_t;

static int  rw_lock_fn     (sync_lock_t* lock);
static int  rw_lock_read   (sync_lock_t* lock);
static int  rw_unlock_fn   (sync_lock_t* lock);
static int  rw_wait_fn     (sync_lock_t* lock);
static int  rw_signal_fn   (sync_lock_t* lock);
static int  rw_broadcast_fn(sync_lock_t* lock);
static void rw_destroy_fn  (sync_lock_t* lock);

static const sync_ops_t rwlock_ops = {
    .lock      = rw_lock_fn,
    .lock_read = rw_lock_read,
    .unlock    = rw_unlock_fn,
    .wait      = rw_wait_fn,
    .signal    = rw_signal_fn,
    .broadcast = rw_broadcast_fn,
    .destroy   = rw_destroy_fn,
};

sync_lock_t* sync_rwlock_open(void)
{
    rwlock_lock_t* rl = (rwlock_lock_t*)calloc(1, sizeof(*rl));
    if (!rl) return NULL;

    rl->base.type = SYNC_RWLOCK;
    rl->base.ops  = &rwlock_ops;

    pthread_rwlockattr_t attr;
    pthread_rwlockattr_init(&attr);
#ifdef PTHREAD_RWLOCK_PREFER_WRITER_NONRECURSIVE_NP
    pthread_rwlockattr_setkind_np(&attr,
                                   PTHREAD_RWLOCK_PREFER_WRITER_NONRECURSIVE_NP);
#endif
    if (pthread_rwlock_init(&rl->rwlock, &attr) != 0) {
        pthread_rwlockattr_destroy(&attr);
        free(rl); errno = ENOMEM; return NULL;
    }
    pthread_rwlockattr_destroy(&attr);
    return &rl->base;
}

static int rw_lock_fn(sync_lock_t* lock)
{
    rwlock_lock_t* rl = (rwlock_lock_t*)lock;
    sync_trace_log_rotating(lock, "WAIT");
    int ret = pthread_rwlock_wrlock(&rl->rwlock);
    if (ret) return -ret;
    sync_trace_log_rotating(lock, "ACQUIRE");
    return 0;
}

static int rw_lock_read(sync_lock_t* lock)
{
    rwlock_lock_t* rl = (rwlock_lock_t*)lock;
    sync_trace_log_rotating(lock, "WAIT");
    int ret = pthread_rwlock_rdlock(&rl->rwlock);
    if (ret) return -ret;
    sync_trace_log_rotating(lock, "ACQUIRE");
    return 0;
}

static int rw_unlock_fn(sync_lock_t* lock)
{
    rwlock_lock_t* rl = (rwlock_lock_t*)lock;
    int ret = pthread_rwlock_unlock(&rl->rwlock);
    if (ret) return -ret;
    sync_trace_log_rotating(lock, "RELEASE");
    return 0;
}

static int  rw_wait_fn     (sync_lock_t* l) { (void)l; return -EINVAL; }
static int  rw_signal_fn   (sync_lock_t* l) { (void)l; return -EINVAL; }
static int  rw_broadcast_fn(sync_lock_t* l) { (void)l; return -EINVAL; }

static void rw_destroy_fn(sync_lock_t* lock)
{
    rwlock_lock_t* rl = (rwlock_lock_t*)lock;
    pthread_rwlock_destroy(&rl->rwlock);
    free(rl);
}
