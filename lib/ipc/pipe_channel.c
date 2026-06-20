/*
 * pipe_channel.c
 * InterSync — IPC_PIPE implementation (vtable model)
 *
 * Exposes:  ipc_pipe_open()
 * Called by: ipc_factory.c → ipc_create(IPC_PIPE, …)
 *
 * Uses a pair of anonymous POSIX pipes for bidirectional communication.
 *
 * Compile (as part of libinterync-ipc.so):
 *   gcc -Wall -Wextra -fPIC -c pipe_channel.c -Ilib/ipc -o build/pipe_channel.o
 */

#include "libinterync_ipc.h"

#include <errno.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

/* -------------------------------------------------------------------------
 * Pipe-specific channel struct
 * ---------------------------------------------------------------------- */
typedef struct {
    ipc_channel_t  base;              /* MUST be first member */
    int            pipe_to_receiver[2];  /* fd[0]=read  fd[1]=write */
    int            pipe_to_sender[2];    /* fd[0]=read  fd[1]=write */
} pipe_channel_t;

/* -------------------------------------------------------------------------
 * Forward declarations of static ops
 * ---------------------------------------------------------------------- */
static int  pipe_send   (ipc_channel_t* ch, const void* data, size_t len);
static int  pipe_receive(ipc_channel_t* ch, void* buffer,     size_t len);
static void pipe_destroy(ipc_channel_t* ch);

static const ipc_ops_t pipe_ops = {
    .send    = pipe_send,
    .receive = pipe_receive,
    .destroy = pipe_destroy,
};

/* -------------------------------------------------------------------------
 * ipc_pipe_open
 * ---------------------------------------------------------------------- */
ipc_channel_t* ipc_pipe_open(const char* name)
{
    pipe_channel_t* pc = (pipe_channel_t*)calloc(1, sizeof(*pc));
    if (!pc) return NULL;

    pc->base.type = IPC_PIPE;
    pc->base.ops  = &pipe_ops;
    strncpy(pc->base.name, name ? name : "pipe", sizeof(pc->base.name) - 1);

    pc->pipe_to_receiver[0] = pc->pipe_to_receiver[1] = -1;
    pc->pipe_to_sender[0]   = pc->pipe_to_sender[1]   = -1;

    if (pipe(pc->pipe_to_receiver) != 0) goto fail;
    if (pipe(pc->pipe_to_sender)   != 0) {
        close(pc->pipe_to_receiver[0]);
        close(pc->pipe_to_receiver[1]);
        goto fail;
    }
    return &pc->base;

fail:
    {
        int saved = errno;
        free(pc);
        errno = saved;
        return NULL;
    }
}

/* -------------------------------------------------------------------------
 * send — writes all len bytes into pipe_to_receiver[1]
 * ---------------------------------------------------------------------- */
static int pipe_send(ipc_channel_t* ch, const void* data, size_t len)
{
    pipe_channel_t* pc = (pipe_channel_t*)ch;
    if (!data || len == 0)          return 0;
    if (pc->pipe_to_receiver[1] < 0) return -EBADF;

    const uint8_t* ptr = (const uint8_t*)data;
    size_t rem = len;
    while (rem > 0) {
        ssize_t n = write(pc->pipe_to_receiver[1], ptr, rem);
        if (n < 0) {
            if (errno == EINTR) continue;
            return -errno;
        }
        ptr += (size_t)n;
        rem -= (size_t)n;
    }
    return 0;
}

/* -------------------------------------------------------------------------
 * receive — reads up to len bytes from pipe_to_receiver[0]
 * ---------------------------------------------------------------------- */
static int pipe_receive(ipc_channel_t* ch, void* buffer, size_t len)
{
    pipe_channel_t* pc = (pipe_channel_t*)ch;
    if (!buffer || len == 0)         return 0;
    if (pc->pipe_to_receiver[0] < 0) return -EBADF;

    ssize_t n;
    do { n = read(pc->pipe_to_receiver[0], buffer, len); }
    while (n < 0 && errno == EINTR);

    return (n < 0) ? -errno : (int)n;
}

/* -------------------------------------------------------------------------
 * destroy
 * ---------------------------------------------------------------------- */
static void pipe_destroy(ipc_channel_t* ch)
{
    pipe_channel_t* pc = (pipe_channel_t*)ch;
    for (int i = 0; i < 2; i++) {
        if (pc->pipe_to_receiver[i] >= 0) {
            close(pc->pipe_to_receiver[i]);
            pc->pipe_to_receiver[i] = -1;
        }
        if (pc->pipe_to_sender[i] >= 0) {
            close(pc->pipe_to_sender[i]);
            pc->pipe_to_sender[i] = -1;
        }
    }
    free(pc);
}
