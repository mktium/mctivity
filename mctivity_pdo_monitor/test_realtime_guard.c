#include <assert.h>

#include "realtime_guard.h"

int main(void)
{
    mctivity_schedule_guard_t guard = {0};

    mctivity_schedule_guard_note_miss(&guard, 2, 1);
    assert(guard.consecutive_misses == 2);
    assert(!guard.fault_latched);

    mctivity_schedule_guard_note_good_cycle(&guard);
    assert(guard.consecutive_misses == 0);
    assert(!guard.fault_latched);

    mctivity_schedule_guard_note_miss(&guard, 2, 0);
    assert(guard.consecutive_misses == 0);
    assert(!guard.fault_latched);

    mctivity_schedule_guard_note_miss(&guard, 1, 1);
    assert(guard.consecutive_misses == 1);
    mctivity_schedule_guard_note_miss(&guard, 1, 1);
    assert(guard.consecutive_misses == 2);
    assert(!guard.fault_latched);
    mctivity_schedule_guard_note_miss(&guard, 1, 1);
    assert(guard.consecutive_misses == 3);
    assert(guard.fault_latched);

    mctivity_schedule_guard_note_good_cycle(&guard);
    assert(guard.consecutive_misses == 3);
    assert(guard.fault_latched);
    return 0;
}
