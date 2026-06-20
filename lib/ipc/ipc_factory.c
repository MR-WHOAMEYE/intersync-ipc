/*
 * ipc_factory.c
 * InterSync — public API dispatch + factory
 *
 * This is the ONLY file that defines ipc_create / ipc_send / ipc_receive /
 * ipc_destroy / ipc_type_name.  It dispatches to the mechanism-specific
 * open() functions (ipc_pipe_open, ipc_queue_open, …) via the vtable
 * stored in the channel handle.
 *
 * Compile: included in the combined libinterync-ipc.so link step.
 */

#include "libinterync_ipc.h"
#include <errno.h>
#include <stdlib.h>

/* -------------------------------------------------------------------------
 * ipc_create — factory
 * ---------------------------------------------------------------------- */
ipc_channel_t* ipc_create(ipc_type_t type, const char* name)
{
    switch (type) {
        case IPC_PIPE:   return ipc_pipe_open(name);
        case IPC_QUEUE:  return ipc_queue_open(name);
        case IPC_SOCKET: return ipc_socket_open(name);
        case IPC_SHM:    return ipc_shm_open(name);
        default:
            errno = EINVAL;
            return NULL;
    }
}

/* -------------------------------------------------------------------------
 * ipc_send — dispatch via vtable
 * ---------------------------------------------------------------------- */
int ipc_send(ipc_channel_t* ch, const void* data, size_t len)
{
    if (!ch || !ch->ops || !ch->ops->send) return -EINVAL;
    return ch->ops->send(ch, data, len);
}

/* -------------------------------------------------------------------------
 * ipc_receive — dispatch via vtable
 * ---------------------------------------------------------------------- */
int ipc_receive(ipc_channel_t* ch, void* buffer, size_t len)
{
    if (!ch || !ch->ops || !ch->ops->receive) return -EINVAL;
    return ch->ops->receive(ch, buffer, len);
}

/* -------------------------------------------------------------------------
 * ipc_destroy — dispatch via vtable
 * ---------------------------------------------------------------------- */
void ipc_destroy(ipc_channel_t* ch)
{
    if (!ch) return;
    if (ch->ops && ch->ops->destroy)
        ch->ops->destroy(ch);
}

/* -------------------------------------------------------------------------
 * ipc_type_name — utility
 * ---------------------------------------------------------------------- */
const char* ipc_type_name(ipc_type_t type)
{
    switch (type) {
        case IPC_PIPE:   return "PIPE";
        case IPC_QUEUE:  return "QUEUE";
        case IPC_SOCKET: return "SOCKET";
        case IPC_SHM:    return "SHM";
        default:         return "UNKNOWN";
    }
}
