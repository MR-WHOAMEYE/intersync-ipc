/*
 * socket_channel.c
 * InterSync — IPC_SOCKET implementation (vtable model)
 *
 * Exposes:  ipc_socket_open()
 * Uses UNIX domain sockets (AF_UNIX / SOCK_STREAM).
 * Socket file created at /tmp/<name>.sock
 */

#include "libinterync_ipc.h"

#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/socket.h>
#include <sys/un.h>

#define SOCKET_PATH_FMT  "/tmp/%s.sock"
#define BACKLOG          1

typedef struct {
    ipc_channel_t base;
    char          socket_path[256];
    int           server_fd;
    int           client_fd;
    int           peer_fd;
} socket_channel_t;

static int  sock_send   (ipc_channel_t* ch, const void* data, size_t len);
static int  sock_receive(ipc_channel_t* ch, void* buffer,     size_t len);
static void sock_destroy(ipc_channel_t* ch);

static const ipc_ops_t socket_ops = {
    .send    = sock_send,
    .receive = sock_receive,
    .destroy = sock_destroy,
};

ipc_channel_t* ipc_socket_open(const char* name)
{
    if (!name) { errno = EINVAL; return NULL; }

    socket_channel_t* sc = (socket_channel_t*)calloc(1, sizeof(*sc));
    if (!sc) return NULL;

    sc->base.type = IPC_SOCKET;
    sc->base.ops  = &socket_ops;
    strncpy(sc->base.name, name, sizeof(sc->base.name) - 1);
    sc->server_fd = sc->client_fd = sc->peer_fd = -1;

    snprintf(sc->socket_path, sizeof(sc->socket_path), SOCKET_PATH_FMT, name);
    unlink(sc->socket_path);

    struct sockaddr_un addr;
    memset(&addr, 0, sizeof(addr));
    addr.sun_family = AF_UNIX;
    strncpy(addr.sun_path, sc->socket_path, sizeof(addr.sun_path) - 1);

    if ((sc->server_fd = socket(AF_UNIX, SOCK_STREAM, 0)) < 0) goto fail;
    if (bind(sc->server_fd, (struct sockaddr*)&addr, sizeof(addr)) < 0) goto fail;
    if (listen(sc->server_fd, BACKLOG) < 0) goto fail;
    if ((sc->client_fd = socket(AF_UNIX, SOCK_STREAM, 0)) < 0) goto fail;
    if (connect(sc->client_fd, (struct sockaddr*)&addr, sizeof(addr)) < 0) goto fail;
    if ((sc->peer_fd = accept(sc->server_fd, NULL, NULL)) < 0) goto fail;

    return &sc->base;

fail:
    {
        int saved = errno;
        if (sc->client_fd >= 0) close(sc->client_fd);
        if (sc->server_fd >= 0) close(sc->server_fd);
        unlink(sc->socket_path);
        free(sc);
        errno = saved;
        return NULL;
    }
}

static int sock_send(ipc_channel_t* ch, const void* data, size_t len)
{
    socket_channel_t* sc = (socket_channel_t*)ch;
    if (!data || len == 0)   return 0;
    if (sc->client_fd < 0)  return -EBADF;

    const uint8_t* ptr = (const uint8_t*)data;
    size_t rem = len;
    while (rem > 0) {
        ssize_t n = write(sc->client_fd, ptr, rem);
        if (n < 0) {
            if (errno == EINTR) continue;
            return -errno;
        }
        ptr += (size_t)n;
        rem -= (size_t)n;
    }
    return 0;
}

static int sock_receive(ipc_channel_t* ch, void* buffer, size_t len)
{
    socket_channel_t* sc = (socket_channel_t*)ch;
    if (!buffer || len == 0) return 0;
    if (sc->peer_fd < 0)     return -EBADF;

    ssize_t n;
    do { n = read(sc->peer_fd, buffer, len); }
    while (n < 0 && errno == EINTR);

    return (n < 0) ? -errno : (int)n;
}

static void sock_destroy(ipc_channel_t* ch)
{
    socket_channel_t* sc = (socket_channel_t*)ch;
    if (sc->peer_fd   >= 0) close(sc->peer_fd);
    if (sc->client_fd >= 0) close(sc->client_fd);
    if (sc->server_fd >= 0) close(sc->server_fd);
    unlink(sc->socket_path);
    free(sc);
}
