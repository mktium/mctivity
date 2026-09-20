#ifndef MCTIVITY_ANTI_SWAY_H
#define MCTIVITY_ANTI_SWAY_H

#include <math.h>
#include <stdint.h>
#include <string.h>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

#define MCTIVITY_ZVD_MIN_PERIOD_MS 10U
#define MCTIVITY_ZVD_MAX_PERIOD_MS 10000U
#define MCTIVITY_ZVD_HISTORY_SIZE (MCTIVITY_ZVD_MAX_PERIOD_MS + 1U)

typedef struct {
    int enabled;
    uint32_t period_ms;
    uint32_t half_period_ms;
    uint32_t sample_index;
    uint32_t damping_permille;
    double amplitude[3];
    int32_t history[MCTIVITY_ZVD_HISTORY_SIZE];
} mctivity_zvd_shaper_t;

static inline int mctivity_zvd_init(
    mctivity_zvd_shaper_t *shaper,
    uint32_t period_ms,
    uint32_t damping_permille,
    int32_t initial_position)
{
    double damping;
    double k;
    double denominator;
    uint32_t index;

    if (!shaper || period_ms < MCTIVITY_ZVD_MIN_PERIOD_MS || period_ms > MCTIVITY_ZVD_MAX_PERIOD_MS ||
        damping_permille >= 1000U) {
        return 0;
    }
    damping = (double)damping_permille / 1000.0;
    k = damping == 0.0 ? 1.0 : exp(-damping * M_PI / sqrt(1.0 - damping * damping));
    denominator = (1.0 + k) * (1.0 + k);
    shaper->enabled = 1;
    shaper->period_ms = period_ms;
    shaper->half_period_ms = period_ms / 2U;
    shaper->sample_index = 0U;
    shaper->damping_permille = damping_permille;
    shaper->amplitude[0] = (k * k) / denominator;
    shaper->amplitude[1] = (2.0 * k) / denominator;
    shaper->amplitude[2] = 1.0 / denominator;
    for (index = 0U; index < MCTIVITY_ZVD_HISTORY_SIZE; index++) {
        shaper->history[index] = initial_position;
    }
    return 1;
}

static inline void mctivity_zvd_disable(mctivity_zvd_shaper_t *shaper)
{
    if (shaper) {
        shaper->enabled = 0;
    }
}

static inline int32_t mctivity_zvd_step(mctivity_zvd_shaper_t *shaper, int32_t nominal_position)
{
    uint32_t current;
    uint32_t half;
    uint32_t full;
    double shaped;

    if (!shaper || !shaper->enabled) {
        return nominal_position;
    }
    current = shaper->sample_index;
    half = (current + MCTIVITY_ZVD_HISTORY_SIZE - shaper->half_period_ms) % MCTIVITY_ZVD_HISTORY_SIZE;
    full = (current + MCTIVITY_ZVD_HISTORY_SIZE - shaper->period_ms) % MCTIVITY_ZVD_HISTORY_SIZE;
    shaper->history[current] = nominal_position;
    shaped = shaper->amplitude[0] * (double)shaper->history[current] +
             shaper->amplitude[1] * (double)shaper->history[half] +
             shaper->amplitude[2] * (double)shaper->history[full];
    shaper->sample_index = (current + 1U) % MCTIVITY_ZVD_HISTORY_SIZE;
    if (shaped > (double)INT32_MAX) {
        return INT32_MAX;
    }
    if (shaped < (double)INT32_MIN) {
        return INT32_MIN;
    }
    return (int32_t)llround(shaped);
}

#endif
