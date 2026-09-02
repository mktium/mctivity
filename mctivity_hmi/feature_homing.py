#!/usr/bin/env python3

"""
Homing feature handler.

The HMI exposes homing as an independent feature. The current implementation
keeps the drive in controller-managed position output, while motiond owns the
homing state machine and zero update.
"""

from feature_contract import motion_not_ready


def handle_axis_command(ctx):
    cmd = ctx.cmd()
    mode = ctx.mode()
    if cmd == "set_mode" and mode == "homing" and ctx.device == "fv3":
        ctx.adapter.fv3_force_csp()
    if cmd == "homing_start_torque":
        ready, message = ctx.adapter.wait_motion_ready(ctx.device)
        if not ready:
            return motion_not_ready(message)
        if ctx.device == "fv3":
            ctx.adapter.fv3_force_csp()
    return ctx.run_transport()
