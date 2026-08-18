#ifndef MCTIVITY_REALTIME_SCHEDULE_H
#define MCTIVITY_REALTIME_SCHEDULE_H

#include <stdint.h>

typedef struct {
    uint64_t deadline_ns;
    uint64_t skipped_periods;
} mctivity_schedule_step_t;

/*
 * Return the first periodic deadline strictly after now_ns. A late caller
 * skips every expired deadline instead of issuing catch-up cycles.
 */
static inline mctivity_schedule_step_t mctivity_schedule_next(
    uint64_t previous_deadline_ns,
    uint64_t now_ns,
    uint64_t period_ns)
{
    mctivity_schedule_step_t step;
    step.deadline_ns = previous_deadline_ns + period_ns;
    step.skipped_periods = 0;

    if (step.deadline_ns <= now_ns) {
        step.skipped_periods = (now_ns - step.deadline_ns) / period_ns + 1;
        step.deadline_ns += step.skipped_periods * period_ns;
    }
    return step;
}

/*
 * Normalize a wake-up that arrived at least one full period after its
 * deadline. The expired wake deadline itself is included in the skip count.
 */
static inline mctivity_schedule_step_t mctivity_schedule_after_late_wake(
    uint64_t expired_deadline_ns,
    uint64_t now_ns,
    uint64_t period_ns)
{
    mctivity_schedule_step_t step =
        mctivity_schedule_next(expired_deadline_ns, now_ns, period_ns);
    step.skipped_periods++;
    return step;
}

#endif
