/*
 * queue_channel.c
 * InterSync — IPC_QUEUE implementation (vtable model)
 *
 * Exposes:  ipc_queue_open()
 * Uses POSIX message queues (mq_open / mq_send / mq_receive).
 * name must start with '/' (POSIX requirement).
 *
 * Link with: -lrt
 */

#include "libinterync_ipc.h"

#include <errno.h>
#include <fcntl.h>
#include <mqueue.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

#define MQ_MAX_MSG_SIZE  8192
#define MQ_MAX_MSGS      10

typedef struct {
    ipc_channel_t base;
    mqd_t         mqd;
} queue_channel_t;

static int  queue_send   (ipc_channel_t* ch, const void* data, size_t len);
static int  queue_receive(ipc_channel_t* ch, void* buffer,     size_t len);
static void queue_destroy(ipc_channel_t* ch);

static const ipc_ops_t queue_ops = {
    .send    = queue_send,
    .receive = queue_receive,
    .destroy = queue_destroy,
};

ipc_channel_t* ipc_queue_open(const char* name)
{
    if (!name || name[0] != '/') { errno = EINVAL; return NULL; }

    queue_channel_t* qc = (queue_channel_t*)calloc(1, sizeof(*qc));
    if (!qc) return NULL;

    qc->base.type = IPC_QUEUE;
    qc->base.ops  = &queue_ops;
    strncpy(qc->base.name, name, sizeof(qc->base.name) - 1);
    qc->mqd = (mqd_t)-1;

    struct mq_attr attr = {
        .mq_flags   = 0,
        .mq_maxmsg  = MQ_MAX_MSGS,
        .mq_msgsize = MQ_MAX_MSG_SIZE,
        .mq_curmsgs = 0,
    };

    qc->mqd = mq_open(name, O_CREAT | O_RDWR, S_IRUSR | S_IWUSR, &attr);
    if (qc->mqd == (mqd_t)-1) {
        int saved = errno;
        free(qc);
        errno = saved;
        return NULL;
    }
    return &qc->base;
}

static int queue_send(ipc_channel_t* ch, const void* data, size_t len)
{
    queue_channel_t* qc = (queue_channel_t*)ch;
    if (!data)                          return -EINVAL;
    if (qc->mqd == (mqd_t)-1)          return -EBADF;
    if (len > MQ_MAX_MSG_SIZE)          return -EMSGSIZE;
    if (len == 0)                       return 0;

    int ret;
    do { ret = mq_send(qc->mqd, (const char*)data, len, 0); }
    while (ret < 0 && errno == EINTR);

    return (ret < 0) ? -errno : 0;
}

static int queue_receive(ipc_channel_t* ch, void* buffer, size_t len)
{
    queue_channel_t* qc = (queue_channel_t*)ch;
    if (!buffer)               return -EINVAL;
    if (qc->mqd == (mqd_t)-1) return -EBADF;
    if (len == 0)              return 0;

    ssize_t n;
    unsigned int prio = 0;
    do { n = mq_receive(qc->mqd, (char*)buffer, len, &prio); }
    while (n < 0 && errno == EINTR);

    return (n < 0) ? -errno : (int)n;
}

static void queue_destroy(ipc_channel_t* ch)
{
    queue_channel_t* qc = (queue_channel_t*)ch;
    if (qc->mqd != (mqd_t)-1) {
        mq_close(qc->mqd);
        mq_unlink(qc->base.name);
    }
    free(qc);
}
