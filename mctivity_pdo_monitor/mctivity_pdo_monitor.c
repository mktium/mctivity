#include <errno.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#include <ecrt.h>

#define MCTIVITY_VENDOR_ID 0x000116c7
#define MCTIVITY_PRODUCT_CODE 0x007e0402
#define PERIOD_NS 1000000L

static volatile sig_atomic_t running = 1;

static ec_master_t *master = NULL;
static ec_domain_t *domain = NULL;
static ec_slave_config_t *sc = NULL;
static uint8_t *domain_pd = NULL;

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

int main(int argc, char **argv)
{
    int seconds = 10;
    struct timespec wake_time;
    uint32_t cycles = 0;
    int32_t hold_position = 0;
    int have_hold_position = 0;

    if (argc > 1) {
        seconds = atoi(argv[1]);
        if (seconds <= 0) {
            fprintf(stderr, "usage: %s [seconds]\n", argv[0]);
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
        return 1;
    }

    sc = ecrt_master_slave_config(master, 0, 0, MCTIVITY_VENDOR_ID, MCTIVITY_PRODUCT_CODE);
    if (!sc) {
        fprintf(stderr, "failed to get MCTIVITY slave configuration\n");
        return 1;
    }

    if (ecrt_slave_config_pdos(sc, EC_END, mctivity_syncs)) {
        fprintf(stderr, "failed to configure MCTIVITY PDOs\n");
        return 1;
    }

    if (ecrt_domain_reg_pdo_entry_list(domain, domain_regs)) {
        fprintf(stderr, "failed to register PDO entries\n");
        return 1;
    }

    if (ecrt_master_activate(master)) {
        fprintf(stderr, "failed to activate master\n");
        return 1;
    }

    domain_pd = ecrt_domain_data(domain);
    if (!domain_pd) {
        fprintf(stderr, "failed to get domain process data\n");
        return 1;
    }

    clock_gettime(CLOCK_MONOTONIC, &wake_time);
    printf("MCTIVITY PDO monitor running for %d seconds. Outputs keep servo disabled.\n", seconds);

    while (running && cycles < (uint32_t)seconds * 1000U) {
        ecrt_master_receive(master);
        ecrt_domain_process(domain);

        uint16_t statusword = EC_READ_U16(domain_pd + off_statusword);
        uint16_t error_code = EC_READ_U16(domain_pd + off_error_code);
        int8_t mode_display = EC_READ_S8(domain_pd + off_mode_display);
        int32_t actual_position = EC_READ_S32(domain_pd + off_position_actual);
        int32_t following_error = EC_READ_S32(domain_pd + off_following_error);

        if (!have_hold_position) {
            hold_position = actual_position;
            have_hold_position = 1;
        }

        // Keep the drive not enabled while still providing valid CSP PDO data.
        EC_WRITE_U16(domain_pd + off_controlword, 0x0000);
        EC_WRITE_S8(domain_pd + off_mode, 8);
        EC_WRITE_S32(domain_pd + off_target_position, hold_position);
        EC_WRITE_U16(domain_pd + off_touch_probe_function, 0x0000);

        ecrt_domain_queue(domain);
        ecrt_master_send(master);

        if (cycles % 1000U == 0) {
            ec_master_state_t master_state;
            ec_domain_state_t domain_state;
            ec_slave_config_state_t slave_state;

            ecrt_master_state(master, &master_state);
            ecrt_domain_state(domain, &domain_state);
            ecrt_slave_config_state(sc, &slave_state);

            printf("t=%us al=0x%02x online=%u op=%u wc=%u wc_state=%u status=0x%04x err=0x%04x mode=%d pos=%d follow=%d\n",
                   cycles / 1000U,
                   slave_state.al_state,
                   slave_state.online,
                   slave_state.operational,
                   domain_state.working_counter,
                   domain_state.wc_state,
                   statusword,
                   error_code,
                   mode_display,
                   actual_position,
                   following_error);
        }

        cycles++;
        sleep_until_next(&wake_time);
    }

    ecrt_release_master(master);
    return 0;
}
