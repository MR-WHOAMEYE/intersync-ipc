/*
 * sync_factory.c
 * InterSync — public sync API dispatch + factory
 *
 * Defines: sync_create / sync_lock / sync_lock_read / sync_unlock /
 *          sync_wait / sync_signal / sync_broadcast / sync_destroy /
 *          sync_type_name
 *
 * All other sync .c files have uniquely-named symbols — no ODR conflicts.
 */

#include "libinterync_sync.h"
#include <errno.h>
#include <stdlib.h>

sync_lock_t* sync_create(sync_type_t type)
{
    switch (type) {
        case SYNC_MUTEX:     return sync_mutex_open();
        case SYNC_SEMAPHORE: return sync_semaphore_open();
        case SYNC_CONDVAR:   return sync_condvar_open();
        case SYNC_RWLOCK:    return sync_rwlock_open();
        default:
            errno = EINVAL;
            return NULL;
    }
}

#define DISPATCH(fn, lock, ...) \
    do { \
        if (!(lock) || !(lock)->ops || !(lock)->ops->fn) return -EINVAL; \
        return (lock)->ops->fn(lock, ##__VA_ARGS__); \
    } while (0)

int sync_lock     (sync_lock_t* l) { DISPATCH(lock,      l); }
int sync_lock_read(sync_lock_t* l) { DISPATCH(lock_read, l); }
int sync_unlock   (sync_lock_t* l) { DISPATCH(unlock,    l); }
int sync_wait     (sync_lock_t* l) { DISPATCH(wait,      l); }
int sync_signal   (sync_lock_t* l) { DISPATCH(signal,    l); }
int sync_broadcast(sync_lock_t* l) { DISPATCH(broadcast, l); }

void sync_destroy(sync_lock_t* lock)
{
    if (!lock) return;
    if (lock->ops && lock->ops->destroy)
        lock->ops->destroy(lock);
}

const char* sync_type_name(sync_type_t type)
{
    switch (type) {
        case SYNC_MUTEX:     return "MUTEX";
        case SYNC_SEMAPHORE: return "SEMAPHORE";
        case SYNC_CONDVAR:   return "CONDVAR";
        case SYNC_RWLOCK:    return "RWLOCK";
        default:             return "UNKNOWN";
    }
}
