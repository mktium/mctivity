#ifndef MCTIVITY_REALTIME_GUARD_H
#define MCTIVITY_REALTIME_GUARD_H

#include <limits.h>
#include <stdint.h>

/* A single 1 ms host wake-up slip is not an EtherCAT link failure.  Require
 * several consecutive missed periods while control is active before the
 * scheduler itself becomes a fail-closed fault.  OP/WC/link failures remain
 * handled by the communication guard without this grace window. */
#define MCTIVITY_SCHEDULE_MISS_LATCH_THRESHOLD 3U

typedef struct {
    unsigned int consecutive_misses;
    int fault_latched;
} mctivity_schedule_guard_t;

static inline void mctivity_schedule_guard_note_miss(
    mctivity_schedule_guard_t *guard,
    uint64_t skipped_periods,
    int control_active)
{
    uint64_t total;

    if (skipped_periods == 0) {
        return;
    }
    if (!control_active) {
        guard->consecutive_misses = 0;
        return;
    }
    total = (uint64_t)guard->consecutive_misses + skipped_periods;
    guard->consecutive_misses = total > UINT_MAX ? UINT_MAX : (unsigned int)total;
    if (guard->consecutive_misses >= MCTIVITY_SCHEDULE_MISS_LATCH_THRESHOLD) {
        guard->fault_latched = 1;
    }
}

static inline void mctivity_schedule_guard_note_good_cycle(
    mctivity_schedule_guard_t *guard)
{
    if (!guard->fault_latched) {
        guard->consecutive_misses = 0;
    }
}

#endif
