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

#include "communication_guard.h"
#include "electronic_gear.h"
#include "realtime_schedule.h"
#include "realtime_guard.h"

#define MCTIVITY_VENDOR_ID 0x000116c7
#define MCTIVITY_PRODUCT_CODE 0x007e0402
#define FV3_VENDOR_ID 0x00000ebc
#define FV3_PRODUCT_CODE 0x00000010
#define USERVO_VENDOR_ID 0x00666999
#define USERVO_PRODUCT_CODE 0x00004806

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
static int uservo_pv_topology = 0;
static int uservo_dual_pv_topology = 0;
static int uservo_dual_gear_topology = 0;
static int uservo_dual_combined_topology = 0;
static int uservo_dual_topology = 0;
static int sync_group_session_active = 0;
static int sync_group_motion_active = 0;
static int sync_group_both_enabled_once = 0;
static int sync_group_safety_latched = 0;
static int gear_group_session_active = 0;
static int gear_group_safety_latched = 0;
static int gear_group_master_axis = AXIS_MCTIVITY;
static int gear_group_slave_axis = AXIS_FV3;
static int commissioning_inhibit = 0;
static int require_realtime = 0;
static int64_t counts_per_rev = LEGACY_COUNTS_PER_REV;
static uint32_t gear_following_error_limit_counts = MCTIVITY_GEAR_DEFAULT_FOLLOWING_ERROR_LIMIT_COUNTS;
static uint32_t gear_max_ratio = MCTIVITY_GEAR_DEFAULT_MAX_RATIO;
static uint32_t gear_max_velocity_cps[AXIS_COUNT] = {37000U, 37000U};
static char gear_last_trip_reason[96];
static int64_t gear_last_trip_position_error;
static int64_t gear_last_trip_step_counts;
static uint32_t gear_last_trip_elapsed_cycles;
static int32_t gear_last_trip_target_raw;
static int32_t gear_last_trip_actual_raw;
static int32_t gear_last_trip_master_raw;

typedef struct {
    uint32_t counts_per_rev;
    uint32_t target_speed_rpm;
    uint32_t max_speed_rpm;
    uint32_t accel_rpm_s;
    uint32_t decel_rpm_s;
    uint32_t stop_decel_rpm_s;
    uint32_t target_velocity_cps;
    uint32_t max_velocity_cps;
    uint32_t accel_cps2;
    uint32_t decel_cps2;
    uint32_t stop_decel_cps2;
} uservo_pv_profile_t;

static uservo_pv_profile_t uservo_pv_profiles[AXIS_COUNT];

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
    mctivity_schedule_guard_t schedule_guard;
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
static unsigned int uservo_off_target_velocity;
static unsigned int uservo_off_digital_output;
static unsigned int uservo_off_statusword;
static unsigned int uservo_off_mode_display;
static unsigned int uservo_off_position_actual;
static unsigned int uservo_off_velocity_actual;
static unsigned int uservo_off_digital_input;

typedef struct {
    unsigned int controlword;
    unsigned int mode;
    unsigned int target_velocity;
    unsigned int digital_output;
    unsigned int statusword;
    unsigned int mode_display;
    unsigned int velocity_actual;
    unsigned int digital_input;
} uservo_pv_offsets_t;

static uservo_pv_offsets_t uservo_dual_pv_offsets[AXIS_COUNT];
static ec_pdo_entry_reg_t uservo_dual_pv_domain_regs[AXIS_COUNT * 8 + 1];

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

/* Combined map: CSP position fields plus the native PV target/feedback fields.
 * The mode selects which target is consumed: mode 3 uses 0x60FF, mode 8 uses
 * 0x607A. Each object is mapped only once, avoiding duplicate controlword and
 * mode entries in the process image. */
static ec_pdo_entry_info_t uservo_combined_pdo_entries[] = {
    {0x6040, 0x00, 16}, {0x6060, 0x00, 8}, {0x607a, 0x00, 32}, {0x60ff, 0x00, 32},
    {0x60fe, 0x01, 32}, {0x6041, 0x00, 16}, {0x6061, 0x00, 8}, {0x6064, 0x00, 32},
    {0x606c, 0x00, 32}, {0x60fd, 0x00, 32},
};

static ec_pdo_info_t uservo_combined_pdos[] = {
    {0x1600, 5, uservo_combined_pdo_entries + 0},
    {0x1a00, 5, uservo_combined_pdo_entries + 5},
};

static ec_sync_info_t uservo_combined_syncs[] = {
    {0, EC_DIR_OUTPUT, 0, NULL, EC_WD_DISABLE},
    {1, EC_DIR_INPUT, 0, NULL, EC_WD_DISABLE},
    {2, EC_DIR_OUTPUT, 1, uservo_combined_pdos + 0, EC_WD_ENABLE},
    {3, EC_DIR_INPUT, 1, uservo_combined_pdos + 1, EC_WD_DISABLE},
    {0xff, 0, 0, NULL, 0}
};

/* Official Uservo PV map: RxPDO 0x1601 / TxPDO 0x1A01. */
static ec_pdo_entry_info_t uservo_pv_pdo_entries[] = {
    {0x6040, 0x00, 16}, {0x6060, 0x00, 8}, {0x60ff, 0x00, 32}, {0x60fe, 0x01, 32},
    {0x6041, 0x00, 16}, {0x6061, 0x00, 8}, {0x606c, 0x00, 32}, {0x60fd, 0x00, 32},
};

static ec_pdo_info_t uservo_pv_pdos[] = {
    {0x1601, 4, uservo_pv_pdo_entries + 0},
    {0x1a01, 4, uservo_pv_pdo_entries + 4},
};

static ec_sync_info_t uservo_pv_syncs[] = {
    {0, EC_DIR_OUTPUT, 0, NULL, EC_WD_DISABLE},
    {1, EC_DIR_INPUT, 0, NULL, EC_WD_DISABLE},
    {2, EC_DIR_OUTPUT, 1, uservo_pv_pdos + 0, EC_WD_ENABLE},
    {3, EC_DIR_INPUT, 1, uservo_pv_pdos + 1, EC_WD_DISABLE},
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

static const ec_pdo_entry_reg_t uservo_pv_domain_regs[] = {
    {0, 0, USERVO_VENDOR_ID, USERVO_PRODUCT_CODE, 0x6040, 0, &uservo_off_controlword, NULL},
    {0, 0, USERVO_VENDOR_ID, USERVO_PRODUCT_CODE, 0x6060, 0, &uservo_off_mode, NULL},
    {0, 0, USERVO_VENDOR_ID, USERVO_PRODUCT_CODE, 0x60ff, 0, &uservo_off_target_velocity, NULL},
    {0, 0, USERVO_VENDOR_ID, USERVO_PRODUCT_CODE, 0x60fe, 1, &uservo_off_digital_output, NULL},
    {0, 0, USERVO_VENDOR_ID, USERVO_PRODUCT_CODE, 0x6041, 0, &uservo_off_statusword, NULL},
    {0, 0, USERVO_VENDOR_ID, USERVO_PRODUCT_CODE, 0x6061, 0, &uservo_off_mode_display, NULL},
    {0, 0, USERVO_VENDOR_ID, USERVO_PRODUCT_CODE, 0x606c, 0, &uservo_off_velocity_actual, NULL},
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
    int32_t velocity_actual_cps;
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
    int gear_direction;
    int gear_has_last_master_pos;
    int32_t gear_last_master_pos_raw;
    uint32_t gear_last_master_cycle;
    int64_t gear_position_error;
    uint32_t gear_error_over_limit_cycles;
    int64_t gear_last_step_counts;
    uint32_t gear_last_elapsed_cycles;
    int gear_safety_latched;
    mctivity_electronic_gear_t gear_math;
} axis_runtime_t;

typedef struct {
    unsigned int controlword;
    unsigned int mode;
    unsigned int target_position;
    unsigned int target_velocity;
    unsigned int digital_output;
    unsigned int statusword;
    unsigned int mode_display;
    unsigned int position_actual;
    unsigned int velocity_actual;
    unsigned int digital_input;
} uservo_csp_offsets_t;

static uservo_csp_offsets_t uservo_dual_csp_offsets[AXIS_COUNT];
static ec_pdo_entry_reg_t uservo_dual_csp_domain_regs[AXIS_COUNT * 8 + 1];
static ec_pdo_entry_reg_t uservo_dual_combined_domain_regs[AXIS_COUNT * 10 + 1];

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

static int env_u32_required(const char *name, uint32_t *result)
{
    const char *value = getenv(name);
    char *end = NULL;
    unsigned long parsed;
    if (!value || !*value) {
        fprintf(stderr, "required profile parameter is missing: %s\n", name);
        return -1;
    }
    errno = 0;
    parsed = strtoul(value, &end, 10);
    if (errno != 0 || end == value || *end != '\0' || parsed == 0 || parsed > UINT32_MAX) {
        fprintf(stderr, "invalid positive profile parameter: %s=%s\n", name, value);
        return -1;
    }
    *result = (uint32_t)parsed;
    return 0;
}

static int axis_is_uservo_pv(int axis)
{
    return uservo_pv_topology && (uservo_dual_pv_topology || axis == AXIS_MCTIVITY);
}

static int axis_uses_native_pv_control(int axis, const char *mode)
{
    return mode && strcmp(mode, "velocity") == 0 &&
           (axis_is_uservo_pv(axis) || uservo_dual_combined_topology);
}

static int axis_is_uservo_gear(int axis)
{
    (void)axis;
    return uservo_dual_gear_topology;
}

static int axis_is_fv3_hardware(int axis)
{
    return axis == AXIS_FV3 && !uservo_dual_topology;
}

static const uservo_pv_profile_t *uservo_pv_profile_for_axis(int axis)
{
    return (axis_is_uservo_pv(axis) || uservo_dual_combined_topology)
        ? &uservo_pv_profiles[axis]
        : NULL;
}

static const char *axis_name(int axis)
{
    if (uservo_dual_topology) {
        return axis == AXIS_FV3 ? "mctivity_e" : "mctivity";
    }
    return axis == AXIS_FV3 ? "fv3" : "mctivity";
}

static const char *axis_label(int axis)
{
    if (uservo_dual_topology) {
        if (uservo_dual_gear_topology) {
            return axis == AXIS_FV3 ? "Axis E Uservo" : "Axis D Uservo";
        }
        return axis == AXIS_FV3 ? "Axis E Uservo PV" : "Axis D Uservo PV";
    }
    if (uservo_axis_d_topology && axis == AXIS_MCTIVITY) {
        return uservo_pv_topology ? "Axis D Uservo PV" : "Axis D Uservo";
    }
    return axis == AXIS_FV3 ? "FV3" : "MCTIVITY";
}

static int axis_from_name(const char *name, int fallback)
{
    if (!name) {
        return fallback;
    }
    if (uservo_dual_topology &&
        (strcmp(name, "mctivity_e") == 0 || strcmp(name, "E") == 0 || strcmp(name, "e") == 0)) {
        return AXIS_FV3;
    }
    if (!uservo_dual_topology && (strcmp(name, "fv3") == 0 || strcmp(name, "flexem") == 0)) {
        return AXIS_FV3;
    }
    if (strcmp(name, "mctivity") == 0 || strcmp(name, "hcfa") == 0 ||
        (uservo_dual_pv_topology && (strcmp(name, "D") == 0 || strcmp(name, "d") == 0))) {
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

static int dual_control_active(void)
{
    return sync_group_session_active || sync_group_motion_active || gear_group_session_active ||
        axes[AXIS_MCTIVITY].st.servo_request || axes[AXIS_MCTIVITY].st.enabled ||
        axes[AXIS_MCTIVITY].st.moving || axes[AXIS_FV3].st.servo_request ||
        axes[AXIS_FV3].st.enabled || axes[AXIS_FV3].st.moving;
}

static void record_axis_d_skipped_periods(uint64_t skipped_periods)
{
    if (skipped_periods == 0) {
        return;
    }
    realtime_status.deadline_miss_count++;
    realtime_status.skipped_periods += skipped_periods;
    realtime_status.consecutive_good_cycles = 0;
    if (uservo_axis_d_topology) {
        mctivity_schedule_guard_note_miss(
            &realtime_status.schedule_guard,
            skipped_periods,
            dual_control_active());
    } else if (realtime_status.timing_guard_armed) {
        realtime_status.communication_timing_fault = 1;
    }
    if (realtime_status.schedule_guard.fault_latched) {
        realtime_status.communication_timing_fault = 1;
    }
}

static int wait_for_axis_d_cycle(uint64_t *previous_deadline_ns, uint64_t *scheduled_time_ns)
{
    uint64_t now_ns = monotonic_now_ns();
    mctivity_schedule_step_t step =
        mctivity_schedule_next(*previous_deadline_ns, now_ns, PERIOD_NS);

    if (step.skipped_periods > 0) {
        record_axis_d_skipped_periods(step.skipped_periods);
    } else if (uservo_axis_d_topology) {
        mctivity_schedule_guard_note_good_cycle(&realtime_status.schedule_guard);
    }
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
    /*
     * Linux/glibc may expose SCHED_RESET_ON_FORK as an enum rather than a
     * preprocessor macro.  Use the kernel ABI bit explicitly so the flag is
     * always removed before comparing the base scheduling policy.
     */
    if (realtime_status.scheduler_policy >= 0) {
        realtime_status.scheduler_policy &= ~0x40000000;
    }
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
        return uservo_dual_topology ? -1 : AXIS_MCTIVITY;
    }
    if (uservo_dual_topology) {
        if (strcmp(dev, "mctivity_e") == 0 || strcmp(dev, "E") == 0 || strcmp(dev, "e") == 0) {
            return AXIS_FV3;
        }
        if (strcmp(dev, "mctivity") == 0 || strcmp(dev, "D") == 0 || strcmp(dev, "d") == 0) {
            return AXIS_MCTIVITY;
        }
        return -1;
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

static int8_t axis_mode_code(int axis, const char *mode)
{
    /* DS1-E4806N PV is CiA 402 mode 3; legacy velocity remains CSV (9). */
    if (axis_uses_native_pv_control(axis, mode)) {
        return 3;
    }
    return mode_code_for_name(mode);
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

static int load_uservo_pv_profile(uservo_pv_profile_t *profile, const char *axis_prefix, int legacy_keys)
{
    char name[96];
#define LOAD_PROFILE_U32(field, suffix, legacy_name)                                      \
    do {                                                                                  \
        if (legacy_keys) {                                                                \
            if (env_u32_required((legacy_name), &(profile)->field) < 0) return -1;        \
        } else {                                                                          \
            snprintf(name, sizeof(name), "MCTIVITY_AXIS_%s_%s", axis_prefix, (suffix)); \
            if (env_u32_required(name, &(profile)->field) < 0) return -1;                 \
        }                                                                                 \
    } while (0)

    memset(profile, 0, sizeof(*profile));
    LOAD_PROFILE_U32(counts_per_rev, "COUNTS_PER_REV", "MCTIVITY_AXIS_COUNTS_PER_REV");
    LOAD_PROFILE_U32(target_speed_rpm, "PV_TARGET_SPEED_RPM", "MCTIVITY_PV_TARGET_SPEED_RPM");
    LOAD_PROFILE_U32(max_speed_rpm, "PV_MAX_SPEED_RPM", "MCTIVITY_PV_MAX_SPEED_RPM");
    LOAD_PROFILE_U32(accel_rpm_s, "PV_ACCEL_RPM_S", "MCTIVITY_PV_ACCEL_RPM_S");
    LOAD_PROFILE_U32(decel_rpm_s, "PV_DECEL_RPM_S", "MCTIVITY_PV_DECEL_RPM_S");
    LOAD_PROFILE_U32(stop_decel_rpm_s, "PV_STOP_DECEL_RPM_S", "MCTIVITY_PV_STOP_DECEL_RPM_S");
#undef LOAD_PROFILE_U32

    if (profile->target_speed_rpm > profile->max_speed_rpm) {
        fprintf(stderr, "axis %s PV target speed exceeds maximum speed\n", axis_prefix);
        return -1;
    }
    if (profile->decel_rpm_s != profile->stop_decel_rpm_s) {
        fprintf(stderr, "axis %s PV 0x6084 deceleration and stop deceleration must match\n", axis_prefix);
        return -1;
    }
    if ((uint64_t)profile->max_speed_rpm * profile->counts_per_rev > (uint64_t)INT32_MAX * 60ULL ||
        (uint64_t)profile->accel_rpm_s * profile->counts_per_rev > (uint64_t)UINT32_MAX * 60ULL ||
        (uint64_t)profile->decel_rpm_s * profile->counts_per_rev > (uint64_t)UINT32_MAX * 60ULL) {
        fprintf(stderr, "axis %s resolved PV parameter exceeds PDO/SDO numeric range\n", axis_prefix);
        return -1;
    }
    profile->target_velocity_cps = (uint32_t)(((uint64_t)profile->target_speed_rpm * profile->counts_per_rev) / 60ULL);
    profile->max_velocity_cps = (uint32_t)(((uint64_t)profile->max_speed_rpm * profile->counts_per_rev) / 60ULL);
    profile->accel_cps2 = (uint32_t)(((uint64_t)profile->accel_rpm_s * profile->counts_per_rev) / 60ULL);
    profile->decel_cps2 = (uint32_t)(((uint64_t)profile->decel_rpm_s * profile->counts_per_rev) / 60ULL);
    profile->stop_decel_cps2 = (uint32_t)(((uint64_t)profile->stop_decel_rpm_s * profile->counts_per_rev) / 60ULL);
    if (profile->target_velocity_cps == 0 || profile->target_velocity_cps > profile->max_velocity_cps ||
        profile->accel_cps2 == 0 || profile->decel_cps2 == 0 || profile->stop_decel_cps2 == 0) {
        fprintf(stderr, "axis %s resolved PV parameters are invalid\n", axis_prefix);
        return -1;
    }
    return 0;
}

static int load_axis_profile_parameters(void)
{
    uint32_t configured_counts_per_rev;
    uint32_t configured_axis_count;
    if (!uservo_axis_d_topology) {
        counts_per_rev = LEGACY_COUNTS_PER_REV;
        return 0;
    }
    if (uservo_dual_combined_topology) {
        if (env_u32_required("MCTIVITY_USERVO_AXIS_COUNT", &configured_axis_count) < 0 ||
            configured_axis_count != AXIS_COUNT) {
            fprintf(stderr, "axis-de-uservo-combined requires exactly %d Uservo axes\n", AXIS_COUNT);
            return -1;
        }
        if (load_uservo_pv_profile(&uservo_pv_profiles[AXIS_MCTIVITY], "D", 0) < 0 ||
            load_uservo_pv_profile(&uservo_pv_profiles[AXIS_FV3], "E", 0) < 0) {
            return -1;
        }
        if (env_u32_required("MCTIVITY_GEAR_FOLLOWING_ERROR_LIMIT_COUNTS", &gear_following_error_limit_counts) < 0 ||
            env_u32_required("MCTIVITY_GEAR_MAX_RATIO", &gear_max_ratio) < 0 ||
            gear_following_error_limit_counts == 0 || gear_max_ratio == 0 ||
            gear_max_ratio > MCTIVITY_GEAR_DEFAULT_MAX_RATIO) {
            fprintf(stderr, "axis-de-uservo-combined safety parameters are invalid\n");
            return -1;
        }
        if (env_u32_required("MCTIVITY_AXIS_D_MAX_SPEED_RPM", &configured_counts_per_rev) < 0) {
            return -1;
        }
        gear_max_velocity_cps[AXIS_MCTIVITY] =
            (uint32_t)(((uint64_t)configured_counts_per_rev * uservo_pv_profiles[AXIS_MCTIVITY].counts_per_rev) / 60ULL);
        if (env_u32_required("MCTIVITY_AXIS_E_MAX_SPEED_RPM", &configured_counts_per_rev) < 0) {
            return -1;
        }
        gear_max_velocity_cps[AXIS_FV3] =
            (uint32_t)(((uint64_t)configured_counts_per_rev * uservo_pv_profiles[AXIS_FV3].counts_per_rev) / 60ULL);
        counts_per_rev = uservo_pv_profiles[AXIS_MCTIVITY].counts_per_rev;
        return 0;
    }
    if (uservo_dual_gear_topology) {
        if (env_u32_required("MCTIVITY_USERVO_AXIS_COUNT", &configured_axis_count) < 0 ||
            configured_axis_count != AXIS_COUNT) {
            fprintf(stderr, "axis-de-uservo-gear requires exactly %d Uservo axes\n", AXIS_COUNT);
            return -1;
        }
        if (env_u32_required("MCTIVITY_AXIS_D_COUNTS_PER_REV", &configured_counts_per_rev) < 0) {
            return -1;
        }
        uservo_pv_profiles[AXIS_MCTIVITY].counts_per_rev = configured_counts_per_rev;
        if (env_u32_required("MCTIVITY_AXIS_E_COUNTS_PER_REV", &configured_counts_per_rev) < 0) {
            return -1;
        }
        uservo_pv_profiles[AXIS_FV3].counts_per_rev = configured_counts_per_rev;
        if (env_u32_required("MCTIVITY_GEAR_FOLLOWING_ERROR_LIMIT_COUNTS", &gear_following_error_limit_counts) < 0 ||
            env_u32_required("MCTIVITY_GEAR_MAX_RATIO", &gear_max_ratio) < 0 ||
            gear_following_error_limit_counts == 0 || gear_max_ratio == 0 || gear_max_ratio > MCTIVITY_GEAR_DEFAULT_MAX_RATIO) {
            fprintf(stderr, "axis-de-uservo-gear safety parameters are invalid\n");
            return -1;
        }
        if (env_u32_required("MCTIVITY_AXIS_D_MAX_SPEED_RPM", &configured_counts_per_rev) < 0) {
            return -1;
        }
        gear_max_velocity_cps[AXIS_MCTIVITY] = (uint32_t)(((uint64_t)configured_counts_per_rev * uservo_pv_profiles[AXIS_MCTIVITY].counts_per_rev) / 60ULL);
        if (env_u32_required("MCTIVITY_AXIS_E_MAX_SPEED_RPM", &configured_counts_per_rev) < 0) {
            return -1;
        }
        gear_max_velocity_cps[AXIS_FV3] = (uint32_t)(((uint64_t)configured_counts_per_rev * uservo_pv_profiles[AXIS_FV3].counts_per_rev) / 60ULL);
        counts_per_rev = uservo_pv_profiles[AXIS_MCTIVITY].counts_per_rev;
        return 0;
    }
    if (uservo_dual_pv_topology) {
        if (env_u32_required("MCTIVITY_USERVO_AXIS_COUNT", &configured_axis_count) < 0 ||
            configured_axis_count != AXIS_COUNT) {
            fprintf(stderr, "axis-de-uservo-pv requires exactly %d Uservo axes\n", AXIS_COUNT);
            return -1;
        }
        if (load_uservo_pv_profile(&uservo_pv_profiles[AXIS_MCTIVITY], "D", 0) < 0 ||
            load_uservo_pv_profile(&uservo_pv_profiles[AXIS_FV3], "E", 0) < 0) {
            return -1;
        }
        counts_per_rev = uservo_pv_profiles[AXIS_MCTIVITY].counts_per_rev;
        return 0;
    }
    if (!uservo_pv_topology) {
        if (env_u32_required("MCTIVITY_AXIS_COUNTS_PER_REV", &configured_counts_per_rev) < 0) {
            return -1;
        }
        counts_per_rev = configured_counts_per_rev;
        return 0;
    }
    if (load_uservo_pv_profile(&uservo_pv_profiles[AXIS_MCTIVITY], "D", 1) < 0) {
        return -1;
    }
    counts_per_rev = uservo_pv_profiles[AXIS_MCTIVITY].counts_per_rev;
    return 0;
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
    const uservo_pv_profile_t *pv = uservo_pv_profile_for_axis(axis);
    const char *topology = uservo_dual_combined_topology
        ? "axis-de-uservo-combined"
        : (uservo_dual_gear_topology
        ? "axis-de-uservo-gear"
        : (uservo_dual_pv_topology
            ? "axis-de-uservo-pv"
            : (uservo_pv_topology ? "axis-d-uservo-pv" : (uservo_axis_d_topology ? "axis-d-uservo" : "legacy-dual"))));
    const char *logical_axis = uservo_dual_topology ? (axis == AXIS_FV3 ? "E" : "D") :
        (uservo_axis_d_topology && axis == AXIS_MCTIVITY ? "D" : (axis == AXIS_FV3 ? "B" : "A"));
    int64_t axis_counts_per_rev = pv ? pv->counts_per_rev
        : (uservo_dual_gear_topology ? (int64_t)uservo_pv_profiles[axis].counts_per_rev : counts_per_rev);
    const axis_runtime_t *gear_slave = &axes[gear_group_slave_axis];
    char out[2600];
    int n = snprintf(
        out, sizeof(out),
        "{\"ok\":true,\"status\":{\"device\":\"%s\",\"logical_axis\":\"%s\",\"topology\":\"%s\","
        "\"counts_per_rev\":%lld,\"commissioning_inhibit\":%s,\"enabled\":%s,\"servo_request\":%s,"
        "\"moving\":%s,\"gear_running\":%s,\"fault\":%s,\"settle_cycles\":%u,\"al_state\":%u,\"operational\":%u,"
        "\"wc\":%u,\"wc_complete\":%s,\"cw\":%u,\"sw\":%u,\"err\":%u,\"mode\":%d,\"commanded_mode\":%d,"
        "\"control_mode\":\"%s\",\"pos_raw\":%d,\"pos\":%d,\"velocity_actual_cps\":%d,\"target_raw\":%d,\"target\":%d,"
        "\"following_error\":%d,\"soft_zero_raw\":%d,\"jog_velocity_cps\":%d,\"torque_cmd\":%d,"
        "\"torque_feedback\":%d,\"homed\":%s,\"cycles\":%u,"
        "\"rt_memory_locked\":%s,\"rt_scheduler_policy\":%d,\"rt_scheduler_priority\":%d,"
        "\"rt_deadline_miss_count\":%llu,\"rt_skipped_periods\":%llu,"
        "\"rt_consecutive_schedule_misses\":%u,\"rt_schedule_timing_fault\":%s,"
        "\"rt_last_wake_lateness_ns\":%llu,\"rt_max_wake_lateness_ns\":%llu,"
        "\"rt_last_cycle_runtime_ns\":%llu,\"rt_max_cycle_runtime_ns\":%llu,"
        "\"wc_change_count\":%llu,\"wc_incomplete_cycles\":%llu,"
        "\"timing_guard_armed\":%s,\"communication_timing_fault\":%s,"
        "\"sync_group_session_active\":%s,\"sync_group_motion_active\":%s,"
        "\"sync_group_both_enabled_once\":%s,\"sync_group_safety_latched\":%s,"
        "\"gear_group_session_active\":%s,\"gear_group_safety_latched\":%s,"
        "\"gear_master\":\"%s\",\"gear_slave\":\"%s\",\"gear_direction\":%d,"
        "\"gear_master_ratio\":%d,\"gear_slave_ratio\":%d,\"gear_position_error\":%lld,"
        "\"gear_position_error_alarm\":%s,\"gear_error_over_limit_cycles\":%u,"
        "\"gear_last_trip_reason\":\"%s\",\"gear_last_trip_position_error\":%lld,"
        "\"gear_last_trip_step_counts\":%lld,\"gear_last_trip_elapsed_cycles\":%u,"
        "\"gear_last_trip_target_raw\":%d,\"gear_last_trip_actual_raw\":%d,\"gear_last_trip_master_raw\":%d,"
        "\"last_command\":\"%s\",\"message\":\"%s\"}}\n",
        axis_name(axis), logical_axis, topology,
        (long long)axis_counts_per_rev, commissioning_inhibit ? "true" : "false",
        s->enabled ? "true" : "false", s->servo_request ? "true" : "false",
        s->moving ? "true" : "false", ax->gear_running ? "true" : "false", s->fault ? "true" : "false",
        s->enable_settle_cycles, s->al_state,
        s->operational, s->wc, s->wc_complete ? "true" : "false", s->cw, s->sw, s->err, s->mode_display,
        ax->commanded_mode,
        s->control_mode, s->pos_raw, s->pos_user, s->velocity_actual_cps, s->target_raw, s->target_user, s->following_error,
        s->soft_zero_raw, s->jog_velocity_cps, s->torque_cmd, s->torque_feedback, s->homed ? "true" : "false",
        s->cycles,
        realtime_status.memory_locked ? "true" : "false",
        realtime_status.scheduler_policy,
        realtime_status.scheduler_priority,
        (unsigned long long)realtime_status.deadline_miss_count,
        (unsigned long long)realtime_status.skipped_periods,
        realtime_status.schedule_guard.consecutive_misses,
        realtime_status.schedule_guard.fault_latched ? "true" : "false",
        (unsigned long long)realtime_status.last_wake_lateness_ns,
        (unsigned long long)realtime_status.max_wake_lateness_ns,
        (unsigned long long)realtime_status.last_cycle_runtime_ns,
        (unsigned long long)realtime_status.max_cycle_runtime_ns,
        (unsigned long long)realtime_status.wc_change_count,
        (unsigned long long)realtime_status.wc_incomplete_cycles,
        realtime_status.timing_guard_armed ? "true" : "false",
        realtime_status.communication_timing_fault ? "true" : "false",
        sync_group_session_active ? "true" : "false",
        sync_group_motion_active ? "true" : "false",
        sync_group_both_enabled_once ? "true" : "false",
        sync_group_safety_latched ? "true" : "false",
        gear_group_session_active ? "true" : "false",
        gear_group_safety_latched ? "true" : "false",
        axis_name(gear_group_master_axis), axis_name(gear_group_slave_axis),
        gear_slave->gear_direction,
        gear_slave->gear_master_ratio, gear_slave->gear_slave_ratio,
        (long long)gear_slave->gear_position_error,
        gear_slave->gear_position_error > (int64_t)gear_following_error_limit_counts ? "true" : "false",
        gear_slave->gear_error_over_limit_cycles,
        gear_last_trip_reason,
        (long long)gear_last_trip_position_error,
        (long long)gear_last_trip_step_counts,
        gear_last_trip_elapsed_cycles,
        gear_last_trip_target_raw,
        gear_last_trip_actual_raw,
        gear_last_trip_master_raw,
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

static void send_sync_status_fd(int fd, const char *action)
{
    char out[768];
    int n = snprintf(
        out,
        sizeof(out),
        "{\"ok\":true,\"group\":{\"topology\":\"axis-de-uservo-pv\",\"action\":\"%s\","
        "\"atomic\":true,\"session_active\":%s,\"motion_active\":%s,\"both_enabled_once\":%s,"
        "\"safety_latched\":%s,"
        "\"devices\":[\"mctivity\",\"mctivity_e\"],"
        "\"d\":{\"enabled\":%s,\"servo_request\":%s,\"moving\":%s,\"velocity\":%d},"
        "\"e\":{\"enabled\":%s,\"servo_request\":%s,\"moving\":%s,\"velocity\":%d}}}\n",
        action,
        sync_group_session_active ? "true" : "false",
        sync_group_motion_active ? "true" : "false",
        sync_group_both_enabled_once ? "true" : "false",
        sync_group_safety_latched ? "true" : "false",
        axes[AXIS_MCTIVITY].st.enabled ? "true" : "false",
        axes[AXIS_MCTIVITY].st.servo_request ? "true" : "false",
        axes[AXIS_MCTIVITY].st.moving ? "true" : "false",
        axes[AXIS_MCTIVITY].st.jog_velocity_cps,
        axes[AXIS_FV3].st.enabled ? "true" : "false",
        axes[AXIS_FV3].st.servo_request ? "true" : "false",
        axes[AXIS_FV3].st.moving ? "true" : "false",
        axes[AXIS_FV3].st.jog_velocity_cps);
    if (n > 0) {
        size_t send_len = (size_t)n < sizeof(out) ? (size_t)n : sizeof(out) - 1;
        (void)send(fd, out, send_len, MSG_NOSIGNAL);
    }
}

static void clear_axis_velocity_command(axis_runtime_t *ax, int disable)
{
    if (disable) {
        ax->st.servo_request = 0;
    }
    ax->st.enable_settle_cycles = 0;
    clear_motion(ax);
    ax->stop_velocity_cps = 0;
    ax->target_velocity_cps = 0;
    ax->st.jog_velocity_cps = 0;
    ax->velocity_remainder = 0;
    ax->pp_pulse_cycles = 0;
    ax->fv3_halt_cycles = 0;
    ax->gear_running = 0;
    ax->gear_has_last_master_pos = 0;
    ax->gear_position_error = 0;
    ax->gear_math.initialized = 0;
    ax->st.moving = 0;
    ax->st.target_raw = ax->st.pos_raw;
    ax->st.target_user = ax->st.pos_user;
}

static int dual_axes_fault_free_and_online(void);

static void gear_group_clear_runtime(int disable)
{
    for (int axis = 0; axis < AXIS_COUNT; axis++) {
        axis_runtime_t *runtime = &axes[axis];
        if (disable) {
            runtime->st.servo_request = 0;
        }
        runtime->st.cw = 0;
        runtime->commanded_mode = 0;
        runtime->gear_running = 0;
        runtime->gear_has_last_master_pos = 0;
        runtime->gear_last_master_cycle = 0;
        runtime->gear_position_error = 0;
        runtime->gear_error_over_limit_cycles = 0;
        runtime->gear_last_step_counts = 0;
        runtime->gear_last_elapsed_cycles = 0;
        runtime->gear_math.initialized = 0;
        clear_motion(runtime);
        runtime->stop_velocity_cps = 0;
        runtime->st.jog_velocity_cps = 0;
        runtime->st.moving = 0;
        runtime->st.target_raw = runtime->st.pos_raw;
        runtime->st.target_user = runtime->st.pos_user;
    }
    gear_group_session_active = 0;
}

static void gear_group_trip(const char *reason)
{
    const axis_runtime_t *slave = &axes[gear_group_slave_axis];
    snprintf(gear_last_trip_reason, sizeof(gear_last_trip_reason), "%s", reason ? reason : "unknown");
    gear_last_trip_position_error = slave->gear_position_error;
    gear_last_trip_step_counts = slave->gear_last_step_counts;
    gear_last_trip_elapsed_cycles = slave->gear_last_elapsed_cycles;
    gear_last_trip_target_raw = slave->st.target_raw;
    gear_last_trip_actual_raw = slave->st.pos_raw;
    gear_last_trip_master_raw = axes[gear_group_master_axis].st.pos_raw;
    gear_group_clear_runtime(1);
    gear_group_safety_latched = 1;
    for (int axis = 0; axis < AXIS_COUNT; axis++) {
        snprintf(axes[axis].st.message, sizeof(axes[axis].st.message), "D/E electronic gear safety stop: %s", reason);
    }
}

static int gear_group_ready(int slave_axis, const char **reason)
{
    int master_axis = axes[slave_axis].gear_master_axis;
    if (slave_axis == master_axis || master_axis < 0 || master_axis >= AXIS_COUNT) {
        *reason = "gear master axis cannot be self";
        return 0;
    }
    if (gear_group_safety_latched) {
        *reason = "gear_group_safety_latched; issue gear_stop after both axes are disabled";
        return 0;
    }
    if (realtime_status.communication_timing_fault || !realtime_status.timing_guard_armed) {
        *reason = realtime_status.communication_timing_fault
            ? "communication_timing_fault" : "timing_guard_not_armed";
        return 0;
    }
    if (!dual_axes_fault_free_and_online()) {
        *reason = "gear axes are not fault-free and online";
        return 0;
    }
    for (int axis = 0; axis < AXIS_COUNT; axis++) {
        const status_t *s = &axes[axis].st;
        if (!s->servo_request || !s->enabled || s->enable_settle_cycles != 0) {
            *reason = "gear axes are not both enabled and settled";
            return 0;
        }
    }
    return 1;
}

static int gear_group_start(int slave_axis, const char **reason)
{
    axis_runtime_t *slave = &axes[slave_axis];
    axis_runtime_t *master = &axes[slave->gear_master_axis];
    if (!gear_group_ready(slave_axis, reason)) {
        return 0;
    }
    if (!mctivity_gear_start(&slave->gear_math, master->st.pos_raw, slave->st.pos_raw)) {
        *reason = "gear_math_initialization_failed";
        return 0;
    }
    gear_group_master_axis = slave->gear_master_axis;
    gear_group_slave_axis = slave_axis;
    gear_group_session_active = 1;
    slave->gear_running = 1;
    slave->gear_has_last_master_pos = 1;
    slave->gear_last_master_cycle = master->st.cycles;
    slave->gear_position_error = 0;
    slave->gear_error_over_limit_cycles = 0;
    set_control_mode(slave, "gear_cam");
    slave->commanded_mode = mode_code_for_name("position");
    snprintf(master->st.message, sizeof(master->st.message), "%s is electronic-gear master", axis_label(master->gear_master_axis));
    snprintf(slave->st.message, sizeof(slave->st.message), "%s electronic gear engaged: master=%s ratio=%d:%d direction=%d",
             axis_label(slave_axis), axis_name(slave->gear_master_axis), slave->gear_slave_ratio,
             slave->gear_master_ratio, slave->gear_direction);
    return 1;
}

static int gear_group_stop(int slave_axis, const char **reason)
{
    axis_runtime_t *slave = &axes[slave_axis];
    int master_axis = gear_group_session_active ? gear_group_master_axis : slave->gear_master_axis;
    if (master_axis < 0 || master_axis >= AXIS_COUNT || master_axis == slave_axis) {
        *reason = "gear master axis cannot be self";
        return 0;
    }
    clear_motion(&axes[master_axis]);
    axes[master_axis].stop_velocity_cps = 0;
    axes[master_axis].st.jog_velocity_cps = 0;
    axes[master_axis].st.target_raw = axes[master_axis].st.pos_raw;
    axes[master_axis].st.target_user = axes[master_axis].st.pos_user;
    gear_group_clear_runtime(0);
    if (gear_group_safety_latched &&
        (axes[AXIS_MCTIVITY].st.servo_request || axes[AXIS_FV3].st.servo_request ||
         axes[AXIS_MCTIVITY].st.enabled || axes[AXIS_FV3].st.enabled ||
         !dual_axes_fault_free_and_online())) {
        *reason = "gear safety latch requires both axes disabled and healthy before clearing";
        return 0;
    }
    gear_group_safety_latched = 0;
    snprintf(axes[slave_axis].st.message, sizeof(axes[slave_axis].st.message), "%s electronic gear disengaged", axis_label(slave_axis));
    return 1;
}

static int dual_axes_fault_free_and_online(void)
{
    for (int axis = 0; axis < AXIS_COUNT; axis++) {
        const status_t *s = &axes[axis].st;
        if (s->fault || !s->operational || !s->wc_complete) {
            return 0;
        }
    }
    return 1;
}

static void handle_sync_velocity_command(int fd, const char *line, const char *cmd)
{
    const uservo_pv_profile_t *pv_d = &uservo_pv_profiles[AXIS_MCTIVITY];
    const uservo_pv_profile_t *pv_e = &uservo_pv_profiles[AXIS_FV3];
    if (!uservo_dual_pv_topology) {
        send_error_fd(fd, "unsupported_for_topology");
        return;
    }
    if (strcmp(cmd, "sync_stop") == 0) {
        uint32_t requested_decel;
        if (find_json_key(line, "deceleration_rpm_s")) {
            if (!find_u32(line, "deceleration_rpm_s", &requested_decel) ||
                requested_decel != pv_d->stop_decel_rpm_s || requested_decel != pv_e->stop_decel_rpm_s) {
                send_error_fd(fd, "sync stop deceleration must match both axis profiles");
                return;
            }
        }
        for (int axis = 0; axis < AXIS_COUNT; axis++) {
            clear_axis_velocity_command(&axes[axis], 0);
            strncpy(axes[axis].st.last_command, "sync_stop", sizeof(axes[axis].st.last_command) - 1);
            snprintf(axes[axis].st.message, sizeof(axes[axis].st.message), "%s synchronized PV stop requested", axis_label(axis));
        }
        sync_group_motion_active = 0;
        send_sync_status_fd(fd, "stop");
        return;
    }
    if (strcmp(cmd, "sync_disable") == 0) {
        for (int axis = 0; axis < AXIS_COUNT; axis++) {
            clear_axis_velocity_command(&axes[axis], 1);
            strncpy(axes[axis].st.last_command, "sync_disable", sizeof(axes[axis].st.last_command) - 1);
            snprintf(axes[axis].st.message, sizeof(axes[axis].st.message), "%s synchronized disable requested", axis_label(axis));
        }
        sync_group_session_active = 0;
        sync_group_motion_active = 0;
        sync_group_both_enabled_once = 0;
        sync_group_safety_latched = 0;
        send_sync_status_fd(fd, "disable");
        return;
    }
    if (commissioning_inhibit) {
        send_error_fd(fd, "commissioning_inhibit");
        return;
    }
    if (realtime_status.communication_timing_fault) {
        send_error_fd(fd, "communication_timing_fault");
        return;
    }
    if (sync_group_safety_latched) {
        send_error_fd(fd, "sync_group_safety_latched; issue sync_disable before retry");
        return;
    }
    if (!realtime_status.timing_guard_armed) {
        send_error_fd(fd, "timing_guard_not_armed");
        return;
    }
    if (!dual_axes_fault_free_and_online()) {
        send_error_fd(fd, "sync_axes_not_fault_free_and_online");
        return;
    }
    if (strcmp(cmd, "sync_enable") == 0) {
        for (int axis = 0; axis < AXIS_COUNT; axis++) {
            axis_runtime_t *ax = &axes[axis];
            clear_axis_velocity_command(ax, 0);
            ax->st.servo_request = 1;
            ax->st.enable_settle_cycles = ENABLE_SETTLE_CYCLES;
            strncpy(ax->st.last_command, "sync_enable", sizeof(ax->st.last_command) - 1);
            snprintf(ax->st.message, sizeof(ax->st.message), "%s synchronized enable requested", axis_label(axis));
        }
        sync_group_session_active = 1;
        sync_group_motion_active = 0;
        sync_group_both_enabled_once = 0;
        send_sync_status_fd(fd, "enable");
        return;
    }
    if (strcmp(cmd, "sync_jog_velocity") == 0) {
        int32_t velocity;
        uint32_t requested_accel;
        uint32_t group_max_velocity = pv_d->max_velocity_cps < pv_e->max_velocity_cps
            ? pv_d->max_velocity_cps : pv_e->max_velocity_cps;
        if (!find_i32(line, "velocity", &velocity) || velocity == 0) {
            send_error_fd(fd, "sync_jog_velocity requires nonzero velocity");
            return;
        }
        if ((int64_t)velocity > (int64_t)group_max_velocity ||
            (int64_t)velocity < -(int64_t)group_max_velocity) {
            send_error_fd(fd, "sync velocity exceeds shared profile maximum");
            return;
        }
        if (find_json_key(line, "acceleration_rpm_s")) {
            if (!find_u32(line, "acceleration_rpm_s", &requested_accel) ||
                requested_accel != pv_d->accel_rpm_s || requested_accel != pv_e->accel_rpm_s) {
                send_error_fd(fd, "sync acceleration must match both axis profiles");
                return;
            }
        }
        for (int axis = 0; axis < AXIS_COUNT; axis++) {
            if (!axes[axis].st.servo_request || !ready_for_motion(&axes[axis])) {
                send_error_fd(fd, "sync axes are not both enabled and settled");
                return;
            }
        }
        for (int axis = 0; axis < AXIS_COUNT; axis++) {
            axis_runtime_t *ax = &axes[axis];
            clear_motion(ax);
            ax->stop_velocity_cps = 0;
            ax->st.jog_velocity_cps = velocity;
            ax->target_velocity_cps = velocity;
            ax->st.moving = 1;
            ax->velocity_remainder = 0;
            ax->commanded_mode = 3;
            set_control_mode(ax, "velocity");
            strncpy(ax->st.last_command, "sync_jog_velocity", sizeof(ax->st.last_command) - 1);
            snprintf(ax->st.message, sizeof(ax->st.message), "%s synchronized PV velocity %d counts/s", axis_label(axis), velocity);
        }
        sync_group_session_active = 1;
        sync_group_motion_active = 1;
        sync_group_both_enabled_once = 1;
        send_sync_status_fd(fd, "jog_velocity");
        return;
    }
    send_error_fd(fd, "unsupported_sync_command");
}

static void handle_command(int fd, const char *line)
{
    int axis;
    char cmd[64];
    if (!command_from_line(line, cmd, sizeof(cmd))) {
        send_error_fd(fd, "missing command");
        return;
    }
    if (strcmp(cmd, "sync_enable") == 0 || strcmp(cmd, "sync_disable") == 0 ||
        strcmp(cmd, "sync_jog_velocity") == 0 || strcmp(cmd, "sync_stop") == 0) {
        handle_sync_velocity_command(fd, line, cmd);
        return;
    }
    axis = axis_from_line(line);
    if (axis < 0 || axis >= AXIS_COUNT) {
        send_error_fd(fd, "unsupported_device");
        return;
    }
    axis_runtime_t *ax = &axes[axis];
    status_t *s = &ax->st;

    if (uservo_dual_pv_topology && (sync_group_session_active || sync_group_safety_latched) &&
        strcmp(cmd, "status") != 0) {
        send_error_fd(fd, "sync_group_active; issue sync_disable before single-axis control");
        return;
    }

    if (uservo_axis_d_topology && commissioning_inhibit &&
        strcmp(cmd, "status") != 0 && strcmp(cmd, "disable") != 0 && strcmp(cmd, "stop") != 0 &&
        strcmp(cmd, "fault_reset") != 0 && strcmp(cmd, "reset_fault") != 0 && strcmp(cmd, "gear_stop") != 0) {
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

    if (uservo_dual_gear_topology && gear_group_safety_latched &&
        strcmp(cmd, "status") != 0 && strcmp(cmd, "gear_stop") != 0 && strcmp(cmd, "disable") != 0 &&
        strcmp(cmd, "stop") != 0) {
        send_error_fd(fd, "gear_group_safety_latched; issue gear_stop after both axes are disabled");
        return;
    }

    if (uservo_dual_gear_topology && gear_group_session_active && axis == gear_group_slave_axis &&
        strcmp(cmd, "status") != 0 && strcmp(cmd, "gear_stop") != 0 && strcmp(cmd, "disable") != 0 &&
        strcmp(cmd, "stop") != 0) {
        send_error_fd(fd, "gear_slave_control_locked; issue gear_stop before changing slave control");
        return;
    }

    if ((uservo_pv_topology &&
         (strcmp(cmd, "home") == 0 || strcmp(cmd, "gear_config") == 0 || strcmp(cmd, "gear_start") == 0 ||
          strcmp(cmd, "gear_stop") == 0 || strcmp(cmd, "move_abs") == 0 || strcmp(cmd, "move_rel") == 0 ||
          strcmp(cmd, "move_curve_rel") == 0 || strcmp(cmd, "torque_cmd") == 0)) ||
        (uservo_axis_d_topology && !uservo_pv_topology && !uservo_dual_gear_topology &&
         (strcmp(cmd, "home") == 0 || strcmp(cmd, "gear_config") == 0 || strcmp(cmd, "gear_start") == 0 ||
          strcmp(cmd, "gear_stop") == 0 || strcmp(cmd, "jog_velocity") == 0 || strcmp(cmd, "torque_cmd") == 0))) {
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
        if (axis_uses_native_pv_control(axis, s->control_mode)) {
            const uservo_pv_profile_t *pv = uservo_pv_profile_for_axis(axis);
            clear_motion(ax);
            s->enable_settle_cycles = 0;
            s->jog_velocity_cps = 0;
            ax->stop_velocity_cps = 0;
            ax->target_velocity_cps = 0;
            ax->velocity_remainder = 0;
            ax->gear_running = 0;
            ax->gear_has_last_master_pos = 0;
            s->target_raw = s->pos_raw;
            s->target_user = s->pos_user;
            strncpy(s->last_command, "stop", sizeof(s->last_command) - 1);
            snprintf(
                s->message,
                sizeof(s->message),
                "%s PV stop requested; target velocity cleared (profile 0x6084=%u rpm/s, %u cnt/s^2)",
                axis_label(axis),
                pv->stop_decel_rpm_s,
                pv->stop_decel_cps2);
            send_status_fd(fd, axis);
            return;
        }
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
        if (axis_is_fv3_hardware(axis) && ax->fv3_feedback_velocity_cps != 0) {
            seed_velocity_cps = ax->fv3_feedback_velocity_cps;
        }
        if (axis_is_fv3_hardware(axis)) {
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
            if (axis_is_fv3_hardware(axis)) {
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
        if (uservo_pv_topology && strcmp(mode, "velocity") != 0) {
            send_error_fd(fd, "unsupported_for_axis_d_uservo_pv");
            return;
        }
        if (uservo_axis_d_topology && !uservo_pv_topology && !uservo_dual_gear_topology &&
            strcmp(mode, "position") != 0 && strcmp(mode, "incremental") != 0 && strcmp(mode, "jog") != 0 &&
            strcmp(mode, "point") != 0) {
            send_error_fd(fd, "unsupported_for_axis_d_uservo");
            return;
        }
        if (uservo_dual_combined_topology &&
            mctivity_gear_mode_change_requires_stop(
                gear_group_session_active,
                axis,
                gear_group_master_axis,
                strcmp(mode, "gear_cam") == 0,
                s->moving,
                s->jog_velocity_cps != 0,
                ax->stop_velocity_cps != 0)) {
            send_error_fd(fd, "combined_mode_change_requires_stop");
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
        ax->commanded_mode = axis_mode_code(axis, mode);
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
        int32_t direction = 1;
        char master_name[24] = {0};
        char direction_name[24] = {0};
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
        if (!find_i32(line, "direction", &direction) && find_str(line, "direction", direction_name, sizeof(direction_name))) {
            if (strcmp(direction_name, "reverse") == 0 || strcmp(direction_name, "opposite") == 0 || strcmp(direction_name, "-1") == 0) {
                direction = -1;
            } else if (strcmp(direction_name, "same") != 0 && strcmp(direction_name, "forward") != 0 && strcmp(direction_name, "+1") != 0) {
                send_error_fd(fd, "gear direction must be same/forward or reverse/opposite");
                return;
            }
        }
        if (uservo_dual_gear_topology &&
            strcmp(master_name, "mctivity") != 0 && strcmp(master_name, "D") != 0 && strcmp(master_name, "d") != 0 &&
            strcmp(master_name, "mctivity_e") != 0 && strcmp(master_name, "E") != 0 && strcmp(master_name, "e") != 0) {
            send_error_fd(fd, "gear master must be D or E");
            return;
        }
        if (master_ratio < 1) {
            master_ratio = 1;
        }
        if (slave_ratio < 1) {
            slave_ratio = 1;
        }
        if (uservo_dual_gear_topology && (master_ratio > gear_max_ratio || slave_ratio > gear_max_ratio)) {
            send_error_fd(fd, "gear ratio exceeds profile maximum");
            return;
        }
        if (direction != 1 && direction != -1) {
            send_error_fd(fd, "gear direction must be 1 or -1");
            return;
        }
        master_axis = axis_from_name(master_name, ax->gear_master_axis);
        if (master_axis == axis) {
            send_error_fd(fd, "gear master axis cannot be self");
            return;
        }
        ax->gear_master_axis = master_axis;
        ax->gear_master_ratio = (int32_t)master_ratio;
        ax->gear_slave_ratio = (int32_t)slave_ratio;
        ax->gear_direction = direction;
        if (uservo_dual_gear_topology && !mctivity_gear_configure(
                &ax->gear_math, direction, (int32_t)master_ratio, (int32_t)slave_ratio)) {
            send_error_fd(fd, "invalid electronic gear configuration");
            return;
        }
        set_control_mode(ax, "gear_cam");
        ax->commanded_mode = mode_code_for_name("position");
        strncpy(s->last_command, "gear_config", sizeof(s->last_command) - 1);
        snprintf(
            s->message,
            sizeof(s->message),
            "%s gear config: master=%s ratio=%u:%u direction=%d",
            axis_label(axis),
            axis_name(master_axis),
            slave_ratio,
            master_ratio, direction);
        send_status_fd(fd, axis);
        return;
    }

    if (strcmp(cmd, "gear_start") == 0) {
        if (uservo_dual_gear_topology) {
            const char *reason = NULL;
            if (!gear_group_start(axis, &reason)) {
                send_error_fd(fd, reason ? reason : "gear start preconditions failed");
                return;
            }
            strncpy(s->last_command, "gear_start", sizeof(s->last_command) - 1);
            send_status_fd(fd, axis);
            return;
        }
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
        if (uservo_dual_gear_topology) {
            const char *reason = NULL;
            if (!gear_group_stop(axis, &reason)) {
                send_error_fd(fd, reason ? reason : "gear stop failed");
                return;
            }
            strncpy(s->last_command, "gear_stop", sizeof(s->last_command) - 1);
            send_status_fd(fd, axis);
            return;
        }
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
        if (axis_is_fv3_hardware(axis)) {
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
        set_control_mode(ax, axis_is_fv3_hardware(axis) ? "position" : "jog");
        ax->commanded_mode = mode_code_for_name(axis_is_fv3_hardware(axis) ? "position" : "jog");
        ax->gear_running = 0;
        ax->gear_has_last_master_pos = 0;
        if (axis_is_fv3_hardware(axis)) {
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
        const uservo_pv_profile_t *pv = uservo_pv_profile_for_axis(axis);
        if (!ready_for_motion(ax)) {
            send_error_fd(fd, "servo is not ready for velocity jog; enable and wait for settle first");
            return;
        }
        if (!find_i32(line, "velocity", &velocity)) {
            velocity = pv ? (int32_t)pv->target_velocity_cps : DEFAULT_JOG_VELOCITY;
        }
        if (pv &&
            ((int64_t)velocity > (int64_t)pv->max_velocity_cps ||
             (int64_t)velocity < -(int64_t)pv->max_velocity_cps)) {
            send_error_fd(fd, "PV velocity exceeds profile maximum");
            return;
        }
        if (uservo_dual_combined_topology &&
            ((int64_t)velocity > (int64_t)gear_max_velocity_cps[axis] ||
             (int64_t)velocity < -(int64_t)gear_max_velocity_cps[axis])) {
            send_error_fd(fd, "CSP software velocity exceeds profile maximum");
            return;
        }
        set_control_mode(ax, "velocity");
        ax->commanded_mode = axis_mode_code(axis, "velocity");
        ax->gear_running = 0;
        ax->gear_has_last_master_pos = 0;
        clear_motion(ax);
        ax->stop_velocity_cps = 0;
        s->jog_velocity_cps = velocity;
        ax->velocity_remainder = 0;
        ax->pp_pulse_cycles = 0;
        ax->fv3_halt_cycles = 0;
        strncpy(s->last_command, "jog_velocity", sizeof(s->last_command) - 1);
        snprintf(s->message, sizeof(s->message), "%s velocity jog %d counts/s using %s target", axis_label(axis), velocity,
                 pv ? "native PV" : "CSP increment");
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
    int native_pv_control = axis_uses_native_pv_control(axis, s->control_mode);
    int gear_tracking_active = 0;
    int pp_active = 0;
    int32_t fv3_pos_step = 0;
    int64_t fv3_pos_delta = 0;
    int32_t previous_target_raw = s->target_raw;
    if (!ax->have_last_cycle_target) {
        previous_target_raw = s->target_raw;
        ax->have_last_cycle_target = 1;
    }
    if (axis_is_fv3_hardware(axis)) {
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
    } else if (native_pv_control) {
        /* Native DS1-E4806N PV: the 0x60ff target is the command source;
         * position targets and CSP increment synthesis are not used. */
        s->target_raw = s->pos_raw;
        s->target_user = s->pos_user;
        ax->target_velocity_cps = (s->servo_request && s->enabled &&
                                   strcmp(s->control_mode, "velocity") == 0)
                                      ? s->jog_velocity_cps
                                      : 0;
    } else if (axis_is_uservo_gear(axis) && gear_group_session_active && axis == gear_group_slave_axis &&
               strcmp(s->control_mode, "gear_cam") == 0 && ax->gear_running && s->enabled) {
        axis_runtime_t *master_ax = &axes[gear_group_master_axis];
        int32_t next_target = s->target_raw;
        int64_t step;
        uint64_t max_step;
        uint32_t elapsed_cycles = master_ax->st.cycles - ax->gear_last_master_cycle;
        ax->gear_last_master_cycle = master_ax->st.cycles;
        ax->gear_last_elapsed_cycles = elapsed_cycles;
        if (!mctivity_gear_target(&ax->gear_math, master_ax->st.pos_raw, &next_target)) {
            gear_group_trip("position target overflow");
            gear_tracking_active = 0;
        } else {
            step = (int64_t)next_target - (int64_t)s->target_raw;
            ax->gear_last_step_counts = step;
            max_step = mctivity_gear_max_target_step_counts(gear_max_velocity_cps[axis], elapsed_cycles);
            if (step < 0 ? (uint64_t)(-step) > max_step : (uint64_t)step > max_step) {
                gear_group_trip("follower speed limit exceeded");
                gear_tracking_active = 0;
            } else {
                s->target_raw = next_target;
                s->target_user = s->target_raw - s->soft_zero_raw;
                ax->gear_position_error = mctivity_gear_abs_error(s->target_raw, s->pos_raw);
                s->following_error = clamp_i64_to_i32((int64_t)s->target_raw - (int64_t)s->pos_raw);
                if (ax->gear_position_error > (int64_t)gear_following_error_limit_counts) {
                    if (ax->gear_error_over_limit_cycles < UINT32_MAX) {
                        ax->gear_error_over_limit_cycles++;
                    }
                } else {
                    ax->gear_error_over_limit_cycles = 0;
                }
                /* Combined D/E reports following error but does not stop on it.
                 * The standalone CSP gear profile retains its immediate trip. */
                if (!uservo_dual_combined_topology &&
                    ax->gear_position_error > (int64_t)gear_following_error_limit_counts) {
                    gear_group_trip("follower position error exceeded limit");
                    gear_tracking_active = 0;
                } else {
                    gear_tracking_active = 1;
                }
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
    } else if (axis_is_fv3_hardware(axis) && ax->fv3_halt_cycles > 0 && s->enabled) {
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
    if (!native_pv_control) {
        ax->target_velocity_cps = clamp_i64_to_i32((int64_t)(s->target_raw - previous_target_raw) * 1000LL);
    }
    if (axis_is_fv3_hardware(axis) && s->servo_request && s->enabled) {
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
    s->moving = (native_pv_control ? ax->target_velocity_cps != 0 : ax->motion.moving) ||
                (!native_pv_control && s->jog_velocity_cps != 0) ||
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
    } else if (axis_is_fv3_hardware(axis) && ax->fv3_halt_cycles > 0 && s->servo_request && s->wc_complete) {
        s->cw = 0x010f;
    } else if (axis_is_fv3_hardware(axis) && ax->pp_pulse_cycles > 0 && s->servo_request && s->wc_complete) {
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
        snprintf(
            s->message,
            sizeof(s->message),
            "%s; restart required",
            realtime_status.schedule_guard.fault_latched
                ? "realtime schedule fault latched"
                : "communication timing fault latched");
    }
}

static void clear_axis_for_communication_fault(axis_runtime_t *axis)
{
    status_t *s = &axis->st;
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
    s->jog_velocity_cps = 0;
    s->moving = 0;
    s->cw = 0;
    s->target_raw = s->pos_raw;
    s->target_user = s->pos_user;
    snprintf(
        s->message,
        sizeof(s->message),
        "%s; restart required",
        realtime_status.schedule_guard.fault_latched
            ? "realtime schedule fault latched"
            : "communication timing fault latched");
}

static void update_dual_uservo_communication_guard(
    unsigned int wc,
    int wc_complete,
    unsigned int slaves_responding,
    int master_link_up)
{
    int healthy = wc_complete && slaves_responding == AXIS_COUNT && master_link_up &&
        axes[AXIS_MCTIVITY].st.operational && axes[AXIS_FV3].st.operational;
    int control_active = dual_control_active();
    if (realtime_status.have_previous_wc && realtime_status.previous_wc != wc) {
        realtime_status.wc_change_count++;
    }
    realtime_status.previous_wc = wc;
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
        /* A stopped, disabled machine can recover from a transient DC/WC loss
         * without a daemon restart.  Disarm the enable gate and require a full
         * healthy window again.  Once control is active, retain the original
         * fail-closed behavior and latch immediately for both axes. */
        realtime_status.timing_guard_armed = 0;
        if (mctivity_dual_comm_loss_latches(control_active)) {
            realtime_status.communication_timing_fault = 1;
        }
    }
    if (realtime_status.communication_timing_fault) {
        if (uservo_dual_gear_topology && gear_group_session_active) {
            gear_group_safety_latched = 1;
            gear_group_session_active = 0;
        }
        for (int axis = 0; axis < AXIS_COUNT; axis++) {
            clear_axis_for_communication_fault(&axes[axis]);
        }
    }
}

static void enforce_sync_group_interlock_before_output(void)
{
    const char *reason = NULL;
    if (!sync_group_session_active) {
        return;
    }
    if (!sync_group_both_enabled_once &&
        axes[AXIS_MCTIVITY].st.servo_request && axes[AXIS_MCTIVITY].st.enabled &&
        axes[AXIS_MCTIVITY].st.enable_settle_cycles == 0 &&
        axes[AXIS_FV3].st.servo_request && axes[AXIS_FV3].st.enabled &&
        axes[AXIS_FV3].st.enable_settle_cycles == 0) {
        sync_group_both_enabled_once = 1;
    }
    if (realtime_status.communication_timing_fault) {
        reason = "communication timing fault";
    } else if (!realtime_status.timing_guard_armed) {
        reason = "timing guard not armed";
    } else {
        for (int axis = 0; axis < AXIS_COUNT; axis++) {
            const status_t *s = &axes[axis].st;
            if (s->fault) {
                reason = axis == AXIS_MCTIVITY ? "Axis D fault" : "Axis E fault";
                break;
            }
            if (!s->operational || !s->wc_complete) {
                reason = axis == AXIS_MCTIVITY ? "Axis D communication unavailable" : "Axis E communication unavailable";
                break;
            }
            if (sync_group_both_enabled_once && (!s->servo_request || !s->enabled)) {
                reason = axis == AXIS_MCTIVITY ? "Axis D no longer ready" : "Axis E no longer ready";
                break;
            }
        }
    }
    if (!reason) {
        return;
    }
    for (int axis = 0; axis < AXIS_COUNT; axis++) {
        axis_runtime_t *runtime = &axes[axis];
        clear_axis_velocity_command(runtime, 1);
        runtime->st.cw = 0;
        runtime->commanded_mode = 0;
        runtime->fault_reset_cycles = 0;
        strncpy(runtime->st.last_command, "sync_group_interlock", sizeof(runtime->st.last_command) - 1);
        snprintf(
            runtime->st.message,
            sizeof(runtime->st.message),
            "group interlock: %s; both axes disabled",
            reason);
    }
    sync_group_session_active = 0;
    sync_group_motion_active = 0;
    sync_group_both_enabled_once = 0;
    sync_group_safety_latched = 1;
}

static void enforce_gear_group_interlock_before_output(void)
{
    const char *reason = NULL;
    const axis_runtime_t *slave;
    if (!uservo_dual_gear_topology || !gear_group_session_active) {
        return;
    }
    slave = &axes[gear_group_slave_axis];
    if (realtime_status.communication_timing_fault) {
        reason = "communication timing fault";
    } else if (!realtime_status.timing_guard_armed) {
        reason = "timing guard not armed";
    } else if (!dual_axes_fault_free_and_online()) {
        reason = "gear axis fault or communication unavailable";
    } else if (!axes[gear_group_master_axis].st.servo_request || !axes[gear_group_master_axis].st.enabled ||
               !slave->st.servo_request || !slave->st.enabled || slave->st.enable_settle_cycles != 0) {
        reason = "gear axis no longer enabled and settled";
    } else if (!uservo_dual_combined_topology &&
               slave->gear_position_error > (int64_t)gear_following_error_limit_counts) {
        reason = "follower position error exceeded limit";
    }
    if (reason) {
        gear_group_trip(reason);
    }
}

static void build_uservo_dual_pv_domain_regs(void)
{
    static const uint16_t indices[8] = {0x6040, 0x6060, 0x60ff, 0x60fe, 0x6041, 0x6061, 0x606c, 0x60fd};
    static const uint8_t subindices[8] = {0, 0, 0, 1, 0, 0, 0, 0};
    for (int axis = 0; axis < AXIS_COUNT; axis++) {
        uservo_pv_offsets_t *off = &uservo_dual_pv_offsets[axis];
        unsigned int *offsets[8] = {
            &off->controlword, &off->mode, &off->target_velocity, &off->digital_output,
            &off->statusword, &off->mode_display, &off->velocity_actual, &off->digital_input,
        };
        for (int entry = 0; entry < 8; entry++) {
            uservo_dual_pv_domain_regs[axis * 8 + entry] = (ec_pdo_entry_reg_t){
                0, (uint16_t)axis, USERVO_VENDOR_ID, USERVO_PRODUCT_CODE,
                indices[entry], subindices[entry], offsets[entry], NULL
            };
        }
    }
    uservo_dual_pv_domain_regs[AXIS_COUNT * 8] = (ec_pdo_entry_reg_t){};
}

static void build_uservo_dual_csp_domain_regs(void)
{
    static const uint16_t indices[8] = {0x6040, 0x6060, 0x607a, 0x60fe, 0x6041, 0x6061, 0x6064, 0x60fd};
    static const uint8_t subindices[8] = {0, 0, 0, 1, 0, 0, 0, 0};
    for (int axis = 0; axis < AXIS_COUNT; axis++) {
        uservo_csp_offsets_t *off = &uservo_dual_csp_offsets[axis];
        unsigned int *offsets[8] = {
            &off->controlword, &off->mode, &off->target_position, &off->digital_output,
            &off->statusword, &off->mode_display, &off->position_actual, &off->digital_input,
        };
        for (int entry = 0; entry < 8; entry++) {
            uservo_dual_csp_domain_regs[axis * 8 + entry] = (ec_pdo_entry_reg_t){
                0, (uint16_t)axis, USERVO_VENDOR_ID, USERVO_PRODUCT_CODE,
                indices[entry], subindices[entry], offsets[entry], NULL
            };
        }
    }
    uservo_dual_csp_domain_regs[AXIS_COUNT * 8] = (ec_pdo_entry_reg_t){};
}

static void build_uservo_dual_combined_domain_regs(void)
{
    static const uint16_t indices[10] = {
        0x6040, 0x6060, 0x607a, 0x60ff, 0x60fe,
        0x6041, 0x6061, 0x6064, 0x606c, 0x60fd,
    };
    static const uint8_t subindices[10] = {0, 0, 0, 0, 1, 0, 0, 0, 0, 0};
    for (int axis = 0; axis < AXIS_COUNT; axis++) {
        uservo_csp_offsets_t *off = &uservo_dual_csp_offsets[axis];
        unsigned int *offsets[10] = {
            &off->controlword, &off->mode, &off->target_position, &off->target_velocity,
            &off->digital_output, &off->statusword, &off->mode_display, &off->position_actual,
            &off->velocity_actual, &off->digital_input,
        };
        for (int entry = 0; entry < 10; entry++) {
            uservo_dual_combined_domain_regs[axis * 10 + entry] = (ec_pdo_entry_reg_t){
                0, (uint16_t)axis, USERVO_VENDOR_ID, USERVO_PRODUCT_CODE,
                indices[entry], subindices[entry], offsets[entry], NULL
            };
        }
    }
    uservo_dual_combined_domain_regs[AXIS_COUNT * 10] = (ec_pdo_entry_reg_t){};
}

static int configure_uservo_pv_profile(
    ec_slave_config_t *slave_config,
    const uservo_pv_profile_t *profile,
    const char *axis_label_text)
{
    if (!profile) {
        return 0;
    }
    if (ecrt_slave_config_sdo32(slave_config, 0x607f, 0, profile->max_velocity_cps) < 0 ||
        ecrt_slave_config_sdo32(slave_config, 0x6083, 0, profile->accel_cps2) < 0 ||
        ecrt_slave_config_sdo32(slave_config, 0x6084, 0, profile->stop_decel_cps2) < 0) {
        fprintf(stderr, "failed to configure %s Uservo PV 0x607f/0x6083/0x6084\n", axis_label_text);
        return -1;
    }
    fprintf(
        stdout,
        "%s Uservo PV profile: target=%u rpm, max=%u rpm (%u cnt/s), accel=%u rpm/s (%u cnt/s^2), decel=%u rpm/s (%u cnt/s^2)\n",
        axis_label_text,
        profile->target_speed_rpm,
        profile->max_speed_rpm,
        profile->max_velocity_cps,
        profile->accel_rpm_s,
        profile->accel_cps2,
        profile->decel_rpm_s,
        profile->decel_cps2);
    return 0;
}

static int run_uservo_axes_de_gear(void)
{
    ec_master_t *master;
    ec_domain_t *domain;
    ec_slave_config_t *slave_configs[AXIS_COUNT];
    uint8_t *process_data;
    uint64_t deadline_ns;
    const int combined = uservo_dual_combined_topology;

    if (prepare_axis_d_realtime() < 0) {
        return 1;
    }
    master = ecrt_request_master(0);
    if (!master) {
        fprintf(stderr, "failed to request EtherCAT master 0\n");
        return 1;
    }
    domain = ecrt_master_create_domain(master);
    if (!domain) {
        fprintf(stderr, "failed to create dual Uservo %s domain\n", combined ? "combined" : "CSP");
        ecrt_release_master(master);
        return 1;
    }
    for (int axis = 0; axis < AXIS_COUNT; axis++) {
        slave_configs[axis] = ecrt_master_slave_config(
            master, 0, (uint16_t)axis, USERVO_VENDOR_ID, USERVO_PRODUCT_CODE);
        if (!slave_configs[axis]) {
            fprintf(stderr, "failed to create %s slave config at position %d\n", axis_label(axis), axis);
            ecrt_release_master(master);
            return 1;
        }
        if (ecrt_slave_config_pdos(
                slave_configs[axis], EC_END, combined ? uservo_combined_syncs : uservo_syncs)) {
            fprintf(stderr, "failed to configure %s Uservo %s PDOs\n", axis_label(axis), combined ? "combined" : "CSP");
            ecrt_release_master(master);
            return 1;
        }
        if (combined && configure_uservo_pv_profile(
                slave_configs[axis], uservo_pv_profile_for_axis(axis), axis_label(axis)) < 0) {
            ecrt_release_master(master);
            return 1;
        }
        ecrt_slave_config_dc(slave_configs[axis], 0x0300, PERIOD_NS, 0, 0, 0);
    }
    if (ecrt_master_select_reference_clock(master, slave_configs[AXIS_MCTIVITY])) {
        fprintf(stderr, "failed to select Axis D Uservo %s DC reference clock\n", combined ? "combined" : "CSP");
        ecrt_release_master(master);
        return 1;
    }
    if (combined) {
        build_uservo_dual_combined_domain_regs();
    } else {
        build_uservo_dual_csp_domain_regs();
    }
    if (ecrt_domain_reg_pdo_entry_list(
            domain, combined ? uservo_dual_combined_domain_regs : uservo_dual_csp_domain_regs)) {
        fprintf(stderr, "failed to register dual Uservo %s PDO entries\n", combined ? "combined" : "CSP");
        ecrt_release_master(master);
        return 1;
    }
    if (ecrt_master_activate(master)) {
        fprintf(stderr, "failed to activate EtherCAT master for dual Uservo %s\n", combined ? "combined" : "CSP");
        ecrt_release_master(master);
        return 1;
    }
    process_data = ecrt_domain_data(domain);
    if (!process_data) {
        fprintf(stderr, "failed to get dual Uservo %s domain data\n", combined ? "combined" : "CSP");
        ecrt_release_master(master);
        return 1;
    }
    for (int axis = 0; axis < AXIS_COUNT; axis++) {
        axes[axis].commanded_mode = 0;
        axes[axis].st.servo_request = 0;
        axes[axis].st.target_raw = 0;
        axes[axis].st.target_user = 0;
        snprintf(axes[axis].st.message, sizeof(axes[axis].st.message),
                 "%s commissioning inhibit active", axis_label(axis));
    }
    printf(
        "Axis D/E dual Uservo %s daemon listening on 127.0.0.1:%d (inhibit=%s, D counts/rev=%u, E counts/rev=%u, error-limit=%u, position-error-action=%s)\n",
        combined ? "combined PV/CSP gear" : "CSP gear",
        SERVER_PORT,
        commissioning_inhibit ? "on" : "off",
        uservo_pv_profiles[AXIS_MCTIVITY].counts_per_rev,
        uservo_pv_profiles[AXIS_FV3].counts_per_rev,
        gear_following_error_limit_counts,
        uservo_dual_combined_topology ? "alarm-only" : "trip");
    fflush(stdout);

    deadline_ns = monotonic_now_ns();
    while (running) {
        uint64_t scheduled_time_ns;
        uint64_t cycle_started_ns;
        uint64_t cycle_finished_ns;
        ec_slave_config_state_t slave_states[AXIS_COUNT];
        ec_domain_state_t domain_state;
        ec_master_state_t master_state;

        if (wait_for_axis_d_cycle(&deadline_ns, &scheduled_time_ns) < 0) {
            perror("Axis D/E CSP cycle sleep");
            break;
        }
        cycle_started_ns = monotonic_now_ns();
        ecrt_master_application_time(master, scheduled_time_ns);
        ecrt_master_receive(master);
        ecrt_domain_process(domain);
        ecrt_domain_state(domain, &domain_state);
        ecrt_master_state(master, &master_state);
        for (int axis = 0; axis < AXIS_COUNT; axis++) {
            axis_runtime_t *runtime = &axes[axis];
            uservo_csp_offsets_t *off = &uservo_dual_csp_offsets[axis];
            ecrt_slave_config_state(slave_configs[axis], &slave_states[axis]);
            runtime->st.sw = EC_READ_U16(process_data + off->statusword);
            runtime->st.err = 0;
            runtime->st.mode_display = EC_READ_S8(process_data + off->mode_display);
            runtime->st.pos_raw = EC_READ_S32(process_data + off->position_actual);
            runtime->st.velocity_actual_cps = combined
                ? EC_READ_S32(process_data + off->velocity_actual)
                : 0;
            runtime->st.following_error = 0;
            runtime->st.torque_feedback = 0;
            runtime->st.al_state = slave_states[axis].al_state;
            runtime->st.operational = slave_states[axis].operational;
            runtime->st.wc = domain_state.working_counter;
            runtime->st.wc_complete = domain_state.wc_state == EC_WC_COMPLETE;
            runtime->st.enabled = operation_enabled(runtime->st.sw);
            runtime->st.fault = (runtime->st.sw & 0x0008) != 0;
            runtime->st.pos_user = runtime->st.pos_raw - runtime->st.soft_zero_raw;
        }

        update_dual_uservo_communication_guard(
            domain_state.working_counter,
            domain_state.wc_state == EC_WC_COMPLETE,
            master_state.slaves_responding,
            master_state.link_up);
        for (int axis = 0; axis < AXIS_COUNT; axis++) {
            axis_runtime_t *runtime = &axes[axis];
            if (commissioning_inhibit) {
                runtime->st.servo_request = 0;
                runtime->st.jog_velocity_cps = 0;
                runtime->st.target_raw = runtime->st.pos_raw;
                runtime->st.target_user = runtime->st.pos_user;
            }
            axis_cycle_logic(runtime, axis);
            if (realtime_status.communication_timing_fault) {
                runtime->st.cw = 0;
            }
        }
        enforce_gear_group_interlock_before_output();
        for (int axis = 0; axis < AXIS_COUNT; axis++) {
            axis_runtime_t *runtime = &axes[axis];
            uservo_csp_offsets_t *off = &uservo_dual_csp_offsets[axis];
            int safety_output_blocked = realtime_status.communication_timing_fault || gear_group_safety_latched;
            uint16_t output_controlword = safety_output_blocked
                ? 0
                : (commissioning_inhibit
                    ? (runtime->st.cw == 0x0080 ? 0x0080 : 0)
                    : runtime->st.cw);
            EC_WRITE_U16(process_data + off->controlword, output_controlword);
            EC_WRITE_S8(process_data + off->mode,
                        commissioning_inhibit || safety_output_blocked ? 0 : runtime->commanded_mode);
            EC_WRITE_S32(process_data + off->target_position,
                         commissioning_inhibit || safety_output_blocked ? runtime->st.pos_raw : runtime->st.target_raw);
            if (combined) {
                EC_WRITE_S32(process_data + off->target_velocity,
                             commissioning_inhibit || safety_output_blocked ||
                             !axis_uses_native_pv_control(axis, runtime->st.control_mode)
                                 ? 0
                                 : runtime->target_velocity_cps);
            }
            EC_WRITE_U32(process_data + off->digital_output, 0);
        }
        ecrt_domain_queue(domain);
        ecrt_master_sync_reference_clock(master);
        ecrt_master_sync_slave_clocks(master);
        ecrt_master_send(master);
        poll_server(AXIS_D_SERVER_ACCEPT_BUDGET, 1U, 1);
        for (int axis = 0; axis < AXIS_COUNT; axis++) {
            axes[axis].st.cycles++;
        }
        cycle_finished_ns = monotonic_now_ns();
        realtime_status.last_cycle_runtime_ns = cycle_finished_ns - cycle_started_ns;
        if (realtime_status.last_cycle_runtime_ns > realtime_status.max_cycle_runtime_ns) {
            realtime_status.max_cycle_runtime_ns = realtime_status.last_cycle_runtime_ns;
        }
    }

    printf("Disabling Axis D/E Uservo CSP outputs before exit...\n");
    for (unsigned int cycle = 0; cycle < AXIS_D_SHUTDOWN_CYCLES; cycle++) {
        uint64_t scheduled_time_ns;
        if (wait_for_axis_d_cycle(&deadline_ns, &scheduled_time_ns) < 0) {
            break;
        }
        ecrt_master_application_time(master, scheduled_time_ns);
        ecrt_master_receive(master);
        ecrt_domain_process(domain);
        for (int axis = 0; axis < AXIS_COUNT; axis++) {
            uservo_csp_offsets_t *off = &uservo_dual_csp_offsets[axis];
            EC_WRITE_U16(process_data + off->controlword, 0);
            EC_WRITE_S8(process_data + off->mode, 0);
            EC_WRITE_S32(process_data + off->target_position,
                         EC_READ_S32(process_data + off->position_actual));
            if (combined) {
                EC_WRITE_S32(process_data + off->target_velocity, 0);
            }
            EC_WRITE_U32(process_data + off->digital_output, 0);
        }
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

static int run_uservo_axes_de_pv(void)
{
    ec_master_t *master;
    ec_domain_t *domain;
    ec_slave_config_t *slave_configs[AXIS_COUNT];
    uint8_t *process_data;
    uint64_t deadline_ns;

    if (prepare_axis_d_realtime() < 0) {
        return 1;
    }
    master = ecrt_request_master(0);
    if (!master) {
        fprintf(stderr, "failed to request EtherCAT master 0\n");
        return 1;
    }
    domain = ecrt_master_create_domain(master);
    if (!domain) {
        fprintf(stderr, "failed to create dual Uservo PV domain\n");
        ecrt_release_master(master);
        return 1;
    }
    for (int axis = 0; axis < AXIS_COUNT; axis++) {
        slave_configs[axis] = ecrt_master_slave_config(
            master, 0, (uint16_t)axis, USERVO_VENDOR_ID, USERVO_PRODUCT_CODE);
        if (!slave_configs[axis]) {
            fprintf(stderr, "failed to create %s slave config at position %d\n", axis_label(axis), axis);
            ecrt_release_master(master);
            return 1;
        }
        if (ecrt_slave_config_pdos(slave_configs[axis], EC_END, uservo_pv_syncs)) {
            fprintf(stderr, "failed to configure %s PDOs\n", axis_label(axis));
            ecrt_release_master(master);
            return 1;
        }
        if (configure_uservo_pv_profile(
                slave_configs[axis], uservo_pv_profile_for_axis(axis), axis_label(axis)) < 0) {
            ecrt_release_master(master);
            return 1;
        }
        ecrt_slave_config_dc(slave_configs[axis], 0x0300, PERIOD_NS, 0, 0, 0);
    }
    if (ecrt_master_select_reference_clock(master, slave_configs[AXIS_MCTIVITY])) {
        fprintf(stderr, "failed to select Axis D Uservo DC reference clock\n");
        ecrt_release_master(master);
        return 1;
    }
    build_uservo_dual_pv_domain_regs();
    if (ecrt_domain_reg_pdo_entry_list(domain, uservo_dual_pv_domain_regs)) {
        fprintf(stderr, "failed to register dual Uservo PV PDO entries\n");
        ecrt_release_master(master);
        return 1;
    }
    if (ecrt_master_activate(master)) {
        fprintf(stderr, "failed to activate EtherCAT master for dual Uservo PV\n");
        ecrt_release_master(master);
        return 1;
    }
    process_data = ecrt_domain_data(domain);
    if (!process_data) {
        fprintf(stderr, "failed to get dual Uservo PV domain data\n");
        ecrt_release_master(master);
        return 1;
    }

    for (int axis = 0; axis < AXIS_COUNT; axis++) {
        axes[axis].commanded_mode = commissioning_inhibit ? 0 : 3;
        axes[axis].st.servo_request = 0;
        axes[axis].st.jog_velocity_cps = 0;
        axes[axis].target_velocity_cps = 0;
        snprintf(
            axes[axis].st.message,
            sizeof(axes[axis].st.message),
            "%s commissioning inhibit %s",
            axis_label(axis),
            commissioning_inhibit ? "active" : "inactive");
    }
    printf(
        "Axis D/E dual Uservo PV daemon listening on 127.0.0.1:%d (inhibit=%s, D counts/rev=%u, E counts/rev=%u)\n",
        SERVER_PORT,
        commissioning_inhibit ? "on" : "off",
        uservo_pv_profiles[AXIS_MCTIVITY].counts_per_rev,
        uservo_pv_profiles[AXIS_FV3].counts_per_rev);
    fflush(stdout);

    deadline_ns = monotonic_now_ns();
    while (running) {
        uint64_t scheduled_time_ns;
        uint64_t cycle_started_ns;
        uint64_t cycle_finished_ns;
        ec_slave_config_state_t slave_states[AXIS_COUNT];
        ec_domain_state_t domain_state;
        ec_master_state_t master_state;

        if (wait_for_axis_d_cycle(&deadline_ns, &scheduled_time_ns) < 0) {
            perror("Axis D/E cycle sleep");
            break;
        }
        cycle_started_ns = monotonic_now_ns();
        ecrt_master_application_time(master, scheduled_time_ns);
        ecrt_master_receive(master);
        ecrt_domain_process(domain);
        ecrt_domain_state(domain, &domain_state);
        ecrt_master_state(master, &master_state);
        for (int axis = 0; axis < AXIS_COUNT; axis++) {
            axis_runtime_t *runtime = &axes[axis];
            uservo_pv_offsets_t *off = &uservo_dual_pv_offsets[axis];
            ecrt_slave_config_state(slave_configs[axis], &slave_states[axis]);
            runtime->st.sw = EC_READ_U16(process_data + off->statusword);
            runtime->st.err = 0;
            runtime->st.mode_display = EC_READ_S8(process_data + off->mode_display);
            runtime->st.velocity_actual_cps = EC_READ_S32(process_data + off->velocity_actual);
            runtime->st.following_error = 0;
            runtime->st.torque_feedback = 0;
            runtime->st.al_state = slave_states[axis].al_state;
            runtime->st.operational = slave_states[axis].operational;
            runtime->st.wc = domain_state.working_counter;
            runtime->st.wc_complete = domain_state.wc_state == EC_WC_COMPLETE;
            runtime->st.enabled = operation_enabled(runtime->st.sw);
            runtime->st.fault = (runtime->st.sw & 0x0008) != 0;
            runtime->st.pos_user = runtime->st.pos_raw - runtime->st.soft_zero_raw;
        }

        update_dual_uservo_communication_guard(
            domain_state.working_counter,
            domain_state.wc_state == EC_WC_COMPLETE,
            master_state.slaves_responding,
            master_state.link_up);
        for (int axis = 0; axis < AXIS_COUNT; axis++) {
            axis_runtime_t *runtime = &axes[axis];
            if (commissioning_inhibit) {
                runtime->st.servo_request = 0;
                runtime->st.jog_velocity_cps = 0;
                runtime->target_velocity_cps = 0;
                runtime->st.target_raw = runtime->st.pos_raw;
                runtime->st.target_user = runtime->st.pos_user;
            }
            axis_cycle_logic(runtime, axis);
            if (realtime_status.communication_timing_fault) {
                runtime->st.cw = 0;
            }
        }
        enforce_sync_group_interlock_before_output();
        for (int axis = 0; axis < AXIS_COUNT; axis++) {
            axis_runtime_t *runtime = &axes[axis];
            uservo_pv_offsets_t *off = &uservo_dual_pv_offsets[axis];
            int safety_output_blocked = realtime_status.communication_timing_fault || sync_group_safety_latched;
            uint16_t output_controlword = safety_output_blocked
                ? 0
                : (commissioning_inhibit
                    ? (runtime->st.cw == 0x0080 ? 0x0080 : 0)
                    : runtime->st.cw);
            EC_WRITE_U16(process_data + off->controlword, output_controlword);
            EC_WRITE_S8(
                process_data + off->mode,
                commissioning_inhibit || safety_output_blocked ? 0 : runtime->commanded_mode);
            EC_WRITE_S32(
                process_data + off->target_velocity,
                commissioning_inhibit || safety_output_blocked ? 0 : runtime->target_velocity_cps);
            EC_WRITE_U32(process_data + off->digital_output, 0);
        }
        ecrt_domain_queue(domain);
        ecrt_master_sync_reference_clock(master);
        ecrt_master_sync_slave_clocks(master);
        ecrt_master_send(master);
        /* One command per dual-axis cycle guarantees an acknowledged atomic group
         * update reaches the next PDO frame before another command can replace it. */
        poll_server(AXIS_D_SERVER_ACCEPT_BUDGET, 1U, 1);
        for (int axis = 0; axis < AXIS_COUNT; axis++) {
            axes[axis].st.cycles++;
        }
        cycle_finished_ns = monotonic_now_ns();
        realtime_status.last_cycle_runtime_ns = cycle_finished_ns - cycle_started_ns;
        if (realtime_status.last_cycle_runtime_ns > realtime_status.max_cycle_runtime_ns) {
            realtime_status.max_cycle_runtime_ns = realtime_status.last_cycle_runtime_ns;
        }
    }

    printf("Disabling Axis D/E Uservo outputs before exit...\n");
    for (unsigned int cycle = 0; cycle < AXIS_D_SHUTDOWN_CYCLES; cycle++) {
        uint64_t scheduled_time_ns;
        if (wait_for_axis_d_cycle(&deadline_ns, &scheduled_time_ns) < 0) {
            break;
        }
        ecrt_master_application_time(master, scheduled_time_ns);
        ecrt_master_receive(master);
        ecrt_domain_process(domain);
        for (int axis = 0; axis < AXIS_COUNT; axis++) {
            uservo_pv_offsets_t *off = &uservo_dual_pv_offsets[axis];
            EC_WRITE_U16(process_data + off->controlword, 0);
            EC_WRITE_S8(process_data + off->mode, 0);
            EC_WRITE_S32(process_data + off->target_velocity, 0);
            EC_WRITE_U32(process_data + off->digital_output, 0);
        }
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

    if (ecrt_slave_config_pdos(slave_config, EC_END, uservo_pv_topology ? uservo_pv_syncs : uservo_syncs)) {
        fprintf(stderr, "failed to configure Uservo axis D PDOs\n");
        ecrt_release_master(master);
        return 1;
    }
    if (configure_uservo_pv_profile(
            slave_config,
            uservo_pv_profile_for_axis(AXIS_MCTIVITY),
            "Axis D") < 0) {
        ecrt_release_master(master);
        return 1;
    }
    ecrt_slave_config_dc(slave_config, 0x0300, PERIOD_NS, 0, 0, 0);
    if (ecrt_master_select_reference_clock(master, slave_config)) {
        fprintf(stderr, "failed to select Uservo axis D DC reference clock\n");
        ecrt_release_master(master);
        return 1;
    }

    if (ecrt_domain_reg_pdo_entry_list(domain, uservo_pv_topology ? uservo_pv_domain_regs : uservo_domain_regs)) {
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
        axis->st.pos_raw = uservo_pv_topology ? axis->st.pos_raw : EC_READ_S32(process_data + uservo_off_position_actual);
        axis->st.velocity_actual_cps = uservo_pv_topology ? EC_READ_S32(process_data + uservo_off_velocity_actual) : 0;
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
            axis->st.jog_velocity_cps = 0;
            axis->target_velocity_cps = 0;
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
        if (uservo_pv_topology) {
            EC_WRITE_S32(process_data + uservo_off_target_velocity,
                         commissioning_inhibit || realtime_status.communication_timing_fault ? 0 : axis->target_velocity_cps);
        } else {
            EC_WRITE_S32(process_data + uservo_off_target_position, axis->st.target_raw);
        }
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
        if (uservo_pv_topology) {
            EC_WRITE_S32(process_data + uservo_off_target_velocity, 0);
        } else {
            EC_WRITE_S32(
                process_data + uservo_off_target_position,
                EC_READ_S32(process_data + uservo_off_position_actual));
        }
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
    const char *profile = getenv("MCTIVITY_PROFILE");

    uservo_dual_pv_topology = topology && strcmp(topology, "axis-de-uservo-pv") == 0;
    uservo_dual_gear_topology = topology && strcmp(topology, "axis-de-uservo-gear") == 0;
    uservo_dual_combined_topology = uservo_dual_gear_topology && profile &&
        strcmp(profile, "axis-de-uservo-combined") == 0;
    uservo_dual_topology = uservo_dual_pv_topology || uservo_dual_gear_topology;
    uservo_pv_topology = topology &&
        (strcmp(topology, "axis-d-uservo-pv") == 0 || uservo_dual_pv_topology);
    uservo_axis_d_topology = topology &&
        (strcmp(topology, "axis-d-uservo") == 0 || uservo_pv_topology || uservo_dual_topology);
    commissioning_inhibit = uservo_axis_d_topology
        ? env_flag_default("MCTIVITY_COMMISSIONING_INHIBIT", 1)
        : 0;
    require_realtime = uservo_axis_d_topology
        ? env_flag_default("MCTIVITY_REQUIRE_REALTIME", 1)
        : 0;
    if (load_axis_profile_parameters() < 0) {
        return 1;
    }

    signal(SIGINT, handle_signal);
    signal(SIGTERM, handle_signal);

    for (int i = 0; i < MAX_CLIENTS; i++) {
        clients[i].fd = -1;
    }
    for (int axis = 0; axis < AXIS_COUNT; axis++) {
        memset(&axes[axis], 0, sizeof(axes[axis]));
        set_control_mode(&axes[axis], axis_is_uservo_pv(axis) ? "velocity" : "position");
        axes[axis].commanded_mode = axis_mode_code(
            axis, axis_is_uservo_pv(axis) ? "velocity" : "position");
        axes[axis].gear_master_axis = axis == AXIS_FV3 ? AXIS_MCTIVITY : AXIS_FV3;
        axes[axis].gear_master_ratio = 1;
        axes[axis].gear_slave_ratio = 1;
        axes[axis].gear_direction = 1;
        (void)mctivity_gear_configure(&axes[axis].gear_math, 1, 1, 1);
        snprintf(axes[axis].st.message, sizeof(axes[axis].st.message), "starting");
    }

    listen_fd = setup_server();
    if (listen_fd < 0) {
        perror("failed to start command server on 127.0.0.1:10001");
        return 1;
    }

    if (uservo_dual_gear_topology) {
        return run_uservo_axes_de_gear();
    }
    if (uservo_dual_pv_topology) {
        return run_uservo_axes_de_pv();
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
