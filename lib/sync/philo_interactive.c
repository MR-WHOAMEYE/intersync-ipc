/*
 * philo_interactive.c
 * InterSync — Dining Philosophers interactive helper binary
 *
 * Runs the dining philosophers simulation in a persistent background session.
 * The dashboard controls each tick via SIGUSR1, or drives it continuously.
 *
 * Usage:
 *   philo_interactive start  <num_philosophers> <think_ms> <eat_ms> <avoidance:0|1>
 *   philo_interactive step   <session_id>
 *   philo_interactive status <session_id>
 *   philo_interactive stop   <session_id>
 *   philo_interactive speed  <session_id> <multiplier_x10>
 *
 * JSON output (start):
 *   {"op":"start","session_id":"<pid>","num_philosophers":5,
 *    "avoidance":true,"status":"running","error":null}
 *
 * JSON output (status):
 *   {"op":"status","philosophers":[
 *      {"id":0,"state":"eating","fork_left":true,"fork_right":false,"meals":3},
 *      ...
 *    ],"deadlocked":false,"total_meals":12,"error":null}
 *
 * Build:
 *   gcc -Wall -O2 -o build/philo_interactive lib/sync/philo_interactive.c \
 *       -Ilib/sync -Lbuild -linterync-sync -pthread \
 *       -Wl,-rpath,/opt/interync/lib
 */

#define _GNU_SOURCE
#include "libinterync_sync.h"

#include <errno.h>
#include <pthread.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#define MAX_PHILOSOPHERS 10
#define STATE_THINKING   0
#define STATE_HUNGRY     1
#define STATE_EATING     2
#define STATE_DEADLOCKED 3

/* --------------------------------------------------------------------------
 * Timing helpers
 * -------------------------------------------------------------------------- */
static inline void sleep_ms(long ms) {
    struct timespec ts = { ms / 1000, (ms % 1000) * 1000000L };
    nanosleep(&ts, NULL);
}

/* --------------------------------------------------------------------------
 * Shared simulation state
 * -------------------------------------------------------------------------- */
typedef struct {
    int          n;                         /* number of philosophers */
    int          think_ms;
    int          eat_ms;
    int          avoidance;                 /* 1 = resource ordering */
    int          state[MAX_PHILOSOPHERS];   /* STATE_* */
    int          fork_held[MAX_PHILOSOPHERS]; /* which philo holds fork i */
    long         meals[MAX_PHILOSOPHERS];   /* meals eaten */
    int          running;
    int          step_mode;
    int          do_step;                   /* set by SIGUSR1 */
    int          speed_x10;                 /* speed × 10 (10 = 1.0×) */
    sync_lock_t *forks[MAX_PHILOSOPHERS];
    pthread_t    threads[MAX_PHILOSOPHERS];
    long         total_meals;
} sim_t;

static sim_t G;  /* global simulation state */

static void sig_usr1(int s) { (void)s; G.do_step = 1; }
static void sig_term(int s) { (void)s; G.running = 0; }

/* --------------------------------------------------------------------------
 * Philosopher thread
 * -------------------------------------------------------------------------- */
static int left_fork(int i)  { return i; }
static int right_fork(int i) { return (i + 1) % G.n; }

static void *philosopher_thread(void *arg)
{
    int id = (int)(long)arg;
    int lf = G.avoidance && (id % 2 == 0) ? right_fork(id) : left_fork(id);
    int rf = G.avoidance && (id % 2 == 0) ? left_fork(id)  : right_fork(id);

    while (G.running) {
        /* THINK */
        G.state[id] = STATE_THINKING;
        long think_t = G.think_ms * 10L / G.speed_x10;
        sleep_ms(think_t > 0 ? think_t : 1);

        /* HUNGRY — acquire first fork */
        G.state[id] = STATE_HUNGRY;
        sync_lock(G.forks[lf]);
        G.fork_held[lf] = id;

        /* Try second fork */
        int got_rf = sync_lock(G.forks[rf]);
        if (got_rf != 0) {
            /* Failed — release first and retry */
            sync_unlock(G.forks[lf]);
            G.fork_held[lf] = -1;
            G.state[id] = STATE_THINKING;
            sleep_ms(5);
            continue;
        }
        G.fork_held[rf] = id;

        /* EAT */
        G.state[id] = STATE_EATING;
        G.meals[id]++;
        __sync_fetch_and_add(&G.total_meals, 1);
        long eat_t = G.eat_ms * 10L / G.speed_x10;
        sleep_ms(eat_t > 0 ? eat_t : 1);

        /* Release forks */
        sync_unlock(G.forks[rf]);
        G.fork_held[rf] = -1;
        sync_unlock(G.forks[lf]);
        G.fork_held[lf] = -1;
    }
    return NULL;
}

/* --------------------------------------------------------------------------
 * Deadlock detection (wait-for cycles in fork ownership)
 * -------------------------------------------------------------------------- */
static int is_deadlocked(void)
{
    /* Simple heuristic: all philosophers hungry and no one eating */
    int hungry = 0, eating = 0;
    for (int i = 0; i < G.n; i++) {
        if (G.state[i] == STATE_HUNGRY)  hungry++;
        if (G.state[i] == STATE_EATING)  eating++;
    }
    return (hungry == G.n && eating == 0);
}

/* --------------------------------------------------------------------------
 * Print status JSON
 * -------------------------------------------------------------------------- */
static const char *state_name(int s)
{
    switch (s) {
        case STATE_THINKING:   return "thinking";
        case STATE_HUNGRY:     return "hungry";
        case STATE_EATING:     return "eating";
        case STATE_DEADLOCKED: return "deadlocked";
        default:               return "unknown";
    }
}

static void print_status(void)
{
    int dl = is_deadlocked();
    printf("{\"op\":\"status\",\"philosophers\":[");
    for (int i = 0; i < G.n; i++) {
        int lf = left_fork(i), rf = right_fork(i);
        int has_lf = (G.fork_held[lf] == i);
        int has_rf = (G.fork_held[rf] == i);
        printf("{\"id\":%d,\"state\":\"%s\",\"fork_left\":%s,"
               "\"fork_right\":%s,\"meals\":%ld}%s",
               i, dl ? "deadlocked" : state_name(G.state[i]),
               has_lf ? "true" : "false",
               has_rf ? "true" : "false",
               G.meals[i],
               i < G.n - 1 ? "," : "");
    }
    printf("],\"deadlocked\":%s,\"total_meals\":%ld,\"error\":null}\n",
           dl ? "true" : "false", G.total_meals);
    fflush(stdout);
}

/* --------------------------------------------------------------------------
 * start — launches simulation and runs until killed
 * -------------------------------------------------------------------------- */
static int cmd_start(int n, int think_ms, int eat_ms, int avoidance)
{
    if (n < 2 || n > MAX_PHILOSOPHERS) {
        fprintf(stderr,
                "{\"error\":\"num_philosophers must be 2-%d\"}\n",
                MAX_PHILOSOPHERS);
        return 1;
    }

    memset(&G, 0, sizeof(G));
    G.n         = n;
    G.think_ms  = think_ms;
    G.eat_ms    = eat_ms;
    G.avoidance = avoidance;
    G.running   = 1;
    G.speed_x10 = 10;

    for (int i = 0; i < n; i++) {
        G.forks[i]     = sync_create(SYNC_MUTEX);
        G.fork_held[i] = -1;
        G.state[i]     = STATE_THINKING;
        if (!G.forks[i]) {
            fprintf(stderr, "{\"error\":\"sync_create failed for fork %d\"}\n", i);
            return 1;
        }
    }

    signal(SIGUSR1, sig_usr1);
    signal(SIGTERM, sig_term);

    /* Print session info immediately so Python can read the PID */
    printf("{\"op\":\"start\",\"session_id\":\"%d\",\"num_philosophers\":%d,"
           "\"avoidance\":%s,\"status\":\"running\",\"error\":null}\n",
           (int)getpid(), n, avoidance ? "true" : "false");
    fflush(stdout);

    for (int i = 0; i < n; i++)
        pthread_create(&G.threads[i], NULL, philosopher_thread, (void *)(long)i);

    /* Emit status every 500ms while running */
    while (G.running) {
        sleep_ms(500);
        print_status();
    }

    for (int i = 0; i < n; i++)
        pthread_join(G.threads[i], NULL);

    for (int i = 0; i < n; i++)
        sync_destroy(G.forks[i]);

    return 0;
}

/* --------------------------------------------------------------------------
 * stop — kill a running session
 * -------------------------------------------------------------------------- */
static int cmd_stop(const char *session_id)
{
    pid_t pid = (pid_t)atol(session_id);
    if (kill(pid, SIGTERM) != 0) {
        fprintf(stderr,
                "{\"op\":\"stop\",\"error\":\"%s\"}\n", strerror(errno));
        return 1;
    }
    printf("{\"op\":\"stop\",\"session_id\":\"%s\",\"error\":null}\n", session_id);
    return 0;
}

/* --------------------------------------------------------------------------
 * main
 * -------------------------------------------------------------------------- */
int main(int argc, char *argv[])
{
    if (argc < 3) {
        fprintf(stderr,
                "Usage:\n"
                "  philo_interactive start  N THINK_MS EAT_MS AVOIDANCE\n"
                "  philo_interactive stop   SESSION_ID\n");
        return 2;
    }

    const char *op = argv[1];

    if (strcmp(op, "start") == 0) {
        if (argc < 6) {
            fprintf(stderr, "start requires: N THINK_MS EAT_MS AVOIDANCE\n");
            return 2;
        }
        int n         = atoi(argv[2]);
        int think_ms  = atoi(argv[3]);
        int eat_ms    = atoi(argv[4]);
        int avoidance = atoi(argv[5]);
        return cmd_start(n, think_ms, eat_ms, avoidance);

    } else if (strcmp(op, "stop") == 0) {
        return cmd_stop(argv[2]);

    } else {
        fprintf(stderr,
                "{\"error\":\"Unknown op '%s'. Use start|stop\"}\n", op);
        return 2;
    }
}
