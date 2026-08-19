#ifndef MCTIVITY_PHASE_SEARCH_GUARD_H
#define MCTIVITY_PHASE_SEARCH_GUARD_H

static inline int mctivity_phase_search_enable_allowed(int axis_d_topology, int confirmed)
{
    return !axis_d_topology || confirmed;
}

static inline int mctivity_phase_search_confirmation_allowed(
    int axis_d_topology,
    int servo_request,
    int enabled,
    int moving,
    int fault,
    int operational,
    int wc_complete,
    int timing_guard_armed,
    int communication_timing_fault)
{
    return axis_d_topology && !servo_request && !enabled && !moving && !fault && operational && wc_complete &&
           timing_guard_armed && !communication_timing_fault;
}

#endif
