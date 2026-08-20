#ifndef MCTIVITY_COMMUNICATION_GUARD_H
#define MCTIVITY_COMMUNICATION_GUARD_H

/* An idle dual-Uservo machine can safely disarm and re-qualify after a
 * transient communication loss.  Once any control session is active, the
 * same loss must latch the fail-closed group interlock immediately. */
static inline int mctivity_dual_comm_loss_latches(int control_active)
{
    return control_active != 0;
}

#endif
