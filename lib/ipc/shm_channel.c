/*
 * shm_channel.c
 * InterSync — IPC_SHM implementation (vtable model)
 *
 * Exposes:  ipc_shm_open()
 * Uses POSIX shared memory + semaphore-protected ring buffer.
 * name must start with '/' (POSIX requirement).
 *
 * Link with: -lrt -pthread
 */

#include "libinterync_ipc.h"

#include <errno.h>
#include <fcntl.h>
#include <semaphore.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

#define SHM_REGION_SIZE  (1 << 17)   /* 128 KiB */
#define SEM_FREE_SFX     "_free"
#define SEM_USED_SFX     "_used"
#define SEM_MTX_SFX      "_mtx"

typedef struct {
    volatile size_t write_pos;
    volatile size_t read_pos;
    size_t          data_size;
} shm_header_t;

#define SHM_DATA_SIZE  (SHM_REGION_SIZE - sizeof(shm_header_t))

typedef struct {
    ipc_channel_t base;
    char          shm_name[256];
    char          sem_free[280];
    char          sem_used[280];
    char          sem_mutex[280];
    int           shm_fd;
    void*         shm_ptr;
    shm_header_t* header;
    uint8_t*      data;
    sem_t*        sem_free_ptr;
    sem_t*        sem_used_ptr;
    sem_t*        sem_mutex_ptr;
} shm_channel_t;

static int  shm_send   (ipc_channel_t* ch, const void* data, size_t len);
static int  shm_receive(ipc_channel_t* ch, void* buffer,     size_t len);
static void shm_destroy(ipc_channel_t* ch);

static const ipc_ops_t shm_ops = {
    .send    = shm_send,
    .receive = shm_receive,
    .destroy = shm_destroy,
};

ipc_channel_t* ipc_shm_open(const char* name)
{
    if (!name || name[0] != '/') { errno = EINVAL; return NULL; }

    shm_channel_t* sc = (shm_channel_t*)calloc(1, sizeof(*sc));
    if (!sc) return NULL;

    sc->base.type = IPC_SHM;
    sc->base.ops  = &shm_ops;
    sc->shm_fd    = -1;
    sc->shm_ptr   = MAP_FAILED;
    sc->sem_free_ptr = sc->sem_used_ptr = sc->sem_mutex_ptr = SEM_FAILED;

    strncpy(sc->shm_name,  name, sizeof(sc->shm_name)  - 1);
    snprintf(sc->sem_free,  sizeof(sc->sem_free),  "%s%s", name, SEM_FREE_SFX);
    snprintf(sc->sem_used,  sizeof(sc->sem_used),  "%s%s", name, SEM_USED_SFX);
    snprintf(sc->sem_mutex, sizeof(sc->sem_mutex), "%s%s", name, SEM_MTX_SFX);
    strncpy(sc->base.name, name, sizeof(sc->base.name) - 1);

    /* Shared memory */
    sc->shm_fd = shm_open(name, O_CREAT | O_RDWR, S_IRUSR | S_IWUSR);
    if (sc->shm_fd < 0) goto fail;
    if (ftruncate(sc->shm_fd, (off_t)SHM_REGION_SIZE) < 0) goto fail;

    sc->shm_ptr = mmap(NULL, SHM_REGION_SIZE, PROT_READ | PROT_WRITE,
                       MAP_SHARED, sc->shm_fd, 0);
    if (sc->shm_ptr == MAP_FAILED) goto fail;

    sc->header = (shm_header_t*)sc->shm_ptr;
    sc->data   = (uint8_t*)sc->shm_ptr + sizeof(shm_header_t);
    sc->header->write_pos = 0;
    sc->header->read_pos  = 0;
    sc->header->data_size = SHM_DATA_SIZE;

    /* Semaphores */
    sem_unlink(sc->sem_free);
    sem_unlink(sc->sem_used);
    sem_unlink(sc->sem_mutex);

    sc->sem_free_ptr = sem_open(sc->sem_free,
                                O_CREAT | O_EXCL, S_IRUSR | S_IWUSR,
                                (unsigned)SHM_DATA_SIZE);
    if (sc->sem_free_ptr == SEM_FAILED) goto fail;

    sc->sem_used_ptr = sem_open(sc->sem_used,
                                O_CREAT | O_EXCL, S_IRUSR | S_IWUSR, 0);
    if (sc->sem_used_ptr == SEM_FAILED) goto fail;

    sc->sem_mutex_ptr = sem_open(sc->sem_mutex,
                                 O_CREAT | O_EXCL, S_IRUSR | S_IWUSR, 1);
    if (sc->sem_mutex_ptr == SEM_FAILED) goto fail;

    return &sc->base;

fail:
    {
        int saved = errno;
        if (sc->sem_mutex_ptr != SEM_FAILED) { sem_close(sc->sem_mutex_ptr); sem_unlink(sc->sem_mutex); }
        if (sc->sem_used_ptr  != SEM_FAILED) { sem_close(sc->sem_used_ptr);  sem_unlink(sc->sem_used);  }
        if (sc->sem_free_ptr  != SEM_FAILED) { sem_close(sc->sem_free_ptr);  sem_unlink(sc->sem_free);  }
        if (sc->shm_ptr != MAP_FAILED && sc->shm_ptr) munmap(sc->shm_ptr, SHM_REGION_SIZE);
        if (sc->shm_fd >= 0) { close(sc->shm_fd); shm_unlink(name); }
        free(sc);
        errno = saved;
        return NULL;
    }
}

static int shm_send(ipc_channel_t* ch, const void* data, size_t len)
{
    shm_channel_t* sc = (shm_channel_t*)ch;
    if (!data || len == 0) return 0;
    if (len > SHM_DATA_SIZE - sizeof(uint32_t)) return -EMSGSIZE;

    uint32_t mlen  = (uint32_t)len;
    size_t   total = sizeof(mlen) + len;

    for (size_t i = 0; i < total; i++) {
        while (sem_wait(sc->sem_free_ptr) < 0 && errno == EINTR) {}
    }

    sem_wait(sc->sem_mutex_ptr);
    size_t dsize = sc->header->data_size;
    size_t wp    = sc->header->write_pos;

    /* Write 4-byte length prefix */
    const uint8_t* lp = (const uint8_t*)&mlen;
    for (size_t i = 0; i < sizeof(mlen); i++) { sc->data[wp % dsize] = lp[i]; wp++; }
    /* Write payload */
    const uint8_t* dp = (const uint8_t*)data;
    for (size_t i = 0; i < len; i++) { sc->data[wp % dsize] = dp[i]; wp++; }
    sc->header->write_pos = wp;
    sem_post(sc->sem_mutex_ptr);

    for (size_t i = 0; i < total; i++) sem_post(sc->sem_used_ptr);
    return 0;
}

static int shm_receive(ipc_channel_t* ch, void* buffer, size_t len)
{
    shm_channel_t* sc = (shm_channel_t*)ch;
    if (!buffer || len == 0) return 0;

    /* Wait for length prefix bytes */
    for (size_t i = 0; i < sizeof(uint32_t); i++) {
        while (sem_wait(sc->sem_used_ptr) < 0 && errno == EINTR) {}
    }

    sem_wait(sc->sem_mutex_ptr);
    size_t   dsize = sc->header->data_size;
    size_t   rp    = sc->header->read_pos;
    uint32_t mlen  = 0;
    uint8_t* lp    = (uint8_t*)&mlen;
    for (size_t i = 0; i < sizeof(mlen); i++) { lp[i] = sc->data[rp % dsize]; rp++; }
    sc->header->read_pos = rp;
    sem_post(sc->sem_mutex_ptr);

    for (size_t i = 0; i < sizeof(uint32_t); i++) sem_post(sc->sem_free_ptr);

    /* Wait for payload bytes */
    for (uint32_t i = 0; i < mlen; i++) {
        while (sem_wait(sc->sem_used_ptr) < 0 && errno == EINTR) {}
    }

    size_t copy_len = (mlen < (uint32_t)len) ? mlen : (uint32_t)len;
    sem_wait(sc->sem_mutex_ptr);
    rp = sc->header->read_pos;
    uint8_t* bp = (uint8_t*)buffer;
    for (uint32_t i = 0; i < mlen; i++) {
        if (i < (uint32_t)copy_len) bp[i] = sc->data[rp % dsize];
        rp++;
    }
    sc->header->read_pos = rp;
    sem_post(sc->sem_mutex_ptr);

    for (uint32_t i = 0; i < mlen; i++) sem_post(sc->sem_free_ptr);
    return (int)copy_len;
}

static void shm_destroy(ipc_channel_t* ch)
{
    shm_channel_t* sc = (shm_channel_t*)ch;
    if (sc->sem_mutex_ptr != SEM_FAILED) { sem_close(sc->sem_mutex_ptr); sem_unlink(sc->sem_mutex); }
    if (sc->sem_used_ptr  != SEM_FAILED) { sem_close(sc->sem_used_ptr);  sem_unlink(sc->sem_used);  }
    if (sc->sem_free_ptr  != SEM_FAILED) { sem_close(sc->sem_free_ptr);  sem_unlink(sc->sem_free);  }
    if (sc->shm_ptr != MAP_FAILED && sc->shm_ptr) munmap(sc->shm_ptr, SHM_REGION_SIZE);
    if (sc->shm_fd >= 0) { close(sc->shm_fd); shm_unlink(sc->shm_name); }
    free(sc);
}
