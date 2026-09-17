#include <assert.h>

#include "travel_calibration.h"

int main(void)
{
    mctivity_travel_calibration_t cal;
    mctivity_travel_calibration_init(&cal);

    assert(!mctivity_travel_calibration_arm(&cal, 1, 0, 0, 10, 0, 0, 1, 1, 0, 1));
    assert(cal.failure == MCTIVITY_TRAVEL_FAIL_MISSING_CURRENT_FEEDBACK);

    mctivity_travel_calibration_init(&cal);
    assert(mctivity_travel_calibration_arm(&cal, -1, 0, 100, 10, 1, 0, 1, 1, 0, 1));
    assert(mctivity_travel_calibration_step(&cal, 1, 99, 10, 1, 1, 1, 0, 1) == MCTIVITY_TRAVEL_ACTION_APPROACH_LEFT);
    assert(mctivity_travel_calibration_step(&cal, 2, 99, 120, 1, 1, 1, 0, 1) == MCTIVITY_TRAVEL_ACTION_APPROACH_LEFT);
    assert(mctivity_travel_calibration_step(&cal, 42, 99, 120, 1, 1, 1, 0, 1) == MCTIVITY_TRAVEL_ACTION_STOP);
    assert(cal.state == MCTIVITY_TRAVEL_CONTACT_DETECTED);
    assert(cal.contact_position == 99);
    assert(mctivity_travel_calibration_complete(&cal));
    assert(cal.state == MCTIVITY_TRAVEL_COMPLETE);

    mctivity_travel_calibration_init(&cal);
    assert(mctivity_travel_calibration_arm(&cal, 1, 0, 0, 10, 1, 0, 1, 1, 0, 1));
    assert(mctivity_travel_calibration_step(&cal, 1, 100, 200, 1, 1, 1, 0, 1) == MCTIVITY_TRAVEL_ACTION_APPROACH_RIGHT);
    assert(mctivity_travel_calibration_step(&cal, 30001, 100, 10, 1, 1, 1, 0, 1) == MCTIVITY_TRAVEL_ACTION_FAIL);
    assert(cal.failure == MCTIVITY_TRAVEL_FAIL_TIMEOUT);

    mctivity_travel_calibration_init(&cal);
    assert(mctivity_travel_calibration_arm(&cal, 1, 0, 0, 10, 1, 0, 1, 1, 0, 1));
    assert(mctivity_travel_calibration_step(&cal, 1, 0, 10, 0, 1, 1, 0, 1) == MCTIVITY_TRAVEL_ACTION_FAIL);
    assert(cal.failure == MCTIVITY_TRAVEL_FAIL_MISSING_CURRENT_FEEDBACK);
    return 0;
}
