#!/usr/bin/env python3

"""
Torque-mode feature handler.

Keeps staged torque behavior explicit in the modular dispatch path.
"""


def handle_axis_command(ctx):
    mode = ctx.mode()
    if ctx.device == "fv3" and ctx.cmd() == "set_mode" and mode == "torque":
        ctx.adapter.fv3_set_mode(mode)
    return ctx.run_transport()
