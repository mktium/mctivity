#ifndef MCTIVITY_ELECTRONIC_GEAR_H
#define MCTIVITY_ELECTRONIC_GEAR_H

#include <stdint.h>

#define MCTIVITY_GEAR_DEFAULT_FOLLOWING_ERROR_LIMIT_COUNTS 200
#define MCTIVITY_GEAR_DEFAULT_MAX_RATIO 200

static inline uint64_t mctivity_gear_max_target_step_counts(
    uint32_t max_velocity_cps,
    uint32_t elapsed_cycles)
{
    uint64_t cycles = elapsed_cycles ? elapsed_cycles : 1U;
    uint64_t numerator;
    uint64_t max_step;
    if ((uint64_t)max_velocity_cps > UINT64_MAX / cycles) {
        return UINT64_MAX;
    }
    numerator = (uint64_t)max_velocity_cps * cycles;
    if (numerator > UINT64_MAX - 999U) {
        return UINT64_MAX;
    }
    max_step = (numerator + 999U) / 1000U;
    return max_step > UINT64_MAX - 2U ? UINT64_MAX : max_step + 2U;
}

/*
 * During an active D/E gear session the follower must remain in gear_cam,
 * while the master is still allowed to receive its normal control mode.
 * Motion already in progress is always a reason to reject a mode change.
 */
static inline int mctivity_gear_mode_change_requires_stop(
    int gear_session_active,
    int axis,
    int gear_master_axis,
    int requested_gear_mode,
    int moving,
    int jog_velocity_active,
    int stop_velocity_active)
{
    if (moving || jog_velocity_active || stop_velocity_active) {
        return 1;
    }
    return gear_session_active && axis != gear_master_axis && !requested_gear_mode;
}

typedef struct {
    int initialized;
    int direction;
    int32_t master_ratio;
    int32_t slave_ratio;
    int32_t master_last_raw;
    int64_t master_unwrapped;
    int64_t master_origin;
    int32_t slave_origin;
} mctivity_electronic_gear_t;

static inline int64_t mctivity_gear_signed_delta(int32_t current, int32_t previous)
{
    int64_t delta = (int64_t)(uint32_t)current - (int64_t)(uint32_t)previous;
    if (delta > INT32_MAX) {
        delta -= (INT64_C(1) << 32);
    } else if (delta < INT32_MIN) {
        delta += (INT64_C(1) << 32);
    }
    return delta;
}

static inline int mctivity_gear_configure(
    mctivity_electronic_gear_t *gear,
    int direction,
    int32_t master_ratio,
    int32_t slave_ratio)
{
    if (!gear || (direction != 1 && direction != -1) || master_ratio < 1 ||
        slave_ratio < 1 || master_ratio > MCTIVITY_GEAR_DEFAULT_MAX_RATIO ||
        slave_ratio > MCTIVITY_GEAR_DEFAULT_MAX_RATIO) {
        return 0;
    }
    gear->initialized = 0;
    gear->direction = direction;
    gear->master_ratio = master_ratio;
    gear->slave_ratio = slave_ratio;
    gear->master_last_raw = 0;
    gear->master_unwrapped = 0;
    gear->master_origin = 0;
    gear->slave_origin = 0;
    return 1;
}

static inline int mctivity_gear_start(
    mctivity_electronic_gear_t *gear,
    int32_t master_raw,
    int32_t slave_raw)
{
    if (!gear || gear->master_ratio < 1 || gear->slave_ratio < 1) {
        return 0;
    }
    gear->initialized = 1;
    gear->master_last_raw = master_raw;
    gear->master_unwrapped = 0;
    gear->master_origin = 0;
    gear->slave_origin = slave_raw;
    return 1;
}

static inline int mctivity_gear_target(
    mctivity_electronic_gear_t *gear,
    int32_t master_raw,
    int32_t *target_raw)
{
    int64_t delta;
    int64_t displacement;
    int64_t scaled;
    int64_t target;
    if (!gear || !gear->initialized || !target_raw) {
        return 0;
    }
    delta = mctivity_gear_signed_delta(master_raw, gear->master_last_raw);
    gear->master_last_raw = master_raw;
    gear->master_unwrapped += delta;
    displacement = gear->master_unwrapped - gear->master_origin;
    scaled = displacement * (int64_t)gear->slave_ratio * (int64_t)gear->direction;
    target = (int64_t)gear->slave_origin + scaled / (int64_t)gear->master_ratio;
    if (target > INT32_MAX || target < INT32_MIN) {
        return 0;
    }
    *target_raw = (int32_t)target;
    return 1;
}

static inline int64_t mctivity_gear_abs_error(int32_t target_raw, int32_t actual_raw)
{
    int64_t error = mctivity_gear_signed_delta(target_raw, actual_raw);
    return error < 0 ? -error : error;
}

#endif
