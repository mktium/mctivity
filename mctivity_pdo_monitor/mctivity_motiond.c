#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <math.h>
#include <netinet/in.h>
#include <sched.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>

#include <ecrt.h>

#include "realtime_schedule.h"

#define MCTIVITY_VENDOR_ID 0x000116c7
#define MCTIVITY_PRODUCT_CODE 0x007e0402
#define FV3_VENDOR_ID 0x00000ebc
#define FV3_PRODUCT_CODE 0x00000010
#define USERVO_VENDOR_ID 0x00666999
#define USERVO_PRODUCT_CODE 0x00004806
#define USERVO_COUNTS_PER_REV 10000LL

#define AXIS_MCTIVITY 0
#define AXIS_FV3 1
#define AXIS_COUNT 2

#define PERIOD_NS 1000000L
#define NSEC_PER_SEC 1000000000LL
#define SERVER_PORT 10001
#define MAX_CLIENTS 8
#define RX_BUF 1024
#define DEFAULT_MOVE_MS 3000
#define ENABLE_SETTLE_CYCLES 300U
#define DEFAULT_JOG_VELOCITY 200000
#define LEGACY_COUNTS_PER_REV 8388608LL
#define DEFAULT_STOP_DECEL_RPM_S 300U
#define CURVE_BLEND_LINEAR 0
#define CURVE_BLEND_SMOOTH 1
#define CURVE_BLEND_AGGRESSIVE 2
#define AXIS_D_GOOD_CYCLES_TO_ARM 1000U
#define AXIS_D_SERVER_ACCEPT_BUDGET 1U
#define AXIS_D_SERVER_COMMAND_BUDGET 2U
#define AXIS_D_SHUTDOWN_CYCLES 20U

static volatile sig_atomic_t running = 1;
static int uservo_axis_d_topology = 0;
static int commissioning_inhibit = 0;
static int require_realtime = 0;
static int64_t counts_per_rev = LEGACY_COUNTS_PER_REV;

typedef struct {
    uint64_t deadline_miss_count;
    uint64_t skipped_periods;
    uint64_t last_wake_lateness_ns;
    uint64_t max_wake_lateness_ns;
    uint64_t last_cycle_runtime_ns;
    uint64_t max_cycle_runtime_ns;
    uint64_t wc_change_count;
    uint64_t wc_incomplete_cycles;
    uint64_t consecutive_good_cycles;
    unsigned int previous_wc;
    int have_previous_wc;
    int timing_guard_armed;
    int communication_timing_fault;
    int memory_locked;
    int scheduler_policy;
    int scheduler_priority;
} realtime_status_t;

static realtime_status_t realtime_status;

/* MCTIVITY PDO offsets (slave 0). */
static unsigned int mctivity_off_controlword;
static unsigned int mctivity_off_mode;
static unsigned int mctivity_off_target_position;
static unsigned int mctivity_off_touch_probe_function;
static unsigned int mctivity_off_error_code;
static unsigned int mctivity_off_statusword;
static unsigned int mctivity_off_mode_display;
static unsigned int mctivity_off_position_actual;
static unsigned int mctivity_off_touch_probe_status;
static unsigned int mctivity_off_touch_probe_pos1;
static unsigned int mctivity_off_following_error;

/* FV3 PDO offsets (slave 1). */
static unsigned int fv3_off_controlword;
static unsigned int fv3_off_target_position;
static unsigned int fv3_off_touch_probe_function;
static unsigned int fv3_off_digital_output;
static unsigned int fv3_off_error_code;
static unsigned int fv3_off_statusword;
static unsigned int fv3_off_position_actual;
static unsigned int fv3_off_torque_actual;
static unsigned int fv3_off_following_error;
static unsigned int fv3_off_touch_probe_status;
static unsigned int fv3_off_touch_probe_pos1;
static unsigned int fv3_off_touch_probe_pos2;
static unsigned int fv3_off_digital_input;

static ec_pdo_entry_info_t mctivity_pdo_entries[] = {
    {0x6040, 0x00, 16}, {0x6060, 0x00, 8},  {0x607a, 0x00, 32},
    {0x60b8, 0x00, 16}, {0x0000, 0x00, 0},  {0x0000, 0x00, 0},
    {0x0000, 0x00, 0},  {0x0000, 0x00, 0},  {0x0000, 0x00, 0},
    {0x0000, 0x00, 0},  {0x0000, 0x00, 0},  {0x0000, 0x00, 0},
    {0x603f, 0x00, 16}, {0x6041, 0x00, 16}, {0x6061, 0x00, 8},
    {0x6064, 0x00, 32}, {0x60b9, 0x00, 16}, {0x60ba, 0x00, 32},
    {0x60f4, 0x00, 32}, {0x0000, 0x00, 0},  {0x0000, 0x00, 0},
    {0x0000, 0x00, 0},  {0x0000, 0x00, 0},  {0x0000, 0x00, 0},
};

static ec_pdo_info_t mctivity_pdos[] = {
    {0x1600, 12, mctivity_pdo_entries + 0},
    {0x1a00, 12, mctivity_pdo_entries + 12},
};

static ec_sync_info_t mctivity_syncs[] = {
    {0, EC_DIR_OUTPUT, 0, NULL, EC_WD_DISABLE},
    {1, EC_DIR_INPUT, 0, NULL, EC_WD_DISABLE},
    {2, EC_DIR_OUTPUT, 1, mctivity_pdos + 0, EC_WD_ENABLE},
    {3, EC_DIR_INPUT, 1, mctivity_pdos + 1, EC_WD_DISABLE},
    {0xff, 0, 0, NULL, 0}
};

static ec_pdo_entry_info_t fv3_pdo_entries[] = {
    {0x6040, 0x00, 16}, {0x607a, 0x00, 32}, {0x60b8, 0x00, 16}, {0x60fe, 0x01, 32},
    {0x603f, 0x00, 16}, {0x6041, 0x00, 16}, {0x6064, 0x00, 32}, {0x6077, 0x00, 16},
    {0x60f4, 0x00, 32}, {0x60b9, 0x00, 16}, {0x60ba, 0x00, 32}, {0x60bc, 0x00, 32},
    {0x60fd, 0x00, 32},
};

static ec_pdo_info_t fv3_pdos[] = {
    {0x1701, 4, fv3_pdo_entries + 0},
    {0x1b01, 9, fv3_pdo_entries + 4},
};

static ec_sync_info_t fv3_syncs[] = {
    {0, EC_DIR_OUTPUT, 0, NULL, EC_WD_DISABLE},
    {1, EC_DIR_INPUT, 0, NULL, EC_WD_DISABLE},
    {2, EC_DIR_OUTPUT, 1, fv3_pdos + 0, EC_WD_ENABLE},
    {3, EC_DIR_INPUT, 1, fv3_pdos + 1, EC_WD_DISABLE},
    {0xff, 0, 0, NULL, 0}
};

/* Uservo DS1-E4806N axis D PDOs (single slave at physical position 0). */
static unsigned int uservo_off_controlword;
static unsigned int uservo_off_mode;
static unsigned int uservo_off_target_position;
static unsigned int uservo_off_digital_output;
static unsigned int uservo_off_statusword;
static unsigned int uservo_off_mode_display;
static unsigned int uservo_off_position_actual;
static unsigned int uservo_off_digital_input;

static ec_pdo_entry_info_t uservo_pdo_entries[] = {
    {0x6040, 0x00, 16}, {0x6060, 0x00, 8}, {0x607a, 0x00, 32}, {0x60fe, 0x01, 32},
    {0x6041, 0x00, 16}, {0x6061, 0x00, 8}, {0x6064, 0x00, 32}, {0x60fd, 0x00, 32},
};

static ec_pdo_info_t uservo_pdos[] = {
    {0x1600, 4, uservo_pdo_entries + 0},
    {0x1a00, 4, uservo_pdo_entries + 4},
};

static ec_sync_info_t uservo_syncs[] = {
    {0, EC_DIR_OUTPUT, 0, NULL, EC_WD_DISABLE},
    {1, EC_DIR_INPUT, 0, NULL, EC_WD_DISABLE},
    {2, EC_DIR_OUTPUT, 1, uservo_pdos + 0, EC_WD_ENABLE},
    {3, EC_DIR_INPUT, 1, uservo_pdos + 1, EC_WD_DISABLE},
    {0xff, 0, 0, NULL, 0}
};

static const ec_pdo_entry_reg_t mctivity_domain_regs[] = {
    {0, 0, MCTIVITY_VENDOR_ID, MCTIVITY_PRODUCT_CODE, 0x6040, 0, &mctivity_off_controlword, NULL},
    {0, 0, MCTIVITY_VENDOR_ID, MCTIVITY_PRODUCT_CODE, 0x6060, 0, &mctivity_off_mode, NULL},
    {0, 0, MCTIVITY_VENDOR_ID, MCTIVITY_PRODUCT_CODE, 0x607a, 0, &mctivity_off_target_position, NULL},
    {0, 0, MCTIVITY_VENDOR_ID, MCTIVITY_PRODUCT_CODE, 0x60b8, 0, &mctivity_off_touch_probe_function, NULL},
    {0, 0, MCTIVITY_VENDOR_ID, MCTIVITY_PRODUCT_CODE, 0x603f, 0, &mctivity_off_error_code, NULL},
    {0, 0, MCTIVITY_VENDOR_ID, MCTIVITY_PRODUCT_CODE, 0x6041, 0, &mctivity_off_statusword, NULL},
    {0, 0, MCTIVITY_VENDOR_ID, MCTIVITY_PRODUCT_CODE, 0x6061, 0, &mctivity_off_mode_display, NULL},
    {0, 0, MCTIVITY_VENDOR_ID, MCTIVITY_PRODUCT_CODE, 0x6064, 0, &mctivity_off_position_actual, NULL},
    {0, 0, MCTIVITY_VENDOR_ID, MCTIVITY_PRODUCT_CODE, 0x60b9, 0, &mctivity_off_touch_probe_status, NULL},
    {0, 0, MCTIVITY_VENDOR_ID, MCTIVITY_PRODUCT_CODE, 0x60ba, 0, &mctivity_off_touch_probe_pos1, NULL},
    {0, 0, MCTIVITY_VENDOR_ID, MCTIVITY_PRODUCT_CODE, 0x60f4, 0, &mctivity_off_following_error, NULL},
    {}
};

static const ec_pdo_entry_reg_t fv3_domain_regs[] = {
    {0, 1, FV3_VENDOR_ID, FV3_PRODUCT_CODE, 0x6040, 0, &fv3_off_controlword, NULL},
    {0, 1, FV3_VENDOR_ID, FV3_PRODUCT_CODE, 0x607a, 0, &fv3_off_target_position, NULL},
    {0, 1, FV3_VENDOR_ID, FV3_PRODUCT_CODE, 0x60b8, 0, &fv3_off_touch_probe_function, NULL},
    {0, 1, FV3_VENDOR_ID, FV3_PRODUCT_CODE, 0x60fe, 1, &fv3_off_digital_output, NULL},
    {0, 1, FV3_VENDOR_ID, FV3_PRODUCT_CODE, 0x603f, 0, &fv3_off_error_code, NULL},
    {0, 1, FV3_VENDOR_ID, FV3_PRODUCT_CODE, 0x6041, 0, &fv3_off_statusword, NULL},
    {0, 1, FV3_VENDOR_ID, FV3_PRODUCT_CODE, 0x6064, 0, &fv3_off_position_actual, NULL},
    {0, 1, FV3_VENDOR_ID, FV3_PRODUCT_CODE, 0x6077, 0, &fv3_off_torque_actual, NULL},
    {0, 1, FV3_VENDOR_ID, FV3_PRODUCT_CODE, 0x60f4, 0, &fv3_off_following_error, NULL},
    {0, 1, FV3_VENDOR_ID, FV3_PRODUCT_CODE, 0x60b9, 0, &fv3_off_touch_probe_status, NULL},
    {0, 1, FV3_VENDOR_ID, FV3_PRODUCT_CODE, 0x60ba, 0, &fv3_off_touch_probe_pos1, NULL},
    {0, 1, FV3_VENDOR_ID, FV3_PRODUCT_CODE, 0x60bc, 0, &fv3_off_touch_probe_pos2, NULL},
    {0, 1, FV3_VENDOR_ID, FV3_PRODUCT_CODE, 0x60fd, 0, &fv3_off_digital_input, NULL},
    {}
};

static const ec_pdo_entry_reg_t uservo_domain_regs[] = {
    {0, 0, USERVO_VENDOR_ID, USERVO_PRODUCT_CODE, 0x6040, 0, &uservo_off_controlword, NULL},
    {0, 0, USERVO_VENDOR_ID, USERVO_PRODUCT_CODE, 0x6060, 0, &uservo_off_mode, NULL},
    {0, 0, USERVO_VENDOR_ID, USERVO_PRODUCT_CODE, 0x607a, 0, &uservo_off_target_position, NULL},
    {0, 0, USERVO_VENDOR_ID, USERVO_PRODUCT_CODE, 0x60fe, 1, &uservo_off_digital_output, NULL},
    {0, 0, USERVO_VENDOR_ID, USERVO_PRODUCT_CODE, 0x6041, 0, &uservo_off_statusword, NULL},
    {0, 0, USERVO_VENDOR_ID, USERVO_PRODUCT_CODE, 0x6061, 0, &uservo_off_mode_display, NULL},
    {0, 0, USERVO_VENDOR_ID, USERVO_PRODUCT_CODE, 0x6064, 0, &uservo_off_position_actual, NULL},
    {0, 0, USERVO_VENDOR_ID, USERVO_PRODUCT_CODE, 0x60fd, 0, &uservo_off_digital_input, NULL},
    {}
};

typedef struct {
    int fd;
    char buf[RX_BUF];
    size_t len;
} client_t;

typedef struct {
    uint16_t sw;
    uint16_t err;
    uint16_t cw;
    int8_t mode_display;
    int32_t pos_raw;
    int32_t pos_user;
    int32_t target_raw;
    int32_t target_user;
    int32_t following_error;
    int16_t torque_feedback;
    unsigned int al_state;
    unsigned int operational;
    unsigned int wc;
    int wc_complete;
    int servo_request;
    int enabled;
    uint32_t enable_settle_cycles;
    int moving;
    int fault;
    int32_t soft_zero_raw;
    int32_t jog_velocity_cps;
    int32_t torque_cmd;
    int homed;
    uint32_t cycles;
    char control_mode[24];
    char last_command[64];
    char message[160];
} status_t;

typedef struct {
    int moving;
    int profile_active;
    int curve_active;
    uint32_t step;
    uint32_t steps;
    int32_t from;
    int32_t to;
    int32_t current_velocity_cps;
    int32_t max_velocity_cps;
    uint32_t accel_cps2;
    uint32_t decel_cps2;
    int32_t min_target_user;
    int32_t max_target_user;
    int curve_blend;
    uint32_t curve_dwell_ms;
    uint32_t curve_dwell_elapsed_ms;
    double curve_elapsed_s;
    double curve_t_acc_s;
    double curve_t_cruise_s;
    double curve_t_dec_s;
    double curve_total_motion_s;
    double curve_distance_counts;
    double curve_position_counts;
    double curve_vpeak_cps;
    double curve_accel_cps2_f;
    double curve_decel_cps2_f;
} motion_t;

typedef struct {
    status_t st;
    motion_t motion;
    int32_t velocity_remainder;
    uint32_t fault_reset_cycles;
    int32_t stop_velocity_cps;
    uint32_t stop_decel_cps2;
    int32_t target_velocity_cps;
    int have_last_cycle_target;
    int8_t commanded_mode;
    int pp_pulse_cycles;
    int fv3_halt_cycles;
    int fv3_have_last_pos;
    int32_t fv3_last_pos_raw;
    int32_t fv3_feedback_velocity_cps;
    uint32_t fv3_motion_hold_cycles;
    int gear_running;
    int gear_master_axis;
    int32_t gear_master_ratio;
    int32_t gear_slave_ratio;
    int gear_has_last_master_pos;
    int32_t gear_last_master_pos_raw;
} axis_runtime_t;

static axis_runtime_t axes[AXIS_COUNT];
static client_t clients[MAX_CLIENTS];
static int listen_fd = -1;

static void handle_signal(int sig)
{
    (void)sig;
    running = 0;
}

static int env_flag_default(const char *name, int fallback)
{
    const char *value = getenv(name);
    if (!value || !*value) {
        return fallback;
    }
    if (strcmp(value, "0") == 0 || strcmp(value, "false") == 0 || strcmp(value, "no") == 0 ||
        strcmp(value, "off") == 0) {
        return 0;
    }
    return 1;
}

static const char *axis_name(int axis)
{
    return axis == AXIS_FV3 ? "fv3" : "mctivity";
}

static const char *axis_label(int axis)
{
    if (uservo_axis_d_topology && axis == AXIS_MCTIVITY) {
        return "Axis D Uservo";
    }
    return axis == AXIS_FV3 ? "FV3" : "MCTIVITY";
}

static int axis_from_name(const char *name, int fallback)
{
    if (!name) {
        return fallback;
    }
    if (strcmp(name, "fv3") == 0 || strcmp(name, "flexem") == 0) {
        return AXIS_FV3;
    }
    if (strcmp(name, "mctivity") == 0 || strcmp(name, "hcfa") == 0) {
        return AXIS_MCTIVITY;
    }
    return fallback;
}

static void sleep_until_next(struct timespec *wake_time)
{
    wake_time->tv_nsec += PERIOD_NS;
    while (wake_time->tv_nsec >= 1000000000L) {
        wake_time->tv_nsec -= 1000000000L;
        wake_time->tv_sec++;
    }

    while (clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, wake_time, NULL) &&
           errno == EINTR && running) {
    }
}

static uint64_t timespec_to_ns(const struct timespec *ts)
{
    return (uint64_t)ts->tv_sec * NSEC_PER_SEC + (uint64_t)ts->tv_nsec;
}

static uint64_t monotonic_now_ns(void)
{
    struct timespec now;
    clock_gettime(CLOCK_MONOTONIC, &now);
    return timespec_to_ns(&now);
}

static int sleep_until_ns(uint64_t deadline_ns)
{
    struct timespec deadline = {
        .tv_sec = (time_t)(deadline_ns / NSEC_PER_SEC),
        .tv_nsec = (long)(deadline_ns % NSEC_PER_SEC),
    };
    int rc;

    do {
        rc = clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &deadline, NULL);
    } while (rc == EINTR && running);
    if (rc != 0 && running) {
        errno = rc;
        return -1;
    }
    return 0;
}

static void record_axis_d_skipped_periods(uint64_t skipped_periods)
{
    if (skipped_periods == 0) {
        return;
    }
    realtime_status.deadline_miss_count++;
    realtime_status.skipped_periods += skipped_periods;
    realtime_status.consecutive_good_cycles = 0;
    if (realtime_status.timing_guard_armed) {
        realtime_status.communication_timing_fault = 1;
    }
}

static int wait_for_axis_d_cycle(uint64_t *previous_deadline_ns, uint64_t *scheduled_time_ns)
{
    uint64_t now_ns = monotonic_now_ns();
    mctivity_schedule_step_t step =
        mctivity_schedule_next(*previous_deadline_ns, now_ns, PERIOD_NS);

    record_axis_d_skipped_periods(step.skipped_periods);
    *previous_deadline_ns = step.deadline_ns;

    for (;;) {
        if (sleep_until_ns(*previous_deadline_ns) < 0) {
            return -1;
        }

        now_ns = monotonic_now_ns();
        realtime_status.last_wake_lateness_ns =
            now_ns > *previous_deadline_ns ? now_ns - *previous_deadline_ns : 0;
        if (realtime_status.last_wake_lateness_ns > realtime_status.max_wake_lateness_ns) {
            realtime_status.max_wake_lateness_ns = realtime_status.last_wake_lateness_ns;
        }
        if (realtime_status.last_wake_lateness_ns < PERIOD_NS) {
            break;
        }

        step = mctivity_schedule_after_late_wake(*previous_deadline_ns, now_ns, PERIOD_NS);
        record_axis_d_skipped_periods(step.skipped_periods);
        *previous_deadline_ns = step.deadline_ns;
    }
    *scheduled_time_ns = *previous_deadline_ns;
    return 0;
}

static int prepare_axis_d_realtime(void)
{
    struct sched_param param;
    volatile unsigned char stack_prefault[64U * 1024U];

    memset(&realtime_status, 0, sizeof(realtime_status));
    for (size_t i = 0; i < sizeof(stack_prefault); i += 4096U) {
        stack_prefault[i] = 0;
    }
    if (mlockall(MCL_CURRENT | MCL_FUTURE) == 0) {
        realtime_status.memory_locked = 1;
    } else {
        perror("mlockall");
    }

    realtime_status.scheduler_policy = sched_getscheduler(0);
    memset(&param, 0, sizeof(param));
    if (sched_getparam(0, &param) == 0) {
        realtime_status.scheduler_priority = param.sched_priority;
    }

    if (require_realtime &&
        (!realtime_status.memory_locked || realtime_status.scheduler_policy != SCHED_FIFO ||
         realtime_status.scheduler_priority <= 0)) {
        fprintf(
            stderr,
            "Axis D requires locked memory and SCHED_FIFO (locked=%d policy=%d priority=%d)\n",
            realtime_status.memory_locked,
            realtime_status.scheduler_policy,
            realtime_status.scheduler_priority);
        return -1;
    }
    return 0;
}

static int set_nonblock(int fd)
{
    int flags = fcntl(fd, F_GETFL, 0);
    if (flags < 0) {
        return -1;
    }
    return fcntl(fd, F_SETFL, flags | O_NONBLOCK);
}

static int operation_enabled(uint16_t sw)
{
    return (sw & 0x006f) == 0x0027;
}

static uint16_t next_controlword(uint16_t sw)
{
    if (sw & 0x0008) {
        return 0x0080;
    }
    if ((sw & 0x004f) == 0x0040) {
        return 0x0006;
    }
    if ((sw & 0x006f) == 0x0021) {
        return 0x0007;
    }
    if ((sw & 0x006f) == 0x0023 || operation_enabled(sw)) {
        return 0x000f;
    }
    return 0x0006;
}

static int32_t smooth_move(int32_t from, int32_t to, uint32_t step, uint32_t steps)
{
    if (step >= steps || steps == 0) {
        return to;
    }
    double x = (double)step / (double)steps;
    double s = x * x * (3.0 - 2.0 * x);
    return from + (int32_t)((to - from) * s);
}

static int32_t clamp_i32(int32_t value, int32_t min_value, int32_t max_value)
{
    if (value < min_value) {
        return min_value;
    }
    if (value > max_value) {
        return max_value;
    }
    return value;
}

static const char *find_json_key(const char *line, const char *key)
{
    char pattern[64];
    const char *p;
    size_t key_len;

    key_len = strlen(key);
    if (key_len + 4 >= sizeof(pattern)) {
        return NULL;
    }
    snprintf(pattern, sizeof(pattern), "\"%s\"", key);
    p = line;
    while ((p = strstr(p, pattern)) != NULL) {
        const char *q = p + strlen(pattern);
        while (*q == ' ' || *q == '\t' || *q == '\r' || *q == '\n') {
            q++;
        }
        if (*q == ':') {
            return q + 1;
        }
        p++;
    }
    return NULL;
}

static int find_i32(const char *line, const char *key, int32_t *out)
{
    const char *p = find_json_key(line, key);
    char *end = NULL;
    long value;
    if (!p) {
        return 0;
    }
    while (*p == ' ' || *p == '\t' || *p == '\r' || *p == '\n') {
        p++;
    }
    errno = 0;
    value = strtol(p, &end, 0);
    if (p == end || errno == ERANGE) {
        return 0;
    }
    while (*end == ' ' || *end == '\t' || *end == '\r' || *end == '\n') {
        end++;
    }
    if (*end != ',' && *end != '}' && *end != '\0') {
        return 0;
    }
    if (value < INT32_MIN || value > INT32_MAX) {
        return 0;
    }
    *out = (int32_t)value;
    return 1;
}

static int find_u32(const char *line, const char *key, uint32_t *out)
{
    int32_t tmp;
    if (!find_i32(line, key, &tmp) || tmp < 0) {
        return 0;
    }
    *out = (uint32_t)tmp;
    return 1;
}

static int find_str(const char *line, const char *key, char *out, size_t out_size)
{
    const char *p;
    const char *start;
    const char *end;
    size_t len;

    if (out_size == 0) {
        return 0;
    }
    p = find_json_key(line, key);
    if (!p) {
        return 0;
    }
    while (*p == ' ' || *p == '\t' || *p == '\r' || *p == '\n') {
        p++;
    }
    if (*p != '"') {
        return 0;
    }
    start = p + 1;
    end = strchr(start, '"');
    if (!end) {
        return 0;
    }
    len = (size_t)(end - start);
    if (len >= out_size) {
        len = out_size - 1;
    }
    memcpy(out, start, len);
    out[len] = '\0';
    return 1;
}

static int axis_from_line(const char *line)
{
    char dev[24];
    if (!find_str(line, "device", dev, sizeof(dev))) {
        return AXIS_MCTIVITY;
    }
    if (strcmp(dev, "fv3") == 0 || strcmp(dev, "flexem") == 0) {
        return AXIS_FV3;
    }
    if (strcmp(dev, "mctivity") == 0) {
        return AXIS_MCTIVITY;
    }
    return -1;
}

static int command_from_line(const char *line, char *out, size_t out_size)
{
    const char *start;
    const char *end;
    size_t len;
    if (find_str(line, "cmd", out, out_size)) {
        return 1;
    }
    if (strncmp(line, "cmd=", 4) != 0) {
        return 0;
    }
    start = line + 4;
    end = start;
    while (*end && *end != '&' && *end != ' ' && *end != '\t' && *end != '\r' && *end != '\n') {
        end++;
    }
    len = (size_t)(end - start);
    if (len == 0 || out_size == 0) {
        return 0;
    }
    if (len >= out_size) {
        len = out_size - 1;
    }
    memcpy(out, start, len);
    out[len] = '\0';
    return 1;
}

static int8_t mode_code_for_name(const char *mode)
{
    if (strcmp(mode, "position") == 0 || strcmp(mode, "incremental") == 0 || strcmp(mode, "point") == 0 ||
        strcmp(mode, "jog") == 0) {
        return 8;
    }
    if (strcmp(mode, "homing") == 0) {
        return 6;
    }
    if (strcmp(mode, "velocity") == 0) {
        return 9;
    }
    if (strcmp(mode, "torque") == 0) {
        return 10;
    }
    return 8;
}

static int is_safe_mode_name(const char *mode)
{
    return strcmp(mode, "position") == 0 ||
           strcmp(mode, "incremental") == 0 ||
           strcmp(mode, "jog") == 0 ||
           strcmp(mode, "point") == 0 ||
           strcmp(mode, "homing") == 0 ||
           strcmp(mode, "velocity") == 0 ||
           strcmp(mode, "torque") == 0 ||
           strcmp(mode, "gear_cam") == 0;
}

static void set_control_mode(axis_runtime_t *ax, const char *mode)
{
    snprintf(ax->st.control_mode, sizeof(ax->st.control_mode), "%s", mode);
}

static int32_t velocity_step_counts(axis_runtime_t *ax, int32_t velocity_cps)
{
    int32_t step;
    ax->velocity_remainder += velocity_cps;
    step = ax->velocity_remainder / 1000;
    ax->velocity_remainder -= step * 1000;
    return step;
}

static int32_t clamp_i64_to_i32(int64_t value)
{
    if (value > INT32_MAX) {
        return INT32_MAX;
    }
    if (value < INT32_MIN) {
        return INT32_MIN;
    }
    return (int32_t)value;
}

static int32_t sign_i32(int32_t value)
{
    if (value > 0) {
        return 1;
    }
    if (value < 0) {
        return -1;
    }
    return 0;
}

static double clamp_unit_interval(double value)
{
    if (value < 0.0) {
        return 0.0;
    }
    if (value > 1.0) {
        return 1.0;
    }
    return value;
}

static int curve_blend_from_name(const char *name)
{
    if (name && strcmp(name, "linear") == 0) {
        return CURVE_BLEND_LINEAR;
    }
    if (name && strcmp(name, "aggressive") == 0) {
        return CURVE_BLEND_AGGRESSIVE;
    }
    return CURVE_BLEND_SMOOTH;
}

static double easing_curve(int blend, double raw)
{
    double t = clamp_unit_interval(raw);
    if (blend == CURVE_BLEND_LINEAR) {
        return t;
    }
    if (blend == CURVE_BLEND_AGGRESSIVE) {
        if (t < 0.5) {
            return 2.0 * t * t;
        }
        return 1.0 - (((-2.0 * t + 2.0) * (-2.0 * t + 2.0)) / 2.0);
    }
    return t * t * (3.0 - 2.0 * t);
}

static int32_t decelerate_velocity(int32_t velocity_cps, uint32_t decel_cps2)
{
    int32_t step = (int32_t)(decel_cps2 / 1000U);
    if (step < 1) {
        step = 1;
    }
    if (velocity_cps > 0) {
        return velocity_cps > step ? velocity_cps - step : 0;
    }
    if (velocity_cps < 0) {
        return velocity_cps < -step ? velocity_cps + step : 0;
    }
    return 0;
}

static uint32_t rpm_to_counts_s(uint32_t rpm)
{
    int64_t counts_s;
    if (rpm == 0) {
        return 0;
    }
    counts_s = ((int64_t)rpm * counts_per_rev) / 60LL;
    if (counts_s < 1) {
        return 1U;
    }
    if (counts_s > INT32_MAX) {
        return INT32_MAX;
    }
    return (uint32_t)counts_s;
}

static uint32_t rpm_s_to_counts_s2(uint32_t rpm_s)
{
    int64_t counts_s2;
    if (rpm_s == 0) {
        rpm_s = DEFAULT_STOP_DECEL_RPM_S;
    }
    counts_s2 = ((int64_t)rpm_s * counts_per_rev) / 60LL;
    if (counts_s2 < 1) {
        return 1U;
    }
    if (counts_s2 > UINT32_MAX) {
        return UINT32_MAX;
    }
    return (uint32_t)counts_s2;
}

static int64_t i64_abs_diff_i32(int32_t a, int32_t b)
{
    int64_t d = (int64_t)a - (int64_t)b;
    return d < 0 ? -d : d;
}

static int ready_for_motion(const axis_runtime_t *ax)
{
    return ax->st.enabled && ax->st.enable_settle_cycles == 0 && !ax->st.fault;
}

static void clear_motion(axis_runtime_t *ax)
{
    memset(&ax->motion, 0, sizeof(ax->motion));
}

static void start_motion_to(axis_runtime_t *ax, int32_t target_user, uint32_t move_ms, uint32_t speed_rpm,
                            uint32_t accel_rpm_s, int have_limits, int32_t min_target_user, int32_t max_target_user)
{
    int32_t requested_target_user = target_user;
    uint32_t max_velocity_cps = rpm_to_counts_s(speed_rpm);
    uint32_t accel_cps2 = rpm_s_to_counts_s2(accel_rpm_s);
    if (move_ms == 0) {
        move_ms = DEFAULT_MOVE_MS;
    }
    if (have_limits) {
        if (min_target_user > max_target_user) {
            int32_t tmp = min_target_user;
            min_target_user = max_target_user;
            max_target_user = tmp;
        }
        target_user = clamp_i32(target_user, min_target_user, max_target_user);
    } else {
        min_target_user = INT32_MIN;
        max_target_user = INT32_MAX;
    }
    clear_motion(ax);
    ax->motion.from = ax->st.pos_raw;
    ax->motion.to = ax->st.soft_zero_raw + target_user;
    ax->motion.steps = move_ms;
    ax->motion.step = 0;
    ax->motion.moving = 1;
    ax->motion.profile_active = max_velocity_cps > 0;
    ax->motion.max_velocity_cps = (int32_t)max_velocity_cps;
    ax->motion.accel_cps2 = accel_cps2;
    ax->motion.decel_cps2 = accel_cps2;
    ax->motion.min_target_user = min_target_user;
    ax->motion.max_target_user = max_target_user;
    ax->stop_velocity_cps = 0;
    ax->st.jog_velocity_cps = 0;
    ax->velocity_remainder = 0;
    ax->st.target_raw = ax->st.pos_raw;
    ax->st.target_user = target_user;
    if (ax->motion.profile_active) {
        snprintf(
            ax->st.message,
            sizeof(ax->st.message),
            requested_target_user != target_user ? "profile move limited to %d counts @ %u rpm / %u rpm/s"
                                                 : "profile move to %d counts @ %u rpm / %u rpm/s",
            target_user,
            speed_rpm,
            accel_rpm_s ? accel_rpm_s : DEFAULT_STOP_DECEL_RPM_S);
    } else {
        snprintf(
            ax->st.message,
            sizeof(ax->st.message),
            requested_target_user != target_user ? "moving to limited target %d counts in %u ms"
                                                 : "moving to %d counts in %u ms",
            target_user,
            move_ms);
    }
}

static void update_profile_motion(axis_runtime_t *ax)
{
    motion_t *motion = &ax->motion;
    status_t *s = &ax->st;
    int64_t remaining = (int64_t)motion->to - (int64_t)s->target_raw;
    int32_t direction = remaining > 0 ? 1 : (remaining < 0 ? -1 : 0);
    int32_t velocity = motion->current_velocity_cps;
    int32_t velocity_sign = sign_i32(velocity);
    uint32_t accel_cps2 = motion->accel_cps2 ? motion->accel_cps2 : rpm_s_to_counts_s2(DEFAULT_STOP_DECEL_RPM_S);
    int32_t velocity_step = (int32_t)(accel_cps2 / 1000U);
    int32_t desired_velocity = 0;
    int64_t stop_distance = 0;
    int32_t position_step;
    int32_t next_target_raw;

    if (velocity_step < 1) {
        velocity_step = 1;
    }
    if (direction == 0 && velocity == 0) {
        s->target_raw = motion->to;
        s->target_user = s->target_raw - s->soft_zero_raw;
        clear_motion(ax);
        ax->velocity_remainder = 0;
        snprintf(s->message, sizeof(s->message), "motion complete");
        return;
    }

    if (velocity != 0) {
        stop_distance = ((int64_t)velocity * (int64_t)velocity) / (2LL * (int64_t)accel_cps2);
    }

    if (direction == 0 || (velocity_sign != 0 && velocity_sign != direction) ||
        stop_distance >= (remaining < 0 ? -remaining : remaining)) {
        desired_velocity = 0;
    } else {
        desired_velocity = direction * motion->max_velocity_cps;
    }

    if (velocity < desired_velocity) {
        velocity += velocity_step;
        if (velocity > desired_velocity) {
            velocity = desired_velocity;
        }
    } else if (velocity > desired_velocity) {
        velocity -= velocity_step;
        if (velocity < desired_velocity) {
            velocity = desired_velocity;
        }
    }

    motion->current_velocity_cps = velocity;
    position_step = velocity_step_counts(ax, motion->current_velocity_cps);
    if (position_step == 0 && direction != 0 && motion->current_velocity_cps == 0) {
        motion->current_velocity_cps = direction * velocity_step;
        position_step = velocity_step_counts(ax, motion->current_velocity_cps);
    }
    next_target_raw = clamp_i64_to_i32((int64_t)s->target_raw + (int64_t)position_step);
    if ((direction > 0 && next_target_raw > motion->to) || (direction < 0 && next_target_raw < motion->to)) {
        next_target_raw = motion->to;
        motion->current_velocity_cps = 0;
    }
    s->target_raw = next_target_raw;
    s->target_user = clamp_i32(s->target_raw - s->soft_zero_raw, motion->min_target_user, motion->max_target_user);
    s->target_raw = s->soft_zero_raw + s->target_user;
    if (s->target_raw == motion->to && motion->current_velocity_cps == 0) {
        clear_motion(ax);
        ax->velocity_remainder = 0;
        snprintf(s->message, sizeof(s->message), "motion complete");
    }
}

static void start_curve_motion(axis_runtime_t *ax, int32_t target_delta_user, uint32_t vmax_counts_s,
                               uint32_t accel_counts_s2, uint32_t decel_counts_s2, uint32_t dwell_ms,
                               int have_limits, int32_t min_target_user, int32_t max_target_user, int curve_blend)
{
    status_t *s = &ax->st;
    int32_t requested_target_user = s->target_user + target_delta_user;
    int32_t final_target_user = requested_target_user;
    int32_t final_target_raw;
    int32_t delta_raw;
    double distance_counts;
    double vpeak;
    double acc;
    double dec;
    double t_acc;
    double t_dec;
    double t_cruise = 0.0;
    double accel_distance;
    double decel_distance;

    if (have_limits) {
        if (min_target_user > max_target_user) {
            int32_t tmp = min_target_user;
            min_target_user = max_target_user;
            max_target_user = tmp;
        }
        final_target_user = clamp_i32(final_target_user, min_target_user, max_target_user);
    } else {
        min_target_user = INT32_MIN;
        max_target_user = INT32_MAX;
    }

    final_target_raw = s->soft_zero_raw + final_target_user;
    delta_raw = final_target_raw - s->pos_raw;
    distance_counts = fabs((double)delta_raw);
    vpeak = (double)(vmax_counts_s > 0 ? vmax_counts_s : 0U);
    acc = (double)(accel_counts_s2 > 0 ? accel_counts_s2 : 0U);
    dec = (double)(decel_counts_s2 > 0 ? decel_counts_s2 : 0U);

    clear_motion(ax);
    ax->stop_velocity_cps = 0;
    ax->st.jog_velocity_cps = 0;
    ax->velocity_remainder = 0;
    ax->motion.from = s->pos_raw;
    ax->motion.to = final_target_raw;
    ax->motion.moving = 1;
    ax->motion.curve_active = 1;
    ax->motion.min_target_user = min_target_user;
    ax->motion.max_target_user = max_target_user;
    ax->motion.curve_blend = curve_blend;
    ax->motion.curve_dwell_ms = dwell_ms;
    ax->motion.curve_dwell_elapsed_ms = 0;
    ax->motion.curve_elapsed_s = 0.0;
    ax->motion.curve_distance_counts = distance_counts;
    ax->motion.curve_position_counts = 0.0;
    ax->motion.curve_vpeak_cps = vpeak;
    ax->motion.curve_accel_cps2_f = acc;
    ax->motion.curve_decel_cps2_f = dec;
    ax->motion.current_velocity_cps = 0;
    s->target_raw = s->pos_raw;
    s->target_user = s->pos_user;

    if (distance_counts < 0.5 || vpeak <= 0.0 || acc <= 0.0 || dec <= 0.0) {
        ax->motion.to = s->pos_raw;
        ax->motion.moving = 0;
        ax->motion.curve_active = 0;
        s->target_raw = s->pos_raw;
        s->target_user = s->pos_user;
        snprintf(s->message, sizeof(s->message), "curve move ignored; invalid or zero-distance target");
        return;
    }

    t_acc = vpeak / acc;
    t_dec = vpeak / dec;
    accel_distance = 0.5 * vpeak * t_acc;
    decel_distance = 0.5 * vpeak * t_dec;
    if (distance_counts > accel_distance + decel_distance + 1e-9) {
        t_cruise = (distance_counts - accel_distance - decel_distance) / vpeak;
    }
    ax->motion.curve_t_acc_s = t_acc;
    ax->motion.curve_t_cruise_s = t_cruise;
    ax->motion.curve_t_dec_s = t_dec;
    ax->motion.curve_total_motion_s = t_acc + t_cruise + t_dec;
    snprintf(
        s->message,
        sizeof(s->message),
        requested_target_user != final_target_user ? "curve move limited to %d counts" : "curve move by %d counts",
        final_target_user - s->pos_user);
}

static double curve_velocity_at(const motion_t *motion, double elapsed_s)
{
    double t = elapsed_s;
    if (t <= motion->curve_t_acc_s + 1e-12) {
        return motion->curve_vpeak_cps * easing_curve(motion->curve_blend, motion->curve_t_acc_s > 0.0 ? t / motion->curve_t_acc_s : 1.0);
    }
    if (t <= motion->curve_t_acc_s + motion->curve_t_cruise_s + 1e-12) {
        return motion->curve_vpeak_cps;
    }
    if (t <= motion->curve_total_motion_s + 1e-12) {
        double dec_t = t - motion->curve_t_acc_s - motion->curve_t_cruise_s;
        double ratio = motion->curve_t_dec_s > 0.0 ? dec_t / motion->curve_t_dec_s : 1.0;
        return motion->curve_vpeak_cps * (1.0 - easing_curve(motion->curve_blend, ratio));
    }
    return 0.0;
}

static void update_curve_motion(axis_runtime_t *ax)
{
    motion_t *motion = &ax->motion;
    status_t *s = &ax->st;
    double next_elapsed_s;
    double velocity;
    int32_t direction;
    int32_t next_target_raw;

    direction = sign_i32(motion->to - motion->from);
    if (direction == 0 || motion->curve_distance_counts < 0.5) {
        s->target_raw = motion->to;
        s->target_user = clamp_i32(s->target_raw - s->soft_zero_raw, motion->min_target_user, motion->max_target_user);
        clear_motion(ax);
        ax->velocity_remainder = 0;
        snprintf(s->message, sizeof(s->message), "curve motion complete");
        return;
    }

    if (motion->curve_elapsed_s + 1e-12 < motion->curve_total_motion_s) {
        next_elapsed_s = motion->curve_elapsed_s + 0.001;
        if (next_elapsed_s > motion->curve_total_motion_s) {
            next_elapsed_s = motion->curve_total_motion_s;
        }
        velocity = curve_velocity_at(motion, next_elapsed_s);
        motion->curve_position_counts += velocity / 1000.0;
        if (motion->curve_position_counts > motion->curve_distance_counts) {
            motion->curve_position_counts = motion->curve_distance_counts;
        }
        next_target_raw = motion->from + direction * (int32_t)llround(motion->curve_position_counts);
        if ((direction > 0 && next_target_raw > motion->to) || (direction < 0 && next_target_raw < motion->to)) {
            next_target_raw = motion->to;
        }
        s->target_raw = next_target_raw;
        s->target_user = clamp_i32(s->target_raw - s->soft_zero_raw, motion->min_target_user, motion->max_target_user);
        s->target_raw = s->soft_zero_raw + s->target_user;
        motion->current_velocity_cps = direction * (int32_t)llround(velocity);
        motion->curve_elapsed_s = next_elapsed_s;
        if (motion->curve_elapsed_s + 1e-12 >= motion->curve_total_motion_s) {
            s->target_raw = motion->to;
            s->target_user = clamp_i32(s->target_raw - s->soft_zero_raw, motion->min_target_user, motion->max_target_user);
            motion->current_velocity_cps = 0;
        }
        return;
    }

    s->target_raw = motion->to;
    s->target_user = clamp_i32(s->target_raw - s->soft_zero_raw, motion->min_target_user, motion->max_target_user);
    motion->current_velocity_cps = 0;
    if (motion->curve_dwell_elapsed_ms < motion->curve_dwell_ms) {
        motion->curve_dwell_elapsed_ms++;
        return;
    }
    clear_motion(ax);
    ax->velocity_remainder = 0;
    snprintf(s->message, sizeof(s->message), "curve motion complete");
}

static void send_status_fd(int fd, int axis)
{
    axis_runtime_t *ax = &axes[axis];
    const status_t *s = &ax->st;
    char out[2000];
    int n = snprintf(
        out, sizeof(out),
        "{\"ok\":true,\"status\":{\"device\":\"%s\",\"topology\":\"%s\","
        "\"counts_per_rev\":%lld,\"commissioning_inhibit\":%s,\"enabled\":%s,\"servo_request\":%s,"
        "\"moving\":%s,\"gear_running\":%s,\"fault\":%s,\"settle_cycles\":%u,\"al_state\":%u,\"operational\":%u,"
        "\"wc\":%u,\"wc_complete\":%s,\"cw\":%u,\"sw\":%u,\"err\":%u,\"mode\":%d,"
        "\"control_mode\":\"%s\",\"pos_raw\":%d,\"pos\":%d,\"target_raw\":%d,\"target\":%d,"
        "\"following_error\":%d,\"soft_zero_raw\":%d,\"jog_velocity_cps\":%d,\"torque_cmd\":%d,"
        "\"torque_feedback\":%d,\"homed\":%s,\"cycles\":%u,"
        "\"rt_memory_locked\":%s,\"rt_scheduler_policy\":%d,\"rt_scheduler_priority\":%d,"
        "\"rt_deadline_miss_count\":%llu,\"rt_skipped_periods\":%llu,"
        "\"rt_last_wake_lateness_ns\":%llu,\"rt_max_wake_lateness_ns\":%llu,"
        "\"rt_last_cycle_runtime_ns\":%llu,\"rt_max_cycle_runtime_ns\":%llu,"
        "\"wc_change_count\":%llu,\"wc_incomplete_cycles\":%llu,"
        "\"timing_guard_armed\":%s,\"communication_timing_fault\":%s,"
        "\"last_command\":\"%s\",\"message\":\"%s\"}}\n",
        axis_name(axis), uservo_axis_d_topology ? "axis-d-uservo" : "legacy-dual",
        (long long)counts_per_rev, commissioning_inhibit ? "true" : "false",
        s->enabled ? "true" : "false", s->servo_request ? "true" : "false",
        s->moving ? "true" : "false", ax->gear_running ? "true" : "false", s->fault ? "true" : "false",
        s->enable_settle_cycles, s->al_state,
        s->operational, s->wc, s->wc_complete ? "true" : "false", s->cw, s->sw, s->err, s->mode_display,
        s->control_mode, s->pos_raw, s->pos_user, s->target_raw, s->target_user, s->following_error,
        s->soft_zero_raw, s->jog_velocity_cps, s->torque_cmd, s->torque_feedback, s->homed ? "true" : "false",
        s->cycles,
        realtime_status.memory_locked ? "true" : "false",
        realtime_status.scheduler_policy,
        realtime_status.scheduler_priority,
        (unsigned long long)realtime_status.deadline_miss_count,
        (unsigned long long)realtime_status.skipped_periods,
        (unsigned long long)realtime_status.last_wake_lateness_ns,
        (unsigned long long)realtime_status.max_wake_lateness_ns,
        (unsigned long long)realtime_status.last_cycle_runtime_ns,
        (unsigned long long)realtime_status.max_cycle_runtime_ns,
        (unsigned long long)realtime_status.wc_change_count,
        (unsigned long long)realtime_status.wc_incomplete_cycles,
        realtime_status.timing_guard_armed ? "true" : "false",
        realtime_status.communication_timing_fault ? "true" : "false",
        s->last_command,
        s->message);
    if (n > 0) {
        size_t send_len = (size_t)n < sizeof(out) ? (size_t)n : sizeof(out) - 1;
        (void)send(fd, out, send_len, MSG_NOSIGNAL);
    }
}

static void send_error_fd(int fd, const char *msg)
{
    char out[256];
    int n = snprintf(out, sizeof(out), "{\"ok\":false,\"error\":\"%s\"}\n", msg);
    if (n > 0) {
        (void)send(fd, out, (size_t)n, MSG_NOSIGNAL);
    }
}

static void handle_command(int fd, const char *line)
{
    int axis = axis_from_line(line);
    if (axis < 0 || axis >= AXIS_COUNT) {
        send_error_fd(fd, "unsupported_device");
        return;
    }
    axis_runtime_t *ax = &axes[axis];
    status_t *s = &ax->st;
    char cmd[64];
    if (!command_from_line(line, cmd, sizeof(cmd))) {
        send_error_fd(fd, "missing command");
        return;
    }

    if (uservo_axis_d_topology && commissioning_inhibit &&
        strcmp(cmd, "status") != 0 && strcmp(cmd, "disable") != 0 && strcmp(cmd, "stop") != 0 &&
        strcmp(cmd, "fault_reset") != 0 && strcmp(cmd, "reset_fault") != 0) {
        send_error_fd(fd, "commissioning_inhibit");
        return;
    }

    if (uservo_axis_d_topology && realtime_status.communication_timing_fault &&
        strcmp(cmd, "status") != 0 && strcmp(cmd, "disable") != 0 && strcmp(cmd, "stop") != 0) {
        send_error_fd(fd, "communication_timing_fault");
        return;
    }

    if (uservo_axis_d_topology && !realtime_status.timing_guard_armed && strcmp(cmd, "enable") == 0) {
        send_error_fd(fd, "timing_guard_not_armed");
        return;
    }

    if (uservo_axis_d_topology &&
        (strcmp(cmd, "home") == 0 || strcmp(cmd, "gear_config") == 0 || strcmp(cmd, "gear_start") == 0 ||
         strcmp(cmd, "gear_stop") == 0 || strcmp(cmd, "jog_velocity") == 0 || strcmp(cmd, "torque_cmd") == 0)) {
        send_error_fd(fd, "unsupported_for_axis_d_uservo");
        return;
    }

    if (strcmp(cmd, "status") == 0) {
        strncpy(s->last_command, "status", sizeof(s->last_command) - 1);
        send_status_fd(fd, axis);
        return;
    }

    if (strcmp(cmd, "enable") == 0) {
        s->servo_request = 1;
        s->enable_settle_cycles = ENABLE_SETTLE_CYCLES;
        clear_motion(ax);
        ax->stop_velocity_cps = 0;
        s->jog_velocity_cps = 0;
        ax->velocity_remainder = 0;
        s->target_raw = s->pos_raw;
        s->target_user = s->pos_raw - s->soft_zero_raw;
        ax->pp_pulse_cycles = 0;
        ax->fv3_halt_cycles = 0;
        ax->gear_running = 0;
        ax->gear_has_last_master_pos = 0;
        strncpy(s->last_command, "enable", sizeof(s->last_command) - 1);
        snprintf(s->message, sizeof(s->message), "%s enable requested; arming current position", axis_label(axis));
        send_status_fd(fd, axis);
        return;
    }

    if (strcmp(cmd, "disable") == 0) {
        s->servo_request = 0;
        s->enable_settle_cycles = 0;
        clear_motion(ax);
        ax->stop_velocity_cps = 0;
        s->jog_velocity_cps = 0;
        ax->velocity_remainder = 0;
        s->target_raw = s->pos_raw;
        s->target_user = s->pos_raw - s->soft_zero_raw;
        ax->pp_pulse_cycles = 0;
        ax->fv3_halt_cycles = 0;
        ax->gear_running = 0;
        ax->gear_has_last_master_pos = 0;
        strncpy(s->last_command, "disable", sizeof(s->last_command) - 1);
        snprintf(s->message, sizeof(s->message), "%s servo output disabled", axis_label(axis));
        send_status_fd(fd, axis);
        return;
    }

    if (strcmp(cmd, "stop") == 0) {
        uint32_t decel_rpm_s = DEFAULT_STOP_DECEL_RPM_S;
        uint32_t decel = rpm_s_to_counts_s2(DEFAULT_STOP_DECEL_RPM_S);
        int32_t seed_velocity_cps;
        int32_t dir;
        int64_t stop_distance;
        int64_t next_target;
        if (find_u32(line, "deceleration_rpm_s", &decel_rpm_s) ||
            find_u32(line, "acceleration_rpm_s", &decel_rpm_s)) {
            decel = rpm_s_to_counts_s2(decel_rpm_s);
        } else if (find_u32(line, "deceleration_counts_s2", &decel)) {
            if (decel == 0) {
                decel = rpm_s_to_counts_s2(DEFAULT_STOP_DECEL_RPM_S);
            }
        } else if (find_u32(line, "deceleration", &decel_rpm_s)) {
            decel = rpm_s_to_counts_s2(decel_rpm_s);
        }
        if (ax->motion.profile_active && ax->motion.current_velocity_cps != 0) {
            seed_velocity_cps = ax->motion.current_velocity_cps;
        } else {
            seed_velocity_cps = ax->target_velocity_cps;
        }
        clear_motion(ax);
        s->enable_settle_cycles = 0;
        s->jog_velocity_cps = 0;
        ax->velocity_remainder = 0;
        ax->pp_pulse_cycles = 0;
        ax->fv3_halt_cycles = 0;
        ax->gear_running = 0;
        ax->gear_has_last_master_pos = 0;
        if (axis == AXIS_FV3 && ax->fv3_feedback_velocity_cps != 0) {
            seed_velocity_cps = ax->fv3_feedback_velocity_cps;
        }
        if (axis == AXIS_FV3) {
            ax->stop_velocity_cps = 0;
            ax->fv3_halt_cycles = 0;
            if (s->enabled && seed_velocity_cps != 0) {
                dir = sign_i32(seed_velocity_cps);
                stop_distance = ((int64_t)seed_velocity_cps * (int64_t)seed_velocity_cps) / (2LL * (int64_t)decel);
                if (stop_distance < 1024) {
                    stop_distance = 1024;
                }
                if (stop_distance > 100000000LL) {
                    stop_distance = 100000000LL;
                }
                next_target = (int64_t)s->pos_raw + (int64_t)dir * stop_distance;
                s->target_raw = clamp_i64_to_i32(next_target);
                s->target_user = s->target_raw - s->soft_zero_raw;
                ax->pp_pulse_cycles = 30;
                snprintf(s->message, sizeof(s->message), "%s decel-stop target staged (%u rpm/s)", axis_label(axis), decel_rpm_s);
            } else {
                s->target_raw = s->pos_raw;
                s->target_user = s->pos_user;
                snprintf(s->message, sizeof(s->message), "%s stop requested; holding current position", axis_label(axis));
            }
            strncpy(s->last_command, "stop", sizeof(s->last_command) - 1);
            send_status_fd(fd, axis);
            return;
        }
        if (s->enabled && seed_velocity_cps != 0) {
            ax->stop_velocity_cps = seed_velocity_cps;
            ax->stop_decel_cps2 = decel;
            snprintf(s->message, sizeof(s->message), "%s controlled stop with decel %u rpm/s", axis_label(axis), decel_rpm_s);
        } else {
            ax->stop_velocity_cps = 0;
            if (axis == AXIS_FV3) {
                s->target_raw = s->pos_raw;
                s->target_user = s->pos_user;
                snprintf(s->message, sizeof(s->message), "%s stop requested; holding current position", axis_label(axis));
            } else {
                s->target_user = s->target_raw - s->soft_zero_raw;
                snprintf(s->message, sizeof(s->message), "%s motion stopped; holding current target", axis_label(axis));
            }
        }
        strncpy(s->last_command, "stop", sizeof(s->last_command) - 1);
        send_status_fd(fd, axis);
        return;
    }

    if (strcmp(cmd, "fault_reset") == 0 || strcmp(cmd, "reset_fault") == 0) {
        s->servo_request = 0;
        s->enable_settle_cycles = 0;
        clear_motion(ax);
        ax->stop_velocity_cps = 0;
        s->jog_velocity_cps = 0;
        ax->velocity_remainder = 0;
        s->target_raw = s->pos_raw;
        s->target_user = s->pos_raw - s->soft_zero_raw;
        ax->fault_reset_cycles = 120;
        ax->pp_pulse_cycles = 0;
        ax->fv3_halt_cycles = 0;
        ax->gear_running = 0;
        ax->gear_has_last_master_pos = 0;
        strncpy(s->last_command, "fault_reset", sizeof(s->last_command) - 1);
        snprintf(s->message, sizeof(s->message), "%s fault reset requested", axis_label(axis));
        send_status_fd(fd, axis);
        return;
    }

    if (strcmp(cmd, "set_zero") == 0 || strcmp(cmd, "home") == 0) {
        int is_home = strcmp(cmd, "home") == 0;
        if (is_home) {
            set_control_mode(ax, "homing");
            ax->commanded_mode = mode_code_for_name("homing");
        }
        s->soft_zero_raw = s->pos_raw;
        s->target_raw = s->pos_raw;
        s->target_user = 0;
        clear_motion(ax);
        ax->stop_velocity_cps = 0;
        s->jog_velocity_cps = 0;
        ax->velocity_remainder = 0;
        s->homed = 1;
        s->enable_settle_cycles = 0;
        ax->pp_pulse_cycles = 0;
        ax->fv3_halt_cycles = 0;
        ax->gear_running = 0;
        ax->gear_has_last_master_pos = 0;
        strncpy(s->last_command, is_home ? "home" : "set_zero", sizeof(s->last_command) - 1);
        snprintf(s->message, sizeof(s->message),
                 is_home ? "%s homing zero updated at current position" : "%s current position set as zero",
                 axis_label(axis));
        send_status_fd(fd, axis);
        return;
    }

    if (strcmp(cmd, "set_mode") == 0) {
        char mode[24];
        if (!find_str(line, "mode", mode, sizeof(mode)) || !is_safe_mode_name(mode)) {
            send_error_fd(fd, "set_mode requires a supported mode");
            return;
        }
        if (uservo_axis_d_topology &&
            strcmp(mode, "position") != 0 && strcmp(mode, "incremental") != 0 && strcmp(mode, "jog") != 0 &&
            strcmp(mode, "point") != 0) {
            send_error_fd(fd, "unsupported_for_axis_d_uservo");
            return;
        }
        clear_motion(ax);
        ax->stop_velocity_cps = 0;
        s->jog_velocity_cps = 0;
        ax->velocity_remainder = 0;
        s->torque_cmd = 0;
        ax->pp_pulse_cycles = 0;
        ax->fv3_halt_cycles = 0;
        ax->gear_running = 0;
        ax->gear_has_last_master_pos = 0;
        if (strcmp(mode, "gear_cam") == 0) {
            s->target_raw = s->pos_raw;
            s->target_user = s->pos_user;
        }
        set_control_mode(ax, mode);
        ax->commanded_mode = mode_code_for_name(mode);
        strncpy(s->last_command, "set_mode", sizeof(s->last_command) - 1);
        if (strcmp(mode, "torque") == 0 || strcmp(mode, "gear_cam") == 0) {
            snprintf(s->message, sizeof(s->message), "%s %s selected; active output needs PDO validation", axis_label(axis), mode);
        } else {
            snprintf(s->message, sizeof(s->message), "%s %s mode selected", axis_label(axis), mode);
        }
        send_status_fd(fd, axis);
        return;
    }

    if (strcmp(cmd, "gear_config") == 0) {
        uint32_t master_ratio = 1;
        uint32_t slave_ratio = 1;
        char master_name[24] = {0};
        int master_axis;
        if (!find_u32(line, "master_ratio", &master_ratio)) {
            (void)find_u32(line, "gear_master_ratio", &master_ratio);
        }
        if (!find_u32(line, "slave_ratio", &slave_ratio)) {
            (void)find_u32(line, "gear_slave_ratio", &slave_ratio);
        }
        if (!find_str(line, "master", master_name, sizeof(master_name))) {
            (void)find_str(line, "master_axis", master_name, sizeof(master_name));
        }
        if (master_ratio < 1) {
            master_ratio = 1;
        }
        if (slave_ratio < 1) {
            slave_ratio = 1;
        }
        master_axis = axis_from_name(master_name, ax->gear_master_axis);
        if (master_axis == axis) {
            send_error_fd(fd, "gear master axis cannot be self");
            return;
        }
        ax->gear_master_axis = master_axis;
        ax->gear_master_ratio = (int32_t)master_ratio;
        ax->gear_slave_ratio = (int32_t)slave_ratio;
        set_control_mode(ax, "gear_cam");
        ax->commanded_mode = mode_code_for_name("position");
        strncpy(s->last_command, "gear_config", sizeof(s->last_command) - 1);
        snprintf(
            s->message,
            sizeof(s->message),
            "%s gear config: master=%s ratio=%u:%u",
            axis_label(axis),
            axis_name(master_axis),
            slave_ratio,
            master_ratio);
        send_status_fd(fd, axis);
        return;
    }

    if (strcmp(cmd, "gear_start") == 0) {
        if (!ready_for_motion(ax)) {
            send_error_fd(fd, "servo is not ready for gear start; enable and wait for settle first");
            return;
        }
        if (ax->gear_master_axis == axis) {
            send_error_fd(fd, "gear master axis cannot be self");
            return;
        }
        set_control_mode(ax, "gear_cam");
        ax->commanded_mode = mode_code_for_name("position");
        clear_motion(ax);
        ax->stop_velocity_cps = 0;
        s->jog_velocity_cps = 0;
        ax->velocity_remainder = 0;
        ax->pp_pulse_cycles = 0;
        ax->fv3_halt_cycles = 0;
        ax->gear_running = 1;
        ax->gear_has_last_master_pos = 0;
        strncpy(s->last_command, "gear_start", sizeof(s->last_command) - 1);
        snprintf(
            s->message,
            sizeof(s->message),
            "%s gear engaged: %s ratio=%d:%d",
            axis_label(axis),
            axis_name(ax->gear_master_axis),
            ax->gear_slave_ratio,
            ax->gear_master_ratio);
        send_status_fd(fd, axis);
        return;
    }

    if (strcmp(cmd, "gear_stop") == 0) {
        ax->gear_running = 0;
        ax->gear_has_last_master_pos = 0;
        ax->pp_pulse_cycles = 0;
        ax->fv3_halt_cycles = 0;
        s->target_raw = s->pos_raw;
        s->target_user = s->pos_user;
        strncpy(s->last_command, "gear_stop", sizeof(s->last_command) - 1);
        snprintf(s->message, sizeof(s->message), "%s gear disengaged", axis_label(axis));
        send_status_fd(fd, axis);
        return;
    }

    if (strcmp(cmd, "move_abs") == 0) {
        int32_t pos;
        int32_t min_pos = 0;
        int32_t max_pos = 0;
        uint32_t move_ms = DEFAULT_MOVE_MS;
        uint32_t speed_rpm = 0;
        uint32_t accel_rpm_s = 0;
        int have_limits = 0;
        if (!ready_for_motion(ax)) {
            send_error_fd(fd, "servo is not ready for motion; enable and wait for settle first");
            return;
        }
        if (!find_i32(line, "pos", &pos)) {
            send_error_fd(fd, "move_abs requires pos");
            return;
        }
        have_limits = find_i32(line, "min_pos", &min_pos) && find_i32(line, "max_pos", &max_pos);
        (void)find_u32(line, "move_ms", &move_ms);
        (void)find_u32(line, "speed_rpm", &speed_rpm);
        (void)find_u32(line, "acceleration_rpm_s", &accel_rpm_s);
        set_control_mode(ax, "position");
        ax->commanded_mode = mode_code_for_name("position");
        ax->gear_running = 0;
        ax->gear_has_last_master_pos = 0;
        if (axis == AXIS_FV3) {
            clear_motion(ax);
            ax->stop_velocity_cps = 0;
            s->jog_velocity_cps = 0;
            ax->velocity_remainder = 0;
            s->target_user = have_limits ? clamp_i32(pos, min_pos, max_pos) : pos;
            s->target_raw = s->soft_zero_raw + s->target_user;
            ax->pp_pulse_cycles = 30;
            ax->fv3_halt_cycles = 0;
            snprintf(s->message, sizeof(s->message), "PP move_abs to %d counts", s->target_user);
        } else {
            start_motion_to(ax, pos, move_ms, speed_rpm, accel_rpm_s, have_limits, min_pos, max_pos);
        }
        strncpy(s->last_command, "move_abs", sizeof(s->last_command) - 1);
        send_status_fd(fd, axis);
        return;
    }

    if (strcmp(cmd, "move_rel") == 0) {
        int32_t delta;
        int32_t min_pos = 0;
        int32_t max_pos = 0;
        uint32_t move_ms = DEFAULT_MOVE_MS;
        uint32_t speed_rpm = 0;
        uint32_t accel_rpm_s = 0;
        int have_limits = 0;
        if (!ready_for_motion(ax)) {
            send_error_fd(fd, "servo is not ready for motion; enable and wait for settle first");
            return;
        }
        if (!find_i32(line, "delta", &delta)) {
            send_error_fd(fd, "move_rel requires delta");
            return;
        }
        have_limits = find_i32(line, "min_pos", &min_pos) && find_i32(line, "max_pos", &max_pos);
        (void)find_u32(line, "move_ms", &move_ms);
        (void)find_u32(line, "speed_rpm", &speed_rpm);
        (void)find_u32(line, "acceleration_rpm_s", &accel_rpm_s);
        set_control_mode(ax, axis == AXIS_FV3 ? "position" : "jog");
        ax->commanded_mode = mode_code_for_name(axis == AXIS_FV3 ? "position" : "jog");
        ax->gear_running = 0;
        ax->gear_has_last_master_pos = 0;
        if (axis == AXIS_FV3) {
            clear_motion(ax);
            ax->stop_velocity_cps = 0;
            s->jog_velocity_cps = 0;
            ax->velocity_remainder = 0;
            s->target_user = s->target_user + delta;
            if (have_limits) {
                s->target_user = clamp_i32(s->target_user, min_pos, max_pos);
            }
            s->target_raw = s->soft_zero_raw + s->target_user;
            ax->pp_pulse_cycles = 30;
            ax->fv3_halt_cycles = 0;
            snprintf(s->message, sizeof(s->message), "PP move_rel by %d counts", delta);
        } else {
            start_motion_to(ax, s->target_user + delta, move_ms, speed_rpm, accel_rpm_s, have_limits, min_pos, max_pos);
        }
        strncpy(s->last_command, "move_rel", sizeof(s->last_command) - 1);
        send_status_fd(fd, axis);
        return;
    }

    if (strcmp(cmd, "move_curve_rel") == 0) {
        int32_t delta = 0;
        int32_t min_pos = 0;
        int32_t max_pos = 0;
        uint32_t vmax_counts_s = 0;
        uint32_t accel_counts_s2 = 0;
        uint32_t decel_counts_s2 = 0;
        uint32_t dwell_ms = 0;
        int have_limits = 0;
        char blend_name[24];
        int curve_blend = CURVE_BLEND_SMOOTH;
        if (!ready_for_motion(ax)) {
            send_error_fd(fd, "servo is not ready for curve motion; enable and wait for settle first");
            return;
        }
        if (!find_i32(line, "target_delta_counts", &delta)) {
            send_error_fd(fd, "move_curve_rel requires target_delta_counts");
            return;
        }
        if (!find_u32(line, "vmax_counts_s", &vmax_counts_s) || vmax_counts_s == 0) {
            send_error_fd(fd, "move_curve_rel requires vmax_counts_s > 0");
            return;
        }
        if (!find_u32(line, "accel_counts_s2", &accel_counts_s2) || accel_counts_s2 == 0) {
            send_error_fd(fd, "move_curve_rel requires accel_counts_s2 > 0");
            return;
        }
        if (!find_u32(line, "decel_counts_s2", &decel_counts_s2) || decel_counts_s2 == 0) {
            send_error_fd(fd, "move_curve_rel requires decel_counts_s2 > 0");
            return;
        }
        have_limits = find_i32(line, "min_pos", &min_pos) && find_i32(line, "max_pos", &max_pos);
        (void)find_u32(line, "dwell_ms", &dwell_ms);
        if (find_str(line, "blend", blend_name, sizeof(blend_name))) {
            curve_blend = curve_blend_from_name(blend_name);
        }
        set_control_mode(ax, "incremental");
        ax->commanded_mode = mode_code_for_name("incremental");
        ax->gear_running = 0;
        ax->gear_has_last_master_pos = 0;
        ax->pp_pulse_cycles = 0;
        ax->fv3_halt_cycles = 0;
        start_curve_motion(
            ax,
            delta,
            vmax_counts_s,
            accel_counts_s2,
            decel_counts_s2,
            dwell_ms,
            have_limits,
            min_pos,
            max_pos,
            curve_blend);
        strncpy(s->last_command, "move_curve_rel", sizeof(s->last_command) - 1);
        send_status_fd(fd, axis);
        return;
    }

    if (strcmp(cmd, "jog_velocity") == 0) {
        int32_t velocity = 0;
        if (!ready_for_motion(ax)) {
            send_error_fd(fd, "servo is not ready for velocity jog; enable and wait for settle first");
            return;
        }
        if (!find_i32(line, "velocity", &velocity)) {
            velocity = DEFAULT_JOG_VELOCITY;
        }
        set_control_mode(ax, "velocity");
        ax->commanded_mode = mode_code_for_name("velocity");
        ax->gear_running = 0;
        ax->gear_has_last_master_pos = 0;
        clear_motion(ax);
        ax->stop_velocity_cps = 0;
        s->jog_velocity_cps = velocity;
        ax->velocity_remainder = 0;
        ax->pp_pulse_cycles = 0;
        ax->fv3_halt_cycles = 0;
        strncpy(s->last_command, "jog_velocity", sizeof(s->last_command) - 1);
        snprintf(s->message, sizeof(s->message), "%s velocity jog %d counts/s using CSP target increments", axis_label(axis), velocity);
        send_status_fd(fd, axis);
        return;
    }

    if (strcmp(cmd, "torque_cmd") == 0) {
        int32_t torque = 0;
        (void)find_i32(line, "torque", &torque);
        set_control_mode(ax, "torque");
        ax->commanded_mode = mode_code_for_name("torque");
        ax->gear_running = 0;
        ax->gear_has_last_master_pos = 0;
        s->torque_cmd = torque;
        clear_motion(ax);
        ax->stop_velocity_cps = 0;
        s->jog_velocity_cps = 0;
        ax->velocity_remainder = 0;
        ax->pp_pulse_cycles = 0;
        ax->fv3_halt_cycles = 0;
        strncpy(s->last_command, "torque_cmd", sizeof(s->last_command) - 1);
        snprintf(s->message, sizeof(s->message), "%s torque command staged only; CST PDO is not active", axis_label(axis));
        send_status_fd(fd, axis);
        return;
    }

    send_error_fd(fd, "unknown command");
}

static int setup_server(void)
{
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    int opt = 1;
    struct sockaddr_in addr;

    if (fd < 0) {
        return -1;
    }
    setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt));
    memset(&addr, 0, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    addr.sin_port = htons(SERVER_PORT);

    if (bind(fd, (struct sockaddr *)&addr, sizeof(addr)) < 0 || listen(fd, 8) < 0) {
        close(fd);
        return -1;
    }
    if (set_nonblock(fd) < 0) {
        close(fd);
        return -1;
    }
    return fd;
}

static void close_client(client_t *c)
{
    if (c->fd >= 0) {
        close(c->fd);
    }
    c->fd = -1;
    c->len = 0;
    c->buf[0] = '\0';
}

static void poll_server(unsigned int accept_budget, unsigned int command_budget, unsigned int read_budget_per_client)
{
    unsigned int accepted = 0;
    unsigned int commands = 0;

    while (accepted < accept_budget) {
        int cfd = accept(listen_fd, NULL, NULL);
        if (cfd < 0) {
            if (errno == EAGAIN || errno == EWOULDBLOCK) {
                break;
            }
            break;
        }
        accepted++;
        if (set_nonblock(cfd) < 0) {
            close(cfd);
            continue;
        }
        int placed = 0;
        for (int i = 0; i < MAX_CLIENTS; i++) {
            if (clients[i].fd < 0) {
                clients[i].fd = cfd;
                clients[i].len = 0;
                clients[i].buf[0] = '\0';
                placed = 1;
                break;
            }
        }
        if (!placed) {
            send_error_fd(cfd, "too many clients");
            close(cfd);
        }
    }

    for (int i = 0; i < MAX_CLIENTS; i++) {
        client_t *c = &clients[i];
        unsigned int reads = 0;
        if (c->fd < 0) {
            continue;
        }

        while (commands < command_budget) {
            char *newline = strchr(c->buf, '\n');
            if (newline != NULL) {
                *newline = '\0';
                handle_command(c->fd, c->buf);
                commands++;
                size_t used = (size_t)(newline - c->buf) + 1;
                memmove(c->buf, c->buf + used, c->len - used + 1);
                c->len -= used;
                continue;
            }

            if (reads >= read_budget_per_client) {
                break;
            }
            if (c->len >= sizeof(c->buf) - 1) {
                send_error_fd(c->fd, "command too long");
                close_client(c);
                break;
            }
            ssize_t n = recv(c->fd, c->buf + c->len, sizeof(c->buf) - c->len - 1, 0);
            reads++;
            if (n == 0) {
                close_client(c);
                break;
            }
            if (n < 0) {
                if (errno == EAGAIN || errno == EWOULDBLOCK) {
                    break;
                }
                close_client(c);
                break;
            }
            c->len += (size_t)n;
            c->buf[c->len] = '\0';
            if (c->len >= sizeof(c->buf) - 1) {
                send_error_fd(c->fd, "command too long");
                close_client(c);
                break;
            }
        }
    }
}

static void axis_cycle_logic(axis_runtime_t *ax, int axis)
{
    status_t *s = &ax->st;
    int gear_tracking_active = 0;
    int pp_active = 0;
    int32_t fv3_pos_step = 0;
    int64_t fv3_pos_delta = 0;
    int32_t previous_target_raw = s->target_raw;
    if (!ax->have_last_cycle_target) {
        previous_target_raw = s->target_raw;
        ax->have_last_cycle_target = 1;
    }
    if (axis == AXIS_FV3) {
        if (ax->fv3_have_last_pos) {
            fv3_pos_step = s->pos_raw - ax->fv3_last_pos_raw;
        } else {
            ax->fv3_have_last_pos = 1;
        }
        fv3_pos_delta = i64_abs_diff_i32(s->pos_raw, ax->fv3_last_pos_raw);
        ax->fv3_last_pos_raw = s->pos_raw;
        ax->fv3_feedback_velocity_cps = clamp_i64_to_i32((int64_t)fv3_pos_step * 1000LL);
        if (fv3_pos_delta > 2048) {
            ax->fv3_motion_hold_cycles = 200;
        } else if (ax->fv3_motion_hold_cycles > 0) {
            ax->fv3_motion_hold_cycles--;
        }
    }

    if (s->fault) {
        clear_motion(ax);
        ax->stop_velocity_cps = 0;
        s->jog_velocity_cps = 0;
        ax->velocity_remainder = 0;
        s->servo_request = 0;
        ax->pp_pulse_cycles = 0;
        ax->fv3_halt_cycles = 0;
        ax->fv3_motion_hold_cycles = 0;
        ax->gear_running = 0;
        ax->gear_has_last_master_pos = 0;
        snprintf(s->message, sizeof(s->message), "fault detected, servo request cleared");
    }

    if (s->servo_request && (!s->enabled || s->enable_settle_cycles > 0)) {
        clear_motion(ax);
        ax->stop_velocity_cps = 0;
        s->jog_velocity_cps = 0;
        ax->velocity_remainder = 0;
        ax->pp_pulse_cycles = 0;
        ax->fv3_halt_cycles = 0;
        ax->fv3_motion_hold_cycles = 0;
        ax->gear_running = 0;
        ax->gear_has_last_master_pos = 0;
        s->target_raw = s->pos_raw;
        s->target_user = s->pos_user;
        if (s->enabled && s->enable_settle_cycles > 0) {
            s->enable_settle_cycles--;
            if (s->enable_settle_cycles == 0) {
                snprintf(s->message, sizeof(s->message), "servo enabled and settled");
            }
        }
    } else if (strcmp(s->control_mode, "gear_cam") == 0 && ax->gear_running && s->enabled) {
        axis_runtime_t *master_ax = &axes[ax->gear_master_axis];
        int32_t master_pos = master_ax->st.pos_raw;
        int32_t master_delta = 0;
        int64_t slave_delta = 0;
        if (ax->gear_master_ratio < 1) {
            ax->gear_master_ratio = 1;
        }
        if (ax->gear_slave_ratio < 1) {
            ax->gear_slave_ratio = 1;
        }
        if (!ax->gear_has_last_master_pos) {
            ax->gear_last_master_pos_raw = master_pos;
            ax->gear_has_last_master_pos = 1;
        }
        master_delta = master_pos - ax->gear_last_master_pos_raw;
        ax->gear_last_master_pos_raw = master_pos;
        if (master_delta != 0) {
            slave_delta = ((int64_t)master_delta * (int64_t)ax->gear_slave_ratio) / (int64_t)ax->gear_master_ratio;
            if (slave_delta != 0) {
                s->target_raw = clamp_i64_to_i32((int64_t)s->target_raw + slave_delta);
                s->target_user = s->target_raw - s->soft_zero_raw;
            }
        }
        gear_tracking_active = 1;
    } else if (ax->motion.moving && s->enabled) {
        if (ax->motion.curve_active) {
            update_curve_motion(ax);
        } else if (ax->motion.profile_active) {
            update_profile_motion(ax);
        } else {
            s->target_raw = smooth_move(ax->motion.from, ax->motion.to, ax->motion.step, ax->motion.steps);
            s->target_user = s->target_raw - s->soft_zero_raw;
            if (ax->motion.step >= ax->motion.steps) {
                int32_t legacy_motion_target = ax->motion.to;
                clear_motion(ax);
                s->target_raw = legacy_motion_target;
                s->target_user = s->target_raw - s->soft_zero_raw;
                snprintf(s->message, sizeof(s->message), "motion complete");
            } else {
                ax->motion.step++;
            }
        }
    } else if (ax->stop_velocity_cps != 0 && s->enabled) {
        s->target_raw += velocity_step_counts(ax, ax->stop_velocity_cps);
        s->target_user = s->target_raw - s->soft_zero_raw;
        ax->stop_velocity_cps = decelerate_velocity(ax->stop_velocity_cps, ax->stop_decel_cps2);
        if (ax->stop_velocity_cps == 0) {
            ax->velocity_remainder = 0;
            snprintf(s->message, sizeof(s->message), "controlled stop complete");
        }
    } else if (axis == AXIS_FV3 && ax->fv3_halt_cycles > 0 && s->enabled) {
        s->target_raw = s->pos_raw;
        s->target_user = s->pos_user;
        ax->fv3_halt_cycles--;
    } else if (s->jog_velocity_cps != 0 && s->enabled) {
        s->target_raw += velocity_step_counts(ax, s->jog_velocity_cps);
        s->target_user = s->target_raw - s->soft_zero_raw;
    } else if (!s->servo_request) {
        s->target_raw = s->pos_raw;
        s->target_user = s->pos_user;
        ax->fv3_motion_hold_cycles = 0;
        ax->gear_running = 0;
        ax->gear_has_last_master_pos = 0;
    }
    ax->target_velocity_cps = clamp_i64_to_i32((int64_t)(s->target_raw - previous_target_raw) * 1000LL);
    if (axis == AXIS_FV3 && s->servo_request && s->enabled) {
        /* FV3 PP: keep motion active while trigger/stop window alive, target gap exists, or position is still changing. */
        pp_active = ax->pp_pulse_cycles > 0 ||
                    ax->fv3_halt_cycles > 0 ||
                    i64_abs_diff_i32(s->target_raw, s->pos_raw) > 2048 ||
                    ax->fv3_motion_hold_cycles > 0;
    }
    if (gear_tracking_active) {
        gear_tracking_active = i64_abs_diff_i32(s->target_raw, s->pos_raw) > 1024 ||
                               i64_abs_diff_i32(axes[ax->gear_master_axis].st.target_raw, axes[ax->gear_master_axis].st.pos_raw) > 1024;
    }
    s->moving = ax->motion.moving ||
                s->jog_velocity_cps != 0 ||
                ax->stop_velocity_cps != 0 ||
                pp_active ||
                gear_tracking_active ||
                (strcmp(s->control_mode, "gear_cam") == 0 && ax->gear_running);

    if (ax->fault_reset_cycles > 0 && s->wc_complete) {
        s->cw = 0x0080;
        ax->fault_reset_cycles--;
        if (ax->fault_reset_cycles == 0) {
            snprintf(s->message, sizeof(s->message), "fault reset pulse complete; servo remains disabled");
        }
    } else if (axis == AXIS_FV3 && ax->fv3_halt_cycles > 0 && s->servo_request && s->wc_complete) {
        s->cw = 0x010f;
    } else if (axis == AXIS_FV3 && ax->pp_pulse_cycles > 0 && s->servo_request && s->wc_complete) {
        /* 30: long pulse window for staged absolute moves, 2: one-shot pulse for gear tracking updates. */
        s->cw = (ax->pp_pulse_cycles > 15 || ax->pp_pulse_cycles == 2) ? 0x003f : 0x000f;
        ax->pp_pulse_cycles--;
    } else if (s->servo_request && s->wc_complete) {
        s->cw = next_controlword(s->sw);
        if (s->enabled) {
            s->cw = 0x000f;
        }
    } else {
        s->cw = 0x0000;
    }
}

static void update_axis_d_communication_guard(axis_runtime_t *axis)
{
    status_t *s = &axis->st;
    int healthy = s->operational && s->wc_complete;

    if (realtime_status.have_previous_wc && realtime_status.previous_wc != s->wc) {
        realtime_status.wc_change_count++;
    }
    realtime_status.previous_wc = s->wc;
    realtime_status.have_previous_wc = 1;

    if (healthy) {
        if (realtime_status.consecutive_good_cycles < AXIS_D_GOOD_CYCLES_TO_ARM) {
            realtime_status.consecutive_good_cycles++;
        }
        if (realtime_status.consecutive_good_cycles >= AXIS_D_GOOD_CYCLES_TO_ARM) {
            realtime_status.timing_guard_armed = 1;
        }
    } else {
        realtime_status.wc_incomplete_cycles++;
        realtime_status.consecutive_good_cycles = 0;
        if (realtime_status.timing_guard_armed) {
            realtime_status.communication_timing_fault = 1;
        }
    }

    if (realtime_status.communication_timing_fault) {
        s->servo_request = 0;
        clear_motion(axis);
        axis->stop_velocity_cps = 0;
        axis->target_velocity_cps = 0;
        axis->velocity_remainder = 0;
        axis->fault_reset_cycles = 0;
        axis->pp_pulse_cycles = 0;
        axis->fv3_halt_cycles = 0;
        axis->gear_running = 0;
        axis->gear_has_last_master_pos = 0;
        axis->commanded_mode = 0;
        s->moving = 0;
        s->cw = 0;
        s->target_raw = s->pos_raw;
        s->target_user = s->pos_user;
        snprintf(s->message, sizeof(s->message), "communication timing fault latched; restart required");
    }
}

static int run_uservo_axis_d(void)
{
    ec_master_t *master;
    ec_domain_t *domain;
    ec_slave_config_t *slave_config;
    uint8_t *process_data;
    uint64_t deadline_ns;
    axis_runtime_t *axis = &axes[AXIS_MCTIVITY];

    if (prepare_axis_d_realtime() < 0) {
        return 1;
    }

    master = ecrt_request_master(0);
    if (!master) {
        fprintf(stderr, "failed to request EtherCAT master 0\n");
        return 1;
    }

    domain = ecrt_master_create_domain(master);
    slave_config = ecrt_master_slave_config(master, 0, 0, USERVO_VENDOR_ID, USERVO_PRODUCT_CODE);
    if (!domain || !slave_config) {
        fprintf(stderr, "failed to create Uservo axis D domain or slave config\n");
        ecrt_release_master(master);
        return 1;
    }

    if (ecrt_slave_config_pdos(slave_config, EC_END, uservo_syncs)) {
        fprintf(stderr, "failed to configure Uservo axis D PDOs\n");
        ecrt_release_master(master);
        return 1;
    }
    ecrt_slave_config_dc(slave_config, 0x0300, PERIOD_NS, 0, 0, 0);
    if (ecrt_master_select_reference_clock(master, slave_config)) {
        fprintf(stderr, "failed to select Uservo axis D DC reference clock\n");
        ecrt_release_master(master);
        return 1;
    }

    if (ecrt_domain_reg_pdo_entry_list(domain, uservo_domain_regs)) {
        fprintf(stderr, "failed to register Uservo axis D PDO entries\n");
        ecrt_release_master(master);
        return 1;
    }
    if (ecrt_master_activate(master)) {
        fprintf(stderr, "failed to activate EtherCAT master for Uservo axis D\n");
        ecrt_release_master(master);
        return 1;
    }

    process_data = ecrt_domain_data(domain);
    if (!process_data) {
        fprintf(stderr, "failed to get Uservo axis D domain data\n");
        ecrt_release_master(master);
        return 1;
    }

    if (commissioning_inhibit) {
        axis->commanded_mode = 0;
        axis->st.servo_request = 0;
        snprintf(axis->st.message, sizeof(axis->st.message), "axis D commissioning inhibit active");
    }

    printf(
        "Axis D Uservo motion daemon listening on 127.0.0.1:%d (inhibit=%s, counts/rev=%lld)\n",
        SERVER_PORT,
        commissioning_inhibit ? "on" : "off",
        (long long)counts_per_rev);
    fflush(stdout);

    deadline_ns = monotonic_now_ns();

    while (running) {
        uint64_t scheduled_time_ns;
        uint64_t cycle_started_ns;
        uint64_t cycle_finished_ns;
        ec_slave_config_state_t slave_state;
        ec_domain_state_t domain_state;

        if (wait_for_axis_d_cycle(&deadline_ns, &scheduled_time_ns) < 0) {
            perror("Axis D cycle sleep");
            break;
        }
        cycle_started_ns = monotonic_now_ns();

        ecrt_master_application_time(master, scheduled_time_ns);
        ecrt_master_receive(master);
        ecrt_domain_process(domain);
        ecrt_slave_config_state(slave_config, &slave_state);
        ecrt_domain_state(domain, &domain_state);

        axis->st.sw = EC_READ_U16(process_data + uservo_off_statusword);
        axis->st.err = 0;
        axis->st.mode_display = EC_READ_S8(process_data + uservo_off_mode_display);
        axis->st.pos_raw = EC_READ_S32(process_data + uservo_off_position_actual);
        axis->st.following_error = 0;
        axis->st.torque_feedback = 0;
        axis->st.al_state = slave_state.al_state;
        axis->st.operational = slave_state.operational;
        axis->st.wc = domain_state.working_counter;
        axis->st.wc_complete = domain_state.wc_state == EC_WC_COMPLETE;
        axis->st.enabled = operation_enabled(axis->st.sw);
        axis->st.fault = (axis->st.sw & 0x0008) != 0;
        axis->st.pos_user = axis->st.pos_raw - axis->st.soft_zero_raw;

        update_axis_d_communication_guard(axis);
        if (commissioning_inhibit) {
            axis->st.servo_request = 0;
            axis->st.target_raw = axis->st.pos_raw;
            axis->st.target_user = axis->st.pos_user;
        }
        axis_cycle_logic(axis, AXIS_MCTIVITY);

        if (realtime_status.communication_timing_fault) {
            axis->st.cw = 0;
        }

        EC_WRITE_U16(
            process_data + uservo_off_controlword,
            realtime_status.communication_timing_fault
                ? 0x0000
                : (commissioning_inhibit ? (axis->st.cw == 0x0080 ? 0x0080 : 0x0000) : axis->st.cw));
        EC_WRITE_S8(
            process_data + uservo_off_mode,
            commissioning_inhibit || realtime_status.communication_timing_fault ? 0 : axis->commanded_mode);
        EC_WRITE_S32(process_data + uservo_off_target_position, axis->st.target_raw);
        EC_WRITE_U32(process_data + uservo_off_digital_output, 0);

        ecrt_domain_queue(domain);
        ecrt_master_sync_reference_clock(master);
        ecrt_master_sync_slave_clocks(master);
        ecrt_master_send(master);

        poll_server(AXIS_D_SERVER_ACCEPT_BUDGET, AXIS_D_SERVER_COMMAND_BUDGET, 1);
        axis->st.cycles++;

        cycle_finished_ns = monotonic_now_ns();
        realtime_status.last_cycle_runtime_ns = cycle_finished_ns - cycle_started_ns;
        if (realtime_status.last_cycle_runtime_ns > realtime_status.max_cycle_runtime_ns) {
            realtime_status.max_cycle_runtime_ns = realtime_status.last_cycle_runtime_ns;
        }
    }

    printf("Disabling Uservo axis D output before exit...\n");
    for (unsigned int i = 0; i < AXIS_D_SHUTDOWN_CYCLES; i++) {
        uint64_t scheduled_time_ns;
        if (wait_for_axis_d_cycle(&deadline_ns, &scheduled_time_ns) < 0) {
            break;
        }
        ecrt_master_application_time(master, scheduled_time_ns);
        ecrt_master_receive(master);
        ecrt_domain_process(domain);
        EC_WRITE_U16(process_data + uservo_off_controlword, 0x0000);
        EC_WRITE_S8(process_data + uservo_off_mode, 0);
        EC_WRITE_S32(
            process_data + uservo_off_target_position,
            EC_READ_S32(process_data + uservo_off_position_actual));
        EC_WRITE_U32(process_data + uservo_off_digital_output, 0);
        ecrt_domain_queue(domain);
        ecrt_master_sync_reference_clock(master);
        ecrt_master_sync_slave_clocks(master);
        ecrt_master_send(master);
    }

    for (int i = 0; i < MAX_CLIENTS; i++) {
        close_client(&clients[i]);
    }
    close(listen_fd);
    ecrt_release_master(master);
    return 0;
}

int main(void)
{
    ec_master_t *master;
    ec_domain_t *domain_mctivity;
    ec_domain_t *domain_fv3;
    ec_slave_config_t *sc_mctivity;
    ec_slave_config_t *sc_fv3;
    uint8_t *pd_mctivity;
    uint8_t *pd_fv3;
    struct timespec wake_time;
    uint64_t app_time_base;
    const char *topology = getenv("MCTIVITY_TOPOLOGY");

    uservo_axis_d_topology = topology && strcmp(topology, "axis-d-uservo") == 0;
    commissioning_inhibit = uservo_axis_d_topology
        ? env_flag_default("MCTIVITY_COMMISSIONING_INHIBIT", 1)
        : 0;
    require_realtime = uservo_axis_d_topology
        ? env_flag_default("MCTIVITY_REQUIRE_REALTIME", 1)
        : 0;
    counts_per_rev = uservo_axis_d_topology ? USERVO_COUNTS_PER_REV : LEGACY_COUNTS_PER_REV;

    signal(SIGINT, handle_signal);
    signal(SIGTERM, handle_signal);

    for (int i = 0; i < MAX_CLIENTS; i++) {
        clients[i].fd = -1;
    }
    for (int axis = 0; axis < AXIS_COUNT; axis++) {
        memset(&axes[axis], 0, sizeof(axes[axis]));
        set_control_mode(&axes[axis], "position");
        axes[axis].commanded_mode = mode_code_for_name("position");
        axes[axis].gear_master_axis = axis == AXIS_FV3 ? AXIS_MCTIVITY : AXIS_FV3;
        axes[axis].gear_master_ratio = 1;
        axes[axis].gear_slave_ratio = 1;
        snprintf(axes[axis].st.message, sizeof(axes[axis].st.message), "starting");
    }

    listen_fd = setup_server();
    if (listen_fd < 0) {
        perror("failed to start command server on 127.0.0.1:10001");
        return 1;
    }

    if (uservo_axis_d_topology) {
        return run_uservo_axis_d();
    }

    master = ecrt_request_master(0);
    if (!master) {
        fprintf(stderr, "failed to request EtherCAT master 0\n");
        return 1;
    }

    domain_mctivity = ecrt_master_create_domain(master);
    domain_fv3 = ecrt_master_create_domain(master);
    sc_mctivity = ecrt_master_slave_config(master, 0, 0, MCTIVITY_VENDOR_ID, MCTIVITY_PRODUCT_CODE);
    sc_fv3 = ecrt_master_slave_config(master, 0, 1, FV3_VENDOR_ID, FV3_PRODUCT_CODE);
    if (!domain_mctivity || !domain_fv3 || !sc_mctivity || !sc_fv3) {
        fprintf(stderr, "failed to create domains or slave configs\n");
        ecrt_release_master(master);
        return 1;
    }

    if (ecrt_slave_config_pdos(sc_mctivity, EC_END, mctivity_syncs)) {
        fprintf(stderr, "failed to configure MCTIVITY PDOs\n");
        ecrt_release_master(master);
        return 1;
    }
    if (ecrt_slave_config_pdos(sc_fv3, EC_END, fv3_syncs)) {
        fprintf(stderr, "failed to configure FV3 PDOs\n");
        ecrt_release_master(master);
        return 1;
    }
    ecrt_slave_config_dc(sc_mctivity, 0x0300, PERIOD_NS, 0, 0, 0);
    ecrt_slave_config_dc(sc_fv3, 0x0300, PERIOD_NS, 0, 0, 0);

    if (ecrt_domain_reg_pdo_entry_list(domain_mctivity, mctivity_domain_regs)) {
        fprintf(stderr, "failed to register MCTIVITY PDO entries\n");
        ecrt_release_master(master);
        return 1;
    }
    if (ecrt_domain_reg_pdo_entry_list(domain_fv3, fv3_domain_regs)) {
        fprintf(stderr, "failed to register FV3 PDO entries\n");
        ecrt_release_master(master);
        return 1;
    }

    if (ecrt_master_activate(master)) {
        fprintf(stderr, "failed to activate master\n");
        ecrt_release_master(master);
        return 1;
    }

    pd_mctivity = ecrt_domain_data(domain_mctivity);
    pd_fv3 = ecrt_domain_data(domain_fv3);
    if (!pd_mctivity || !pd_fv3) {
        fprintf(stderr, "failed to get domain data\n");
        ecrt_release_master(master);
        return 1;
    }

    printf("Dual-axis motion daemon listening on 127.0.0.1:%d (mctivity,fv3)\n", SERVER_PORT);
    fflush(stdout);

    clock_gettime(CLOCK_MONOTONIC, &wake_time);
    app_time_base = timespec_to_ns(&wake_time);

    while (running) {
        uint64_t app_time = app_time_base + (uint64_t)axes[AXIS_MCTIVITY].st.cycles * PERIOD_NS;
        ec_slave_config_state_t mctivity_slave_state;
        ec_slave_config_state_t fv3_slave_state;
        ec_domain_state_t mctivity_domain_state;
        ec_domain_state_t fv3_domain_state;

        ecrt_master_application_time(master, app_time);
        ecrt_master_receive(master);
        ecrt_domain_process(domain_mctivity);
        ecrt_domain_process(domain_fv3);

        ecrt_slave_config_state(sc_mctivity, &mctivity_slave_state);
        ecrt_slave_config_state(sc_fv3, &fv3_slave_state);
        ecrt_domain_state(domain_mctivity, &mctivity_domain_state);
        ecrt_domain_state(domain_fv3, &fv3_domain_state);

        /* MCTIVITY inputs. */
        axes[AXIS_MCTIVITY].st.sw = EC_READ_U16(pd_mctivity + mctivity_off_statusword);
        axes[AXIS_MCTIVITY].st.err = EC_READ_U16(pd_mctivity + mctivity_off_error_code);
        axes[AXIS_MCTIVITY].st.mode_display = EC_READ_S8(pd_mctivity + mctivity_off_mode_display);
        axes[AXIS_MCTIVITY].st.pos_raw = EC_READ_S32(pd_mctivity + mctivity_off_position_actual);
        axes[AXIS_MCTIVITY].st.following_error = EC_READ_S32(pd_mctivity + mctivity_off_following_error);
        axes[AXIS_MCTIVITY].st.torque_feedback = 0;
        axes[AXIS_MCTIVITY].st.al_state = mctivity_slave_state.al_state;
        axes[AXIS_MCTIVITY].st.operational = mctivity_slave_state.operational;
        axes[AXIS_MCTIVITY].st.wc = mctivity_domain_state.working_counter;
        axes[AXIS_MCTIVITY].st.wc_complete = mctivity_domain_state.wc_state == EC_WC_COMPLETE;
        axes[AXIS_MCTIVITY].st.enabled = operation_enabled(axes[AXIS_MCTIVITY].st.sw);
        axes[AXIS_MCTIVITY].st.fault = (axes[AXIS_MCTIVITY].st.sw & 0x0008) != 0 || axes[AXIS_MCTIVITY].st.err != 0;
        axes[AXIS_MCTIVITY].st.pos_user = axes[AXIS_MCTIVITY].st.pos_raw - axes[AXIS_MCTIVITY].st.soft_zero_raw;

        /* FV3 inputs. */
        axes[AXIS_FV3].st.sw = EC_READ_U16(pd_fv3 + fv3_off_statusword);
        axes[AXIS_FV3].st.err = EC_READ_U16(pd_fv3 + fv3_off_error_code);
        axes[AXIS_FV3].st.mode_display = axes[AXIS_FV3].commanded_mode;
        axes[AXIS_FV3].st.pos_raw = EC_READ_S32(pd_fv3 + fv3_off_position_actual);
        axes[AXIS_FV3].st.following_error = EC_READ_S32(pd_fv3 + fv3_off_following_error);
        axes[AXIS_FV3].st.torque_feedback = EC_READ_S16(pd_fv3 + fv3_off_torque_actual);
        axes[AXIS_FV3].st.al_state = fv3_slave_state.al_state;
        axes[AXIS_FV3].st.operational = fv3_slave_state.operational;
        axes[AXIS_FV3].st.wc = fv3_domain_state.working_counter;
        axes[AXIS_FV3].st.wc_complete = fv3_domain_state.wc_state == EC_WC_COMPLETE;
        axes[AXIS_FV3].st.enabled = operation_enabled(axes[AXIS_FV3].st.sw);
        axes[AXIS_FV3].st.fault = (axes[AXIS_FV3].st.sw & 0x0008) != 0 || axes[AXIS_FV3].st.err != 0;
        axes[AXIS_FV3].st.pos_user = axes[AXIS_FV3].st.pos_raw - axes[AXIS_FV3].st.soft_zero_raw;

        axis_cycle_logic(&axes[AXIS_MCTIVITY], AXIS_MCTIVITY);
        axis_cycle_logic(&axes[AXIS_FV3], AXIS_FV3);

        EC_WRITE_U16(pd_mctivity + mctivity_off_controlword, axes[AXIS_MCTIVITY].st.cw);
        EC_WRITE_S8(pd_mctivity + mctivity_off_mode, axes[AXIS_MCTIVITY].commanded_mode);
        EC_WRITE_S32(pd_mctivity + mctivity_off_target_position, axes[AXIS_MCTIVITY].st.target_raw);
        EC_WRITE_U16(pd_mctivity + mctivity_off_touch_probe_function, 0x0000);

        EC_WRITE_U16(pd_fv3 + fv3_off_controlword, axes[AXIS_FV3].st.cw);
        EC_WRITE_S32(pd_fv3 + fv3_off_target_position, axes[AXIS_FV3].st.target_raw);
        EC_WRITE_U16(pd_fv3 + fv3_off_touch_probe_function, 0x0000);
        EC_WRITE_U32(pd_fv3 + fv3_off_digital_output, 0);

        ecrt_domain_queue(domain_mctivity);
        ecrt_domain_queue(domain_fv3);
        ecrt_master_sync_reference_clock(master);
        ecrt_master_sync_slave_clocks(master);
        ecrt_master_send(master);

        poll_server(UINT32_MAX, UINT32_MAX, UINT32_MAX);

        axes[AXIS_MCTIVITY].st.cycles++;
        axes[AXIS_FV3].st.cycles++;
        sleep_until_next(&wake_time);
    }

    printf("Disabling drive outputs before exit...\n");
    for (int i = 0; i < 300; i++) {
        ecrt_master_receive(master);
        ecrt_domain_process(domain_mctivity);
        ecrt_domain_process(domain_fv3);

        EC_WRITE_U16(pd_mctivity + mctivity_off_controlword, 0x0000);
        EC_WRITE_S8(pd_mctivity + mctivity_off_mode, axes[AXIS_MCTIVITY].commanded_mode);
        EC_WRITE_S32(pd_mctivity + mctivity_off_target_position, axes[AXIS_MCTIVITY].st.target_raw);
        EC_WRITE_U16(pd_mctivity + mctivity_off_touch_probe_function, 0x0000);

        EC_WRITE_U16(pd_fv3 + fv3_off_controlword, 0x0000);
        EC_WRITE_S32(pd_fv3 + fv3_off_target_position, axes[AXIS_FV3].st.target_raw);
        EC_WRITE_U16(pd_fv3 + fv3_off_touch_probe_function, 0x0000);
        EC_WRITE_U32(pd_fv3 + fv3_off_digital_output, 0);

        ecrt_domain_queue(domain_mctivity);
        ecrt_domain_queue(domain_fv3);
        ecrt_master_sync_reference_clock(master);
        ecrt_master_sync_slave_clocks(master);
        ecrt_master_send(master);
        sleep_until_next(&wake_time);
    }

    for (int i = 0; i < MAX_CLIENTS; i++) {
        close_client(&clients[i]);
    }
    close(listen_fd);
    ecrt_release_master(master);
    return 0;
}
