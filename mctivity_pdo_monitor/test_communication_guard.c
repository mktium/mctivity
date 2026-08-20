#include <assert.h>

#include "communication_guard.h"

int main(void)
{
    assert(!mctivity_dual_comm_loss_latches(0));
    assert(mctivity_dual_comm_loss_latches(1));
    assert(mctivity_dual_comm_loss_latches(2));
    return 0;
}
