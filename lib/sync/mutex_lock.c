/*
 * mutex_lock.c
 * InterSync — SYNC_MUTEX implementation (vtable model)
 *
 * Exposes:  sync_mutex_open()
 *           sync_trace_log()  ← used by ALL sync .c files
 *
 * Priority-inheritance pthread mutex with PTHREAD_MUTEX_ERRORCHECK.
 * Every lock/unlock appends to /tmp/interync_lock_trace.log.
 */

#include "libinterync_sync.h"

#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#include <stdatomic.h>

#define TRACE_LOG_PATH_DEFAULT "/tmp/interync_lock_trace.log"

static atomic_int _trace_line_count = 0;
static atomic_int _trace_file_index = 0;
static atomic_int _trace_cap_inited = 0;
static int _trace_cap = INTSYNC_TRACE_CAP_DEFAULT;

/* -------------------------------------------------------------------------
 * sync_trace_log_rotating — exported symbol, called by all four .c files
 * ---------------------------------------------------------------------- */
void sync_trace_log_rotating(const void* lock_ptr, const char* event)
{
    if (!atomic_load(&_trace_cap_inited)) {
        const char* env_cap = getenv("INTSYNC_TRACE_CAP");
        if (env_cap) {
            _trace_cap = atoi(env_cap);
            if (_trace_cap <= 0) _trace_cap = INTSYNC_TRACE_CAP_DEFAULT;
        }
        atomic_store(&_trace_cap_inited, 1);
    }

    int current_line = atomic_fetch_add(&_trace_line_count, 1);
    if (current_line >= _trace_cap) {
        /* Swap files and reset counter */
        atomic_fetch_xor(&_trace_file_index, 1);
        atomic_store(&_trace_line_count, 0);
        current_line = 0;
        
        /* Truncate the new file we're swapping to */
        int idx = atomic_load(&_trace_file_index);
        const char* trunc_path = (idx == 0) ? INTSYNC_TRACE_FILE_0 : INTSYNC_TRACE_FILE_1;
        int tfd = open(trunc_path, O_WRONLY | O_CREAT | O_TRUNC, 0644);
        if (tfd >= 0) close(tfd);
    }

    struct timespec ts;
    clock_gettime(CLOCK_REALTIME, &ts);
    uint64_t ns = (uint64_t)ts.tv_sec * 1000000000ULL + (uint64_t)ts.tv_nsec;

    char line[128];
    int len = snprintf(line, sizeof(line),
                       "%llu,%d,%p,%s\n",
                       (unsigned long long)ns,
                       (int)getpid(),
                       lock_ptr,
                       event);

    int idx = atomic_load(&_trace_file_index);
    const char* target_path = (idx == 0) ? INTSYNC_TRACE_FILE_0 : INTSYNC_TRACE_FILE_1;

    int fd = open(target_path,
                  O_WRONLY | O_CREAT | O_APPEND,
                  S_IRUSR | S_IWUSR | S_IRGRP | S_IROTH);
    if (fd >= 0) {
        (void)write(fd, line, (size_t)len);
        close(fd);
    }
}

void sync_trace_log(const void* lock_ptr, const char* event)
{
    sync_trace_log_rotating(lock_ptr, event);
}

/* -------------------------------------------------------------------------
 * Mutex-specific struct
 * ---------------------------------------------------------------------- */
typedef struct {
    sync_lock_t     base;   /* MUST be first */
    pthread_mutex_t mutex;
} mutex_lock_t;

static int  mutex_lock_fn    (sync_lock_t* lock);
static int  mutex_lock_read  (sync_lock_t* lock);
static int  mutex_unlock_fn  (sync_lock_t* lock);
static int  mutex_wait_fn    (sync_lock_t* lock);
static int  mutex_signal_fn  (sync_lock_t* lock);
static int  mutex_broadcast_fn(sync_lock_t* lock);
static void mutex_destroy_fn (sync_lock_t* lock);

static const sync_ops_t mutex_ops = {
    .lock      = mutex_lock_fn,
    .lock_read = mutex_lock_read,
    .unlock    = mutex_unlock_fn,
    .wait      = mutex_wait_fn,
    .signal    = mutex_signal_fn,
    .broadcast = mutex_broadcast_fn,
    .destroy   = mutex_destroy_fn,
};

sync_lock_t* sync_mutex_open(void)
{
    mutex_lock_t* ml = (mutex_lock_t*)calloc(1, sizeof(*ml));
    if (!ml) return NULL;

    ml->base.type = SYNC_MUTEX;
    ml->base.ops  = &mutex_ops;

    pthread_mutexattr_t attr;
    if (pthread_mutexattr_init(&attr) != 0) goto fail;
    if (pthread_mutexattr_setprotocol(&attr, PTHREAD_PRIO_INHERIT) != 0) {
        pthread_mutexattr_destroy(&attr); goto fail;
    }
    if (pthread_mutexattr_settype(&attr, PTHREAD_MUTEX_ERRORCHECK) != 0) {
        pthread_mutexattr_destroy(&attr); goto fail;
    }
    if (pthread_mutex_init(&ml->mutex, &attr) != 0) {
        pthread_mutexattr_destroy(&attr); goto fail;
    }
    pthread_mutexattr_destroy(&attr);
    return &ml->base;

fail:
    free(ml);
    errno = ENOMEM;
    return NULL;
}

static int mutex_lock_fn(sync_lock_t* lock)
{
    mutex_lock_t* ml = (mutex_lock_t*)lock;
    sync_trace_log(lock, "WAIT");
    int ret = pthread_mutex_lock(&ml->mutex);
    if (ret) return -ret;
    sync_trace_log(lock, "ACQUIRE");
    return 0;
}

static int mutex_lock_read(sync_lock_t* lock)
{
    return mutex_lock_fn(lock);
}

static int mutex_unlock_fn(sync_lock_t* lock)
{
    mutex_lock_t* ml = (mutex_lock_t*)lock;
    int ret = pthread_mutex_unlock(&ml->mutex);
    if (ret) return -ret;
    sync_trace_log(lock, "RELEASE");
    return 0;
}

static int mutex_wait_fn    (sync_lock_t* l) { (void)l; return -EINVAL; }
static int mutex_signal_fn  (sync_lock_t* l) { (void)l; return -EINVAL; }
static int mutex_broadcast_fn(sync_lock_t* l) { (void)l; return -EINVAL; }

static void mutex_destroy_fn(sync_lock_t* lock)
{
    mutex_lock_t* ml = (mutex_lock_t*)lock;
    pthread_mutex_destroy(&ml->mutex);
    free(ml);
}
