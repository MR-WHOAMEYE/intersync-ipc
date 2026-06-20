/*
 * condvar_lock.c
 * InterSync — SYNC_CONDVAR implementation (vtable model)
 *
 * Exposes:  sync_condvar_open()
 * Bundles a priority-inherit mutex + pthread_cond_t (CLOCK_MONOTONIC).
 */

#include "libinterync_sync.h"

#include <errno.h>
#include <pthread.h>
#include <stdlib.h>

typedef struct {
    sync_lock_t     base;
    pthread_mutex_t mutex;
    pthread_cond_t  cond;
} condvar_lock_t;

static int  cv_lock_fn    (sync_lock_t* lock);
static int  cv_lock_read  (sync_lock_t* lock);
static int  cv_unlock_fn  (sync_lock_t* lock);
static int  cv_wait_fn    (sync_lock_t* lock);
static int  cv_signal_fn  (sync_lock_t* lock);
static int  cv_broadcast_fn(sync_lock_t* lock);
static void cv_destroy_fn (sync_lock_t* lock);

static const sync_ops_t condvar_ops = {
    .lock      = cv_lock_fn,
    .lock_read = cv_lock_read,
    .unlock    = cv_unlock_fn,
    .wait      = cv_wait_fn,
    .signal    = cv_signal_fn,
    .broadcast = cv_broadcast_fn,
    .destroy   = cv_destroy_fn,
};

sync_lock_t* sync_condvar_open(void)
{
    condvar_lock_t* cl = (condvar_lock_t*)calloc(1, sizeof(*cl));
    if (!cl) return NULL;

    cl->base.type = SYNC_CONDVAR;
    cl->base.ops  = &condvar_ops;

    pthread_mutexattr_t mattr;
    pthread_mutexattr_init(&mattr);
    pthread_mutexattr_setprotocol(&mattr, PTHREAD_PRIO_INHERIT);
    if (pthread_mutex_init(&cl->mutex, &mattr) != 0) {
        pthread_mutexattr_destroy(&mattr); free(cl); errno = ENOMEM; return NULL;
    }
    pthread_mutexattr_destroy(&mattr);

    pthread_condattr_t cattr;
    pthread_condattr_init(&cattr);
    pthread_condattr_setclock(&cattr, CLOCK_MONOTONIC);
    if (pthread_cond_init(&cl->cond, &cattr) != 0) {
        pthread_condattr_destroy(&cattr);
        pthread_mutex_destroy(&cl->mutex);
        free(cl); errno = ENOMEM; return NULL;
    }
    pthread_condattr_destroy(&cattr);
    return &cl->base;
}

static int cv_lock_fn(sync_lock_t* lock)
{
    condvar_lock_t* cl = (condvar_lock_t*)lock;
    sync_trace_log(lock, "WAIT");
    int ret = pthread_mutex_lock(&cl->mutex);
    if (ret) return -ret;
    sync_trace_log(lock, "ACQUIRE");
    return 0;
}

static int cv_lock_read(sync_lock_t* lock) { return cv_lock_fn(lock); }

static int cv_unlock_fn(sync_lock_t* lock)
{
    condvar_lock_t* cl = (condvar_lock_t*)lock;
    int ret = pthread_mutex_unlock(&cl->mutex);
    if (ret) return -ret;
    sync_trace_log(lock, "RELEASE");
    return 0;
}

static int cv_wait_fn(sync_lock_t* lock)
{
    condvar_lock_t* cl = (condvar_lock_t*)lock;
    sync_trace_log(lock, "WAIT");
    int ret = pthread_cond_wait(&cl->cond, &cl->mutex);
    if (ret) return -ret;
    sync_trace_log(lock, "ACQUIRE");
    return 0;
}

static int cv_signal_fn(sync_lock_t* lock)
{
    condvar_lock_t* cl = (condvar_lock_t*)lock;
    int ret = pthread_cond_signal(&cl->cond);
    return ret ? -ret : 0;
}

static int cv_broadcast_fn(sync_lock_t* lock)
{
    condvar_lock_t* cl = (condvar_lock_t*)lock;
    int ret = pthread_cond_broadcast(&cl->cond);
    return ret ? -ret : 0;
}

static void cv_destroy_fn(sync_lock_t* lock)
{
    condvar_lock_t* cl = (condvar_lock_t*)lock;
    pthread_cond_destroy(&cl->cond);
    pthread_mutex_destroy(&cl->mutex);
    free(cl);
}
