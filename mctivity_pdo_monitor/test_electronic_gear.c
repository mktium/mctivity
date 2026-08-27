#include <assert.h>
#include <stdint.h>

#include "electronic_gear.h"

int main(void)
{
    mctivity_electronic_gear_t gear;
    int32_t target;

    assert(mctivity_gear_signed_delta(INT32_MIN, INT32_MAX) == 1);
    assert(mctivity_gear_signed_delta(INT32_MAX, INT32_MIN) == -1);
    assert(mctivity_gear_configure(&gear, 1, 1, 1));
    assert(mctivity_gear_start(&gear, 100, 1000));
    assert(mctivity_gear_target(&gear, 101, &target) && target == 1001);
    assert(mctivity_gear_target(&gear, 103, &target) && target == 1003);

    assert(mctivity_gear_configure(&gear, -1, 2, 3));
    assert(mctivity_gear_start(&gear, 200, -500));
    assert(mctivity_gear_target(&gear, 201, &target) && target == -501);
    assert(mctivity_gear_target(&gear, 203, &target) && target == -504);

    assert(mctivity_gear_configure(&gear, 1, 3, 2));
    assert(mctivity_gear_start(&gear, INT32_MAX, 0));
    assert(mctivity_gear_target(&gear, INT32_MIN, &target) && target == 0);
    assert(mctivity_gear_target(&gear, INT32_MIN + 1, &target) && target == 1);
    assert(mctivity_gear_abs_error(-100, 100) == 200);
    assert(!mctivity_gear_mode_change_requires_stop(1, 0, 0, 0, 0, 0, 0));
    assert(mctivity_gear_mode_change_requires_stop(1, 1, 0, 0, 0, 0, 0));
    assert(!mctivity_gear_mode_change_requires_stop(1, 1, 0, 1, 0, 0, 0));
    assert(mctivity_gear_mode_change_requires_stop(1, 0, 0, 0, 1, 0, 0));
    assert(!mctivity_gear_mode_change_requires_stop(0, 1, 0, 0, 0, 0, 0));
    assert(!mctivity_gear_configure(&gear, 0, 1, 1));
    assert(!mctivity_gear_configure(&gear, 1, 0, 1));
    assert(!mctivity_gear_configure(&gear, 1, 201, 1));
    return 0;
}
