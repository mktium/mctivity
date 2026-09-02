#include <errno.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>
#include <unistd.h>

#include <ecrt.h>
#include "motion_test_guard.h"

#define MCTIVITY_VENDOR_ID 0x000116c7
#define MCTIVITY_PRODUCT_CODE 0x007e0402
#define PERIOD_NS 1000000L

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
    {0x6040, 0x00, 16},
    {0x6060, 0x00, 8},
    {0x607a, 0x00, 32},
    {0x60b8, 0x00, 16},
    {0x0000, 0x00, 0},
    {0x0000, 0x00, 0},
    {0x0000, 0x00, 0},
    {0x0000, 0x00, 0},
    {0x0000, 0x00, 0},
    {0x0000, 0x00, 0},
    {0x0000, 0x00, 0},
    {0x0000, 0x00, 0},
    {0x603f, 0x00, 16},
    {0x6041, 0x00, 16},
    {0x6061, 0x00, 8},
    {0x6064, 0x00, 32},
    {0x60b9, 0x00, 16},
    {0x60ba, 0x00, 32},
    {0x60f4, 0x00, 32},
    {0x0000, 0x00, 0},
    {0x0000, 0x00, 0},
    {0x0000, 0x00, 0},
    {0x0000, 0x00, 0},
    {0x0000, 0x00, 0},
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

static int cia402_ready_to_switch_on(uint16_t sw)
{
    return (sw & 0x006f) == 0x0021;
}

static int cia402_switched_on(uint16_t sw)
{
    return (sw & 0x006f) == 0x0023;
}

static int cia402_operation_enabled(uint16_t sw)
{
    return (sw & 0x006f) == 0x0027;
}

static uint16_t next_controlword(uint16_t sw)
{
    if (sw & 0x0008) {
        return 0x0080; // Fault reset.
    }

    if ((sw & 0x004f) == 0x0040) {
        return 0x0006; // Shutdown: switch on disabled -> ready to switch on.
    }

    if (cia402_ready_to_switch_on(sw)) {
        return 0x0007; // Switch on.
    }

    if (cia402_switched_on(sw) || cia402_operation_enabled(sw)) {
        return 0x000f; // Enable operation and keep it enabled.
    }

    return 0x0006;
}

int main(int argc, char **argv)
{
    if (!acknowledge_motion_test(&argc, &argv)) {
        return 2;
    }
    int hold_seconds = 5;
    ec_master_t *master;
    ec_domain_t *domain;
    ec_slave_config_t *sc;
    uint8_t *domain_pd;
    struct timespec wake_time;
    uint32_t cycles = 0;
    uint32_t enabled_cycles = 0;
    int32_t hold_position = 0;
    int have_hold_position = 0;
    int reached_enabled = 0;

    if (argc > 1) {
        hold_seconds = atoi(argv[1]);
        if (hold_seconds <= 0) {
            fprintf(stderr, "usage: %s [hold_seconds]\n", argv[0]);
            return 2;
        }
    }

    signal(SIGINT, handle_signal);
    signal(SIGTERM, handle_signal);

    master = ecrt_request_master(0);
    if (!master) {
        fprintf(stderr, "failed to request EtherCAT master 0\n");
        return 1;
    }

    domain = ecrt_master_create_domain(master);
    if (!domain) {
        fprintf(stderr, "failed to create process data domain\n");
        ecrt_release_master(master);
        return 1;
    }

    sc = ecrt_master_slave_config(master, 0, 0, MCTIVITY_VENDOR_ID, MCTIVITY_PRODUCT_CODE);
    if (!sc) {
        fprintf(stderr, "failed to get MCTIVITY slave configuration\n");
        ecrt_release_master(master);
        return 1;
    }

    if (ecrt_slave_config_pdos(sc, EC_END, mctivity_syncs)) {
        fprintf(stderr, "failed to configure MCTIVITY PDOs\n");
        ecrt_release_master(master);
        return 1;
    }

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
        fprintf(stderr, "failed to get process data\n");
        ecrt_release_master(master);
        return 1;
    }

    clock_gettime(CLOCK_MONOTONIC, &wake_time);
    printf("MCTIVITY enable-hold test: hold current position for %d seconds after enable.\n",
           hold_seconds);

    while (running && cycles < 30000U) {
        ecrt_master_receive(master);
        ecrt_domain_process(domain);

        uint16_t sw = EC_READ_U16(domain_pd + off_statusword);
        uint16_t err = EC_READ_U16(domain_pd + off_error_code);
        int8_t mode_display = EC_READ_S8(domain_pd + off_mode_display);
        int32_t pos = EC_READ_S32(domain_pd + off_position_actual);
        int32_t following_error = EC_READ_S32(domain_pd + off_following_error);

        ec_slave_config_state_t slave_state;
        ec_domain_state_t domain_state;

        ecrt_slave_config_state(sc, &slave_state);
        ecrt_domain_state(domain, &domain_state);

        if (!have_hold_position && domain_state.working_counter >= 3 &&
            domain_state.wc_state == EC_WC_COMPLETE) {
            hold_position = pos;
            have_hold_position = 1;
            printf("Latched hold position from valid PDO data: %d\n", hold_position);
            fflush(stdout);
        }

        uint16_t cw = have_hold_position ? next_controlword(sw) : 0x0000;
        if (have_hold_position && cia402_operation_enabled(sw)) {
            reached_enabled = 1;
            enabled_cycles++;
            cw = 0x000f;
        }

        EC_WRITE_U16(domain_pd + off_controlword, cw);
        EC_WRITE_S8(domain_pd + off_mode, 8);
        EC_WRITE_S32(domain_pd + off_target_position, hold_position);
        EC_WRITE_U16(domain_pd + off_touch_probe_function, 0x0000);

        ecrt_domain_queue(domain);
        ecrt_master_send(master);

        if (cycles % 250U == 0) {
            printf("t=%0.3fs al=0x%02x op=%u wc=%u cw=0x%04x sw=0x%04x err=0x%04x mode=%d pos=%d target=%d follow=%d\n",
                   cycles / 1000.0,
                   slave_state.al_state,
                   slave_state.operational,
                   domain_state.working_counter,
                   cw,
                   sw,
                   err,
                   mode_display,
                   pos,
                   hold_position,
                   following_error);
            fflush(stdout);
        }

        if (reached_enabled && enabled_cycles >= (uint32_t)hold_seconds * 1000U) {
            break;
        }

        cycles++;
        sleep_until_next(&wake_time);
    }

    printf("Disabling drive output...\n");
    for (int i = 0; i < 300 && running; i++) {
        ecrt_master_receive(master);
        ecrt_domain_process(domain);

        EC_WRITE_U16(domain_pd + off_controlword, 0x0000);
        EC_WRITE_S8(domain_pd + off_mode, 8);
        EC_WRITE_S32(domain_pd + off_target_position, hold_position);
        EC_WRITE_U16(domain_pd + off_touch_probe_function, 0x0000);

        ecrt_domain_queue(domain);
        ecrt_master_send(master);
        sleep_until_next(&wake_time);
    }

    ecrt_release_master(master);

    if (!reached_enabled) {
        fprintf(stderr, "drive did not reach operation enabled before timeout\n");
        return 1;
    }

    printf("Enable-hold test completed and drive output disabled.\n");
    return 0;
}
