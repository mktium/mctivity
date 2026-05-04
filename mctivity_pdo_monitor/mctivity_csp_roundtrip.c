#include <errno.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>
#include <unistd.h>

#include <ecrt.h>

#define MCTIVITY_VENDOR_ID 0x000116c7
#define MCTIVITY_PRODUCT_CODE 0x007e0402
#define PERIOD_NS 1000000L
#define NSEC_PER_SEC 1000000000LL
#define DEFAULT_REV_COUNTS 8388608

static volatile sig_atomic_t running = 1;

static unsigned int off_controlword;
static unsigned int off_mode;
static unsigned int off_target_position;
static unsigned int off_touch_probe_function;
static unsigned int off_error_code;
static unsigned int off_statusword;
static unsigned int off_mode_display;
static unsigned int off_position_actual;
static unsigned int off_touch_probe_status;
static unsigned int off_touch_probe_pos1;
static unsigned int off_following_error;

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

static const ec_pdo_entry_reg_t domain_regs[] = {
    {0, 0, MCTIVITY_VENDOR_ID, MCTIVITY_PRODUCT_CODE, 0x6040, 0, &off_controlword, NULL},
    {0, 0, MCTIVITY_VENDOR_ID, MCTIVITY_PRODUCT_CODE, 0x6060, 0, &off_mode, NULL},
    {0, 0, MCTIVITY_VENDOR_ID, MCTIVITY_PRODUCT_CODE, 0x607a, 0, &off_target_position, NULL},
    {0, 0, MCTIVITY_VENDOR_ID, MCTIVITY_PRODUCT_CODE, 0x60b8, 0, &off_touch_probe_function, NULL},
    {0, 0, MCTIVITY_VENDOR_ID, MCTIVITY_PRODUCT_CODE, 0x603f, 0, &off_error_code, NULL},
    {0, 0, MCTIVITY_VENDOR_ID, MCTIVITY_PRODUCT_CODE, 0x6041, 0, &off_statusword, NULL},
    {0, 0, MCTIVITY_VENDOR_ID, MCTIVITY_PRODUCT_CODE, 0x6061, 0, &off_mode_display, NULL},
    {0, 0, MCTIVITY_VENDOR_ID, MCTIVITY_PRODUCT_CODE, 0x6064, 0, &off_position_actual, NULL},
    {0, 0, MCTIVITY_VENDOR_ID, MCTIVITY_PRODUCT_CODE, 0x60b9, 0, &off_touch_probe_status, NULL},
    {0, 0, MCTIVITY_VENDOR_ID, MCTIVITY_PRODUCT_CODE, 0x60ba, 0, &off_touch_probe_pos1, NULL},
    {0, 0, MCTIVITY_VENDOR_ID, MCTIVITY_PRODUCT_CODE, 0x60f4, 0, &off_following_error, NULL},
    {}
};

static void handle_signal(int sig)
{
    (void)sig;
    running = 0;
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
    if (step >= steps) {
        return to;
    }

    double x = (double)step / (double)steps;
    double s = x * x * (3.0 - 2.0 * x);
    return from + (int32_t)((to - from) * s);
}

static int32_t command_for_enabled_cycle(int32_t center, int32_t half_counts,
                                         uint32_t enabled_cycle,
                                         uint32_t move_cycles,
                                         uint32_t hold_cycles,
                                         int roundtrips)
{
    uint32_t leg_cycles = move_cycles + hold_cycles;
    uint32_t total_legs = (uint32_t)roundtrips * 2U;
    uint32_t leg = enabled_cycle / leg_cycles;
    uint32_t in_leg = enabled_cycle % leg_cycles;

    if (leg >= total_legs) {
        return center;
    }

    int32_t from = (leg % 2U == 0U) ? center : center + half_counts;
    int32_t to = (leg % 2U == 0U) ? center + half_counts : center;

    if (in_leg < move_cycles) {
        return smooth_move(from, to, in_leg, move_cycles);
    }
    return to;
}

int main(int argc, char **argv)
{
    int roundtrips = 10;
    int32_t half_counts = DEFAULT_REV_COUNTS / 2;
    int move_ms = 3000;
    int hold_ms = 0;
    ec_master_t *master;
    ec_domain_t *domain;
    ec_slave_config_t *sc;
    uint8_t *domain_pd;
    struct timespec wake_time;
    uint64_t app_time_base;
    uint32_t cycles = 0;
    uint32_t enabled_cycles = 0;
    int32_t center = 0;
    int32_t target = 0;
    int have_center = 0;
    int reached_enabled = 0;
    int failed = 0;

    if (argc > 1) {
        roundtrips = atoi(argv[1]);
    }
    if (argc > 2) {
        half_counts = (int32_t)strtol(argv[2], NULL, 0);
    }
    if (argc > 3) {
        move_ms = atoi(argv[3]);
    }
    if (argc > 4) {
        hold_ms = atoi(argv[4]);
    }
    if (roundtrips <= 0 || half_counts == 0 || move_ms <= 0 || hold_ms < 0) {
        fprintf(stderr, "usage: %s [roundtrips=10] [half_counts=4194304] [move_ms=3000] [hold_ms=0]\n", argv[0]);
        return 2;
    }

    signal(SIGINT, handle_signal);
    signal(SIGTERM, handle_signal);

    master = ecrt_request_master(0);
    if (!master) {
        fprintf(stderr, "failed to request EtherCAT master 0\n");
        return 1;
    }

    domain = ecrt_master_create_domain(master);
    sc = ecrt_master_slave_config(master, 0, 0, MCTIVITY_VENDOR_ID, MCTIVITY_PRODUCT_CODE);
    if (!domain || !sc) {
        fprintf(stderr, "failed to create domain or slave config\n");
        ecrt_release_master(master);
        return 1;
    }

    if (ecrt_slave_config_pdos(sc, EC_END, mctivity_syncs)) {
        fprintf(stderr, "failed to configure PDOs\n");
        ecrt_release_master(master);
        return 1;
    }

    ecrt_slave_config_dc(sc, 0x0300, PERIOD_NS, 0, 0, 0);

    if (ecrt_domain_reg_pdo_entry_list(domain, domain_regs)) {
        fprintf(stderr, "failed to register PDO entries\n");
        ecrt_release_master(master);
        return 1;
    }

    if (ecrt_master_activate(master)) {
        fprintf(stderr, "failed to activate master\n");
        ecrt_release_master(master);
        return 1;
    }

    domain_pd = ecrt_domain_data(domain);
    if (!domain_pd) {
        fprintf(stderr, "failed to get domain data\n");
        ecrt_release_master(master);
        return 1;
    }

    printf("MCTIVITY CSP roundtrip: roundtrips=%d, half_counts=%d, move_ms=%d, hold_ms=%d\n",
           roundtrips, half_counts, move_ms, hold_ms);

    clock_gettime(CLOCK_MONOTONIC, &wake_time);
    app_time_base = timespec_to_ns(&wake_time);

    uint32_t total_enabled_cycles =
        (uint32_t)roundtrips * 2U * ((uint32_t)move_ms + (uint32_t)hold_ms);

    while (running && cycles < total_enabled_cycles + 10000U) {
        uint64_t app_time = app_time_base + (uint64_t)cycles * PERIOD_NS;

        ecrt_master_application_time(master, app_time);
        ecrt_master_receive(master);
        ecrt_domain_process(domain);

        uint16_t sw = EC_READ_U16(domain_pd + off_statusword);
        uint16_t err = EC_READ_U16(domain_pd + off_error_code);
        int8_t mode = EC_READ_S8(domain_pd + off_mode_display);
        int32_t pos = EC_READ_S32(domain_pd + off_position_actual);
        int32_t follow = EC_READ_S32(domain_pd + off_following_error);

        ec_slave_config_state_t slave_state;
        ec_domain_state_t domain_state;
        ecrt_slave_config_state(sc, &slave_state);
        ecrt_domain_state(domain, &domain_state);

        if (!have_center && domain_state.working_counter >= 3 &&
            domain_state.wc_state == EC_WC_COMPLETE) {
            center = pos;
            target = center;
            have_center = 1;
            printf("Latched center: %d; positive half target: %d\n",
                   center, center + half_counts);
            fflush(stdout);
        }

        if (err != 0) {
            fprintf(stderr, "drive error detected: 0x%04x\n", err);
            failed = 1;
            break;
        }

        uint16_t cw = have_center ? next_controlword(sw) : 0x0000;
        if (have_center && operation_enabled(sw)) {
            reached_enabled = 1;
            cw = 0x000f;
            target = command_for_enabled_cycle(center, half_counts, enabled_cycles,
                                               (uint32_t)move_ms, (uint32_t)hold_ms,
                                               roundtrips);
            enabled_cycles++;
        }

        EC_WRITE_U16(domain_pd + off_controlword, cw);
        EC_WRITE_S8(domain_pd + off_mode, 8);
        EC_WRITE_S32(domain_pd + off_target_position, target);
        EC_WRITE_U16(domain_pd + off_touch_probe_function, 0x0000);

        ecrt_domain_queue(domain);
        ecrt_master_sync_reference_clock(master);
        ecrt_master_sync_slave_clocks(master);
        ecrt_master_send(master);

        if (cycles % 500U == 0) {
            printf("t=%0.3fs al=0x%02x op=%u wc=%u cw=0x%04x sw=0x%04x err=0x%04x mode=%d pos=%d target=%d follow=%d trip=%u\n",
                   cycles / 1000.0,
                   slave_state.al_state,
                   slave_state.operational,
                   domain_state.working_counter,
                   cw, sw, err, mode, pos, target, follow,
                   reached_enabled ? enabled_cycles / (2U * ((uint32_t)move_ms + (uint32_t)hold_ms)) : 0U);
            fflush(stdout);
        }

        if (reached_enabled && enabled_cycles >= total_enabled_cycles) {
            break;
        }

        cycles++;
        sleep_until_next(&wake_time);
    }

    printf("Disabling drive output at target=%d...\n", target);
    for (int i = 0; i < 300 && running; i++) {
        uint64_t app_time = app_time_base + (uint64_t)(cycles + i) * PERIOD_NS;
        ecrt_master_application_time(master, app_time);
        ecrt_master_receive(master);
        ecrt_domain_process(domain);
        EC_WRITE_U16(domain_pd + off_controlword, 0x0000);
        EC_WRITE_S8(domain_pd + off_mode, 8);
        EC_WRITE_S32(domain_pd + off_target_position, target);
        EC_WRITE_U16(domain_pd + off_touch_probe_function, 0x0000);
        ecrt_domain_queue(domain);
        ecrt_master_sync_reference_clock(master);
        ecrt_master_sync_slave_clocks(master);
        ecrt_master_send(master);
        sleep_until_next(&wake_time);
    }

    ecrt_release_master(master);

    if (failed || !reached_enabled) {
        fprintf(stderr, "roundtrip test failed or did not enable\n");
        return 1;
    }

    printf("Roundtrip CSP test completed and drive output disabled.\n");
    return 0;
}
