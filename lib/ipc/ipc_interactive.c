/*
 * ipc_interactive.c
 * InterSync — interactive IPC helper binary
 *
 * Thin CLI wrapper over libinterync-ipc.so.
 * All results are written as JSON to stdout.
 * Errors are written as JSON to stderr with a non-zero exit code.
 *
 * Usage:
 *   ipc_interactive send   PIPE|QUEUE|SOCKET|SHM <size_bytes> [channel_name]
 *   ipc_interactive recv   PIPE|QUEUE|SOCKET|SHM <size_bytes> [channel_name]
 *   ipc_interactive burst  PIPE|QUEUE|SOCKET|SHM <size_bytes> <count> [channel_name]
 *
 * JSON output (send):
 *   {"op":"send","mechanism":"PIPE","bytes":256,
 *    "send_time_us":12.3,"error":null}
 *
 * JSON output (burst):
 *   {"op":"burst","mechanism":"PIPE","count":50,"bytes_total":12800,
 *    "total_time_us":980.5,"avg_latency_us":19.6,"throughput_mbs":13.06,"error":null}
 *
 * Build (inside WSL / container — run via Makefile):
 *   gcc -Wall -O2 -o build/ipc_interactive lib/ipc/ipc_interactive.c \
 *       -Ilib/ipc -Lbuild -linterync-ipc -lrt -pthread \
 *       -Wl,-rpath,/opt/interync/lib
 */

#include "libinterync_ipc.h"

#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

/* --------------------------------------------------------------------------
 * Timing helpers
 * -------------------------------------------------------------------------- */
static inline double now_us(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (double)ts.tv_sec * 1e6 + (double)ts.tv_nsec / 1e3;
}

/* --------------------------------------------------------------------------
 * Mechanism name → enum
 * -------------------------------------------------------------------------- */
static ipc_type_t parse_mechanism(const char *name)
{
    if (strcmp(name, "PIPE")   == 0) return IPC_PIPE;
    if (strcmp(name, "QUEUE")  == 0) return IPC_QUEUE;
    if (strcmp(name, "SOCKET") == 0) return IPC_SOCKET;
    if (strcmp(name, "SHM")    == 0) return IPC_SHM;
    fprintf(stderr,
            "{\"error\":\"Unknown mechanism '%s'. Use PIPE|QUEUE|SOCKET|SHM\"}\n",
            name);
    exit(2);
}

/* --------------------------------------------------------------------------
 * JSON error output
 * -------------------------------------------------------------------------- */
static void json_error(const char *op, const char *msg)
{
    fprintf(stderr,
            "{\"op\":\"%s\",\"error\":\"%s\"}\n",
            op, msg);
}

/* --------------------------------------------------------------------------
 * send
 * -------------------------------------------------------------------------- */
static int cmd_send(ipc_type_t type, const char *mech,
                    size_t size, const char *chan_name)
{
    ipc_channel_t *ch = ipc_create(type, chan_name);
    if (!ch) {
        char buf[128];
        snprintf(buf, sizeof(buf), "ipc_create failed: %s", strerror(errno));
        json_error("send", buf);
        return 1;
    }

    /* Allocate and fill a test payload */
    unsigned char *data = (unsigned char *)malloc(size);
    if (!data) { json_error("send", "malloc failed"); ipc_destroy(ch); return 1; }
    for (size_t i = 0; i < size; i++) data[i] = (unsigned char)(i & 0xFF);

    double t0 = now_us();
    int rc = ipc_send(ch, data, size);
    double send_us = now_us() - t0;

    free(data);
    ipc_destroy(ch);

    if (rc != 0) {
        char buf[128];
        snprintf(buf, sizeof(buf), "ipc_send failed: errno=%d", -rc);
        json_error("send", buf);
        return 1;
    }

    printf("{\"op\":\"send\",\"mechanism\":\"%s\",\"bytes\":%zu,"
           "\"send_time_us\":%.2f,\"error\":null}\n",
           mech, size, send_us);
    return 0;
}

/* --------------------------------------------------------------------------
 * recv
 * -------------------------------------------------------------------------- */
static int cmd_recv(ipc_type_t type, const char *mech,
                    size_t size, const char *chan_name)
{
    ipc_channel_t *ch = ipc_create(type, chan_name);
    if (!ch) {
        char buf[128];
        snprintf(buf, sizeof(buf), "ipc_create failed: %s", strerror(errno));
        json_error("recv", buf);
        return 1;
    }

    unsigned char *buf = (unsigned char *)malloc(size);
    if (!buf) { json_error("recv", "malloc failed"); ipc_destroy(ch); return 1; }

    double t0 = now_us();
    int rc = ipc_receive(ch, buf, size);
    double recv_us = now_us() - t0;

    int bytes_recvd = rc >= 0 ? rc : 0;
    free(buf);
    ipc_destroy(ch);

    if (rc < 0) {
        char errbuf[128];
        snprintf(errbuf, sizeof(errbuf), "ipc_receive failed: errno=%d", -rc);
        json_error("recv", errbuf);
        return 1;
    }

    printf("{\"op\":\"recv\",\"mechanism\":\"%s\",\"bytes\":%d,"
           "\"recv_time_us\":%.2f,\"error\":null}\n",
           mech, bytes_recvd, recv_us);
    return 0;
}

/* --------------------------------------------------------------------------
 * burst — sends <count> messages of <size> bytes sequentially
 * -------------------------------------------------------------------------- */
static int cmd_burst(ipc_type_t type, const char *mech,
                     size_t size, long count, const char *chan_name)
{
    ipc_channel_t *ch = ipc_create(type, chan_name);
    if (!ch) {
        char buf[128];
        snprintf(buf, sizeof(buf), "ipc_create failed: %s", strerror(errno));
        json_error("burst", buf);
        return 1;
    }

    unsigned char *data = (unsigned char *)malloc(size);
    if (!data) { json_error("burst", "malloc failed"); ipc_destroy(ch); return 1; }
    for (size_t i = 0; i < size; i++) data[i] = (unsigned char)(i & 0xFF);

    double t_total_start = now_us();
    long sent = 0;

    for (long i = 0; i < count; i++) {
        int rc = ipc_send(ch, data, size);
        if (rc != 0) break;
        sent++;
        /* Emit a progress line per message so Python can stream it */
        printf("{\"progress\":%ld,\"total\":%ld}\n", sent, count);
        fflush(stdout);
    }

    double total_us = now_us() - t_total_start;
    double avg_us   = sent > 0 ? total_us / (double)sent : 0.0;
    double bytes_total = (double)sent * (double)size;
    double throughput  = total_us > 0
        ? (bytes_total / (1024.0 * 1024.0)) / (total_us / 1e6)
        : 0.0;

    free(data);
    ipc_destroy(ch);

    if (sent < count) {
        fprintf(stderr,
                "{\"op\":\"burst\",\"sent\":%ld,\"requested\":%ld,"
                "\"error\":\"ipc_send failed mid-burst\"}\n",
                sent, count);
        return 1;
    }

    printf("{\"op\":\"burst\",\"mechanism\":\"%s\",\"count\":%ld,"
           "\"bytes_total\":%.0f,\"total_time_us\":%.2f,"
           "\"avg_latency_us\":%.2f,\"throughput_mbs\":%.3f,\"error\":null}\n",
           mech, sent, bytes_total, total_us, avg_us, throughput);
    return 0;
}

/* --------------------------------------------------------------------------
 * main
 * -------------------------------------------------------------------------- */
int main(int argc, char *argv[])
{
    if (argc < 4) {
        fprintf(stderr,
                "Usage:\n"
                "  ipc_interactive send   MECH SIZE [chan]\n"
                "  ipc_interactive recv   MECH SIZE [chan]\n"
                "  ipc_interactive burst  MECH SIZE COUNT [chan]\n");
        return 2;
    }

    const char *op   = argv[1];
    const char *mech = argv[2];
    ipc_type_t  type = parse_mechanism(mech);

    if (strcmp(op, "send") == 0) {
        size_t size = (size_t)atol(argv[3]);
        const char *chan = argc >= 5 ? argv[4] : "interync-ch";
        return cmd_send(type, mech, size, chan);

    } else if (strcmp(op, "recv") == 0) {
        size_t size = (size_t)atol(argv[3]);
        const char *chan = argc >= 5 ? argv[4] : "interync-ch";
        return cmd_recv(type, mech, size, chan);

    } else if (strcmp(op, "burst") == 0) {
        if (argc < 5) {
            fprintf(stderr,
                    "burst requires: MECH SIZE COUNT [chan]\n");
            return 2;
        }
        size_t size  = (size_t)atol(argv[3]);
        long   count = atol(argv[4]);
        const char *chan = argc >= 6 ? argv[5] : "interync-ch";
        return cmd_burst(type, mech, size, count, chan);

    } else {
        fprintf(stderr,
                "{\"error\":\"Unknown op '%s'. Use send|recv|burst\"}\n",
                op);
        return 2;
    }
}
