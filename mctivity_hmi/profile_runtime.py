#!/usr/bin/env python3
"""Resolve mctivity profiles and derive axis runtime parameters."""

import json
from pathlib import Path


class ProfileRuntimeError(ValueError):
    pass


def rpm_to_counts_s(rpm, counts_per_rev):
    return int(rpm) * int(counts_per_rev) // 60


def rpm_s_to_counts_s2(rpm_s, counts_per_rev):
    return int(rpm_s) * int(counts_per_rev) // 60


def load_profile(path):
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("modules"), list):
        raise ProfileRuntimeError(f"invalid profile: {path}")
    data = dict(data)
    data["modules"] = [str(item) for item in data["modules"] if isinstance(item, str) and item.strip()]
    data["domains"] = [str(item) for item in data.get("domains", []) if isinstance(item, str)]
    return data


def module_manifest_path(modules_root, module_id):
    return Path(modules_root) / module_id.replace("-", "/") / "module.json"


def load_manifest(modules_root, module_id):
    path = module_manifest_path(modules_root, module_id)
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ProfileRuntimeError(f"invalid module manifest: {path}")
    data = dict(data)
    data.setdefault("id", module_id)
    data.setdefault("requires", [])
    data.setdefault("conflicts", [])
    data.setdefault("capabilities", [])
    return data


def _positive_int(device, key, fallback=None):
    value = device.get(key, fallback)
    try:
        value = int(value)
    except (TypeError, ValueError) as exc:
        raise ProfileRuntimeError(f"invalid axis parameter {key}: {value!r}") from exc
    if value <= 0:
        raise ProfileRuntimeError(f"axis parameter must be positive: {key}={value}")
    return value


def normalize_axis_device(device):
    if not isinstance(device, dict):
        raise ProfileRuntimeError("axis device must be an object")
    result = dict(device)
    counts_per_rev = _positive_int(result, "counts_per_rev")
    default_speed_rpm = _positive_int(result, "default_speed_rpm")
    max_speed_rpm = _positive_int(result, "max_speed_rpm")
    default_accel_rpm_s = _positive_int(result, "default_accel_rpm_s")
    max_accel_rpm_s = _positive_int(result, "max_accel_rpm_s", default_accel_rpm_s)
    default_decel_rpm_s = _positive_int(result, "default_decel_rpm_s", default_accel_rpm_s)
    max_decel_rpm_s = _positive_int(result, "max_decel_rpm_s", max_accel_rpm_s)
    stop_decel_rpm_s = _positive_int(result, "stop_decel_rpm_s", default_decel_rpm_s)
    velocity_step_counts_s = _positive_int(
        result,
        "velocity_step_counts_s",
        result.get("position_step_counts", 1),
    )
    velocity_step_rpm = _positive_int(result, "velocity_step_rpm", 1)

    if default_speed_rpm > max_speed_rpm:
        raise ProfileRuntimeError("default speed exceeds maximum speed")
    if default_accel_rpm_s > max_accel_rpm_s:
        raise ProfileRuntimeError("default acceleration exceeds maximum acceleration")
    if default_decel_rpm_s > max_decel_rpm_s:
        raise ProfileRuntimeError("default deceleration exceeds maximum deceleration")

    derived_default_velocity = rpm_to_counts_s(default_speed_rpm, counts_per_rev)
    derived_max_velocity = rpm_to_counts_s(max_speed_rpm, counts_per_rev)
    if derived_default_velocity > 2_147_483_647 or derived_max_velocity > 2_147_483_647:
        raise ProfileRuntimeError("resolved velocity exceeds signed 32-bit PDO range")
    for key, derived in (
        ("default_velocity_counts_s", derived_default_velocity),
        ("max_velocity_counts_s", derived_max_velocity),
    ):
        if key in result and int(result[key]) != derived:
            raise ProfileRuntimeError(f"{key} does not match rpm/counts_per_rev conversion")
        result[key] = derived

    result["counts_per_rev"] = counts_per_rev
    result["default_speed_rpm"] = default_speed_rpm
    result["max_speed_rpm"] = max_speed_rpm
    result["default_accel_rpm_s"] = default_accel_rpm_s
    result["max_accel_rpm_s"] = max_accel_rpm_s
    result["default_decel_rpm_s"] = default_decel_rpm_s
    result["max_decel_rpm_s"] = max_decel_rpm_s
    result["stop_decel_rpm_s"] = stop_decel_rpm_s
    result["velocity_step_counts_s"] = velocity_step_counts_s
    result["velocity_step_rpm"] = velocity_step_rpm
    result["default_accel_counts_s2"] = rpm_s_to_counts_s2(default_accel_rpm_s, counts_per_rev)
    result["default_decel_counts_s2"] = rpm_s_to_counts_s2(default_decel_rpm_s, counts_per_rev)
    result["stop_decel_counts_s2"] = rpm_s_to_counts_s2(stop_decel_rpm_s, counts_per_rev)
    if max(
        result["default_accel_counts_s2"],
        result["default_decel_counts_s2"],
        result["stop_decel_counts_s2"],
    ) > 4_294_967_295:
        raise ProfileRuntimeError("resolved acceleration/deceleration exceeds unsigned 32-bit SDO range")
    return result


def build_module_runtime(profile_path, modules_root, strict=False):
    try:
        profile = load_profile(profile_path)
    except Exception as exc:
        if strict:
            raise
        return {
            "profile": "fallback-safe",
            "domains": [],
            "modules": [],
            "active_features": [],
            "axis_devices": [],
            "capabilities": [],
            "warnings": [f"profile_load_failed:{profile_path}:{exc}"],
        }

    capabilities = set()
    active_features = []
    warnings = []
    manifests = {}
    axis_devices = []
    for module_id in profile["modules"]:
        try:
            manifest = load_manifest(modules_root, module_id)
        except Exception as exc:
            if strict:
                raise
            warnings.append(f"module_manifest_missing:{module_id}:{exc}")
            continue
        manifests[module_id] = manifest
        active_features.append(module_id)
        if manifest.get("type") == "axis_device":
            try:
                axis_devices.append(normalize_axis_device(manifest.get("device")))
            except Exception as exc:
                if strict:
                    raise
                warnings.append(f"axis_device_invalid:{module_id}:{exc}")
        for capability in manifest.get("capabilities", []):
            if isinstance(capability, str):
                capabilities.add(capability)

    loaded = set(active_features)
    for module_id, manifest in manifests.items():
        for requirement in manifest.get("requires", []):
            if isinstance(requirement, str) and requirement not in loaded:
                message = f"module_missing_requirement:{module_id}:{requirement}"
                if strict:
                    raise ProfileRuntimeError(message)
                warnings.append(message)
        for conflict in manifest.get("conflicts", []):
            if isinstance(conflict, str) and conflict in loaded:
                message = f"module_conflict:{module_id}:{conflict}"
                if strict:
                    raise ProfileRuntimeError(message)
                warnings.append(message)
    if "axis.state.persist" not in capabilities:
        warnings.append("ui_state_persist_disabled")

    return {
        "profile": str(profile.get("profile", Path(profile_path).stem)),
        "domains": profile.get("domains", []),
        "modules": profile["modules"],
        "active_features": active_features,
        "axis_devices": axis_devices,
        "capabilities": sorted(capabilities),
        "warnings": warnings,
    }
