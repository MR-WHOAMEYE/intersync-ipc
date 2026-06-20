# Benchmarking InterSync

This document outlines the requirements and caveats for accurately benchmarking the lock-free data structures (SPSC/MPMC queues) and IPC mechanisms in the InterSync platform.

## WSL2 Caveats

If you are running the benchmarks inside Windows Subsystem for Linux (WSL2), you must be aware of the following architectural caveats that **will** impact your benchmark accuracy:

1. **Virtualized Scheduler:** WSL2 is a utility Virtual Machine. It shares CPU scheduling dynamically with the Windows Host. When benchmarking nanosecond-level operations (like our 43ns SPSC queues), the Windows host occasionally preempting the WSL VM to perform background tasks will manifest as massive latency spikes (jitter).
2. **Timer Granularity:** The virtualized hardware clock in WSL2 may not offer the same high-resolution granularity as a bare-metal Linux kernel, leading to clustering in latency histograms.
3. **Cache Topologies:** Lock-free algorithms rely heavily on CPU Cache Coherence (MESI protocol). Virtualized environments abstract the true hardware L1/L2/L3 cache topologies, making cache-line contention measurements slightly synthetic.

## Kernel Requirements

For accurate and representative results, especially for your Capstone Report:

- **Linux Kernel 5.15+:** Recommended for modern scheduler behaviors and stable C11 Atomics support.
- **io_uring:** The zero-context-switch asynchronous wakeups we implemented require **Kernel 5.1 or newer**. If your environment runs an older kernel, `io_uring` will fall back or fail to initialize.

## Best Practices for the Capstone Report

If possible, record your official Capstone benchmarks on a **bare-metal Linux machine** (or a dedicated, heavily isolated VM like VirtualBox Parrot OS where you can pin CPU cores). 

When documenting your results, ensure you record:
- CPU Model and Base/Boost Frequencies
- L1/L2/L3 Cache Sizes
- Exact Linux Kernel Version (`uname -r`)
- The C Compiler version (`gcc --version` or `clang --version`) used to build the binaries.
