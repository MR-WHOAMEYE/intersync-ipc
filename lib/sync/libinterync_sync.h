/*
 * libinterync_sync.h  (vtable model)
 * InterSync: IPC & Synchronization Platform
 *
 * Public API defined in sync_factory.c; each .c file exposes uniquely-named
 * open functions (sync_mutex_open, sync_semaphore_open, …).
 *
 * Lock-trace log: /tmp/interync_lock_trace.log
 * Format: <timestamp_ns>,<pid>,<lock_ptr_hex>,<ACQUIRE|RELEASE|WAIT>
 */

#ifndef LIBINTERYNC_SYNC_H
#define LIBINTERYNC_SYNC_H

#include <stdint.h>
#include <sys/types.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    SYNC_MUTEX     = 0,
    SYNC_SEMAPHORE = 1,
    SYNC_CONDVAR   = 2,
    SYNC_RWLOCK    = 3
} sync_type_t;

typedef struct sync_lock sync_lock_t;

typedef struct {
    int  (*lock)     (sync_lock_t* lock);
    int  (*lock_read)(sync_lock_t* lock);
    int  (*unlock)   (sync_lock_t* lock);
    int  (*wait)     (sync_lock_t* lock);
    int  (*signal)   (sync_lock_t* lock);
    int  (*broadcast)(sync_lock_t* lock);
    void (*destroy)  (sync_lock_t* lock);
} sync_ops_t;

struct sync_lock {
    sync_type_t       type;
    const sync_ops_t* ops;
};

/* Internal open hooks — implemented in each .c file */
sync_lock_t* sync_mutex_open(void);
sync_lock_t* sync_semaphore_open(void);
sync_lock_t* sync_condvar_open(void);
sync_lock_t* sync_rwlock_open(void);

/* Default capacity per log file for rotation */
#define INTSYNC_TRACE_CAP_DEFAULT 10000

/* Trace files used for rotating log */
#define INTSYNC_TRACE_FILE_0 "/tmp/interync_lock_trace.0.log"
#define INTSYNC_TRACE_FILE_1 "/tmp/interync_lock_trace.1.log"

// ---------------------------------------------------------
// Lock Data Structures (Black-Boxed via handles)
// ---------------------------------------------------------

/* Public API — implemented in sync_factory.c */
sync_lock_t* sync_create   (sync_type_t type);
int          sync_lock      (sync_lock_t* lock);
int          sync_lock_read (sync_lock_t* lock);
int          sync_unlock    (sync_lock_t* lock);
int          sync_wait      (sync_lock_t* lock);
int          sync_signal    (sync_lock_t* lock);
int          sync_broadcast (sync_lock_t* lock);
void         sync_destroy   (sync_lock_t* lock);
const char*  sync_type_name (sync_type_t type);

/* Trace log helper — defined in mutex_lock.c, used by all .c files */
void sync_trace_log(const void* lock_ptr, const char* event);

#ifdef __cplusplus
}
#endif
#endif /* LIBINTERYNC_SYNC_H */
