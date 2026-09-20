#include <assert.h>
#include <math.h>

#include "anti_sway.h"

int main(void)
{
    mctivity_zvd_shaper_t shaper;
    int32_t output = 100;
    unsigned int index;

    assert(mctivity_zvd_init(&shaper, 1150, 50, 100));
    assert(shaper.half_period_ms == 575U);
    assert(fabs(shaper.amplitude[0] + shaper.amplitude[1] + shaper.amplitude[2] - 1.0) < 1e-12);
    output = mctivity_zvd_step(&shaper, 1100);
    assert(output >= 100 && output <= 1100);
    for (index = 0U; index < 1152U; index++) {
        output = mctivity_zvd_step(&shaper, 1100);
    }
    assert(output == 1100);

    assert(!mctivity_zvd_init(&shaper, 9, 50, 0));
    assert(!mctivity_zvd_init(&shaper, 10001, 50, 0));
    assert(!mctivity_zvd_init(&shaper, 1150, 1000, 0));
    mctivity_zvd_disable(&shaper);
    assert(mctivity_zvd_step(&shaper, -321) == -321);
    return 0;
}
