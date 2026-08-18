CC ?= gcc
CFLAGS ?= -O2 -Wall -Wextra
ETHERCAT_PREFIX ?= /opt/etherlab
CPPFLAGS ?= -I$(ETHERCAT_PREFIX)/include
LDFLAGS ?= -L$(ETHERCAT_PREFIX)/lib
LDLIBS ?= -lethercat

all: mctivity_pdo_monitor mctivity_enable_hold mctivity_csp_step mctivity_csp_roundtrip mctivity_motiond

mctivity_pdo_monitor: mctivity_pdo_monitor.c
	$(CC) $(CPPFLAGS) $(CFLAGS) $(LDFLAGS) -o $@ $< $(LDLIBS)

mctivity_enable_hold: mctivity_enable_hold.c
	$(CC) $(CPPFLAGS) $(CFLAGS) $(LDFLAGS) -o $@ $< $(LDLIBS)

mctivity_csp_step: mctivity_csp_step.c
	$(CC) $(CPPFLAGS) $(CFLAGS) $(LDFLAGS) -o $@ $< $(LDLIBS)

mctivity_csp_roundtrip: mctivity_csp_roundtrip.c
	$(CC) $(CPPFLAGS) $(CFLAGS) $(LDFLAGS) -o $@ $< $(LDLIBS)

mctivity_motiond: mctivity_motiond.c
	$(CC) $(CPPFLAGS) $(CFLAGS) $(LDFLAGS) -o $@ $< $(LDLIBS) -lm

clean:
	rm -f mctivity_pdo_monitor mctivity_enable_hold mctivity_csp_step mctivity_csp_roundtrip mctivity_motiond

.PHONY: all clean
