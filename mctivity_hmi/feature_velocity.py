#!/usr/bin/env python3

"""
Velocity-mode feature handler.

Phase 2 Step-2 keeps behavior identical by forwarding to transport layer.
"""

from feature_contract import motion_not_ready


def handle_axis_command(ctx):
    cmd = ctx.cmd()
    mode = ctx.mode()
    if cmd == "jog_velocity":
        ready, message = ctx.adapter.wait_motion_ready(ctx.device)
        if not ready:
            return motion_not_ready(message)
    if ctx.device == "fv3" and cmd == "set_mode" and mode == "velocity":
        ctx.adapter.fv3_set_mode(mode)
    return ctx.run_transport()
