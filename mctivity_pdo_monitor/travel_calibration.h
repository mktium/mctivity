#ifndef MCTIVITY_TRAVEL_CALIBRATION_H
#define MCTIVITY_TRAVEL_CALIBRATION_H

#include <stdint.h>

typedef enum {
    MCTIVITY_TRAVEL_IDLE = 0,
    MCTIVITY_TRAVEL_APPROACHING_LEFT,
    MCTIVITY_TRAVEL_APPROACHING_RIGHT,
    MCTIVITY_TRAVEL_CONTACT_DETECTED,
    MCTIVITY_TRAVEL_STOP_REQUESTED,
    MCTIVITY_TRAVEL_COMPLETE,
    MCTIVITY_TRAVEL_FAILED,
    MCTIVITY_TRAVEL_CANCELLED
} mctivity_travel_state_t;

typedef enum {
    MCTIVITY_TRAVEL_ACTION_HOLD = 0,
    MCTIVITY_TRAVEL_ACTION_APPROACH_LEFT = -1,
    MCTIVITY_TRAVEL_ACTION_APPROACH_RIGHT = 1,
    MCTIVITY_TRAVEL_ACTION_STOP = 2,
    MCTIVITY_TRAVEL_ACTION_FAIL = 3
} mctivity_travel_action_t;

typedef enum {
    MCTIVITY_TRAVEL_FAIL_NONE = 0,
    MCTIVITY_TRAVEL_FAIL_INHIBIT,
    MCTIVITY_TRAVEL_FAIL_COMMUNICATION,
    MCTIVITY_TRAVEL_FAIL_DRIVE_FAULT,
    MCTIVITY_TRAVEL_FAIL_DISABLED,
    MCTIVITY_TRAVEL_FAIL_MISSING_CURRENT_FEEDBACK,
    MCTIVITY_TRAVEL_FAIL_TIMEOUT,
    MCTIVITY_TRAVEL_FAIL_INVALID_DIRECTION
} mctivity_travel_failure_t;

typedef struct {
    mctivity_travel_state_t state;
    mctivity_travel_failure_t failure;
    int direction;
    int32_t approach_speed_counts_s;
    int32_t current_baseline;
    int32_t current_delta_threshold;
    int32_t current_absolute_limit;
    int32_t min_position_progress_counts;
    uint32_t timeout_cycles;
    uint32_t contact_hold_cycles;
    uint32_t started_cycle;
    uint32_t contact_since_cycle;
    int32_t last_position;
    int32_t contact_position;
    int32_t contact_current;
} mctivity_travel_calibration_t;

static inline void mctivity_travel_calibration_init(mctivity_travel_calibration_t *cal)
{
    *cal = (mctivity_travel_calibration_t){
        .state = MCTIVITY_TRAVEL_IDLE,
        .failure = MCTIVITY_TRAVEL_FAIL_NONE,
        .approach_speed_counts_s = 500,
        .current_delta_threshold = 100,
        .current_absolute_limit = 1000,
        .min_position_progress_counts = 2,
        .timeout_cycles = 30000,
        .contact_hold_cycles = 40,
    };
}

static inline int mctivity_travel_calibration_active(const mctivity_travel_calibration_t *cal)
{
    return cal->state == MCTIVITY_TRAVEL_APPROACHING_LEFT ||
           cal->state == MCTIVITY_TRAVEL_APPROACHING_RIGHT ||
           cal->state == MCTIVITY_TRAVEL_CONTACT_DETECTED ||
           cal->state == MCTIVITY_TRAVEL_STOP_REQUESTED;
}

static inline mctivity_travel_action_t mctivity_travel_calibration_fail(
    mctivity_travel_calibration_t *cal,
    mctivity_travel_failure_t failure)
{
    cal->state = MCTIVITY_TRAVEL_FAILED;
    cal->failure = failure;
    cal->contact_since_cycle = 0;
    return MCTIVITY_TRAVEL_ACTION_FAIL;
}

static inline int mctivity_travel_calibration_arm(
    mctivity_travel_calibration_t *cal,
    int direction,
    uint32_t cycle,
    int32_t position,
    int32_t baseline_current,
    int current_feedback_valid,
    int commissioning_inhibit,
    int operational,
    int wc_complete,
    int fault,
    int enabled)
{
    if (commissioning_inhibit) {
        mctivity_travel_calibration_fail(cal, MCTIVITY_TRAVEL_FAIL_INHIBIT);
        return 0;
    }
    if (!operational || !wc_complete) {
        mctivity_travel_calibration_fail(cal, MCTIVITY_TRAVEL_FAIL_COMMUNICATION);
        return 0;
    }
    if (fault) {
        mctivity_travel_calibration_fail(cal, MCTIVITY_TRAVEL_FAIL_DRIVE_FAULT);
        return 0;
    }
    if (!enabled) {
        mctivity_travel_calibration_fail(cal, MCTIVITY_TRAVEL_FAIL_DISABLED);
        return 0;
    }
    if (!current_feedback_valid) {
        mctivity_travel_calibration_fail(cal, MCTIVITY_TRAVEL_FAIL_MISSING_CURRENT_FEEDBACK);
        return 0;
    }
    if (direction != -1 && direction != 1) {
        mctivity_travel_calibration_fail(cal, MCTIVITY_TRAVEL_FAIL_INVALID_DIRECTION);
        return 0;
    }
    cal->state = direction < 0 ? MCTIVITY_TRAVEL_APPROACHING_LEFT : MCTIVITY_TRAVEL_APPROACHING_RIGHT;
    cal->failure = MCTIVITY_TRAVEL_FAIL_NONE;
    cal->direction = direction;
    cal->current_baseline = baseline_current;
    cal->started_cycle = cycle;
    cal->contact_since_cycle = 0;
    cal->last_position = position;
    cal->contact_position = position;
    cal->contact_current = baseline_current;
    return 1;
}

static inline mctivity_travel_action_t mctivity_travel_calibration_step(
    mctivity_travel_calibration_t *cal,
    uint32_t cycle,
    int32_t position,
    int32_t current,
    int current_feedback_valid,
    int operational,
    int wc_complete,
    int fault,
    int enabled)
{
    int current_high;
    int stalled;
    if (!mctivity_travel_calibration_active(cal)) {
        return MCTIVITY_TRAVEL_ACTION_HOLD;
    }
    if (!operational || !wc_complete) {
        return mctivity_travel_calibration_fail(cal, MCTIVITY_TRAVEL_FAIL_COMMUNICATION);
    }
    if (fault) {
        return mctivity_travel_calibration_fail(cal, MCTIVITY_TRAVEL_FAIL_DRIVE_FAULT);
    }
    if (!enabled) {
        return mctivity_travel_calibration_fail(cal, MCTIVITY_TRAVEL_FAIL_DISABLED);
    }
    if (!current_feedback_valid) {
        return mctivity_travel_calibration_fail(cal, MCTIVITY_TRAVEL_FAIL_MISSING_CURRENT_FEEDBACK);
    }
    if (cycle - cal->started_cycle > cal->timeout_cycles) {
        return mctivity_travel_calibration_fail(cal, MCTIVITY_TRAVEL_FAIL_TIMEOUT);
    }
    if (cal->state == MCTIVITY_TRAVEL_CONTACT_DETECTED || cal->state == MCTIVITY_TRAVEL_STOP_REQUESTED) {
        return MCTIVITY_TRAVEL_ACTION_STOP;
    }
    current_high = current >= cal->current_baseline + cal->current_delta_threshold ||
                   current >= cal->current_absolute_limit;
    stalled = cal->last_position - position <= cal->min_position_progress_counts &&
              position - cal->last_position <= cal->min_position_progress_counts;
    if (current_high && stalled) {
        if (cal->contact_since_cycle == 0) {
            cal->contact_since_cycle = cycle;
        }
        if (cycle - cal->contact_since_cycle >= cal->contact_hold_cycles) {
            cal->state = MCTIVITY_TRAVEL_CONTACT_DETECTED;
            cal->contact_position = position;
            cal->contact_current = current;
            return MCTIVITY_TRAVEL_ACTION_STOP;
        }
    } else {
        cal->contact_since_cycle = 0;
    }
    cal->last_position = position;
    return cal->direction < 0 ? MCTIVITY_TRAVEL_ACTION_APPROACH_LEFT : MCTIVITY_TRAVEL_ACTION_APPROACH_RIGHT;
}

static inline void mctivity_travel_calibration_cancel(mctivity_travel_calibration_t *cal)
{
    cal->state = MCTIVITY_TRAVEL_CANCELLED;
    cal->failure = MCTIVITY_TRAVEL_FAIL_NONE;
}

static inline int mctivity_travel_calibration_complete(mctivity_travel_calibration_t *cal)
{
    if (cal->state != MCTIVITY_TRAVEL_CONTACT_DETECTED && cal->state != MCTIVITY_TRAVEL_STOP_REQUESTED) {
        return 0;
    }
    cal->state = MCTIVITY_TRAVEL_COMPLETE;
    return 1;
}

#endif
