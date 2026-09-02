#!/usr/bin/env python3

"""
Feature dispatch registry for axis command routing.

This module keeps external command semantics unchanged, while making
mode-feature routing explicit and modular.
"""

from feature_contract import FeatureContext, ProtocolAdapter
from feature_anti_sway_position import handle_axis_command as handle_anti_sway_position_command
from feature_gear_cam import handle_axis_command as handle_gear_cam_command
from feature_homing import handle_axis_command as handle_homing_command
from feature_multi_point import handle_axis_command as handle_multi_point_command
from feature_multi_point import guarded_axis_command
from feature_position import handle_axis_command as handle_position_command
from feature_registry import FEATURE_REGISTRY
from feature_torque import handle_axis_command as handle_torque_command
from feature_velocity import handle_axis_command as handle_velocity_command


FEATURE_HANDLERS = {
    "position": handle_position_command,
    "anti_sway_position": handle_anti_sway_position_command,
    "incremental": handle_position_command,
    "multi_point": handle_multi_point_command,
    "homing": handle_homing_command,
    "velocity": handle_velocity_command,
    "torque": handle_torque_command,
    "gear_cam": handle_gear_cam_command,
}


def feature_key_from_payload(payload):
    cmd = str(payload.get("cmd", "")).strip().lower()
    if cmd == "set_mode":
        mode = str(payload.get("mode", "")).strip().lower()
        for key, spec in FEATURE_REGISTRY.items():
            if mode in set(spec.get("modes", set())):
                return key
        return "default"
    for key, spec in FEATURE_REGISTRY.items():
        commands = set(spec.get("commands", set()))
        if cmd in commands:
            return key
    return "default"


def dispatch_axis_command(device, payload, transport_fn, adapter=None, enabled_feature_keys=None):
    feature_key = feature_key_from_payload(payload)
    handler = FEATURE_HANDLERS.get(feature_key)
    enabled = set(enabled_feature_keys or [])
    if handler is not None and feature_key not in enabled:
        return {
            "ok": False,
            "error": f"feature_not_loaded:{feature_key}",
            "feature": feature_key,
            "enabled_features": sorted(enabled),
        }
    if adapter is None:
        adapter = ProtocolAdapter()
    ctx = FeatureContext(device=device, payload=payload, transport_fn=transport_fn, adapter=adapter)
    return guarded_axis_command(
        device, payload, transport_fn,
        lambda: handler(ctx) if handler is not None else ctx.run_transport(),
    )
