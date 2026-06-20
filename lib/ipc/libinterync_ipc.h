/*
 * libinterync_ipc.h  (revised — factory/dispatch model)
 * InterSync: IPC & Synchronization Platform
 *
 * Each IPC mechanism is implemented as a self-contained struct with its
 * own vtable of send/receive/destroy operations.  The public API functions
 * (ipc_create / ipc_send / ipc_receive / ipc_destroy) live in ipc_factory.c
 * and dispatch through the vtable, so all four .c files can coexist in the
 * same shared library without symbol collisions.
 *
 * Error convention:
 *   int-returning functions → 0 on success; negative errno on failure.
 *   Pointer-returning functions → NULL on failure (errno set).
 */

#ifndef LIBINTERYNC_IPC_H
#define LIBINTERYNC_IPC_H

#include <stddef.h>
#include <stdint.h>
#include <sys/types.h>

#ifdef __cplusplus
extern "C" {
#endif

/* -------------------------------------------------------------------------
 * IPC mechanism selector
 * ---------------------------------------------------------------------- */
typedef enum {
    IPC_PIPE   = 0,
    IPC_QUEUE  = 1,
    IPC_SOCKET = 2,
    IPC_SHM    = 3
} ipc_type_t;

/* -------------------------------------------------------------------------
 * Vtable — populated by each mechanism's _open() function
 * ---------------------------------------------------------------------- */
typedef struct ipc_channel ipc_channel_t;

typedef struct {
    int  (*send)   (ipc_channel_t* ch, const void* data, size_t len);
    int  (*receive)(ipc_channel_t* ch, void* buffer,     size_t len);
    void (*destroy)(ipc_channel_t* ch);
} ipc_ops_t;

/* -------------------------------------------------------------------------
 * Channel handle — common header + mechanism-private data
 * ---------------------------------------------------------------------- */
struct ipc_channel {
    ipc_type_t  type;
    const ipc_ops_t* ops;    /* vtable set by each mechanism's open() */
    char        name[256];
    /* Mechanism-private state follows — each .c file casts to its own struct */
};

/* -------------------------------------------------------------------------
 * Internal factory hooks (implemented in each .c file, called by factory)
 * ---------------------------------------------------------------------- */
ipc_channel_t* ipc_pipe_open  (const char* name);
ipc_channel_t* ipc_queue_open (const char* name);
ipc_channel_t* ipc_socket_open(const char* name);
ipc_channel_t* ipc_shm_open   (const char* name);

/* -------------------------------------------------------------------------
 * Public API (implemented in ipc_factory.c)
 * ---------------------------------------------------------------------- */
ipc_channel_t* ipc_create (ipc_type_t type, const char* name);
int            ipc_send   (ipc_channel_t* ch, const void* data, size_t len);
int            ipc_receive(ipc_channel_t* ch, void* buffer, size_t len);
void           ipc_destroy(ipc_channel_t* ch);
const char*    ipc_type_name(ipc_type_t type);

#ifdef __cplusplus
}
#endif
#endif /* LIBINTERYNC_IPC_H */
