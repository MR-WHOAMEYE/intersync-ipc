/*
 * spsc_inline_asm.h
 * InterSync — Inline assembly helpers for the SPSC/MPMC hot path.
 *
 * All macros are designed to be zero-overhead abstractions:
 *   - On x86-64: expands to actual PAUSE / PREFETCHT0 / PREFETCHW / MFENCE
 *   - On other architectures: falls back to C11 atomic thread fence or nop
 *
 * C11 stdatomic handles the acquire/release ordering for CAS operations.
 * This file only provides *performance hints* that C11 cannot express:
 *   • CPU spin-loop hint (PAUSE / YIELD)
 *   • Data prefetch (PREFETCHT0, PREFETCHW)
 *   • Full memory fence for the overflow/non-temporal store path
 *
 * Never use MFENCE around regular CAS — the CAS itself implies acquire/release
 * on x86.  MFENCE is only needed before non-temporal (MOVNTI) stores.
 */

#pragma once
#ifndef SPSC_INLINE_ASM_H
#define SPSC_INLINE_ASM_H

#include <stdint.h>

/* =========================================================================
 * Architecture detection
 * ========================================================================= */

#if defined(__x86_64__) || defined(__i386__)
#  define SPSC_ARCH_X86 1
#elif defined(__aarch64__) || defined(__arm__)
#  define SPSC_ARCH_ARM 1
#elif defined(__riscv)
#  define SPSC_ARCH_RISCV 1
#endif

/* =========================================================================
 * CPU spin-loop hint
 * =========================================================================
 * Tells the CPU that this is a busy-wait loop, reducing power consumption
 * and improving hyperthreading performance on x86 (PAUSE ≈ 140 cycles of
 * pipeline drain that also clears the memory order buffer).
 * On ARM: YIELD instructs the core to offer its slot to the other SMT thread.
 */
#if defined(SPSC_ARCH_X86)
#  define SPSC_PAUSE()  __asm__ volatile("pause" ::: "memory")
#elif defined(SPSC_ARCH_ARM)
#  define SPSC_PAUSE()  __asm__ volatile("yield" ::: "memory")
#elif defined(SPSC_ARCH_RISCV)
   /* RISC-V has no dedicated spin hint; fence.i is the closest approximation */
#  define SPSC_PAUSE()  __asm__ volatile("fence.i" ::: "memory")
#else
#  include <stdatomic.h>
#  define SPSC_PAUSE()  atomic_thread_fence(memory_order_seq_cst)
#endif

/* =========================================================================
 * Prefetch helpers
 * =========================================================================
 * PREFETCHT0: fetch into L1 data cache (consumer hot path — next slot data)
 * PREFETCHW:  fetch with intent to write (producer hot path — next slot state)
 *
 * These are purely advisory; wrong hints hurt nothing but skip the benefit.
 */
#if defined(SPSC_ARCH_X86)
#  define SPSC_PREFETCH_READ(addr)  \
       __asm__ volatile("prefetcht0 %0" :: "m"(*(const char*)(addr)))
#  define SPSC_PREFETCH_WRITE(addr) \
       __asm__ volatile("prefetchw %0" :: "m"(*(char*)(addr)))
#elif defined(__GNUC__) || defined(__clang__)
   /* GCC/Clang builtin works on ARM64 and RISC-V */
#  define SPSC_PREFETCH_READ(addr)  __builtin_prefetch((addr), 0, 3)
#  define SPSC_PREFETCH_WRITE(addr) __builtin_prefetch((addr), 1, 3)
#else
#  define SPSC_PREFETCH_READ(addr)  ((void)(addr))
#  define SPSC_PREFETCH_WRITE(addr) ((void)(addr))
#endif

/* =========================================================================
 * Full memory fence (store-store + load-store barrier)
 * =========================================================================
 * Used ONLY before non-temporal stores (MOVNTI) in the overflow path to
 * ensure all prior writes are globally visible.  Do NOT use around normal
 * CAS operations — the C11 acquire/release ordering on those is sufficient.
 *
 * On x86:  MFENCE
 * On ARM:  DMB ISH (inner-shareable domain barrier)
 * On RISC-V: FENCE rw, rw
 */
#if defined(SPSC_ARCH_X86)
#  define SPSC_MFENCE() __asm__ volatile("mfence" ::: "memory")
#elif defined(SPSC_ARCH_ARM)
#  define SPSC_MFENCE() __asm__ volatile("dmb ish" ::: "memory")
#elif defined(SPSC_ARCH_RISCV)
#  define SPSC_MFENCE() __asm__ volatile("fence rw, rw" ::: "memory")
#else
#  include <stdatomic.h>
#  define SPSC_MFENCE() atomic_thread_fence(memory_order_seq_cst)
#endif

/* =========================================================================
 * Non-temporal (streaming) store — bypass cache for large overflow payloads
 * =========================================================================
 * Only available on x86.  On other architectures, falls back to a plain
 * memcpy.  Caller must issue SPSC_MFENCE() after all MOVNTI stores before
 * making the slot visible.
 *
 * Usage pattern:
 *   for (i = 0; i < n; i += 8) SPSC_NT_STORE_64(dst + i, src64[i/8]);
 *   SPSC_MFENCE();
 */
#if defined(SPSC_ARCH_X86)
#  define SPSC_NT_STORE_64(dst, val) \
       __asm__ volatile("movnti %1, %0" : "=m"(*(uint64_t*)(dst)) : "r"((uint64_t)(val)))
#else
   /* Fall back: plain store; caller must ensure alignment */
#  define SPSC_NT_STORE_64(dst, val) (*(uint64_t*)(dst) = (uint64_t)(val))
#endif

/* =========================================================================
 * CAS wrappers (thin convenience macros around C11 stdatomic)
 * =========================================================================
 * These are NOT needed for correctness (callers use stdatomic directly)
 * but document the acquire/release ordering at the call site clearly.
 *
 * SPSC_CAS_ACQ_REL(ptr, expected_ptr, desired)
 *   → strong CAS; success=acquire, fail=relaxed  (hot-path default)
 */
#include <stdatomic.h>

#define SPSC_CAS_STRONG(ptr, exp_ptr, desired)               \
    atomic_compare_exchange_strong_explicit(                  \
        (ptr), (exp_ptr), (desired),                          \
        memory_order_acquire,   /* success: acquire */        \
        memory_order_relaxed)   /* failure: relaxed */

/* Weak CAS — may spuriously fail; only use in loops where a retry is fine */
#define SPSC_CAS_WEAK(ptr, exp_ptr, desired)                  \
    atomic_compare_exchange_weak_explicit(                    \
        (ptr), (exp_ptr), (desired),                          \
        memory_order_acquire,                                 \
        memory_order_relaxed)

#endif /* SPSC_INLINE_ASM_H */
