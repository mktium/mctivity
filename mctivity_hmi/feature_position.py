#!/usr/bin/env python3

"""
Position-mode feature handler.

Phase 2 Step-2 keeps behavior identical by forwarding to transport layer.
"""

from feature_contract import motion_not_ready


def handle_axis_command(ctx):
    cmd = ctx.cmd()
    mode = ctx.mode()
    if cmd in ("move_abs", "move_rel", "move_curve_rel"):
        ready, message = ctx.adapter.wait_motion_ready(ctx.device)
        if not ready:
            return motion_not_ready(message)
    if ctx.device == "fv3":
        if cmd in ("move_abs", "move_rel"):
            ctx.adapter.apply_fv3_profile(ctx.payload)
        if cmd == "move_curve_rel":
            ctx.adapter.fv3_force_csp()
        if cmd == "set_mode" and mode in ("position", "incremental", "jog", "point", "homing"):
            ctx.adapter.fv3_set_mode(mode)
    return ctx.run_transport()
