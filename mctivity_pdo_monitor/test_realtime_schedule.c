#include <assert.h>
#include <stdint.h>
#include <stdio.h>

#include "realtime_schedule.h"

static void expect_step(
    uint64_t previous_deadline_ns,
    uint64_t now_ns,
    uint64_t expected_deadline_ns,
    uint64_t expected_skipped_periods)
{
    const uint64_t period_ns = 1000000ULL;
    mctivity_schedule_step_t step =
        mctivity_schedule_next(previous_deadline_ns, now_ns, period_ns);
    assert(step.deadline_ns == expected_deadline_ns);
    assert(step.skipped_periods == expected_skipped_periods);
}

static void expect_late_wake(
    uint64_t expired_deadline_ns,
    uint64_t now_ns,
    uint64_t expected_deadline_ns,
    uint64_t expected_skipped_periods)
{
    const uint64_t period_ns = 1000000ULL;
    mctivity_schedule_step_t step =
        mctivity_schedule_after_late_wake(expired_deadline_ns, now_ns, period_ns);
    assert(step.deadline_ns == expected_deadline_ns);
    assert(step.skipped_periods == expected_skipped_periods);
}

int main(void)
{
    const uint64_t base = 1000000000ULL;

    expect_step(base, base + 200000ULL, base + 1000000ULL, 0);
    expect_step(base, base + 1200000ULL, base + 2000000ULL, 1);
    expect_step(base, base + 5400000ULL, base + 6000000ULL, 5);
    expect_step(base, base + 1000000ULL, base + 2000000ULL, 1);

    expect_late_wake(base, base + 1200000ULL, base + 2000000ULL, 2);
    expect_late_wake(base, base + 5400000ULL, base + 6000000ULL, 6);

    puts("realtime schedule tests passed");
    return 0;
}
