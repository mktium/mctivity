#!/usr/bin/env python3

"""
Gear-cam feature handler.

Phase 2 Step-2 keeps behavior identical by forwarding to transport layer.
"""

from feature_contract import motion_not_ready


def handle_axis_command(ctx):
    cmd = ctx.cmd()
    mode = ctx.mode()
    if cmd == "gear_start":
        ready, message = ctx.adapter.wait_motion_ready(ctx.device)
        if not ready:
            return motion_not_ready(message)
    if ctx.device == "fv3":
        if cmd == "set_mode" and mode == "gear_cam":
            ctx.adapter.fv3_set_mode(mode)
        if cmd in ("gear_config", "gear_start"):
            ctx.adapter.fv3_force_csp()
    return ctx.run_transport()
