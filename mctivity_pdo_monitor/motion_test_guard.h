#ifndef MCTIVITY_MOTION_TEST_GUARD_H
#define MCTIVITY_MOTION_TEST_GUARD_H

#include <stdio.h>
#include <string.h>

static int acknowledge_motion_test(int *argc, char ***argv)
{
    if (*argc < 2 || strcmp((*argv)[1], "--confirm-motion") != 0) {
        fprintf(stderr,
                "WARNING: This test enables a real drive and may move machinery.\n"
                "Verify the topology, travel clearance, independent limits and emergency stop.\n"
                "It bypasses HMI limits. Do not run alongside the motion daemon.\n"
                "No hardware has been accessed. To proceed, use: %s --confirm-motion [test arguments]\n",
                (*argv)[0]);
        return 0;
    }
    for (int i = 1; i < *argc; ++i) {
        (*argv)[i] = (*argv)[i + 1];
    }
    --*argc;
    return 1;
}

#endif
