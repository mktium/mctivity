#!/usr/bin/env python3

import math

"""
Anti-sway positioning feature handler.

The feature module normalizes HMI inputs and prepares an executable run
request. Motion transport is still orchestrated by the host HMI layer so
the feature remains protocol-neutral and easy to assemble.
"""


def _finite_float(value, default=0.0):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number


def _normalize_algorithm(value):
    key = str(value or "").strip().lower()
    if key in ("zvd_terminal", "terminal", "endpoint", "endpoint_zvd"):
        return "zvd_terminal"
    return "zvd_continuous"


def _zvd_plan(rod_length_mm, algorithm="ZVD", natural_period_s=None):
    algorithm_key = _normalize_algorithm(algorithm)
    length_m = max(0.001, float(rod_length_mm or 520.0) / 1000.0)
    effective_length_m = length_m * (2.0 / 3.0)
    calculated_period_s = 2.0 * math.pi * math.sqrt(effective_length_m / 9.80665)
    natural_period_s = _finite_float(natural_period_s, calculated_period_s)
    if natural_period_s is None:
        natural_period_s = calculated_period_s
    natural_period_s = max(0.05, min(10.0, natural_period_s))
    terminal = algorithm_key == "zvd_terminal"
    impulses = (
        []
        if terminal
        else [
            {"time_s": 0.0, "amplitude": 0.25},
            {"time_s": natural_period_s / 2.0, "amplitude": 0.5},
            {"time_s": natural_period_s, "amplitude": 0.25},
        ]
    )
    return {
        "version": "anti_sway_plan.v1",
        "algorithm": algorithm_key,
        "model": (
            "period_matched_crane_endpoint_open_loop"
            if terminal
            else "uniform_rod_physical_pendulum_open_loop_zvd"
        ),
        "mode": "terminal_endpoint" if terminal else "full_path",
        "base_profile": "period_matched_trapezoid" if terminal else "smoothstep5",
        "rod_length_mm": float(rod_length_mm or 520.0),
        "effective_pendulum_length_mm": effective_length_m * 1000.0,
        "natural_period_s": natural_period_s,
        "calculated_period_s": calculated_period_s,
        "period_source": "measured" if abs(natural_period_s - calculated_period_s) > 1e-6 else "calculated",
        "shaper_delay_s": 0.0 if terminal else natural_period_s,
        "impulses": impulses,
        "preview_only": True,
        "motion_connected": False,
    }


def _bool_payload(value, default=True):
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() not in ("0", "false", "no", "off")
    return bool(value)


def _execution_segments(current_counts, target_counts, plan):
    delta = int(target_counts) - int(current_counts)
    impulses = list(plan.get("impulses") or [])
    if not impulses:
        if plan.get("mode") == "terminal_endpoint":
            return [
                {
                    "index": 1,
                    "weight": 1.0,
                    "impulse_time_s": 0.0,
                    "start_counts": int(current_counts),
                    "target_counts": int(target_counts),
                    "delta_counts": delta,
                }
            ]
        impulses = [
            {"time_s": 0.0, "amplitude": 0.25},
            {"time_s": 0.0, "amplitude": 0.5},
            {"time_s": 0.0, "amplitude": 0.25},
        ]
    segments = []
    previous = int(current_counts)
    cumulative = 0.0
    for index, impulse in enumerate(impulses, start=1):
        cumulative += float(impulse.get("amplitude") or 0.0)
        if index == len(impulses):
            absolute = int(target_counts)
        else:
            absolute = int(round(int(current_counts) + delta * cumulative))
        segments.append(
            {
                "index": index,
                "weight": float(impulse.get("amplitude") or 0.0),
                "impulse_time_s": float(impulse.get("time_s") or 0.0),
                "start_counts": previous,
                "target_counts": absolute,
                "delta_counts": absolute - previous,
            }
        )
        previous = absolute
    return segments


def _continuous_curve_command(request, plan):
    terminal = plan.get("algorithm") == "zvd_terminal"
    return {
        "cmd": "terminal_anti_sway_curve_abs" if terminal else "anti_sway_curve_abs",
        "target_counts": int(request["target_counts"]),
        "speed_rpm": int(request["speed_rpm"]),
        "acceleration_rpm_s": int(request["acceleration_rpm_s"]),
        "natural_period_ms": max(50, int(round(float(plan.get("natural_period_s") or 0.0) * 1000.0))),
    }


def _normalized_request(ctx, payload):
    sensor_axis = str(payload.get("sensor_axis") or "aux_encoder").strip().lower()
    if sensor_axis in ("encoder", "aux", "afm60", "sick_afm60"):
        sensor_axis = "aux_encoder"
    rod_length_mm = _finite_float(payload.get("rod_length_mm"), 520.0)
    natural_period_s = _finite_float(payload.get("natural_period_s"), None)
    algorithm = _normalize_algorithm(payload.get("algorithm"))
    plan = _zvd_plan(rod_length_mm, algorithm, natural_period_s)
    command = {
        "current_counts": int(payload.get("current_counts") or 0),
        "target_counts": int(payload.get("target_counts") or 0),
        "speed_rpm": int(payload.get("speed_rpm") or 0),
        "acceleration_rpm_s": int(payload.get("acceleration_rpm_s") or 0),
    }
    return {
        "version": "anti_sway_input.v1",
        "control_axis": ctx.device,
        "sensor_axis": sensor_axis,
        **command,
        "rod_length_mm": rod_length_mm,
        "allowed_angle_deg": _finite_float(payload.get("allowed_angle_deg"), 3.0),
        "natural_period_s": plan["natural_period_s"],
        "algorithm": algorithm,
        "anti_sway_plan": plan,
        "motion_connected": False,
    }


def execution_is_allowed(payload, enabled):
    cmd = str(payload.get("cmd", "")).strip().lower()
    requires_execution = cmd in ("anti_sway_curve_abs", "terminal_anti_sway_curve_abs")
    if cmd == "anti_sway_run":
        requires_execution = not _bool_payload(payload.get("dry_run"), True)
    return not requires_execution or bool(enabled)


def execution_disabled_result():
    return {"ok": False, "error": "anti_sway_execution_disabled",
            "message": "Anti-sway execution is disabled; preview and dry run remain available."}


def handle_axis_command(ctx):
    cmd = ctx.cmd()
    mode = ctx.mode()
    if not execution_is_allowed(ctx.payload, ctx.adapter.anti_sway_execute_enabled):
        return execution_disabled_result()
    if cmd == "set_mode" and mode == "anti_sway_position":
        return ctx.run_transport()
    if cmd in ("anti_sway_input", "anti_sway_run"):
        payload = ctx.payload
        request = _normalized_request(ctx, payload)
        plan = request["anti_sway_plan"]
        if cmd == "anti_sway_run":
            dry_run = _bool_payload(payload.get("dry_run"), True)
            execution_plan = dict(plan)
            execution_plan["preview_only"] = dry_run
            execution_plan["motion_connected"] = not dry_run
            segments = _execution_segments(
                request["current_counts"],
                request["target_counts"],
                execution_plan,
            )
            curve_command = _continuous_curve_command(request, execution_plan)
            return {
                "ok": True,
                "anti_sway_plan": execution_plan,
                "anti_sway_run_request": {
                    "version": "anti_sway_run_request.v1",
                    "control_axis": ctx.device,
                    "sensor_axis": request["sensor_axis"],
                    "dry_run": dry_run,
                    "status": "prepared" if dry_run else "armed",
                    "execution_strategy": "terminal_endpoint_curve" if execution_plan.get("algorithm") == "zvd_terminal" else "continuous_zvd_curve",
                    "command": {
                        "current_counts": request["current_counts"],
                        "target_counts": request["target_counts"],
                        "speed_rpm": request["speed_rpm"],
                        "acceleration_rpm_s": request["acceleration_rpm_s"],
                    },
                    "segments": segments,
                    "curve_command": curve_command,
                    "anti_sway_plan": execution_plan,
                    "motion_connected": not dry_run,
                },
            }
        return {
            "ok": True,
            "anti_sway_plan": plan,
            "anti_sway_input": request,
        }
    if cmd in ("anti_sway_curve_abs", "terminal_anti_sway_curve_abs"):
        return ctx.run_transport()
    return ctx.run_transport()
