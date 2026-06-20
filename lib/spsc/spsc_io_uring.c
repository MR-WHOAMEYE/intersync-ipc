/*
 * spsc_io_uring.c
 * InterSync — Async notification for SPSC using io_uring (Phase 2)
 *
 * Provides a notification mechanism for consumers that shouldn't busy-wait.
 * If <liburing.h> is not available, these functions gracefully return -ENOSYS.
 */

#include "libinterync_spsc.h"

#include <errno.h>

#if __has_include(<liburing.h>)
#include <liburing.h>
#include <sys/eventfd.h>
#include <unistd.h>
#include <stdlib.h>

/* Internal struct to hide io_uring details from headers */
typedef struct {
    struct io_uring ring;
    int efd;
} uring_ctx_t;

int spsc_async_init(spsc_async_t* ah, spsc_ring_buffer_t* rb) {
    if (!ah || !rb) return -EINVAL;
    
    ah->ring = rb;
    ah->registered = false;
    
    uring_ctx_t* ctx = malloc(sizeof(uring_ctx_t));
    if (!ctx) return -ENOMEM;
    
    if (io_uring_queue_init(8, &ctx->ring, 0) < 0) {
        free(ctx);
        return -ENOSYS;
    }
    
    ctx->efd = eventfd(0, EFD_NONBLOCK | EFD_CLOEXEC);
    if (ctx->efd < 0) {
        io_uring_queue_exit(&ctx->ring);
        free(ctx);
        return -errno;
    }
    
    ah->uring = (void*)ctx;
    ah->registered = true;
    return 0;
}

int spsc_push_async(spsc_async_t* ah, const void* data, size_t size) {
    if (!ah || !ah->registered) return -EINVAL;
    
    int rc = spsc_push(ah->ring, data, size);
    if (rc != 0) return rc;
    
    uring_ctx_t* ctx = (uring_ctx_t*)ah->uring;
    
    /* Notify consumer by writing to eventfd via io_uring */
    struct io_uring_sqe* sqe = io_uring_get_sqe(&ctx->ring);
    if (sqe) {
        static const uint64_t val = 1;
        io_uring_prep_write(sqe, ctx->efd, &val, sizeof(val), 0);
        io_uring_submit(&ctx->ring);
    }
    
    return 0;
}

int spsc_wait_pop(spsc_async_t* ah, void* buffer, size_t* out_size, struct timespec* timeout) {
    if (!ah || !ah->registered) return -EINVAL;
    
    /* Try pop first */
    int rc = spsc_pop(ah->ring, buffer, out_size);
    if (rc >= 0) return rc;
    if (rc != -EAGAIN) return rc;
    
    uring_ctx_t* ctx = (uring_ctx_t*)ah->uring;
    
    struct io_uring_sqe* sqe = io_uring_get_sqe(&ctx->ring);
    if (!sqe) return -EAGAIN;
    
    uint64_t val = 0;
    io_uring_prep_read(sqe, ctx->efd, &val, sizeof(val), 0);
    
    if (timeout) {
        struct __kernel_timespec ts = {
            .tv_sec = timeout->tv_sec,
            .tv_nsec = timeout->tv_nsec
        };
        io_uring_prep_timeout(io_uring_get_sqe(&ctx->ring), &ts, 1, 0);
    }
    
    io_uring_submit(&ctx->ring);
    
    struct io_uring_cqe* cqe;
    int ret = io_uring_wait_cqe(&ctx->ring, &cqe);
    if (ret < 0) return ret;
    
    io_uring_cqe_seen(&ctx->ring, cqe);
    
    /* Try pop again after wake up */
    return spsc_pop(ah->ring, buffer, out_size);
}

void spsc_async_destroy(spsc_async_t* ah) {
    if (!ah || !ah->registered) return;
    
    uring_ctx_t* ctx = (uring_ctx_t*)ah->uring;
    close(ctx->efd);
    io_uring_queue_exit(&ctx->ring);
    free(ctx);
    ah->registered = false;
}

#else

/* Stub implementation if liburing is not installed */
int spsc_async_init(spsc_async_t* ah, spsc_ring_buffer_t* rb) {
    (void)ah; (void)rb;
    return -ENOSYS;
}

int spsc_push_async(spsc_async_t* ah, const void* data, size_t size) {
    (void)ah; (void)data; (void)size;
    return -ENOSYS;
}

int spsc_wait_pop(spsc_async_t* ah, void* buffer, size_t* out_size, struct timespec* timeout) {
    (void)ah; (void)buffer; (void)out_size; (void)timeout;
    return -ENOSYS;
}

void spsc_async_destroy(spsc_async_t* ah) {
    (void)ah;
}

#endif
