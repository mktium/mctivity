CC ?= gcc
CFLAGS ?= -O2 -Wall -Wextra
LDLIBS ?= -lethercat

all: mctivity_pdo_monitor mctivity_motiond

motion-test-tools: mctivity_enable_hold mctivity_csp_step mctivity_csp_roundtrip

mctivity_pdo_monitor: mctivity_pdo_monitor.c
	$(CC) $(CFLAGS) -o $@ $< $(LDLIBS)

mctivity_enable_hold: mctivity_enable_hold.c motion_test_guard.h
	$(CC) $(CFLAGS) -o $@ $< $(LDLIBS)

mctivity_csp_step: mctivity_csp_step.c motion_test_guard.h
	$(CC) $(CFLAGS) -o $@ $< $(LDLIBS)

mctivity_csp_roundtrip: mctivity_csp_roundtrip.c motion_test_guard.h
	$(CC) $(CFLAGS) -o $@ $< $(LDLIBS)

mctivity_motiond: mctivity_motiond.c
	$(CC) $(CFLAGS) -o $@ $< $(LDLIBS) -lm

clean:
	rm -f mctivity_pdo_monitor mctivity_enable_hold mctivity_csp_step mctivity_csp_roundtrip mctivity_motiond

.PHONY: all motion-test-tools clean
