#include <assert.h>

#include "phase_search_guard.h"

int main(void)
{
    assert(mctivity_phase_search_enable_allowed(0, 0));
    assert(!mctivity_phase_search_enable_allowed(1, 0));
    assert(mctivity_phase_search_enable_allowed(1, 1));

    assert(mctivity_phase_search_confirmation_allowed(1, 0, 0, 0, 0, 1, 1, 1, 0));
    assert(!mctivity_phase_search_confirmation_allowed(0, 0, 0, 0, 0, 1, 1, 1, 0));
    assert(!mctivity_phase_search_confirmation_allowed(1, 1, 0, 0, 0, 1, 1, 1, 0));
    assert(!mctivity_phase_search_confirmation_allowed(1, 0, 1, 0, 0, 1, 1, 1, 0));
    assert(!mctivity_phase_search_confirmation_allowed(1, 0, 0, 1, 0, 1, 1, 1, 0));
    assert(!mctivity_phase_search_confirmation_allowed(1, 0, 0, 0, 1, 1, 1, 1, 0));
    assert(!mctivity_phase_search_confirmation_allowed(1, 0, 0, 0, 0, 0, 1, 1, 0));
    assert(!mctivity_phase_search_confirmation_allowed(1, 0, 0, 0, 0, 1, 0, 1, 0));
    assert(!mctivity_phase_search_confirmation_allowed(1, 0, 0, 0, 0, 1, 1, 0, 0));
    assert(!mctivity_phase_search_confirmation_allowed(1, 0, 0, 0, 0, 1, 1, 1, 1));
    return 0;
}
