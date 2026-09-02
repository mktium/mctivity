#!/usr/bin/env python3
import hmac
import json
import math
import mimetypes
import os
import re
import socket
import subprocess
import threading
import time
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
from web_security import is_loopback_host, validate_web_access
from feature_dispatch import dispatch_axis_command as feature_dispatch_axis_command
from feature_contract import ProtocolAdapter
from feature_registry import (
    describe_feature_assembly,
    get_feature_registry_source,
    get_feature_registry_warnings,
    resolve_enabled_feature_keys,
)


def _env_int(name, default):
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name, default):
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _default_ui_state_path():
    xdg_state = os.environ.get("XDG_STATE_HOME")
    if xdg_state:
        return os.path.join(xdg_state, "mctivity", "mctivity_hmi_state.json")
    return os.path.join(Path.home(), ".local", "state", "mctivity", "mctivity_hmi_state.json")


def _split_host_header(host):
    host = str(host or "").strip()
    if not host:
        return ""
    if host.startswith("["):
        end = host.find("]")
        if end >= 0:
            return host[1:end].lower()
    if host.count(":") == 1:
        return host.rsplit(":", 1)[0].lower()
    return host.strip("[]").lower()


def _port_from_host_header(host):
    host = str(host or "").strip()
    if host.startswith("["):
        end = host.find("]")
        if end >= 0 and len(host) > end + 1 and host[end + 1] == ":":
            return host[end + 2 :]
        return str(WEB_PORT)
    if host.count(":") == 1:
        return host.rsplit(":", 1)[1]
    return str(WEB_PORT)


def _allowed_host_names():
    allowed = {"127.0.0.1", "localhost", "::1"}
    for item in os.environ.get("MCTIVITY_ALLOWED_HOSTS", "").split(","):
        name = item.strip().strip("[]").lower()
        if name:
            allowed.add(name)
    return allowed


def _env_bool(name, default=False):
    raw = os.environ.get(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() not in ("0", "false", "no", "off", "")


MOTIOND_HOST = os.environ.get("MCTIVITY_HOST", "127.0.0.1")
MOTIOND_PORT = _env_int("MCTIVITY_PORT", 10001)
WEB_HOST = os.environ.get("MCTIVITY_WEB_HOST", "127.0.0.1")
WEB_PORT = _env_int("MCTIVITY_WEB_PORT", 2015)
ENABLE_AUX_ENCODER = _env_bool("MCTIVITY_AUX_ENCODER_ENABLED", True)
FV3_SLAVE_POSITION = _env_int("MCTIVITY_FV3_SLAVE_POSITION", 1)
FV3_STATUS_TTL_SEC = _env_float("MCTIVITY_FV3_STATUS_TTL_SEC", 0.5)
MAX_REQUEST_BYTES = max(1024, _env_int("MCTIVITY_MAX_REQUEST_BYTES", 32768))
MAX_JOG_VELOCITY_CPS = max(1, _env_int("MCTIVITY_MAX_JOG_VELOCITY_CPS", 1200000))
MAX_CURVE_VELOCITY_CPS = max(1, _env_int("MCTIVITY_MAX_CURVE_VELOCITY_CPS", 1200000))
MAX_CURVE_ACCEL_COUNTS_S2 = max(1, _env_int("MCTIVITY_MAX_CURVE_ACCEL_COUNTS_S2", 1200000))
MAX_SPEED_RPM = max(1, _env_int("MCTIVITY_MAX_SPEED_RPM", 6000))
MAX_ACCEL_RPM_S = max(1, _env_int("MCTIVITY_MAX_ACCEL_RPM_S", 12000))
MAX_MOVE_MS = max(1, _env_int("MCTIVITY_MAX_MOVE_MS", 60000))
MAX_GEAR_RATIO = max(1, _env_int("MCTIVITY_MAX_GEAR_RATIO", 200))
MAX_TORQUE_PERCENT = max(0, _env_int("MCTIVITY_MAX_TORQUE_PERCENT", 100))
MAX_POINT_TABLE_ROWS = max(1, min(255, _env_int("MCTIVITY_MAX_POINT_TABLE_ROWS", 64)))
MAX_POINT_DWELL_MS = max(0, _env_int("MCTIVITY_MAX_POINT_DWELL_MS", 60000))
MAX_POINT_TABLE_CYCLES = max(1, min(1000, _env_int("MCTIVITY_MAX_POINT_TABLE_CYCLES", 1000)))
MAX_HOMING_SEARCH_COUNTS = max(1, _env_int("MCTIVITY_MAX_HOMING_SEARCH_COUNTS", 8388608 * 30))
MAX_HOMING_TIMEOUT_MS = max(1, _env_int("MCTIVITY_MAX_HOMING_TIMEOUT_MS", 60000))
MAX_HOMING_TORQUE_HOLD_MS = max(1, _env_int("MCTIVITY_MAX_HOMING_TORQUE_HOLD_MS", 1000))
ANTI_SWAY_EXECUTE_ENABLED = _env_bool("MCTIVITY_ANTI_SWAY_EXECUTE_ENABLED", False)
API_TOKEN = os.environ.get("MCTIVITY_API_TOKEN", "").strip()
UI_STATE_PATH = os.environ.get(
    "MCTIVITY_UI_STATE_PATH",
    _default_ui_state_path(),
)
_ui_state_lock = threading.RLock()
MODULES_ROOT = os.environ.get(
    "MCTIVITY_MODULES_ROOT",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "modules"),
)
PROFILE_NAME = os.environ.get("MCTIVITY_PROFILE", "standard")
PROFILE_PATH = os.environ.get(
    "MCTIVITY_PROFILE_PATH",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "profiles", f"{PROFILE_NAME}.json"),
)
ASSETS_ROOT = Path(__file__).with_name("assets")
MOTION_CURVE_EDITOR_ASSET_PATH = Path(__file__).with_name("motion_curve_editor_block.js")

_COMMAND_CAPABILITY = {
    "set_mode": "axis.control.mode.select",
    "enable": "axis.control.enable",
    "disable": "axis.control.enable",
    "stop": "axis.control.stop",
    "fault_reset": "axis.control.fault.reset",
    "reset_fault": "axis.control.fault.reset",
    "set_zero": "axis.control.zero",
    "home": "axis.control.zero",
    "homing_set_current": "axis.mode.homing.execute",
    "homing_start_torque": "axis.mode.homing.execute",
    "homing_stop": "axis.mode.homing.execute",
    "move_abs": "axis.mode.position.execute",
    "move_rel": "axis.mode.position.execute",
    "anti_sway_input": "axis.mode.anti_sway_position.input",
    "anti_sway_run": "axis.mode.anti_sway_position.input",
    "anti_sway_curve_abs": "axis.mode.anti_sway_position.input",
    "terminal_anti_sway_curve_abs": "axis.mode.anti_sway_position.input",
    "move_curve_rel": "axis.mode.incremental.execute",
    "point_table_write": "axis.mode.multi_point.execute",
    "point_table_run": "axis.mode.multi_point.execute",
    "point_table_stop": "axis.mode.multi_point.execute",
    "point_table_clear": "axis.mode.multi_point.execute",
    "point_table_status": "axis.mode.multi_point.execute",
    "jog_velocity": "axis.mode.velocity.execute",
    "torque_cmd": "axis.mode.torque.execute",
    "gear_config": "axis.mode.gear_cam.execute",
    "gear_start": "axis.mode.gear_cam.execute",
    "gear_stop": "axis.mode.gear_cam.execute",
}
_VALID_COMMANDS = set(_COMMAND_CAPABILITY) | {"status"}
_MODE_CAPABILITY = {
    "position": "axis.mode.position.execute",
    "anti_sway_position": "axis.mode.anti_sway_position.input",
    "incremental": "axis.mode.incremental.execute",
    "jog": "axis.mode.jog.execute",
    "point": "axis.mode.point.execute",
    "multi_point": "axis.mode.multi_point.execute",
    "homing": "axis.mode.homing.execute",
    "velocity": "axis.mode.velocity.execute",
    "torque": "axis.mode.torque.execute",
    "gear_cam": "axis.mode.gear_cam.execute",
}
_VALID_MODES = set(_MODE_CAPABILITY)
_COMMAND_FIELD_ORDER = {
    "status": ["cmd", "device"],
    "enable": ["cmd", "device"],
    "disable": ["cmd", "device"],
    "stop": [
        "cmd",
        "device",
        "deceleration_rpm_s",
        "acceleration_rpm_s",
        "deceleration_counts_s2",
        "deceleration",
    ],
    "fault_reset": ["cmd", "device"],
    "reset_fault": ["cmd", "device"],
    "set_zero": ["cmd", "device"],
    "home": ["cmd", "device"],
    "homing_set_current": ["cmd", "device", "position"],
    "homing_start_torque": [
        "cmd",
        "device",
        "direction",
        "speed_rpm",
        "torque_threshold",
        "set_position",
        "backoff_distance",
        "backoff_position",
        "max_distance",
        "acceleration_rpm_s",
        "deceleration_rpm_s",
        "timeout_ms",
        "torque_hold_ms",
    ],
    "homing_stop": ["cmd", "device", "deceleration_rpm_s", "deceleration_counts_s2", "deceleration"],
    "set_mode": ["cmd", "device", "mode"],
    "move_abs": [
        "cmd",
        "device",
        "pos",
        "move_ms",
        "speed_rpm",
        "acceleration_rpm_s",
        "min_pos",
        "max_pos",
    ],
    "move_rel": [
        "cmd",
        "device",
        "delta",
        "move_ms",
        "speed_rpm",
        "acceleration_rpm_s",
        "min_pos",
        "max_pos",
    ],
    "anti_sway_input": [
        "cmd",
        "device",
        "sensor_axis",
        "current_counts",
        "target_counts",
        "speed_rpm",
        "acceleration_rpm_s",
        "rod_length_mm",
        "natural_period_s",
        "allowed_angle_deg",
        "algorithm",
    ],
    "anti_sway_run": [
        "cmd",
        "device",
        "sensor_axis",
        "current_counts",
        "target_counts",
        "speed_rpm",
        "acceleration_rpm_s",
        "rod_length_mm",
        "natural_period_s",
        "allowed_angle_deg",
        "algorithm",
        "dry_run",
    ],
    "anti_sway_curve_abs": [
        "cmd",
        "device",
        "pos",
        "speed_rpm",
        "acceleration_rpm_s",
        "natural_period_ms",
        "min_pos",
        "max_pos",
    ],
    "terminal_anti_sway_curve_abs": [
        "cmd",
        "device",
        "pos",
        "speed_rpm",
        "acceleration_rpm_s",
        "natural_period_ms",
        "min_pos",
        "max_pos",
    ],
    "move_curve_rel": [
        "cmd",
        "device",
        "target_delta_counts",
        "vmax_counts_s",
        "accel_counts_s2",
        "decel_counts_s2",
        "dwell_ms",
        "blend",
        "min_pos",
        "max_pos",
    ],
    "point_table_write": [
        "cmd",
        "device",
        "start",
        "step",
        "cycle_count",
        "rows",
    ],
    "point_table_run": [
        "cmd",
        "device",
        "cycle_count",
    ],
    "point_table_stop": ["cmd", "device"],
    "point_table_clear": ["cmd", "device"],
    "point_table_status": ["cmd", "device"],
    "jog_velocity": ["cmd", "device", "velocity"],
    "torque_cmd": ["cmd", "device", "torque"],
    "gear_config": [
        "cmd",
        "device",
        "master",
        "master_axis",
        "master_ratio",
        "slave_ratio",
        "gear_master_ratio",
        "gear_slave_ratio",
    ],
    "gear_start": [
        "cmd",
        "device",
        "master",
        "master_axis",
        "master_ratio",
        "slave_ratio",
        "gear_master_ratio",
        "gear_slave_ratio",
    ],
    "gear_stop": ["cmd", "device"],
}
_INT32_MIN = -(2**31)
_INT32_MAX = 2**31 - 1
_REQUIRED_INT_FIELDS = {
    "move_abs": ["pos"],
    "move_rel": ["delta"],
    "move_curve_rel": ["target_delta_counts", "vmax_counts_s", "accel_counts_s2", "decel_counts_s2"],
    "jog_velocity": ["velocity"],
    "torque_cmd": ["torque"],
    "homing_set_current": ["position"],
    "homing_start_torque": ["direction", "speed_rpm", "torque_threshold", "set_position", "max_distance"],
    "anti_sway_curve_abs": ["pos", "speed_rpm", "acceleration_rpm_s", "natural_period_ms"],
    "terminal_anti_sway_curve_abs": ["pos", "speed_rpm", "acceleration_rpm_s", "natural_period_ms"],
}
_OPTIONAL_INT_FIELDS = {
    "stop": ["deceleration_rpm_s", "acceleration_rpm_s", "deceleration_counts_s2", "deceleration"],
    "homing_stop": ["deceleration_rpm_s", "deceleration_counts_s2", "deceleration"],
    "homing_start_torque": ["backoff_distance", "backoff_position", "acceleration_rpm_s", "deceleration_rpm_s", "timeout_ms", "torque_hold_ms"],
    "move_abs": ["move_ms", "speed_rpm", "acceleration_rpm_s", "min_pos", "max_pos"],
    "move_rel": ["move_ms", "speed_rpm", "acceleration_rpm_s", "min_pos", "max_pos"],
    "anti_sway_input": ["speed_rpm", "acceleration_rpm_s"],
    "anti_sway_curve_abs": ["min_pos", "max_pos"],
    "terminal_anti_sway_curve_abs": ["min_pos", "max_pos"],
    "move_curve_rel": ["dwell_ms", "min_pos", "max_pos"],
    "gear_config": ["master_ratio", "slave_ratio", "gear_master_ratio", "gear_slave_ratio"],
    "gear_start": ["master_ratio", "slave_ratio", "gear_master_ratio", "gear_slave_ratio"],
}
_NONNEGATIVE_INT_FIELDS = {
    "deceleration_rpm_s",
    "acceleration_rpm_s",
    "deceleration_counts_s2",
    "deceleration",
    "move_ms",
    "speed_rpm",
    "acceleration_rpm_s",
    "dwell_ms",
    "deceleration_rpm_s",
    "backoff_distance",
    "timeout_ms",
    "torque_hold_ms",
}
_POSITIVE_INT_FIELDS = {
    "vmax_counts_s",
    "accel_counts_s2",
    "decel_counts_s2",
    "master_ratio",
    "slave_ratio",
    "gear_master_ratio",
    "gear_slave_ratio",
    "max_distance",
    "natural_period_ms",
}
_MODE_HMI_MODULE = {
    "position": "feature-hmi-single-point",
    "anti_sway_position": "feature-hmi-anti-sway-position",
    "incremental": "feature-hmi-incremental",
    "jog": "feature-hmi-jog",
    "point": "feature-hmi-point",
    "multi_point": "feature-hmi-multi-point",
    "homing": "feature-hmi-homing",
    "velocity": "feature-hmi-velocity",
    "torque": "feature-hmi-torque",
    "gear_cam": "feature-hmi-electronic-gear",
}
_DEVICE_CAPABILITY = {
    "fv3": "axis.device.fv3.access",
    "aux_encoder": "axis.device.aux_encoder.access",
}
_DEFAULT_CAPABILITIES = [
    "axis.feedback.view",
    "axis.control.mode.select",
    "axis.control.enable",
    "axis.control.stop",
    "axis.control.fault.reset",
    "axis.control.zero",
    "axis.mode.position.execute",
    "axis.mode.anti_sway_position.input",
    "axis.mode.incremental.execute",
    "axis.mode.jog.execute",
    "axis.mode.point.execute",
    "axis.mode.multi_point.execute",
    "axis.mode.homing.execute",
    "axis.mode.velocity.execute",
    "axis.mode.torque.execute",
    "axis.mode.gear_cam.execute",
    "axis.state.persist",
]


def _load_profile(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    modules = data.get("modules")
    if not isinstance(modules, list):
        return None
    data["modules"] = [str(m) for m in modules if isinstance(m, str) and m.strip()]
    data["domains"] = [str(d) for d in data.get("domains", []) if isinstance(d, str)]
    return data


def _module_manifest_path(module_id):
    return Path(MODULES_ROOT) / module_id.replace("-", "/") / "module.json"


def _load_manifest(module_id):
    path = _module_manifest_path(module_id)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    data.setdefault("id", module_id)
    data.setdefault("requires", [])
    data.setdefault("conflicts", [])
    data.setdefault("capabilities", [])
    return data


def _build_module_runtime():
    profile = _load_profile(PROFILE_PATH)
    if not profile:
        return {
            "profile": "fallback-safe",
            "domains": [],
            "modules": [],
            "active_features": [],
            "capabilities": [],
            "warnings": [f"profile_load_failed:{PROFILE_PATH}"],
        }
    warnings = []
    manifests = {}
    for module_id in profile["modules"]:
        if module_id == "feature-logic-aux-encoder" and not ENABLE_AUX_ENCODER:
            continue
        manifest = _load_manifest(module_id)
        if not manifest:
            warnings.append(f"module_manifest_missing:{module_id}")
            continue
        manifests[module_id] = manifest
    # Resolve dependencies fail-closed so a partially assembled feature cannot
    # remain visible merely because its manifest was requested by the profile.
    loaded = set(manifests)
    reported_missing = set()
    while True:
        invalid = set()
        for module_id in loaded:
            manifest = manifests[module_id]
            missing = sorted(
                req
                for req in manifest.get("requires", [])
                if isinstance(req, str) and req not in loaded
            )
            if not missing:
                continue
            invalid.add(module_id)
            for req in missing:
                warning = f"module_missing_requirement:{module_id}:{req}"
                if warning not in reported_missing:
                    warnings.append(warning)
                    reported_missing.add(warning)
        if not invalid:
            break
        loaded.difference_update(invalid)

    active_features = [module_id for module_id in profile["modules"] if module_id in loaded]
    capabilities = set()
    for module_id in active_features:
        manifest = manifests[module_id]
        for cap in manifest.get("capabilities", []):
            if isinstance(cap, str):
                capabilities.add(cap)
        for conf in manifest.get("conflicts", []):
            if isinstance(conf, str) and conf in loaded:
                warnings.append(f"module_conflict:{module_id}:{conf}")
    if "axis.state.persist" not in capabilities:
        warnings.append("ui_state_persist_disabled")
    return {
        "profile": str(profile.get("profile", PROFILE_NAME)),
        "domains": profile.get("domains", []),
        "modules": profile["modules"],
        "active_features": active_features,
        "capabilities": sorted(capabilities),
        "warnings": warnings,
    }


_MODULE_RUNTIME = _build_module_runtime()
_MODULE_RUNTIME["warnings"] = list(_MODULE_RUNTIME.get("warnings", [])) + get_feature_registry_warnings()
_CAPABILITY_SET = set(_MODULE_RUNTIME.get("capabilities", []))
_ENABLED_FEATURE_KEYS = resolve_enabled_feature_keys(_MODULE_RUNTIME.get("active_features", []))
_FEATURE_ASSEMBLY = describe_feature_assembly(_MODULE_RUNTIME.get("active_features", []))


def capability_manifest():
    return {
        "ok": True,
        "profile": _MODULE_RUNTIME.get("profile"),
        "domains": _MODULE_RUNTIME.get("domains", []),
        "active_features": _MODULE_RUNTIME.get("active_features", []),
        "capabilities": _MODULE_RUNTIME.get("capabilities", []),
        "warnings": _MODULE_RUNTIME.get("warnings", []),
        "enabled_feature_keys": sorted(_ENABLED_FEATURE_KEYS),
        "feature_assembly": _FEATURE_ASSEMBLY,
        "feature_registry_source": get_feature_registry_source(),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "mode_capability_map": _MODE_CAPABILITY,
        "mode_hmi_module_map": _MODE_HMI_MODULE,
        "device_capability_map": _DEVICE_CAPABILITY,
        "anti_sway_execution": {
            "enabled": ANTI_SWAY_EXECUTE_ENABLED,
            "limit_mode": "transmission_soft_limits",
            "strategy": "continuous_zvd_curve",
            "strategies": ["continuous_zvd_curve", "terminal_endpoint_curve"],
        },
    }


def _command_required_capability(payload):
    cmd = str(payload.get("cmd", "")).strip().lower()
    if not cmd or cmd == "status":
        return None
    if cmd == "set_mode":
        mode_name = str(payload.get("mode", "")).strip().lower()
        return _MODE_CAPABILITY.get(mode_name) or _COMMAND_CAPABILITY.get(cmd)
    return _COMMAND_CAPABILITY.get(cmd)


def _command_is_enabled(payload):
    required = _command_required_capability(payload)
    if not required:
        return True, None
    if required in _CAPABILITY_SET:
        return True, required
    return False, required


def _normalize_command_name(payload):
    cmd = str(payload.get("cmd", "")).strip().lower()
    if cmd in _VALID_COMMANDS:
        return cmd
    return None


def _strict_int(value, min_value=_INT32_MIN, max_value=_INT32_MAX):
    if isinstance(value, bool):
        raise ValueError("boolean is not an integer")
    if isinstance(value, int):
        number = value
    elif isinstance(value, float) and value.is_integer():
        number = int(value)
    elif isinstance(value, str) and re.fullmatch(r"[+-]?\d+", value.strip()):
        number = int(value.strip())
    else:
        raise ValueError("invalid integer")
    if number < min_value or number > max_value:
        raise ValueError("integer out of range")
    return number


def _validate_command_numbers(cmd, clean):
    try:
        for key in _REQUIRED_INT_FIELDS.get(cmd, []):
            if key not in clean:
                return False
            clean[key] = _strict_int(clean[key])
        for key in _OPTIONAL_INT_FIELDS.get(cmd, []):
            if key in clean:
                clean[key] = _strict_int(clean[key])
    except ValueError:
        return False
    for key in _NONNEGATIVE_INT_FIELDS:
        if key in clean and clean[key] < 0:
            return False
    for key in _POSITIVE_INT_FIELDS:
        if key in clean and clean[key] <= 0:
            return False
    if "min_pos" in clean and "max_pos" in clean and clean["min_pos"] > clean["max_pos"]:
        return False
    if "velocity" in clean and abs(clean["velocity"]) > MAX_JOG_VELOCITY_CPS:
        return False
    if "vmax_counts_s" in clean and clean["vmax_counts_s"] > MAX_CURVE_VELOCITY_CPS:
        return False
    for key in ("accel_counts_s2", "decel_counts_s2", "deceleration_counts_s2"):
        if key in clean and clean[key] > MAX_CURVE_ACCEL_COUNTS_S2:
            return False
    if "speed_rpm" in clean and clean["speed_rpm"] > MAX_SPEED_RPM:
        return False
    for key in ("acceleration_rpm_s", "deceleration_rpm_s", "deceleration"):
        if key in clean and clean[key] > MAX_ACCEL_RPM_S:
            return False
    if "move_ms" in clean and clean["move_ms"] > MAX_MOVE_MS:
        return False
    if "torque" in clean and abs(clean["torque"]) > MAX_TORQUE_PERCENT:
        return False
    if "torque_threshold" in clean and (clean["torque_threshold"] <= 0 or clean["torque_threshold"] > MAX_TORQUE_PERCENT):
        return False
    if cmd == "homing_start_torque":
        if clean.get("direction") not in (-1, 1):
            return False
        if clean.get("speed_rpm", 0) <= 0:
            return False
        if clean.get("max_distance", 0) > MAX_HOMING_SEARCH_COUNTS:
            return False
        if clean.get("backoff_distance", 0) > MAX_HOMING_SEARCH_COUNTS:
            return False
        if clean.get("timeout_ms", 0) > MAX_HOMING_TIMEOUT_MS:
            return False
        if clean.get("torque_hold_ms", 0) > MAX_HOMING_TORQUE_HOLD_MS:
            return False
    if cmd in ("anti_sway_curve_abs", "terminal_anti_sway_curve_abs"):
        if clean.get("speed_rpm", 0) <= 0 or clean.get("acceleration_rpm_s", 0) <= 0:
            return False
        if clean.get("natural_period_ms", 0) < 50 or clean.get("natural_period_ms", 0) > 10000:
            return False
    for key in ("master_ratio", "slave_ratio", "gear_master_ratio", "gear_slave_ratio"):
        if key in clean and clean[key] > MAX_GEAR_RATIO:
            return False
    return True


def _sanitize_loop_mode(value):
    loop_mode = str(value or "single").strip().lower()
    if loop_mode not in ("single", "cycle"):
        return None
    return loop_mode


def _sanitize_point_table_cycle_count(payload, default=1):
    if "cycle_count" in payload:
        try:
            return _strict_int(payload.get("cycle_count"), 1, MAX_POINT_TABLE_CYCLES)
        except ValueError:
            return None
    if "loop_mode" in payload:
        loop_mode = _sanitize_loop_mode(payload.get("loop_mode"))
        if loop_mode is None:
            return None
        return MAX_POINT_TABLE_CYCLES if loop_mode == "cycle" else 1
    try:
        return _strict_int(default, 1, MAX_POINT_TABLE_CYCLES)
    except ValueError:
        return None


def _sanitize_point_table_rows(rows):
    if not isinstance(rows, list) or not rows or len(rows) > MAX_POINT_TABLE_ROWS:
        return None
    clean_rows = []
    used_rows = set()
    try:
        for item in rows:
            if not isinstance(item, dict):
                return None
            row_no = _strict_int(item.get("row"), 1, 255)
            if row_no in used_rows:
                return None
            used_rows.add(row_no)
            pos = _strict_int(item.get("pos"))
            speed_rpm = _strict_int(item.get("speed_rpm"), 1, MAX_SPEED_RPM)
            acceleration_rpm_s = _strict_int(item.get("acceleration_rpm_s"), 0, MAX_ACCEL_RPM_S)
            dwell_ms = _strict_int(item.get("dwell_ms", 0), 0, MAX_POINT_DWELL_MS)
            clean_row = {
                "row": row_no,
                "pos": pos,
                "speed_rpm": speed_rpm,
                "acceleration_rpm_s": acceleration_rpm_s,
                "dwell_ms": dwell_ms,
                "enabled": bool(item.get("enabled", True)),
            }
            if "move_ms" in item:
                clean_row["move_ms"] = _strict_int(item["move_ms"], 1, MAX_MOVE_MS)
            if "min_pos" in item or "max_pos" in item:
                if "min_pos" not in item or "max_pos" not in item:
                    return None
                min_pos = _strict_int(item["min_pos"])
                max_pos = _strict_int(item["max_pos"])
                if min_pos > max_pos:
                    return None
                clean_row["min_pos"] = min_pos
                clean_row["max_pos"] = max_pos
            clean_rows.append(clean_row)
    except ValueError:
        return None
    clean_rows.sort(key=lambda row: row["row"])
    return clean_rows


def _sanitize_point_table_payload(cmd, payload, clean):
    try:
        if cmd == "point_table_write":
            rows = _sanitize_point_table_rows(payload.get("rows"))
            if rows is None:
                return None
            clean["rows"] = rows
            start = _strict_int(payload.get("start", rows[0]["row"]), 1, 255)
            step = _strict_int(payload.get("step", len(rows)), 1, MAX_POINT_TABLE_ROWS)
            if step > 255 - start + 1:
                return None
            cycle_count = _sanitize_point_table_cycle_count(payload, 1)
            if cycle_count is None:
                return None
            clean["start"] = start
            clean["step"] = step
            clean["cycle_count"] = cycle_count
            return clean
        if cmd == "point_table_run":
            if "cycle_count" in payload or "loop_mode" in payload:
                cycle_count = _sanitize_point_table_cycle_count(payload, 1)
                if cycle_count is None:
                    return None
                clean["cycle_count"] = cycle_count
            return clean
        if cmd in ("point_table_stop", "point_table_clear", "point_table_status"):
            return clean
    except ValueError:
        return None
    return None


def _sanitize_anti_sway_input_payload(payload, device, clean):
    sensor_axis = str(payload.get("sensor_axis") or payload.get("sensorAxis") or "aux_encoder").strip().lower()
    if sensor_axis in ("encoder", "aux", "afm60", "sick_afm60"):
        sensor_axis = "aux_encoder"
    if sensor_axis not in ("mctivity", "fv3", "aux_encoder") or sensor_axis == device:
        return None
    if _DEVICE_CAPABILITY.get(sensor_axis) and _DEVICE_CAPABILITY[sensor_axis] not in _CAPABILITY_SET:
        return None
    try:
        target_counts = _strict_int(payload.get("target_counts", 0))
        speed_rpm = _strict_int(payload.get("speed_rpm", 0), 0, MAX_SPEED_RPM)
        acceleration_rpm_s = _strict_int(payload.get("acceleration_rpm_s", 0), 0, MAX_ACCEL_RPM_S)
    except ValueError:
        return None
    rod_length = _finite_float(payload.get("rod_length_mm"))
    natural_period = _finite_float(payload.get("natural_period_s"))
    allowed_angle = _finite_float(payload.get("allowed_angle_deg"))
    if rod_length is None:
        rod_length = 520.0
    if allowed_angle is None:
        allowed_angle = 3.0
    rod_length = max(1.0, min(100000.0, rod_length))
    if natural_period is not None:
        natural_period = max(0.05, min(10.0, natural_period))
    allowed_angle = max(0.1, min(45.0, allowed_angle))
    algorithm = str(payload.get("algorithm") or "ZVD").strip()[:64] or "ZVD"
    clean.update(
        {
            "sensor_axis": sensor_axis,
            "target_counts": target_counts,
            "speed_rpm": speed_rpm,
            "acceleration_rpm_s": acceleration_rpm_s,
            "rod_length_mm": rod_length,
            "natural_period_s": natural_period,
            "allowed_angle_deg": allowed_angle,
            "algorithm": algorithm,
        }
    )
    if clean.get("cmd") == "anti_sway_run":
        try:
            current_counts = _strict_int(payload.get("current_counts", target_counts))
        except ValueError:
            return None
        clean["current_counts"] = current_counts
        dry_run = payload.get("dry_run", True)
        if isinstance(dry_run, str):
            dry_run = dry_run.strip().lower() not in ("0", "false", "no", "off")
        if not dry_run and not ANTI_SWAY_EXECUTE_ENABLED:
            return None
        clean["dry_run"] = bool(dry_run)
    return clean


def _sanitize_command_payload(payload, device):
    cmd = _normalize_command_name(payload)
    if cmd is None:
        return None
    order = _COMMAND_FIELD_ORDER.get(cmd)
    if not order:
        return None
    clean = {"cmd": cmd, "device": device}
    for key in order:
        if key in ("cmd", "device"):
            continue
        if key in payload:
            clean[key] = payload[key]
    if cmd.startswith("point_table_"):
        return _sanitize_point_table_payload(cmd, payload, clean)
    if cmd in ("anti_sway_input", "anti_sway_run"):
        return _sanitize_anti_sway_input_payload(payload, device, clean)
    if cmd == "set_mode":
        mode_name = str(clean.get("mode", "")).strip().lower()
        if mode_name not in _VALID_MODES:
            return None
        clean["mode"] = mode_name
    if cmd == "move_curve_rel":
        blend = str(clean.get("blend", "smooth")).strip().lower()
        if blend not in ("linear", "smooth", "aggressive"):
            return None
        clean["blend"] = blend
    if cmd in ("gear_config", "gear_start"):
        master = str(clean.get("master", "")).strip().lower()
        if master in ("encoder", "aux", "afm60", "sick_afm60"):
            master = "aux_encoder"
        if master and master not in ("mctivity", "fv3", "aux_encoder", "virtual"):
            return None
        if master:
            clean["master"] = master
        master_axis = str(clean.get("master_axis", "")).strip().lower()
        if master_axis in ("encoder", "aux", "afm60", "sick_afm60"):
            master_axis = "aux_encoder"
        if master_axis and master_axis not in ("mctivity", "fv3", "aux_encoder", "virtual"):
            return None
        if master_axis:
            clean["master_axis"] = master_axis
    if not _validate_command_numbers(cmd, clean):
        return None
    return clean


def _sanitize_rejection_detail(payload, device):
    cmd = str((payload or {}).get("cmd", "")).strip().lower()
    if cmd == "anti_sway_run":
        try:
            target_counts = _strict_int(payload.get("target_counts", 0))
            current_counts = _strict_int(payload.get("current_counts", target_counts))
        except ValueError:
            return {
                "message": "防摇执行参数不可用：当前位置或目标位置不是有效整数。",
                "command": cmd,
            }
        if not payload.get("dry_run", True) and not ANTI_SWAY_EXECUTE_ENABLED:
            return {
                "message": "防摇实跑尚未解锁，当前只能干跑。",
                "command": cmd,
            }
        return {
            "message": "防摇执行参数不可用：请检查摆角检测轴、速度、加速度和目标位置范围。",
            "command": cmd,
        }
    if cmd == "move_abs":
        checks = [
            ("pos", "目标位置"),
            ("move_ms", "运动时间"),
            ("speed_rpm", "速度"),
            ("acceleration_rpm_s", "加速度"),
            ("min_pos", "反向软限位"),
            ("max_pos", "正向软限位"),
        ]
        for key, label in checks:
            if key == "pos" or key in payload:
                try:
                    _strict_int(payload.get(key))
                except ValueError:
                    return {
                        "message": f"绝对定位参数不可用：{label}不是有效整数。",
                        "command": cmd,
                        "field": key,
                        "value": payload.get(key),
                    }
        try:
            clean_pos = _strict_int(payload.get("pos"))
            clean_min = _strict_int(payload.get("min_pos")) if "min_pos" in payload else None
            clean_max = _strict_int(payload.get("max_pos")) if "max_pos" in payload else None
            clean_speed = _strict_int(payload.get("speed_rpm")) if "speed_rpm" in payload else None
            clean_accel = _strict_int(payload.get("acceleration_rpm_s")) if "acceleration_rpm_s" in payload else None
            clean_move_ms = _strict_int(payload.get("move_ms")) if "move_ms" in payload else None
        except ValueError:
            clean_pos = clean_min = clean_max = clean_speed = clean_accel = clean_move_ms = None
        if clean_min is not None and clean_max is not None and clean_min > clean_max:
            return {
                "message": f"绝对定位参数不可用：软限位顺序异常，min_pos={clean_min}，max_pos={clean_max}。",
                "command": cmd,
                "field": "motion_bounds",
                "min_pos": clean_min,
                "max_pos": clean_max,
            }
        if clean_min is not None and clean_max is not None and clean_pos is not None and not (clean_min <= clean_pos <= clean_max):
            return {
                "message": f"绝对定位参数不可用：目标位置 {clean_pos} counts 超出软限位 {clean_min}..{clean_max} counts。",
                "command": cmd,
                "field": "pos",
                "pos": clean_pos,
                "min_pos": clean_min,
                "max_pos": clean_max,
            }
        if clean_speed is not None and (clean_speed <= 0 or clean_speed > MAX_SPEED_RPM):
            return {
                "message": f"绝对定位参数不可用：速度 {clean_speed} rpm 超出 1..{MAX_SPEED_RPM} rpm。",
                "command": cmd,
                "field": "speed_rpm",
                "speed_rpm": clean_speed,
            }
        if clean_accel is not None and (clean_accel <= 0 or clean_accel > MAX_ACCEL_RPM_S):
            return {
                "message": f"绝对定位参数不可用：加速度 {clean_accel} rpm/s 超出 1..{MAX_ACCEL_RPM_S} rpm/s。",
                "command": cmd,
                "field": "acceleration_rpm_s",
                "acceleration_rpm_s": clean_accel,
            }
        if clean_move_ms is not None and (clean_move_ms <= 0 or clean_move_ms > MAX_MOVE_MS):
            return {
                "message": f"绝对定位参数不可用：运动时间 {clean_move_ms} ms 超出 1..{MAX_MOVE_MS} ms。",
                "command": cmd,
                "field": "move_ms",
                "move_ms": clean_move_ms,
            }
        return {
            "message": "绝对定位参数不可用：请检查目标位置、速度、加速度和软限位范围。",
            "command": cmd,
        }
    return {
        "message": "命令参数不可用，请检查数值范围。",
        "command": cmd,
    }


def _normalize_device(raw):
    device = str(raw or "mctivity").strip().lower()
    if device in ("encoder", "aux", "afm60", "sick_afm60"):
        device = "aux_encoder"
    if device not in ("mctivity", "fv3", "aux_encoder"):
        return None
    required = _DEVICE_CAPABILITY.get(device)
    if required and required not in _CAPABILITY_SET:
        return None
    return device

HTML = r"""
<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover, user-scalable=no, maximum-scale=1" />
<meta name="apple-mobile-web-app-capable" content="yes" />
<meta name="apple-mobile-web-app-title" content="Motion HMI" />
<meta name="apple-mobile-web-app-status-bar-style" content="default" />
<meta name="mobile-web-app-capable" content="yes" />
<title>多轴控制</title>
<style>
:root {
  --abs-thumb-size:36px;
  --theme-blue:#2A83B7; --theme-deep:#1A69A5; --ink:#111; --dark:#404040;
  --mid:#A6A6A6; --light:#D9D9D9; --paper:#fff; --soft:#F7FAFC;
  --ok:#16864A; --warn:#C77600; --bad:#BA1A1A; --line:rgba(42,131,183,.25);
  --shadow:0 10px 26px rgba(26,105,165,.10);
}
* { box-sizing:border-box; -webkit-tap-highlight-color:transparent; }
html, body { width:100%; height:100%; overflow:hidden; overscroll-behavior:none; position:fixed; inset:0; touch-action:none; }
body { margin:0; color:var(--ink); font-family:MiSans,"MiSans VF","PingFang SC","Helvetica Neue",Helvetica,Arial,sans-serif; background:linear-gradient(180deg,#fff 0%,#f5f9fc 100%); -webkit-user-select:none; user-select:none; }
main { width:100%; height:100dvh; max-width:1360px; margin:0 auto; padding:8px 14px 12px; display:grid; grid-template-rows:auto auto minmax(0,1fr); gap:7px; }
.topbar { display:grid; grid-template-columns:1fr auto; gap:14px; align-items:end; border-bottom:3px solid var(--theme-blue); padding-bottom:9px; min-height:64px; margin-bottom:14px; }
.topbar-left { display:flex; align-items:center; gap:10px; min-width:0; }
h1 { margin:0; color:var(--theme-deep); font-size:clamp(20px,2.4vw,30px); line-height:1; font-weight:800; letter-spacing:0; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.topbar-right { display:flex; align-items:center; justify-content:flex-end; gap:10px; position:relative; }
.brand-wordmark { color:var(--theme-deep); font-size:19px; line-height:1; font-weight:900; letter-spacing:.08em; white-space:nowrap; text-transform:none; transform:translate(.33em,.33em); }
.logo { width:34px; height:34px; object-fit:contain; object-position:center; flex:0 0 auto; }
.subbar { display:flex; align-items:center; gap:10px; min-height:38px; }
.protocol-chip { display:inline-flex; align-items:center; justify-content:center; min-height:36px; padding:0; border:0; border-radius:0; background:transparent; color:var(--theme-deep); font-size:28px; font-weight:900; letter-spacing:.02em; text-transform:none; white-space:nowrap; }
.tabs { display:flex; flex:0 0 auto; gap:10px; margin:0 0 0 auto; min-height:36px; position:relative; z-index:12; pointer-events:auto; }
.assembly-status { display:flex; align-items:center; gap:8px; margin-left:8px; min-height:30px; min-width:0; flex:1 1 auto; overflow:auto hidden; scrollbar-width:none; }
.assembly-status::-webkit-scrollbar { display:none; }
.assembly-chip { display:inline-flex; align-items:center; min-height:28px; padding:3px 9px; border:1px solid var(--line); border-radius:999px; background:#fff; color:#5c6672; font-size:11px; line-height:1; font-weight:900; white-space:nowrap; }
.assembly-chip strong { color:var(--theme-deep); margin-left:4px; font-weight:900; }
.api-token-input { width:120px; min-height:28px; padding:4px 9px; border:1px solid var(--line); border-radius:999px; background:#fff; color:var(--theme-deep); font-size:11px; font-weight:900; outline:none; }
.api-token-input:focus { border-color:var(--theme-blue); box-shadow:0 0 0 3px rgba(42,131,183,.12); }
.tab-btn { position:relative; z-index:13; min-height:36px; padding:7px 18px; border:1px solid var(--line); border-radius:999px; background:#fff; color:var(--theme-deep); box-shadow:none; pointer-events:auto; }
.tab-btn.active { color:#fff; background:var(--theme-blue); border-color:var(--theme-blue); }
.lang-menu { position:relative; flex:0 0 auto; }
.lang-btn { width:28px; height:28px; min-width:28px; min-height:28px; padding:0; border:0; border-radius:0; background:transparent; color:var(--theme-deep); line-height:1; font-weight:400; box-shadow:none; display:grid; place-items:center; cursor:pointer; }
.lang-btn:hover, .lang-btn[aria-expanded="true"] { transform:none; box-shadow:none; color:var(--theme-blue); background:transparent; }
.menu-lines { display:grid; gap:6px; width:22px; }
.menu-lines span { display:block; height:2px; border-radius:999px; background:currentColor; }
.lang-dropdown { position:absolute; right:0; top:calc(100% + 8px); min-width:132px; padding:6px; display:none; background:rgba(255,255,255,.98); border:1px solid var(--line); border-radius:12px; box-shadow:var(--shadow); z-index:20; }
.lang-dropdown.open { display:grid; gap:4px; }
.lang-option { min-height:36px; padding:8px 12px; border:0; border-radius:9px; background:transparent; color:var(--theme-deep); font:inherit; font-size:13px; font-weight:800; text-align:left; }
.lang-option.active { background:var(--soft); color:var(--theme-blue); }
.lang-option:hover { background:var(--soft); }
.tab-panel { display:none; min-height:0; overflow:hidden; }
.tab-panel.active { display:block; }
.monitor-grid { height:100%; display:grid; grid-template-columns:minmax(320px,1.02fr) minmax(250px,.72fr) minmax(330px,1fr); gap:10px; align-items:start; min-height:0; overflow:hidden; }
.left-stack, .middle-stack, .right-stack { min-height:0; display:grid; gap:9px; align-content:start; }
.right-stack { align-self:stretch; height:100%; }
.card { background:rgba(255,255,255,.96); border:1px solid var(--line); border-radius:12px; padding:10px; box-shadow:var(--shadow); overflow:hidden; }
h2 { margin:0 0 8px; font-size:17px; line-height:1.1; font-weight:900; }
.status { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:7px; }
.tile { border:1px solid rgba(166,166,166,.30); border-radius:9px; padding:7px 9px; background:#fff; min-height:52px; }
.label { display:block; font-size:10px; letter-spacing:.04em; color:#666; margin-bottom:3px; }
.value { font-size:18px; line-height:1.05; font-weight:900; overflow-wrap:anywhere; }
.good { color:var(--ok); } .bad { color:var(--bad); } .info { color:var(--theme-deep); }
.feedback-grid { display:grid; grid-template-columns:1fr 1fr; gap:10px; }
.feedback-card { border:1px solid rgba(166,166,166,.30); border-radius:12px; background:#fff; padding:10px; min-height:172px; display:grid; align-content:start; gap:8px; }
.feedback-card.encoder { grid-column:1 / -1; grid-template-columns:152px minmax(0,1fr); align-items:center; min-width:0; overflow:hidden; }
.feedback-title { color:#555; font-size:13px; line-height:1.1; font-weight:900; text-align:center; }
.encoder-title { grid-column:1; grid-row:1; align-self:end; margin-bottom:-2px; }
.encoder-dial { grid-column:1; grid-row:2; }
.feedback-dial { width:min(100%,144px); aspect-ratio:1; border-radius:50%; position:relative; margin:auto; background:radial-gradient(circle at center,#fff 0 58%,transparent 59% 72%,#fff 73% 100%), repeating-conic-gradient(from 0deg,var(--theme-blue) 0deg 1.1deg,transparent 1.1deg 6deg), radial-gradient(circle at center,#fff 0 57%,#eef6fb 58% 71%,#fff 72% 100%); border:2px solid var(--theme-blue); box-shadow:inset 0 0 0 7px #fff, 0 8px 16px rgba(42,131,183,.12); }
.feedback-dial.small { width:min(100%,120px); }
.feedback-dial.dashboard::after { content:""; position:absolute; inset:-4px; border-radius:50%; background:conic-gradient(from 120deg,#fff 0deg 120deg,transparent 120deg 360deg); pointer-events:none; z-index:0; }
.feedback-hand { position:absolute; left:50%; top:50%; width:4px; height:38%; margin-left:-2px; margin-top:-38%; border-radius:10px; background:var(--bad); transform-origin:50% 100%; transform:rotate(0deg); box-shadow:0 4px 10px rgba(186,26,26,.22); transition:transform .16s linear; z-index:1; }
.feedback-hub { position:absolute; left:50%; top:50%; width:20px; height:20px; margin:-10px 0 0 -10px; border-radius:50%; background:var(--ink); border:5px solid #fff; box-shadow:0 4px 8px rgba(0,0,0,.15); z-index:2; }
.feedback-value { position:absolute; left:0; right:0; bottom:8px; z-index:3; display:grid; justify-items:center; gap:1px; font-variant-numeric:tabular-nums; pointer-events:none; }
.feedback-number { color:var(--theme-deep); font-size:20px; line-height:.95; font-weight:900; }
.feedback-unit { color:#667; font-size:10px; font-weight:900; }
.feedback-tick { position:absolute; z-index:3; color:#5d6c76; font-size:8px; line-height:1; font-weight:900; transform:translate(-50%, -50%); font-variant-numeric:tabular-nums; }
.tick-0 { left:24%; top:66%; } .tick-20 { left:22%; top:39%; } .tick-40 { left:40%; top:22%; } .tick-60 { left:60%; top:22%; } .tick-80 { left:78%; top:39%; } .tick-100 { left:76%; top:66%; }
.tick-1000 { left:27%; top:36%; } .tick-2000 { left:73%; top:36%; } .tick-3000 { left:76%; top:66%; }
.tick-k1 { left:25%; top:34%; } .tick-k2 { left:75%; top:34%; } .tick-k3 { left:78%; top:68%; }
.feedback-metrics { display:grid; grid-template-columns:1fr; gap:7px; align-self:stretch; min-width:0; }
.feedback-metric { border:1px solid rgba(166,166,166,.28); border-radius:9px; background:var(--soft); padding:7px 10px; min-height:39px; display:grid; grid-template-columns:minmax(7.5em,1fr) minmax(11ch,13.5ch); align-items:center; gap:8px; min-width:0; max-width:100%; }
.feedback-card.encoder .feedback-metrics { grid-column:2; grid-row:1 / span 2; }
.feedback-metric .label { font-size:11px; min-width:0; line-height:1.25; word-break:keep-all; text-align:left; }
.feedback-metric .value { color:var(--theme-deep); font-size:18px; font-variant-numeric:tabular-nums; white-space:nowrap; overflow-wrap:normal; text-align:right; justify-self:end; min-width:13.5ch; max-width:100%; }
.feedback-metric.vertical { grid-template-columns:1fr; align-items:start; align-content:center; gap:4px; min-height:62px; }
.feedback-metric.vertical .label { margin:0; }
.feedback-metric.vertical .value { justify-self:stretch; width:100%; min-width:0; text-align:right; }
body.device-aux-encoder .motor-only { display:none !important; }
body.device-aux-encoder .feedback-grid { grid-template-columns:1fr; grid-template-rows:minmax(0,1fr) auto; }
body.device-aux-encoder .speed-feedback-card { grid-column:1 / -1; min-height:148px; }
body.device-aux-encoder #absPos { pointer-events:none; cursor:default; }
body.device-aux-encoder #absPos::-webkit-slider-thumb { opacity:0; }
body.device-aux-encoder #absPos::-moz-range-thumb { opacity:0; }
body.device-aux-encoder .current-position-marker { opacity:1; }
body.device-aux-encoder .controls { grid-template-rows:1fr auto; }
.controls { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px; }
select, input[type=number] { width:100%; min-height:36px; border:1px solid rgba(166,166,166,.45); border-radius:9px; background:#fff; color:var(--ink); padding:7px 9px; font:inherit; font-size:14px; font-weight:800; }
.mode-row { display:grid; grid-template-columns:minmax(88px,1fr) minmax(130px,1.25fr); align-items:center; gap:12px; min-height:70px; padding:10px 12px; margin-bottom:10px; border:1px solid rgba(166,166,166,.30); border-radius:12px; background:#fff; }
.mode-row .label { margin:0; color:var(--ink); font-size:20px; line-height:1; font-weight:900; letter-spacing:0; }
.mode-row select { min-height:42px; font-size:14px; }
body.lang-en .mode-row .label { font-size:16px; }
body.lang-en .label-subtext { font-size:10px; }
.label-stack { display:grid; gap:4px; }
.label-subtext { color:#667; font-size:12px; line-height:1; font-weight:900; }
.transmission-trigger { width:100%; min-height:56px; border:1px solid rgba(42,131,183,.18); border-radius:12px; background:linear-gradient(180deg,#ffffff 0%,#f4f8fb 100%); padding:9px 12px; display:grid; grid-template-columns:minmax(0,1fr) auto; align-items:center; gap:10px; color:var(--ink); box-shadow:none; }
.transmission-trigger:hover { transform:none; box-shadow:none; }
.transmission-copy { min-width:0; display:grid; gap:2px; text-align:left; }
.transmission-primary { color:var(--theme-deep); font-size:13px; line-height:1.1; font-weight:900; font-variant-numeric:tabular-nums; white-space:nowrap; }
.transmission-secondary { color:#677582; font-size:11px; line-height:1.05; font-weight:900; font-variant-numeric:tabular-nums; white-space:nowrap; }
.transmission-chevron { color:var(--theme-blue); font-size:20px; line-height:1; font-weight:900; }
.control-stack { display:grid; gap:8px; }
.control-row { display:grid; grid-template-columns:1fr 1fr; gap:8px; }
.control-row.three { grid-template-columns:1fr 1fr 1fr; }
.control-note { min-height:24px; color:#666; font-size:11px; line-height:1.25; font-weight:700; }
.mode-panel { display:none; }
.mode-panel.active { display:grid; gap:8px; }
.active-mode-card { min-height:0; }
.active-mode-card.incremental-scroll { height:100%; display:grid; grid-template-rows:auto minmax(0,1fr); overflow:hidden; }
.active-mode-card.incremental-scroll #incrementalProfileHost { width:100%; }
.active-mode-card.incremental-scroll .slider-card { overflow:visible; }
.active-mode-card.incremental-scroll .mode-panel.active { min-height:0; align-content:start; overflow:auto; -webkit-overflow-scrolling:touch; touch-action:pan-y; padding-right:6px; }
.active-mode-card.multi-point-scroll { height:100%; display:grid; grid-template-rows:auto minmax(0,1fr); overflow:hidden; }
.active-mode-card.multi-point-scroll .mode-panel.active { min-height:0; grid-template-rows:auto minmax(0,1fr); overflow:hidden; }
.active-mode-card.anti-sway-scroll { height:100%; display:grid; grid-template-rows:auto minmax(0,1fr); overflow:hidden; }
.active-mode-card.anti-sway-scroll .mode-panel.active { min-height:0; align-content:start; overflow:auto; -webkit-overflow-scrolling:touch; touch-action:pan-y; padding-right:6px; }
.config-grid { height:100%; display:grid; grid-template-columns:minmax(0,1.2fr) minmax(300px,.8fr); gap:10px; align-items:start; overflow:hidden; }
.param-grid { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:8px; }
.param-card { border:1px solid rgba(166,166,166,.30); border-radius:10px; padding:10px; background:#fff; display:grid; gap:7px; }
.param-card h3 { margin:0; font-size:14px; line-height:1.1; }
.param-card label { display:grid; gap:4px; color:#666; font-size:11px; font-weight:800; }
button { border:0; border-radius:999px; padding:9px 13px; min-height:40px; font-size:14px; font-weight:900; cursor:pointer; color:#fff; background:var(--dark); box-shadow:0 8px 18px rgba(64,64,64,.12); transition:transform .12s ease, box-shadow .12s ease; touch-action:manipulation; }
button:hover { transform:translateY(-1px); box-shadow:0 12px 24px rgba(64,64,64,.16); }
button.stop { background:var(--warn); } button.blue { background:var(--theme-deep); } button.neutral { background:var(--dark); }
.enable-toggle { grid-column:1 / -1; display:flex; justify-content:space-between; align-items:center; gap:16px; padding:10px 12px; min-height:70px; border-radius:12px; color:var(--ink); background:#fff; border:1px solid rgba(166,166,166,.30); box-shadow:none; text-align:left; }
.enable-toggle:hover { transform:none; box-shadow:none; }
.enable-copy { display:grid; gap:4px; }
.toggle-title { font-size:20px; line-height:1; font-weight:900; }
.toggle-subtitle { color:#777; font-size:13px; line-height:1; font-weight:800; }
.toggle-track { flex:0 0 auto; width:58px; height:34px; border-radius:999px; padding:3px; background:#d5d7dc; box-shadow:inset 0 0 0 1px rgba(17,17,17,.06); transition:background .18s ease; }
.enable-toggle.on .toggle-track { background:var(--theme-blue); }
.enable-toggle.motion-on .toggle-track { background:var(--ok); }
.power-toggle .toggle-track { background:var(--bad); }
.power-toggle.on .toggle-track { background:var(--ok); }
.power-toggle .toggle-subtitle { color:var(--bad); }
.power-toggle.on .toggle-subtitle { color:var(--ok); }
.motion-toggle .toggle-track { background:var(--bad); }
.motion-toggle.motion-on .toggle-track { background:var(--ok); }
.motion-toggle .toggle-subtitle { color:var(--bad); }
.motion-toggle.motion-on .toggle-subtitle { color:var(--ok); animation:motionBlink .7s infinite; }
.toggle-knob { display:block; width:28px; height:28px; border-radius:50%; background:#fff; box-shadow:0 2px 7px rgba(17,17,17,.24); transform:translateX(0); transition:transform .18s cubic-bezier(.2,.8,.2,1); }
.enable-toggle.on .toggle-knob, .enable-toggle.motion-on .toggle-knob { transform:translateX(24px); }
.status-button { flex:0 0 auto; min-width:58px; min-height:36px; padding:0; border-radius:999px; background:var(--ok); color:transparent; text-align:center; font-size:0; line-height:1; box-shadow:0 8px 18px rgba(22,134,74,.18); overflow:hidden; }
.status-button.fault { min-width:68px; padding:0 14px; display:inline-flex; align-items:center; justify-content:center; background:var(--bad); color:#fff; font-size:13px; font-weight:900; box-shadow:0 8px 18px rgba(186,26,26,.20); animation:faultBlink .72s infinite; }
.status-button:disabled { cursor:default; opacity:1; }
.status-button:disabled:hover { transform:none; box-shadow:0 8px 18px rgba(22,134,74,.18); }
.fault-toggle.fault-on .toggle-subtitle { color:var(--bad); animation:faultBlink .72s infinite; }
.fault-summary { flex:1 1 auto; min-width:0; display:grid; gap:4px; justify-items:end; color:#7a7f86; font-size:12px; line-height:1; font-weight:900; font-variant-numeric:tabular-nums; }
.fault-toggle.fault-on .fault-summary { color:var(--bad); }
.fault-code { color:inherit; }
.fault-name { color:inherit; font-size:11px; }
.mock-fault-panel { grid-column:1 / -1; display:grid; gap:7px; padding:10px 12px; border:1px dashed rgba(26,108,168,.35); border-radius:12px; background:#f7fbfe; color:#426072; }
.mock-fault-panel[hidden] { display:none; }
.mock-fault-head { display:flex; align-items:center; justify-content:space-between; gap:10px; font-size:12px; line-height:1.2; font-weight:900; }
.mock-fault-badge { flex:0 0 auto; border-radius:999px; padding:3px 7px; background:#e7f3fb; color:var(--theme-deep); font-size:10px; letter-spacing:.02em; text-transform:uppercase; }
.mock-fault-select { width:100%; min-width:0; border:1px solid rgba(26,108,168,.22); border-radius:9px; padding:8px 9px; background:#fff; color:var(--ink); font-size:12px; font-weight:800; outline:none; }
.mock-fault-select:focus { border-color:rgba(26,108,168,.7); box-shadow:0 0 0 3px rgba(26,108,168,.12); }
.mock-fault-hint { color:#6b7884; font-size:11px; line-height:1.35; font-weight:700; }
@keyframes faultBlink { 0%,100% { opacity:1; } 50% { opacity:.42; } }
@keyframes motionBlink { 0%,100% { opacity:1; } 50% { opacity:.35; } }
.axis-card { display:grid; grid-template-columns:180px minmax(0,1fr); gap:10px; align-items:center; }
.dial { width:180px; height:180px; border-radius:50%; position:relative; margin:auto; background:radial-gradient(circle at center,#fff 0 42%,transparent 43%), repeating-conic-gradient(from 0deg,var(--theme-blue) 0deg 1.1deg,transparent 1.1deg 6deg), radial-gradient(circle at center,#fff 0 57%,#eef6fb 58% 71%,#fff 72% 100%); border:2px solid var(--theme-blue); box-shadow:inset 0 0 0 9px #fff, 0 10px 20px rgba(42,131,183,.12); }
.hand { position:absolute; left:50%; top:50%; width:5px; height:68px; margin-left:-2.5px; margin-top:-68px; border-radius:10px; background:var(--bad); transform-origin:50% 100%; transform:rotate(0deg); box-shadow:0 5px 12px rgba(186,26,26,.22); transition:transform .16s linear; }
.hub { position:absolute; left:50%; top:50%; width:24px; height:24px; margin:-12px 0 0 -12px; border-radius:50%; background:var(--ink); border:6px solid #fff; box-shadow:0 4px 10px rgba(0,0,0,.15); }
.north,.east,.south,.west { position:absolute; font-size:11px; font-weight:900; color:var(--theme-deep); background:#fff; border-radius:999px; padding:2px 5px; }
.north { left:50%; top:22px; transform:translateX(-50%); } .east { right:22px; top:50%; transform:translateY(-50%); } .south { left:50%; bottom:22px; transform:translateX(-50%); } .west { left:22px; top:50%; transform:translateY(-50%); }
.axis-readout { display:grid; grid-template-columns:1fr 1fr; gap:10px; }
.big-angle { grid-column:1 / -1; font-size:54px; line-height:.9; color:var(--theme-deep); font-weight:900; }
.sliders { display:grid; gap:8px; }
		.slider-card { border:1px solid rgba(166,166,166,.30); border-radius:10px; padding:9px; background:#fff; }
		.slider-head { display:flex; justify-content:space-between; gap:10px; align-items:baseline; margin-bottom:4px; }
		.slider-title { font-size:13px; font-weight:900; color:var(--dark); }
		.slider-number { font-size:14px; font-weight:900; color:var(--theme-deep); text-align:right; }
.homing-tabs { display:grid; grid-template-columns:1fr 1fr; gap:6px; padding:3px; border-radius:11px; background:#eef4f8; border:1px solid rgba(42,131,183,.16); }
.homing-tab { min-height:36px; padding:7px 9px; border-radius:8px; background:transparent; color:#5f6d78; box-shadow:none; font-size:13px; line-height:1.1; }
.homing-tab:hover { transform:none; box-shadow:none; }
.homing-tab.active { background:#fff; color:var(--theme-deep); box-shadow:0 6px 14px rgba(42,131,183,.12); }
.homing-method-panel { display:none; }
.homing-method-panel.active { display:grid; gap:10px; }
.homing-card { display:grid; gap:10px; }
.homing-grid { display:grid; grid-template-columns:1fr 1fr; gap:10px; }
.homing-field { display:grid; gap:5px; min-width:0; }
.homing-field > span { color:#66727f; font-size:11px; line-height:1.1; font-weight:900; }
.homing-wide { grid-column:1 / -1; }
	.unit-input { display:grid; grid-template-columns:minmax(0,1fr) auto; align-items:center; gap:6px; }
	.unit-input span { color:#52606d; font-size:12px; line-height:1; font-weight:900; white-space:nowrap; }
	.homing-set-current-row { display:grid; grid-template-columns:minmax(0,1fr) auto; align-items:center; gap:8px; }
	.homing-inline-apply { min-height:36px; padding:7px 14px; border-radius:10px; background:var(--theme-deep); box-shadow:none; white-space:nowrap; font-size:13px; }
	.homing-card.torque-unavailable .homing-torque-only { opacity:.46; pointer-events:none; }
	input[type=range] { width:100%; accent-color:var(--theme-blue); touch-action:pan-x; }
#absPos { appearance:none; -webkit-appearance:none; height:46px; accent-color:var(--warn); background:transparent; cursor:pointer; }
#absPos::-webkit-slider-runnable-track { height:10px; border-radius:999px; border:1px solid rgba(120,130,140,.45); background:linear-gradient(90deg,#b8bec5 0 50%,#fff 50% 100%); box-shadow:inset 0 1px 2px rgba(0,0,0,.12); }
#absPos::-webkit-slider-thumb { -webkit-appearance:none; width:36px; height:36px; margin-top:-14px; border-radius:50%; background:var(--warn); border:4px solid #fff; box-shadow:0 5px 14px rgba(0,0,0,.30); }
#absPos::-moz-range-track { height:9px; border-radius:999px; border:1px solid rgba(120,130,140,.45); background:linear-gradient(90deg,#b8bec5 0 50%,#fff 50% 100%); box-shadow:inset 0 1px 2px rgba(0,0,0,.12); }
#absPos::-moz-range-thumb { width:30px; height:30px; border-radius:50%; background:var(--warn); border:4px solid #fff; box-shadow:0 5px 14px rgba(0,0,0,.30); }
.position-param { --side-width:130px; --position-gap:12px; --position-marker-space:28px; --slider-top-offset:calc(122px + var(--position-marker-space)); --slider-track-height:150px; display:grid; grid-template-columns:minmax(0,1fr) var(--side-width); gap:var(--position-gap); align-items:stretch; }
.axis-control { position:relative; min-height:calc(346px + var(--position-marker-space)); height:100%; display:grid; grid-template-rows:auto auto minmax(150px,1fr); gap:10px; align-content:stretch; }
.axis-control > button.blue { position:relative; top:12px; }
.position-axis { position:relative; z-index:3; width:calc(100% + var(--side-width) + var(--position-gap)); display:grid; grid-template-rows:46px auto; gap:2px; margin-top:var(--position-marker-space); padding:0 0 10px; }
.position-axis.unhomed .current-position-marker { display:none; }
.current-position-marker { position:absolute; left:calc((var(--abs-thumb-size) / 2) + (var(--marker-pct, 0) * (100% - var(--abs-thumb-size)))); top:-28px; transform:translateX(-50%); display:grid; justify-items:center; gap:1px; pointer-events:none; opacity:1; transition:left .16s linear; z-index:4; }
.current-position-marker::after { content:""; width:0; height:0; border-left:10px solid transparent; border-right:10px solid transparent; border-top:14px solid var(--ok); filter:drop-shadow(0 2px 4px rgba(22,134,74,.28)); }
.current-position-value { min-width:8.5ch; padding:2px 6px; border:1px solid rgba(22,134,74,.22); border-radius:999px; background:#fff; color:var(--theme-deep); font-size:12px; line-height:1.1; font-weight:900; font-variant-numeric:tabular-nums; text-align:center; white-space:nowrap; box-shadow:0 2px 6px rgba(22,134,74,.10); }
.axis-scale { display:none; }
.axis-line { display:none; }
.axis-zero { display:none; }
.axis-labels { display:grid; grid-template-columns:1fr auto 1fr; margin:0 0 2px; color:#666; font-size:12px; font-weight:900; }
.axis-labels span:nth-child(2) { color:var(--bad); padding:0 8px; }
.axis-labels span:last-child { text-align:right; }
.target-readout { position:relative; min-height:150px; border:1px solid rgba(42,131,183,.22); border-radius:12px; background:var(--soft); display:grid; grid-template-columns:minmax(0,1fr) minmax(86px,.62fr); align-items:center; gap:12px; padding:12px; text-align:center; }
.target-readout.linear-mode { grid-template-columns:1fr; }
.target-readout.linear-mode .target-cell.secondary { display:none; }
.target-cell { display:grid; gap:5px; min-width:0; }
.target-cell:first-child { transform:translateY(.5em); }
.target-cell:last-child { transform:translateY(.5em); }
.target-cell > div { display:block; width:100%; min-width:0; white-space:nowrap; }
.target-label { color:#667; font-size:12px; line-height:1; font-weight:900; }
.target-number { display:block; width:100%; color:var(--theme-deep); font-size:clamp(41px,5.1vw,68px); line-height:.92; font-weight:900; font-variant-numeric:tabular-nums; text-align:center; }
.target-unit { position:absolute !important; top:12px; right:14px; z-index:3; display:inline-block; color:#667; font-size:15px; line-height:1; font-weight:900; margin:0; white-space:nowrap; transform:none; }
.target-angle { display:inline-block; transform:translateY(-2em); color:var(--theme-deep); font-size:clamp(15px,1.5vw,21px); line-height:.95; font-weight:900; font-variant-numeric:tabular-nums; }
.vertical-sliders { position:relative; z-index:1; display:grid; grid-template-columns:1fr 1fr; gap:8px; min-height:210px; height:calc(100% - var(--slider-top-offset)); align-self:start; margin-top:var(--slider-top-offset); }
.vertical-slider { display:grid; grid-template-rows:auto 1fr auto; gap:6px; justify-items:center; min-width:0; padding:8px 5px; border:1px solid rgba(166,166,166,.30); border-radius:10px; background:#fff; }
.vertical-slider label { color:#666; font-size:11px; line-height:1.1; font-weight:900; text-align:center; }
.vertical-slider input[type=range] { appearance:none; -webkit-appearance:none; width:48px; height:var(--slider-track-height); writing-mode:vertical-lr; direction:rtl; accent-color:var(--theme-blue); background:transparent; cursor:pointer; touch-action:pan-y; }
.vertical-slider input[type=range]::-webkit-slider-runnable-track { width:11px; margin:0 auto; border-radius:999px; border:1px solid rgba(42,131,183,.28); background:#e7f0f6; box-shadow:inset 0 1px 2px rgba(0,0,0,.10); }
.vertical-slider input[type=range]::-webkit-slider-thumb { -webkit-appearance:none; width:34px; height:34px; margin-left:-11.5px; border-radius:50%; background:var(--theme-blue); border:4px solid #fff; box-shadow:0 5px 14px rgba(0,0,0,.28); }
.vertical-slider input[type=range]::-moz-range-track { width:11px; border-radius:999px; border:1px solid rgba(42,131,183,.28); background:#e7f0f6; box-shadow:inset 0 1px 2px rgba(0,0,0,.10); }
.vertical-slider input[type=range]::-moz-range-thumb { width:28px; height:28px; border-radius:50%; background:var(--theme-blue); border:4px solid #fff; box-shadow:0 5px 14px rgba(0,0,0,.28); }
.vertical-slider span { color:var(--theme-deep); font-size:12px; font-weight:900; text-align:center; overflow-wrap:anywhere; }
.gear-panel { display:grid; gap:10px; }
.gear-meta { display:grid; grid-template-columns:1fr 1fr; gap:8px; }
.gear-field { border:1px solid rgba(166,166,166,.30); border-radius:10px; background:#fff; padding:8px; display:grid; gap:6px; }
.gear-field-head { display:flex; align-items:baseline; justify-content:space-between; gap:8px; min-width:0; }
.gear-field .label { margin:0; color:var(--ink); font-size:20px; line-height:1; font-weight:900; letter-spacing:0; }
.gear-axis-unit { color:#52606d; font-size:17px; line-height:1; font-weight:900; letter-spacing:0; white-space:nowrap; }
.gear-field select { min-height:34px; }
.gear-name { color:var(--theme-deep); font-size:20px; line-height:1; font-weight:900; }
.gear-ratio { display:grid; gap:8px; padding-top:2px; }
.gear-ratio .vertical-sliders { margin-top:0; min-height:176px; grid-template-columns:1fr 1fr; }
.gear-ratio .vertical-slider.gear-slider { display:flex; align-items:center; justify-content:center; padding:6px; min-height:186px; }
.gear-wheel { width:min(100%,240px); height:176px; position:relative; overflow:hidden; border-radius:12px; border:1px solid rgba(120,132,146,.45); background:linear-gradient(180deg,#aab2bc 0%,#d7dde4 12%,#f3f5f8 28%,#ffffff 50%,#f3f5f8 72%,#d7dde4 88%,#aab2bc 100%); box-shadow:inset 0 1px 0 rgba(255,255,255,.85); }
.gear-wheel::before { content:""; position:absolute; left:0; right:0; top:50%; height:46px; transform:translateY(-50%); background:linear-gradient(180deg,rgba(255,255,255,.80) 0%,rgba(226,232,238,.86) 50%,rgba(255,255,255,.80) 100%); border-top:1px solid rgba(130,142,156,.45); border-bottom:1px solid rgba(130,142,156,.45); z-index:1; }
.gear-wheel::after { content:""; position:absolute; left:0; right:0; top:0; bottom:0; background:linear-gradient(180deg,rgba(32,40,50,.28) 0%,rgba(255,255,255,0) 22%,rgba(255,255,255,0) 78%,rgba(32,40,50,.28) 100%); pointer-events:none; z-index:1; }
.gear-wheel { touch-action:none; cursor:ns-resize; }
.gear-wheel-values { position:absolute; inset:0; display:grid; grid-template-rows:repeat(5,1fr); align-items:center; justify-items:center; z-index:2; }
.gear-ratio-value { color:#1b2430; font-weight:900; font-variant-numeric:tabular-nums; text-align:center; user-select:none; line-height:1; }
.gear-ratio-value.current { font-size:clamp(38px,4.8vw,64px); font-weight:900; }
.gear-ratio-value.edge { font-size:clamp(24px,3.1vw,34px); opacity:.88; font-weight:900; }
.gear-ratio-value.far { font-size:clamp(18px,2.4vw,26px); opacity:.60; font-weight:900; }
.gear-panel.locked { background:#f8fafc; }
.gear-panel.locked .gear-field select { background:#ecf0f4; color:#7b8692; border-color:rgba(123,134,146,.45); }
.gear-ratio.locked { pointer-events:none; opacity:.72; }
.meta { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:6px; margin:4px 0 7px; color:#666; font-size:11px; }
.meta span { background:var(--soft); border-radius:8px; padding:5px 6px; min-height:24px; }
.point-table { display:grid; gap:10px; }
.point-row { display:grid; grid-template-columns:44px 1fr auto; gap:8px; align-items:center; padding:8px; border:1px solid rgba(166,166,166,.30); border-radius:10px; background:#fff; }
.point-row strong { font-size:17px; }
.point-actions { display:flex; gap:8px; justify-content:flex-end; }
.point-actions button { min-height:34px; padding:7px 12px; font-size:12px; }
.multi-point-card { display:grid; grid-template-rows:auto minmax(0,1fr) auto; gap:10px; height:100%; min-height:0; overflow:hidden; }
.multi-point-strip { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:8px; align-items:end; }
.multi-point-chip { display:grid; gap:4px; min-width:0; }
.multi-point-chip label { font-size:11px; line-height:1; font-weight:900; color:#66727f; }
.multi-point-chip input,
.multi-point-chip select { width:100%; min-height:34px; border:1px solid var(--line); border-radius:8px; padding:6px 8px; color:var(--theme-deep); background:#fff; font-size:13px; font-weight:900; outline:none; }
.multi-point-chip button { width:100%; min-height:34px; padding:7px 10px; font-size:12px; }
.multi-point-axis { min-height:66px; display:grid; grid-template-rows:44px auto; gap:2px; padding:0 0 2px; }
.multi-point-axis .axis-labels { margin:0; }
.multi-point-axis-track { --marker-pct:0; position:relative; min-height:44px; }
.multi-point-axis-track::before { content:""; position:absolute; left:18px; right:18px; top:27px; height:10px; border-radius:999px; border:1px solid rgba(120,130,140,.45); background:linear-gradient(90deg,#b8bec5 0 50%,#fff 50% 100%); box-shadow:inset 0 1px 2px rgba(0,0,0,.12); }
.multi-point-current-marker { position:absolute; left:calc(18px + (var(--marker-pct, 0) * (100% - 36px))); top:0; transform:translateX(-50%); display:grid; justify-items:center; gap:1px; pointer-events:none; transition:left .16s linear; z-index:2; }
.multi-point-current-value { min-width:8.5ch; padding:2px 6px; border:1px solid rgba(22,134,74,.22); border-radius:999px; background:#fff; color:var(--theme-deep); font-size:12px; line-height:1.1; font-weight:900; font-variant-numeric:tabular-nums; text-align:center; white-space:nowrap; box-shadow:0 2px 6px rgba(22,134,74,.10); }
.multi-point-current-marker::after { content:""; width:0; height:0; border-left:10px solid transparent; border-right:10px solid transparent; border-top:14px solid var(--ok); filter:drop-shadow(0 2px 4px rgba(22,134,74,.28)); }
.homing-axis { padding:2px 0 0; }
.homing-axis .multi-point-axis-track { min-height:46px; }
.homing-axis .multi-point-axis-track::before { top:29px; }
.multi-point-axis.unhomed .multi-point-current-marker { display:none; }
.homing-unhomed-label { position:absolute; left:50%; top:29px; transform:translate(-50%,-50%); z-index:1; min-width:7ch; padding:3px 9px; border:1px solid rgba(173,41,48,.22); border-radius:999px; background:#fff7f7; color:var(--bad); font-size:12px; line-height:1.1; font-weight:900; text-align:center; box-shadow:0 2px 6px rgba(173,41,48,.08); }
.position-axis .homing-unhomed-label { top:20px; }
.position-axis:not(.unhomed) .homing-unhomed-label,
.multi-point-axis:not(.unhomed) .homing-unhomed-label { display:none; }
.anti-sway-panel { display:grid; gap:8px; }
.anti-sway-card { border:1px solid rgba(166,166,166,.30); border-radius:10px; padding:9px; background:#fff; display:grid; gap:8px; }
.anti-sway-card-head { display:flex; align-items:baseline; justify-content:space-between; gap:10px; min-width:0; }
.anti-sway-title { color:var(--dark); font-size:13px; line-height:1.1; font-weight:900; }
.anti-sway-badge { flex:0 0 auto; border-radius:999px; padding:3px 8px; background:#e7f3fb; color:var(--theme-deep); font-size:10px; line-height:1; font-weight:900; text-transform:uppercase; }
.anti-sway-badge.execution-locked,
.anti-sway-badge.execution-unlocked { text-transform:none; box-shadow:inset 0 0 0 1px rgba(0,0,0,.06); }
.anti-sway-badge.execution-locked { background:#fff4e5; color:#9a4b00; box-shadow:inset 0 0 0 1px rgba(210,126,0,.24); }
.anti-sway-badge.execution-unlocked { background:rgba(22,134,74,.12); color:var(--ok); box-shadow:inset 0 0 0 1px rgba(22,134,74,.24); }
.anti-sway-axis { --anti-sway-track-center:50px; min-height:112px; grid-template-rows:92px auto; padding-top:0; }
.anti-sway-axis .multi-point-axis-track { min-height:92px; }
.anti-sway-axis .multi-point-axis-track::before { top:calc(var(--anti-sway-track-center) - 5px); background:linear-gradient(90deg,#b8bec5 0 var(--zero-pct,50%),#fff var(--zero-pct,50%) 100%); }
.anti-sway-axis .multi-point-current-marker { top:calc(var(--anti-sway-track-center) - 47px); }
.anti-sway-target-input { position:absolute; left:18px; top:calc(var(--anti-sway-track-center) - 24px); width:calc(100% - 36px); height:52px; z-index:0; appearance:none; -webkit-appearance:none; background:transparent; opacity:0; pointer-events:none; touch-action:none; }
.anti-sway-target-input::-webkit-slider-runnable-track { height:52px; background:transparent; border:0; }
.anti-sway-target-input::-webkit-slider-thumb { -webkit-appearance:none; width:36px; height:52px; background:transparent; border:0; }
.anti-sway-target-input::-moz-range-track { height:52px; background:transparent; border:0; }
.anti-sway-target-input::-moz-range-thumb { width:36px; height:52px; background:transparent; border:0; }
.anti-sway-target-marker { position:absolute; left:calc(18px + (var(--target-frac, 0) * (100% - 36px))); top:calc(var(--anti-sway-track-center) - 15px); transform:translateX(-50%); width:30px; height:30px; border-radius:50%; background:var(--warn); border:4px solid #fff; box-shadow:0 5px 14px rgba(0,0,0,.24); cursor:grab; touch-action:none; transition:left .16s linear; z-index:5; }
.anti-sway-target-marker.dragging { cursor:grabbing; transition:none; }
.anti-sway-target-label { position:absolute; left:50%; top:34px; transform:translateX(-50%); min-width:8.5ch; padding:2px 6px; border:1px solid rgba(210,126,0,.20); border-radius:999px; background:#fff; color:var(--theme-deep); font-size:12px; line-height:1.1; font-weight:900; font-variant-numeric:tabular-nums; text-align:center; white-space:nowrap; box-shadow:0 2px 6px rgba(210,126,0,.10); pointer-events:none; }
.anti-sway-slider-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; align-items:stretch; }
.anti-sway-slider { display:grid; gap:7px; min-width:0; border:1px solid rgba(42,131,183,.16); border-radius:10px; background:var(--soft); padding:8px; }
.anti-sway-slider-head { display:flex; align-items:baseline; justify-content:space-between; gap:10px; min-width:0; }
.anti-sway-slider-head span { color:#66727f; font-size:11px; line-height:1; font-weight:900; white-space:nowrap; }
.anti-sway-slider-head strong { color:var(--theme-deep); font-size:14px; line-height:1; font-weight:900; font-variant-numeric:tabular-nums; text-align:right; white-space:nowrap; }
.anti-sway-slider input[type=range] { appearance:none; -webkit-appearance:none; width:100%; height:28px; background:transparent; accent-color:var(--theme-blue); cursor:pointer; }
.anti-sway-slider input[type=range]::-webkit-slider-runnable-track { height:10px; border-radius:999px; border:1px solid rgba(42,131,183,.24); background:#e7f0f6; box-shadow:inset 0 1px 2px rgba(0,0,0,.10); }
.anti-sway-slider input[type=range]::-webkit-slider-thumb { -webkit-appearance:none; width:28px; height:28px; margin-top:-10px; border-radius:50%; background:var(--theme-blue); border:4px solid #fff; box-shadow:0 5px 14px rgba(0,0,0,.24); }
.anti-sway-slider input[type=range]::-moz-range-track { height:10px; border-radius:999px; border:1px solid rgba(42,131,183,.24); background:#e7f0f6; box-shadow:inset 0 1px 2px rgba(0,0,0,.10); }
.anti-sway-slider input[type=range]::-moz-range-thumb { width:22px; height:22px; border-radius:50%; background:var(--theme-blue); border:4px solid #fff; box-shadow:0 5px 14px rgba(0,0,0,.24); }
.anti-sway-plan { display:grid; gap:6px; border:1px solid rgba(42,131,183,.16); border-radius:10px; background:var(--soft); padding:8px; min-width:0; }
.anti-sway-plan[hidden] { display:none; }
.anti-sway-plan-head { display:flex; align-items:baseline; justify-content:space-between; gap:10px; min-width:0; }
.anti-sway-plan-title { color:var(--dark); font-size:12px; line-height:1; font-weight:900; }
.anti-sway-plan-state { color:#66727f; font-size:10px; line-height:1; font-weight:900; white-space:nowrap; }
.anti-sway-plan-chart { position:relative; height:76px; border:1px solid rgba(42,131,183,.14); border-radius:8px; overflow:hidden; background:#fff; }
.anti-sway-plan-chart svg { width:100%; height:100%; display:block; }
.anti-sway-plan-grid { stroke:rgba(42,131,183,.12); stroke-width:1; }
.anti-sway-plan-normal { fill:none; stroke:#9aa4af; stroke-width:2.5; stroke-dasharray:6 5; stroke-linecap:round; stroke-linejoin:round; }
.anti-sway-plan-shaped { fill:none; stroke:var(--theme-blue); stroke-width:3.2; stroke-linecap:round; stroke-linejoin:round; }
.anti-sway-plan-legend { display:flex; justify-content:space-between; gap:8px; min-width:0; }
.anti-sway-plan-legend span { display:inline-flex; align-items:center; gap:5px; min-width:0; color:#66727f; font-size:10px; line-height:1; font-weight:900; white-space:nowrap; }
.anti-sway-plan-legend i { display:inline-block; width:16px; height:3px; border-radius:999px; background:#9aa4af; }
.anti-sway-plan-legend i.shaped { background:var(--theme-blue); }
.anti-sway-plan-stats { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:6px; }
.anti-sway-plan-stat { display:flex; align-items:baseline; justify-content:space-between; gap:5px; min-width:0; padding:5px 7px; border:1px solid rgba(42,131,183,.12); border-radius:999px; background:#fff; }
.anti-sway-plan-stat span { color:#66727f; font-size:9px; line-height:1; font-weight:900; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.anti-sway-plan-stat strong { color:var(--theme-deep); font-size:11px; line-height:1; font-weight:900; font-variant-numeric:tabular-nums; white-space:nowrap; }
.anti-sway-settings { display:grid; grid-template-columns:minmax(0,1fr) minmax(0,1fr) minmax(0,1.08fr); gap:6px; align-items:stretch; }
.anti-sway-chip { position:relative; min-height:30px; height:30px; border:1px solid rgba(42,131,183,.16); border-radius:9px; background:#fff; padding:5px 7px; display:flex; align-items:center; justify-content:space-between; gap:6px; min-width:0; overflow:hidden; }
.anti-sway-chip[role=button] { cursor:pointer; }
.anti-sway-chip[role=button]:focus-visible { outline:3px solid rgba(42,131,183,.18); outline-offset:2px; }
.anti-sway-chip span { min-width:0; color:#66727f; font-size:10px; line-height:1; font-weight:900; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.anti-sway-chip strong { flex:0 0 auto; color:var(--theme-deep); font-size:13px; line-height:1; font-weight:900; font-variant-numeric:tabular-nums; white-space:nowrap; }
.anti-sway-calibration { grid-column:1 / -1; min-height:30px; height:30px; border:1px solid rgba(42,131,183,.16); border-radius:9px; background:#fff; padding:5px 8px; display:flex; align-items:center; justify-content:space-between; gap:8px; box-shadow:none; color:var(--theme-deep); font-size:10px; line-height:1; font-weight:900; }
.anti-sway-calibration:hover { transform:none; box-shadow:0 4px 10px rgba(42,131,183,.10); }
.anti-sway-calibration span { color:#66727f; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.anti-sway-calibration strong { color:var(--theme-deep); font-size:13px; font-variant-numeric:tabular-nums; white-space:nowrap; }
.anti-sway-select-chip { align-items:center; }
.anti-sway-select-chip select { appearance:none; -webkit-appearance:none; flex:0 0 auto; width:48px; height:22px; min-height:22px; padding:0 8px 0 0; border:0; border-radius:0; background:transparent; color:var(--theme-deep); font:inherit; font-size:13px; line-height:1; font-weight:900; text-align:right; outline:none; }
.anti-sway-algorithm-chip select { width:78px; }
.anti-sway-hidden-limit { position:absolute; width:1px; height:1px; overflow:hidden; opacity:0; pointer-events:none; }
.anti-sway-chart-wrap { display:grid; gap:7px; }
.anti-sway-chart { position:relative; height:174px; border:1px solid rgba(42,131,183,.18); border-radius:10px; overflow:hidden; background:linear-gradient(180deg,#fbfdff 0%,#f3f8fb 100%); }
.anti-sway-chart svg { width:100%; height:100%; display:block; }
.anti-sway-grid-line { stroke:rgba(42,131,183,.13); stroke-width:1; }
.anti-sway-zero-line { stroke:rgba(17,24,39,.32); stroke-width:1.4; stroke-dasharray:5 5; }
.anti-sway-band { fill:rgba(22,134,74,.10); }
.anti-sway-wave { fill:none; stroke:var(--theme-blue); stroke-width:3.2; stroke-linecap:round; stroke-linejoin:round; }
.anti-sway-decay { fill:none; stroke:var(--ok); stroke-width:3.2; stroke-linecap:round; stroke-linejoin:round; }
.anti-sway-chart-label { position:absolute; left:8px; color:#66727f; font-size:10px; line-height:1; font-weight:900; font-variant-numeric:tabular-nums; }
.anti-sway-chart-label[role=button] { cursor:pointer; pointer-events:auto; padding:3px 5px; border-radius:999px; background:rgba(255,255,255,.76); border:1px solid rgba(42,131,183,.12); color:var(--theme-deep); }
.anti-sway-chart-label.top { top:8px; }
.anti-sway-chart-label.mid { top:50%; transform:translateY(-50%); }
.anti-sway-chart-label.bottom { bottom:8px; }
.anti-sway-current-badge { position:absolute; top:8px; right:8px; padding:4px 8px; border:1px solid rgba(22,134,74,.18); border-radius:999px; background:rgba(255,255,255,.86); color:var(--ok); font-size:11px; line-height:1; font-weight:900; font-variant-numeric:tabular-nums; box-shadow:0 2px 8px rgba(22,134,74,.08); }
.anti-sway-chart-stats { position:absolute; left:64px; right:8px; bottom:8px; display:flex; align-items:center; justify-content:flex-end; gap:6px; pointer-events:none; min-width:0; }
.anti-sway-chart-stat { display:inline-flex; align-items:baseline; gap:3px; min-width:0; padding:4px 6px; border:1px solid rgba(42,131,183,.12); border-radius:999px; background:rgba(255,255,255,.82); color:#66727f; font-size:10px; line-height:1; font-weight:900; white-space:nowrap; box-shadow:0 2px 8px rgba(42,131,183,.06); }
.anti-sway-chart-stat strong { color:var(--theme-deep); font-size:11px; line-height:1; font-weight:900; font-variant-numeric:tabular-nums; }
.anti-sway-preview-note { color:#66727f; font-size:11px; line-height:1.35; font-weight:700; }
body.lang-en .anti-sway-chart-stat { font-size:9px; padding-left:5px; padding-right:5px; }
.multi-point-scroll { min-height:0; overflow:auto; -webkit-overflow-scrolling:touch; touch-action:pan-y; border:1px solid rgba(166,166,166,.28); border-radius:8px; background:#fff; }
.multi-point-table { width:100%; border-collapse:collapse; table-layout:fixed; font-size:12px; }
.multi-point-table th,
.multi-point-table td { border-right:1px solid rgba(166,166,166,.22); border-bottom:1px solid rgba(166,166,166,.22); padding:5px; text-align:center; }
.multi-point-table th { position:sticky; top:0; z-index:1; background:#eef6fb; color:var(--theme-deep); font-weight:900; }
.multi-point-table th:last-child,
.multi-point-table td:last-child { border-right:0; }
.multi-point-table tr.running-row td { background:#e8f8ef; }
.multi-point-row-cell { display:flex; align-items:center; justify-content:center; gap:6px; }
.multi-point-row-cell.clickable { cursor:pointer; }
.multi-point-cycle-indicator { min-width:0; color:var(--ok); font-size:11px; line-height:1; font-weight:900; white-space:nowrap; }
.multi-point-table input { width:100%; min-width:0; border:1px solid transparent; border-radius:6px; padding:6px 5px; background:#f8fbfd; color:var(--ink); font-size:12px; font-weight:800; text-align:right; outline:none; }
.multi-point-table input[type=checkbox] { width:18px; height:18px; accent-color:var(--theme-blue); }
.multi-point-table input[readonly] { color:#5c6672; background:transparent; }
.multi-point-status { min-height:18px; font-size:12px; font-weight:800; color:#66727f; }
.multi-point-status-button { width:100%; border:0; border-radius:0; padding:0; min-height:18px; background:transparent; color:#66727f; font-size:12px; line-height:1.4; font-weight:800; text-align:left; box-shadow:none; cursor:default; }
.multi-point-status-button:hover { transform:none; box-shadow:none; }
.multi-point-status-button.clickable { color:var(--theme-deep); cursor:pointer; }
.multi-point-status-button.clickable:hover { color:var(--theme-blue); }
.multi-point-status-button:disabled { opacity:1; }
.multi-point-cycle-popup { position:fixed; left:max(14px, env(safe-area-inset-left)); top:calc(88px + env(safe-area-inset-top)); width:min(540px, calc(100vw - 28px)); display:none; z-index:46; }
.multi-point-cycle-popup.open { display:block; }
.multi-point-cycle-popup-card { border:1px solid rgba(42,131,183,.22); border-radius:20px; background:rgba(255,255,255,.98); box-shadow:0 18px 42px rgba(17,24,39,.20); padding:24px 24px 26px; display:grid; gap:20px; backdrop-filter:blur(10px); }
.multi-point-cycle-popup-head { display:flex; align-items:flex-start; justify-content:space-between; gap:15px; }
.multi-point-cycle-popup-copy { display:grid; gap:6px; }
.multi-point-cycle-popup-title { color:#66727f; font-size:20px; line-height:1; font-weight:800; }
.multi-point-cycle-popup-row { color:var(--theme-deep); font-size:39px; line-height:1; font-weight:900; }
.multi-point-cycle-popup-close { width:51px; min-width:51px; height:51px; min-height:51px; border:1px solid rgba(166,166,166,.28); border-radius:999px; padding:0; background:#fff; color:#66727f; font-size:33px; line-height:1; font-weight:700; box-shadow:none; }
.multi-point-cycle-popup-close:hover { transform:none; box-shadow:none; color:var(--theme-deep); }
.multi-point-cycle-popup-body { display:flex; align-items:flex-end; gap:11px; }
.multi-point-cycle-popup-current { color:var(--theme-deep); font-size:clamp(108px, 16.5vw, 186px); line-height:.84; font-weight:900; font-variant-numeric:tabular-nums; }
.multi-point-cycle-popup-slash { color:#94a0ac; font-size:clamp(51px, 7.5vw, 81px); line-height:1; font-weight:900; padding-bottom:18px; }
.multi-point-cycle-popup-total { color:#94a0ac; font-size:clamp(42px, 6vw, 60px); line-height:1; font-weight:800; font-variant-numeric:tabular-nums; padding-bottom:21px; }
.multi-point-cycle-popup-status { color:#66727f; font-size:18px; line-height:1.25; font-weight:800; }
.hidden { display:none !important; }
.modal-shell { position:fixed; inset:0; display:none; place-items:center; padding:18px; background:rgba(17,24,39,.36); z-index:50; }
.modal-shell.open { display:grid; }
.modal-card { width:min(520px,100%); border:1px solid var(--line); border-radius:12px; background:#fff; box-shadow:0 18px 46px rgba(17,24,39,.22); padding:14px; display:grid; gap:12px; }
.modal-title { margin:0; color:var(--theme-deep); font-size:18px; line-height:1.1; font-weight:900; }
.modal-grid { display:grid; gap:10px; }
.modal-mode-row { margin:0; min-height:58px; }
.modal-ratio-row { display:grid; grid-template-columns:auto minmax(88px,1fr) minmax(78px,.9fr); gap:8px; align-items:center; }
.modal-ratio-row.motor { grid-template-columns:auto minmax(88px,1fr) auto; }
.direction-control { min-height:42px; display:flex; align-items:center; justify-content:center; gap:14px; }
.direction-label { color:#667; font-size:17px; line-height:1; font-weight:900; min-width:3.2em; text-align:center; }
.direction-toggle { box-sizing:border-box; width:58px; height:34px; border:0; border-radius:999px; padding:3px; background:var(--ok); box-shadow:inset 0 0 0 1px rgba(17,17,17,.06); display:flex; align-items:center; cursor:pointer; transition:background .18s ease; }
.direction-toggle.reverse { background:var(--bad); }
.direction-toggle .knob { width:28px; height:28px; border-radius:50%; background:#fff; box-shadow:0 2px 7px rgba(17,17,17,.24); transform:translateX(24px); transition:transform .18s cubic-bezier(.2,.8,.2,1); }
.direction-toggle.reverse .knob { transform:translateX(0); }
	.modal-inline-label { color:var(--ink); font-size:14px; font-weight:900; white-space:nowrap; }
		.modal-inline-unit { color:#4c5966; font-size:14px; line-height:1; font-weight:900; white-space:nowrap; }
		.modal-actions { display:grid; grid-template-columns:1fr 1fr; gap:8px; }
		.diag-body { max-height:min(56dvh,460px); overflow:auto; border:1px solid rgba(166,166,166,.28); border-radius:10px; background:#f8fafc; padding:10px; color:#304050; font-size:12px; line-height:1.5; font-weight:700; white-space:pre-wrap; }
		.motion-confirm-card { width:min(620px,100%); gap:14px; }
		.motion-confirm-body { min-height:96px; font-size:15px; line-height:1.55; border-color:rgba(199,118,0,.24); background:#fffaf2; color:#243140; }
		.motion-confirm-actions { grid-template-columns:.76fr 1.24fr; gap:12px; }
		.motion-confirm-actions button { min-height:60px; font-size:20px; box-shadow:0 12px 26px rgba(17,24,39,.16); }
		.motion-confirm-actions .motion-confirm-primary { background:var(--theme-blue); box-shadow:0 14px 32px rgba(42,131,183,.30); }
		.touch-keypad-shell { position:fixed; inset:0; display:none; align-items:flex-end; justify-content:center; padding:18px; background:rgba(17,24,39,.18); z-index:62; }
	.touch-keypad-shell.open { display:flex; }
	.touch-keypad { --keypad-x:0px; --keypad-y:0px; width:min(392px, calc(100vw - 24px)); border:1px solid rgba(255,255,255,.72); border-radius:28px; background:rgba(246,248,251,.94); box-shadow:0 24px 60px rgba(17,24,39,.28), inset 0 1px 0 rgba(255,255,255,.82); padding:10px 12px 14px; display:grid; gap:10px; transform:translate(var(--keypad-x), var(--keypad-y)); backdrop-filter:blur(22px) saturate(1.16); touch-action:none; }
	.touch-keypad.dragging { box-shadow:0 30px 70px rgba(17,24,39,.34), inset 0 1px 0 rgba(255,255,255,.82); }
	.touch-keypad-head { display:grid; grid-template-columns:minmax(0,1fr) 38px; align-items:center; gap:8px; padding:0 2px; cursor:grab; user-select:none; touch-action:none; }
	.touch-keypad-head:active { cursor:grabbing; }
	.touch-keypad-grip { grid-column:1 / -1; width:54px; height:5px; border-radius:999px; background:rgba(92,104,116,.30); justify-self:center; margin:0 0 2px; }
	.touch-keypad-title { color:#52606d; font-size:13px; line-height:1.15; font-weight:900; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; padding-left:4px; }
	.touch-keypad-close { width:36px; min-width:36px; height:36px; min-height:36px; padding:0; border-radius:999px; border:0; background:rgba(229,233,238,.92); color:#5f6b77; box-shadow:inset 0 1px 0 rgba(255,255,255,.84); font-size:24px; line-height:1; }
	.touch-keypad-value { min-height:52px; border:1px solid rgba(255,255,255,.74); border-radius:18px; padding:8px 14px; background:rgba(255,255,255,.86); color:#111827; font-size:31px; line-height:1.08; font-weight:900; font-variant-numeric:tabular-nums; text-align:right; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; box-shadow:inset 0 1px 2px rgba(17,24,39,.08); }
	.touch-keypad-grid { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:8px; }
	.touch-keypad-grid button,
	.touch-keypad-actions button { min-height:54px; padding:8px; border:0; border-radius:16px; font-size:22px; line-height:1; font-weight:900; box-shadow:0 1px 1px rgba(17,24,39,.08), inset 0 1px 0 rgba(255,255,255,.72); transition:transform .06s ease, filter .08s ease; }
	.touch-keypad-grid button:active,
	.touch-keypad-actions button:active { transform:scale(.97); filter:brightness(.97); }
	.touch-keypad-key.digit { background:rgba(255,255,255,.96); color:#111827; }
	.touch-keypad-key.utility { background:rgba(221,226,232,.96); color:#2f3a45; }
	.touch-keypad-key.destructive { background:rgba(255,231,231,.96); color:#ad2930; }
	.touch-keypad-key.primary { background:#1f7fbc; color:#fff; box-shadow:0 8px 18px rgba(31,127,188,.24), inset 0 1px 0 rgba(255,255,255,.25); }
	.touch-keypad-actions { display:grid; grid-template-columns:1fr 1fr 1.2fr; gap:8px; }
	.touch-keypad-actions button { font-size:16px; }
	.touch-keypad button:disabled { opacity:.42; pointer-events:none; }
	@media (max-width:1180px) { .feedback-card.encoder { grid-template-columns:128px minmax(0,1fr); column-gap:8px; } .feedback-card.encoder .feedback-metrics { min-width:0; max-width:100%; } .feedback-metric { grid-template-columns:minmax(0,1fr) minmax(0,9.5ch); gap:6px; padding:7px 8px; min-height:48px; } .feedback-metric .label { font-size:10px; min-width:0; overflow-wrap:anywhere; word-break:break-word; } .feedback-metric .value { min-width:0; max-width:100%; font-size:15px; white-space:normal; overflow-wrap:anywhere; word-break:break-word; align-self:start; } .feedback-metric.vertical { min-height:70px; } }
@media (max-width:980px) { main { padding:7px 9px 9px; } .monitor-grid { grid-template-columns:minmax(245px,1fr) minmax(190px,.68fr) minmax(245px,.95fr); gap:8px; } .card { padding:8px; } .protocol-chip { font-size:24px; } .brand-wordmark { font-size:19px; } .logo { width:34px; height:34px; } .axis-card { grid-template-columns:128px minmax(0,1fr); gap:8px; } .dial { width:128px; height:128px; } .hand { height:48px; margin-top:-48px; } .big-angle { font-size:38px; } .tile { min-height:44px; padding:5px 7px; } .value { font-size:15px; } .slider-number { font-size:13px; } .meta { font-size:10px; } .param-grid { grid-template-columns:repeat(2,minmax(0,1fr)); } .position-param { --side-width:108px; --slider-top-offset:calc(114px + var(--position-marker-space)); --slider-track-height:150px; } .axis-control { min-height:calc(324px + var(--position-marker-space)); } .vertical-sliders { min-height:188px; } .target-number { font-size:41px; } .target-angle { font-size:15px; } .multi-point-strip { grid-template-columns:repeat(2,minmax(0,1fr)); } .multi-point-table { font-size:11px; } .multi-point-table input { font-size:11px; padding:5px 4px; } .multi-point-cycle-popup { top:calc(76px + env(safe-area-inset-top)); width:min(480px, calc(100vw - 24px)); } }
@media (max-width:820px) { .feedback-card.encoder { grid-template-columns:1fr; } .encoder-title { grid-column:1; grid-row:auto; justify-self:center; align-self:auto; margin-bottom:0; } .encoder-dial { grid-column:1; grid-row:auto; } .feedback-card.encoder .feedback-metrics { grid-column:1; grid-row:auto; } }
@media (max-width:720px) { .monitor-grid { grid-template-columns:minmax(0,1fr); grid-auto-rows:max-content; align-content:start; overflow:auto; touch-action:pan-y; } .right-stack { display:none; } .middle-stack { grid-template-columns:minmax(0,1fr); } .axis-card { grid-template-columns:140px 1fr; } .status { grid-template-columns:repeat(3,1fr); } .subbar { flex-wrap:wrap; } .tabs { flex:1 0 100%; order:3; } .assembly-status { order:2; width:100%; margin-left:0; flex-wrap:wrap; } .protocol-chip { font-size:20px; } .brand-wordmark { font-size:19px; } .logo { width:34px; height:34px; } h1 { font-size:18px; } }
</style>
</head>
<body>
<main>
<header class="topbar">
  <div class="topbar-left">
    <div class="lang-menu">
      <button id="langToggleBtn" class="lang-btn" type="button" aria-haspopup="menu" aria-expanded="false" aria-label="Language">
        <span class="menu-lines" aria-hidden="true"><span></span><span></span><span></span></span>
      </button>
      <div id="langDropdown" class="lang-dropdown" role="menu" aria-label="Language">
        <button id="langZhBtn" class="lang-option" type="button" role="menuitem" onclick="setLanguage('zh')">中文</button>
        <button id="langEnBtn" class="lang-option" type="button" role="menuitem" onclick="setLanguage('en')">English</button>
      </div>
    </div>
    <h1 id="pageTitle">轴控</h1>
  </div>
  <div class="topbar-right">
    <span class="brand-wordmark">mctivity</span>
    <img class="logo" src="/assets/logo.png" alt="mctivity logo" />
  </div>
</header>
<div class="subbar">
  <span id="protocolChip" class="protocol-chip">EtherCAT</span>
  <div class="assembly-status" aria-live="polite">
    <span class="assembly-chip"><span id="profileLabel">Profile</span><strong id="profileValue">--</strong></span>
    <span id="featureChip" class="assembly-chip" role="button" tabindex="0" title="features"><span id="featureCountLabel">Features</span><strong id="featureCountValue">--</strong></span>
    <span class="assembly-chip"><span id="capabilityCountLabel">Caps</span><strong id="capabilityCountValue">--</strong></span>
    <span id="warningChip" class="assembly-chip" role="button" tabindex="0" title="warnings"><span id="warningCountLabel">Warn</span><strong id="warningCountValue">--</strong></span>
    <input id="apiTokenInput" class="api-token-input" type="password" autocomplete="off" spellcheck="false" placeholder="API Token" aria-label="API Token">
  </div>
  <nav class="tabs" aria-label="Axis selector">
    <button id="tabMonitorBtn" class="tab-btn active" type="button" data-device="mctivity">轴 A</button>
    <button id="tabConfigBtn" class="tab-btn" type="button" data-device="fv3">轴 B</button>
    <button id="tabEncoderBtn" class="tab-btn" type="button" data-device="aux_encoder">轴 C</button>
  </nav>
</div>
<section id="tabMonitor" class="tab-panel active">
  <div class="monitor-grid">
    <div class="left-stack">
      <section class="card">
        <h2 id="feedbackPanelTitle">电机反馈</h2>
        <div class="feedback-grid">
          <div class="feedback-card encoder">
            <div class="feedback-title encoder-title">位置</div>
            <div class="feedback-dial encoder-dial">
              <div class="north">0</div><div class="east">90</div><div class="south">180</div><div class="west">270</div>
              <div id="encoderHand" class="feedback-hand"></div><div class="feedback-hub"></div>
            </div>
            <div class="feedback-metrics">
              <div class="feedback-metric"><span class="label">当前圈数</span><span id="encoderTurns" class="value">--</span></div>
              <div class="feedback-metric"><span class="label">当前编码器角度</span><span id="encoderAngle" class="value">--</span></div>
              <div class="feedback-metric vertical"><span class="label">编码器脉冲数</span><span id="encoderPulses" class="value">--</span></div>
              <div class="feedback-metric"><span class="label">单圈计数</span><span id="encoderSingleTurn" class="value">--</span></div>
            </div>
          </div>
          <div class="feedback-card torque-feedback-card motor-only">
            <div class="feedback-title">扭矩</div>
            <div class="feedback-dial small dashboard">
              <span class="feedback-tick tick-0">0</span><span class="feedback-tick tick-20">20</span><span class="feedback-tick tick-40">40</span><span class="feedback-tick tick-60">60</span><span class="feedback-tick tick-80">80</span><span class="feedback-tick tick-100">100</span>
              <div id="torqueHand" class="feedback-hand"></div><div class="feedback-hub"></div>
              <div class="feedback-value"><span id="torqueGaugeValue" class="feedback-number">0.0</span><span class="feedback-unit">%</span></div>
            </div>
          </div>
          <div class="feedback-card speed-feedback-card">
            <div class="feedback-title">速度</div>
            <div class="feedback-dial small dashboard">
              <span class="feedback-tick tick-k1">1</span><span class="feedback-tick tick-k2">2</span><span class="feedback-tick tick-k3">3</span>
              <div id="speedHand" class="feedback-hand"></div><div class="feedback-hub"></div>
              <div class="feedback-value"><span id="speedGaugeValue" class="feedback-number">0.0</span><span class="feedback-unit">krpm</span></div>
            </div>
          </div>
        </div>
      </section>
    </div>
    <div class="middle-stack">
      <section class="card">
        <h2>控制</h2>
        <div class="mode-row mode-select-row">
          <span class="label">模式</span>
          <select id="modeSelect" onchange="setMode()">
            <option value="position">单点定位</option>
            <option value="anti_sway_position">电子防摇定位</option>
            <option value="incremental">增量位移</option>
            <option value="jog">点动</option>
            <option value="point">点位表</option>
            <option value="multi_point">多点定位</option>
            <option value="homing">回零</option>
            <option value="velocity">速度控制</option>
            <option value="torque">转矩控制</option>
            <option value="gear_cam">电子齿轮</option>
          </select>
        </div>
        <div class="mode-row">
          <span class="label-stack"><span id="transmissionLabel" class="label">传动</span><span id="transmissionTypeLabel" class="label-subtext">旋转</span></span>
          <button class="transmission-trigger" onclick="openTransmissionDialog()">
            <span class="transmission-copy">
              <span id="transmissionLoadSummary" class="transmission-primary">负载 360.0 deg</span>
              <span id="transmissionMotorSummary" class="transmission-secondary">电机 1 Rev</span>
            </span>
            <span class="transmission-chevron">›</span>
          </button>
        </div>
        <div class="controls">
          <button id="enableToggle" class="enable-toggle power-toggle motor-only" onclick="toggleEnable()"><span class="enable-copy"><span class="toggle-title">使能</span><span id="enableToggleText" class="toggle-subtitle">OFF</span></span><span class="toggle-track"><span class="toggle-knob"></span></span></button>
          <button id="motionIndicator" class="enable-toggle motion-toggle motor-only" onclick="startSinglePointMotion()"><span class="enable-copy"><span class="toggle-title">启停</span><span id="motionIndicatorText" class="toggle-subtitle">STANDSTILL</span></span><span class="toggle-track"><span class="toggle-knob"></span></span></button>
          <div id="faultIndicator" class="enable-toggle fault-toggle"><span class="enable-copy"><span class="toggle-title">状态</span><span id="faultIndicatorText" class="toggle-subtitle">READY</span></span><span class="fault-summary"><span id="faultCodeText" class="fault-code">0x0000</span><span id="faultNameText" class="fault-name">正常</span></span><button id="faultIndicatorButton" class="status-button" type="button" onclick="resetFault(event)" aria-label="READY" disabled></button></div>
          <div id="mockFaultPanel" class="mock-fault-panel" hidden>
            <div class="mock-fault-head"><span id="mockFaultLabel">机器码模拟</span><span id="mockFaultBadge" class="mock-fault-badge">Mock</span></div>
            <select id="mockFaultSelect" class="mock-fault-select" onchange="changeMockFaultCode()"></select>
            <div id="mockFaultHint" class="mock-fault-hint">仅本机测试使用，不会下发运动命令。</div>
          </div>
        </div>
      </section>
    </div>
    <div class="right-stack">
      <section class="card active-mode-card">
        <h2 id="modePanelTitle">定位参数</h2>
        <div id="panel-position" class="mode-panel">
          <div class="slider-card position-param">
            <div class="axis-control">
              <div class="slider-head"><span id="positionParamTitle" class="slider-title">目标绝对位置</span></div>
              <div class="position-axis">
                <div id="currentPositionMarker" class="current-position-marker"><span id="currentPositionValue" class="current-position-value">0.0 deg</span></div>
                <div id="positionUnhomedLabel" class="homing-unhomed-label">未回零</div>
                <input id="absPos" type="range" min="-1677721600" max="1677721600" step="1024" value="0" oninput="updateSliders()">
                <div class="axis-labels"><span id="axisMinRev">-200 rev</span><span></span><span id="axisMaxRev">+200 rev</span></div>
              </div>
              <div class="target-readout">
                <span class="target-unit">rev</span>
                <div class="target-cell"><div><span id="targetRevBig" class="target-number">0</span></div></div>
                <div class="target-cell secondary"><span id="targetAngleBig" class="target-angle">0.0 deg</span></div>
              </div>
            </div>
            <div class="vertical-sliders">
              <div class="vertical-slider">
                <label for="absSpeedRpm">速度</label>
                <input id="absSpeedRpm" type="range" min="1" max="__MAX_SPEED_RPM__" step="1" value="120" oninput="updateSliders()">
                <span id="absSpeedText">120 rpm</span>
              </div>
              <div class="vertical-slider">
                <label for="absAccel">加速度</label>
                <input id="absAccel" type="range" min="10" max="__MAX_ACCEL_RPM_S__" step="10" value="300" oninput="updateSliders()">
                <span id="absAccelText">300 rpm/s</span>
              </div>
            </div>
          </div>
        </div>
        <div id="panel-anti_sway_position" class="mode-panel">
          <div class="anti-sway-panel">
            <div class="anti-sway-card">
              <div class="anti-sway-card-head">
                <span id="antiSwayPositionParamsTitle" class="anti-sway-title">定位参数</span>
                <span id="antiSwayPreviewBadge" class="anti-sway-badge">UI 预览</span>
              </div>
              <div id="antiSwayAxis" class="multi-point-axis anti-sway-axis">
                <div id="antiSwayAxisTrack" class="multi-point-axis-track">
                  <input id="antiSwayTargetInput" class="anti-sway-target-input" type="range" min="-1677721600" max="1677721600" step="1024" value="0" oninput="syncAntiSwayTargetFromInput()">
                  <div id="antiSwayTargetMarker" class="anti-sway-target-marker" onpointerdown="startAntiSwayTargetDrag(event)">
                    <span id="antiSwayTargetLabel" class="anti-sway-target-label">0.0 deg</span>
                  </div>
                  <div id="antiSwayCurrentMarker" class="multi-point-current-marker">
                    <span id="antiSwayCurrentLabel" class="multi-point-current-value">0.0 deg</span>
                  </div>
                  <div id="antiSwayUnhomedLabel" class="homing-unhomed-label">未回零</div>
                </div>
                <div class="axis-labels"><span id="antiSwayAxisMin">-360 deg</span><span></span><span id="antiSwayAxisMax">+360 deg</span></div>
              </div>
              <div class="anti-sway-slider-grid">
                <label class="anti-sway-slider" for="antiSwaySpeedRpm">
                  <span class="anti-sway-slider-head"><span id="antiSwaySpeedTitle">速度</span><strong id="antiSwaySpeedValue">--</strong></span>
                  <input id="antiSwaySpeedRpm" type="range" min="1" max="__MAX_SPEED_RPM__" step="1" value="120" oninput="syncAntiSwayMotionControls()">
                </label>
                <label class="anti-sway-slider" for="antiSwayAccel">
                  <span class="anti-sway-slider-head"><span id="antiSwayAccelTitle">加速度</span><strong id="antiSwayAccelValue">--</strong></span>
                  <input id="antiSwayAccel" type="range" min="10" max="__MAX_ACCEL_RPM_S__" step="10" value="300" oninput="syncAntiSwayMotionControls()">
                </label>
              </div>
              <div class="anti-sway-plan" data-anti-sway-debug-panel hidden>
                <div class="anti-sway-plan-head">
                  <span id="antiSwayPlanTitle" class="anti-sway-plan-title">轨迹预演</span>
                  <span id="antiSwayPlanState" class="anti-sway-plan-state">仅预演</span>
                </div>
                <div class="anti-sway-plan-chart">
                  <svg viewBox="0 0 420 86" preserveAspectRatio="none" aria-hidden="true">
                    <line class="anti-sway-plan-grid" x1="0" y1="12" x2="420" y2="12"></line>
                    <line class="anti-sway-plan-grid" x1="0" y1="43" x2="420" y2="43"></line>
                    <line class="anti-sway-plan-grid" x1="0" y1="74" x2="420" y2="74"></line>
                    <path id="antiSwayPlanNormalPath" class="anti-sway-plan-normal" d="M4 74 L416 12"></path>
                    <path id="antiSwayPlanShapedPath" class="anti-sway-plan-shaped" d="M4 74 L416 12"></path>
                  </svg>
                </div>
                <div class="anti-sway-plan-legend">
                  <span><i></i><span id="antiSwayPlanNormalLabel">普通定位</span></span>
                  <span><i class="shaped"></i><span id="antiSwayPlanShapedLabel">防摇定位</span></span>
                </div>
                <div class="anti-sway-plan-stats">
                  <div class="anti-sway-plan-stat"><span id="antiSwayNaturalPeriodTitle">自然周期</span><strong id="antiSwayNaturalPeriodValue">--</strong></div>
                  <div class="anti-sway-plan-stat"><span id="antiSwayPlanDelayTitle">预估延时</span><strong id="antiSwayPlanDelayValue">--</strong></div>
                  <div class="anti-sway-plan-stat"><span id="antiSwayPlanDurationTitle">预演时长</span><strong id="antiSwayPlanDurationValue">--</strong></div>
                </div>
              </div>
            </div>
            <div class="anti-sway-card">
              <div class="anti-sway-card-head">
                <span id="antiSwayMonitorTitle" class="anti-sway-title">摆动监控</span>
                <span id="antiSwayMonitorBadge" class="anti-sway-badge">Preview</span>
              </div>
		              <div class="anti-sway-settings">
		                <label class="anti-sway-chip anti-sway-select-chip anti-sway-algorithm-chip" for="antiSwayAlgorithm"><span id="antiSwayAlgorithmTitle">防摇算法</span><select id="antiSwayAlgorithm" onchange="onAntiSwaySettingsChange(true)"></select></label>
		                <label class="anti-sway-chip anti-sway-select-chip" for="antiSwaySensorAxis"><span id="antiSwaySensorAxisTitle">摆角检测</span><select id="antiSwaySensorAxis" onchange="onAntiSwaySettingsChange(true)"></select></label>
	                <div id="antiSwayRodChip" class="anti-sway-chip" role="button" tabindex="0" onclick="openAntiSwayRodEditor(event)" onkeydown="handleAntiSwayRodKey(event)"><span id="antiSwayRodTitle">摆杆长度</span><strong id="antiSwayRodValue">520 mm</strong><label class="anti-sway-hidden-limit"><input id="antiSwayRodInput" type="number" inputmode="decimal" data-touch-keypad min="1" max="100000" step="1" value="520" oninput="onAntiSwaySettingsChange(false)" onchange="onAntiSwaySettingsChange(true)"><span>mm</span></label></div>
	                <button id="antiSwayPeriodCalibrate" class="anti-sway-calibration" type="button" onclick="calibrateAntiSwayPeriodFromWave()"><span id="antiSwayCalibratePeriodTitle">校准周期</span><strong id="antiSwayMeasuredPeriodValue">计算 --</strong></button>
		              </div>
              <div class="anti-sway-chart-wrap">
                <div class="anti-sway-chart">
                  <label class="anti-sway-hidden-limit"><span id="antiSwayLimitTitle">允许摆角</span><input id="antiSwayLimitInput" type="number" inputmode="decimal" data-touch-keypad min="0.1" max="45" step="0.1" value="3" oninput="onAntiSwaySettingsChange(false)" onchange="onAntiSwaySettingsChange(true)"><span>deg</span></label>
                  <svg viewBox="0 0 420 150" preserveAspectRatio="none" aria-hidden="true">
                    <rect id="antiSwayBand" class="anti-sway-band" x="0" y="58" width="420" height="34"></rect>
                    <line class="anti-sway-grid-line" x1="0" y1="25" x2="420" y2="25"></line>
                    <line class="anti-sway-grid-line" x1="0" y1="58" x2="420" y2="58"></line>
                    <line class="anti-sway-zero-line" x1="0" y1="75" x2="420" y2="75"></line>
                    <line class="anti-sway-grid-line" x1="0" y1="92" x2="420" y2="92"></line>
                    <line class="anti-sway-grid-line" x1="0" y1="125" x2="420" y2="125"></line>
                    <polyline id="antiSwayWave" class="anti-sway-wave" points="4,75 416,75"></polyline>
                    <polyline id="antiSwayDecay" class="anti-sway-decay" points=""></polyline>
                  </svg>
	                  <span id="antiSwayChartTop" class="anti-sway-chart-label top" role="button" tabindex="0" onclick="openAntiSwayLimitEditor(event)" onkeydown="handleAntiSwayLimitKey(event)">+3 deg</span>
	                  <span id="antiSwayChartZero" class="anti-sway-chart-label mid">0</span>
	                  <span id="antiSwayChartBottom" class="anti-sway-chart-label bottom" role="button" tabindex="0" onclick="openAntiSwayLimitEditor(event)" onkeydown="handleAntiSwayLimitKey(event)">-3 deg</span>
	                  <span id="antiSwayChartCurrent" class="anti-sway-current-badge">当前 --</span>
	                  <div class="anti-sway-chart-stats">
	                    <span class="anti-sway-chart-stat"><span id="antiSwayPeakTitle">峰值</span><strong id="antiSwayPeakValue">--</strong></span>
	                    <span class="anti-sway-chart-stat"><span id="antiSwayResidualTitle">残余</span><strong id="antiSwayResidualValue">--</strong></span>
	                    <span class="anti-sway-chart-stat anti-sway-phase-stat"><span id="antiSwayPhaseTitle">相位</span><strong id="antiSwayPhaseValue">--</strong></span>
	                    <span class="anti-sway-chart-stat"><span id="antiSwaySettleTitle">稳定</span><strong id="antiSwaySettleValue">--</strong></span>
	                  </div>
	                </div>
	                <div id="antiSwayPreviewNote" class="anti-sway-preview-note">当前只展示界面结构，暂未接入防摇控制逻辑。</div>
              </div>
            </div>
          </div>
        </div>
        <div id="panel-incremental" class="mode-panel">
          <div class="slider-card">
            <div id="incrementalProfileHost"></div>
          </div>
        </div>
        <div id="panel-jog" class="mode-panel">
          <div class="slider-card">
            <div class="slider-head"><span class="slider-title">相对位移</span><span id="relText" class="slider-number">--</span></div>
            <input id="relDelta" type="range" min="-8388608" max="8388608" step="1024" value="4194304" oninput="updateSliders()">
            <div class="meta"><span id="relRev">--</span><span id="relDeg">--</span><span id="relRpm">--</span></div>
            <button class="blue" onclick="moveRel()">相对移动</button>
          </div>
          <div class="slider-card">
            <div class="slider-head"><span class="slider-title">运动时间</span><span id="msText" class="slider-number">--</span></div>
            <input id="moveMs" type="range" min="500" max="15000" step="100" value="3000" oninput="updateSliders()">
            <div class="meta"><span>500 ms</span><span>越大越慢</span><span>15000 ms</span></div>
          </div>
        </div>
        <div id="panel-point" class="mode-panel">
          <div class="point-table">
            <div class="point-row"><strong>P1</strong><span id="p1Quick">0 cnt</span><div class="point-actions"><button class="blue" onclick="gotoPoint(1)">移动</button></div></div>
            <div class="point-row"><strong>P2</strong><span id="p2Quick">4194304 cnt</span><div class="point-actions"><button class="blue" onclick="gotoPoint(2)">移动</button></div></div>
            <div class="point-row"><strong>P3</strong><span id="p3Quick">8388608 cnt</span><div class="point-actions"><button class="blue" onclick="gotoPoint(3)">移动</button></div></div>
          </div>
        </div>
        <div id="panel-multi_point" class="mode-panel">
          <div class="multi-point-axis">
            <div id="multiPointAxisTrack" class="multi-point-axis-track">
              <div id="multiPointCurrentMarker" class="multi-point-current-marker">
                <span id="multiPointCurrentValue" class="multi-point-current-value">0.0 deg</span>
              </div>
              <div id="multiPointUnhomedLabel" class="homing-unhomed-label">未回零</div>
            </div>
            <div class="axis-labels"><span id="multiPointAxisMin">-360 deg</span><span></span><span id="multiPointAxisMax">+360 deg</span></div>
          </div>
          <div class="slider-card multi-point-card">
            <div class="multi-point-strip">
              <div class="multi-point-chip">
                <label id="multiPointStartLabel" for="multiPointStart">起始点表</label>
                <input id="multiPointStart" type="number" min="1" max="255" step="1" value="1" onchange="onMultiPointSettingsChange()">
              </div>
              <div class="multi-point-chip">
                <label id="multiPointStepLabel" for="multiPointStep">点表步数</label>
                <input id="multiPointStep" type="number" min="1" max="64" step="1" value="3" onchange="onMultiPointSettingsChange()">
              </div>
              <div class="multi-point-chip">
                <label id="multiPointCycleLabel" for="multiPointCycleCount">循环次数</label>
                <input id="multiPointCycleCount" type="number" min="1" max="1000" step="1" value="1" onchange="onMultiPointSettingsChange()">
              </div>
              <div class="multi-point-chip">
                <label>&nbsp;</label>
                <button id="multiPointEditBtn" class="blue" type="button" onclick="toggleMultiPointEdit()">开始修改</button>
              </div>
            </div>
            <div class="multi-point-scroll">
              <table class="multi-point-table" aria-label="多点定位点表">
                <thead>
                  <tr>
                    <th id="multiPointRowHead">序号</th>
                    <th id="multiPointTargetHead">目标位置</th>
                    <th id="multiPointSpeedHead">速度</th>
                    <th id="multiPointAccelHead">加速度</th>
                    <th id="multiPointDwellHead">停留</th>
                    <th id="multiPointEnableHead">启用</th>
                  </tr>
                </thead>
                <tbody id="multiPointTableBody"></tbody>
              </table>
            </div>
            <button id="multiPointStatus" class="multi-point-status multi-point-status-button" type="button" onclick="openMultiPointCyclePopup()" disabled>等待点表</button>
          </div>
        </div>
	        <div id="panel-homing" class="mode-panel">
		          <div class="slider-card homing-card">
		            <div class="homing-tabs" role="tablist" aria-label="回零方式">
		              <button id="homingTabSetCurrent" class="homing-tab active" type="button" role="tab" aria-selected="true" onclick="setHomingMethod('set_current')">在当前位置回零</button>
		              <button id="homingTabTorqueEnd" class="homing-tab" type="button" role="tab" aria-selected="false" onclick="setHomingMethod('torque_end')">末端受阻回零</button>
		            </div>
		            <div id="homingAxis" class="multi-point-axis homing-axis unhomed">
		              <div id="homingAxisTrack" class="multi-point-axis-track">
		                <div id="homingCurrentMarker" class="multi-point-current-marker">
		                  <span id="homingCurrentValue" class="multi-point-current-value">0.0 deg</span>
		                </div>
		                <div id="homingUnhomedLabel" class="homing-unhomed-label">未回零</div>
		              </div>
		              <div class="axis-labels"><span id="homingAxisMin">-360 deg</span><span></span><span id="homingAxisMax">+360 deg</span></div>
		            </div>
		            <div id="homingPanelSetCurrent" class="homing-method-panel active">
	              <label class="homing-field">
	                <span id="homingSetCurrentPositionLabel">将当前位置设定为</span>
	                <div class="homing-set-current-row">
	                  <div class="unit-input"><input id="homingSetPosition" type="number" inputmode="decimal" data-touch-keypad data-touch-signed="true" step="0.001" value="0" onchange="onHomingSettingsChange()"><span id="homingSetPositionUnit">deg</span></div>
	                  <button id="homingSetCurrentApply" class="homing-inline-apply" type="button" onclick="startHomingFromPanel()">确定</button>
	                </div>
	              </label>
	            </div>
	            <div id="homingPanelTorqueEnd" class="homing-method-panel">
	              <div class="homing-grid">
	                <label class="homing-field">
	                  <span id="homingTorqueSetPositionLabel">受阻后设定位置</span>
	                  <div class="unit-input"><input id="homingTorqueSetPosition" type="number" inputmode="decimal" data-touch-keypad min="0" step="0.001" value="0" onchange="onHomingSettingsChange()"><span id="homingTorqueSetPositionUnit">deg</span></div>
	                </label>
	                <label class="homing-field">
	                  <span id="homingDirectionLabel">回零方向</span>
	                  <select id="homingDirection" onchange="onHomingSettingsChange()">
	                    <option value="reverse">反向</option>
	                    <option value="forward">正向</option>
	                  </select>
	                </label>
	                <label class="homing-field">
	                  <span id="homingSpeedLabel">回零速度</span>
	                  <div class="unit-input"><input id="homingSpeed" type="number" inputmode="decimal" data-touch-keypad min="0.001" step="0.001" value="5" onchange="onHomingSettingsChange()"><span id="homingSpeedUnit">deg/s</span></div>
	                </label>
	                <label class="homing-field">
	                  <span id="homingTorqueLabel">扭矩阈值</span>
	                  <div class="unit-input"><input id="homingTorqueThreshold" type="number" inputmode="numeric" data-touch-keypad min="1" max="100" step="1" value="20" onchange="onHomingSettingsChange()"><span>%</span></div>
	                </label>
	                <label class="homing-field homing-wide">
	                  <span id="homingMaxDistanceLabel">最大搜索距离</span>
	                  <div class="unit-input"><input id="homingMaxDistance" type="number" inputmode="decimal" data-touch-keypad min="0.001" step="0.001" value="10" onchange="onHomingSettingsChange()"><span id="homingMaxDistanceUnit">deg</span></div>
	                </label>
		              </div>
		            </div>
		            <div id="homingNote" class="control-note">当前位置设定不会驱动电机运动，只更新当前位置对应的负载坐标。</div>
		          </div>
		        </div>
        <div id="panel-velocity" class="mode-panel">
          <div class="slider-card">
            <div class="slider-head"><span class="slider-title">速度点动</span><span id="velText" class="slider-number">--</span></div>
            <input id="velCps" type="range" min="10000" max="1200000" step="10000" value="200000" oninput="updateSliders()">
            <div class="control-row three">
              <button class="blue" onclick="jogVelocity(-Number(velCps.value))">反转</button>
              <button class="stop" onclick="stopMotion()">停止</button>
              <button class="blue" onclick="jogVelocity(Number(velCps.value))">正转</button>
            </div>
          </div>
        </div>
        <div id="panel-torque" class="mode-panel">
          <div class="slider-card">
            <div class="slider-head"><span class="slider-title">转矩指令</span><span id="torqueText" class="slider-number">--</span></div>
            <input id="torqueCmd" type="range" min="-100" max="100" step="1" value="0" oninput="updateSliders()">
            <div class="control-note">当前仅暂存指令，未切换到 CST 转矩 PDO 输出。</div>
            <button class="neutral" onclick="sendTorque()">写入暂存</button>
          </div>
        </div>
        <div id="panel-gear_cam" class="mode-panel">
          <div id="gearPanelCard" class="slider-card gear-panel">
            <div class="gear-meta">
              <div class="gear-field">
                <div class="gear-field-head">
                  <span class="label">主轴</span>
                  <span id="gearMasterUnitBadge" class="gear-axis-unit">deg</span>
                </div>
                <select id="gearMasterSelect" onchange="updateGearMaster()">
                  <option value="mctivity">Axis A</option>
                  <option value="fv3">Axis B</option>
                  <option value="aux_encoder">Axis C</option>
                  <option value="virtual">虚拟主轴</option>
                </select>
              </div>
              <div class="gear-field">
                <div class="gear-field-head">
                  <span class="label">从轴</span>
                  <span id="gearSlaveUnitBadge" class="gear-axis-unit">deg</span>
                </div>
                <span id="gearSlaveName" class="gear-name">--</span>
              </div>
            </div>
            <div id="gearRatioBlock" class="gear-ratio">
              <div class="vertical-sliders">
                <div class="vertical-slider gear-slider master">
                  <div class="gear-wheel" onwheel="onGearWheel(event, 'master')" onclick="onGearTap(event, 'master')" onpointerdown="startGearDrag(event, 'master')" onpointermove="moveGearDrag(event)" onpointerup="endGearDrag()" onpointercancel="endGearDrag()">
                    <div class="gear-wheel-values">
                      <span id="gearMasterPrev2" class="gear-ratio-value far">0</span>
                      <span id="gearMasterPrev" class="gear-ratio-value edge">0</span>
                      <span id="gearMasterRatioText" class="gear-ratio-value current">1</span>
                      <span id="gearMasterNext" class="gear-ratio-value edge">2</span>
                      <span id="gearMasterNext2" class="gear-ratio-value far">3</span>
                    </div>
                  </div>
                  <input id="gearMasterRatio" type="hidden" value="1">
                </div>
                <div class="vertical-slider gear-slider slave">
                  <div class="gear-wheel" onwheel="onGearWheel(event, 'slave')" onclick="onGearTap(event, 'slave')" onpointerdown="startGearDrag(event, 'slave')" onpointermove="moveGearDrag(event)" onpointerup="endGearDrag()" onpointercancel="endGearDrag()">
                    <div class="gear-wheel-values">
                      <span id="gearSlavePrev2" class="gear-ratio-value far">0</span>
                      <span id="gearSlavePrev" class="gear-ratio-value edge">0</span>
                      <span id="gearSlaveRatioText" class="gear-ratio-value current">1</span>
                      <span id="gearSlaveNext" class="gear-ratio-value edge">2</span>
                      <span id="gearSlaveNext2" class="gear-ratio-value far">3</span>
                    </div>
                  </div>
                  <input id="gearSlaveRatio" type="hidden" value="1">
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>
    </div>
  </div>
</section>
<section id="tabConfig" class="tab-panel">
  <div class="config-grid">
    <section class="card">
      <h2>单点定位</h2>
      <div class="param-grid">
        <div class="param-card">
          <h3>位置/点动</h3>
          <label>默认相对位移<input id="cfgRel" type="number" value="4194304" step="1024" onchange="applyConfig()"></label>
          <label>默认绝对位置<input id="cfgAbs" type="number" value="0" step="1024" onchange="applyConfig()"></label>
          <label>默认运动时间 ms<input id="cfgMs" type="number" value="3000" min="500" max="15000" step="100" onchange="applyConfig()"></label>
        </div>
        <div class="param-card">
          <h3>速度/转矩</h3>
          <label>默认速度 cnt/s<input id="cfgVel" type="number" value="200000" min="10000" step="10000" onchange="applyConfig()"></label>
          <label>转矩上限 %<input id="cfgTorqueLimit" type="number" value="100" min="1" max="100" step="1" onchange="applyConfig()"></label>
        </div>
        <div class="param-card">
          <h3>电子齿轮</h3>
          <label>齿轮比分子<input id="cfgGearNum" type="number" value="1" min="1" step="1"></label>
          <label>齿轮比分母<input id="cfgGearDen" type="number" value="1" min="1" step="1"></label>
        </div>
        <div class="param-card">
          <h3>电子凸轮</h3>
          <label>凸轮表编号<input id="cfgCam" type="number" value="1" min="1" step="1"></label>
          <label>同步周期 ms<input id="cfgSyncMs" type="number" value="1" min="1" step="1"></label>
        </div>
      </div>
    </section>
    <section class="card">
      <h2>点表配置</h2>
      <div class="point-table">
        <div class="point-row"><strong>P1</strong><span id="p1">0 cnt</span><div class="point-actions"><button class="neutral" onclick="savePoint(1)">记录</button></div></div>
        <div class="point-row"><strong>P2</strong><span id="p2">4194304 cnt</span><div class="point-actions"><button class="neutral" onclick="savePoint(2)">记录</button></div></div>
        <div class="point-row"><strong>P3</strong><span id="p3">8388608 cnt</span><div class="point-actions"><button class="neutral" onclick="savePoint(3)">记录</button></div></div>
      </div>
    </section>
  </div>
</section>
</main>
<div id="transmissionModal" class="modal-shell" onclick="maybeCloseTransmissionDialog(event)">
  <div class="modal-card" onclick="event.stopPropagation()">
    <h3 id="transmissionModalTitle" class="modal-title">传动设定</h3>
    <div class="modal-grid">
      <div class="mode-row modal-mode-row">
        <span id="transmissionTypeFieldLabel" class="label">运动类型</span>
        <select id="transmissionType" onchange="onTransmissionTypeChange()">
          <option value="rotary">旋转</option>
          <option value="linear">直线</option>
        </select>
      </div>
      <div class="mode-row modal-mode-row">
        <span id="transmissionTravelModeLabel" class="label">运动方式</span>
        <select id="transmissionTravelMode" onchange="onTransmissionTravelModeChange()">
          <option value="periodic">周期</option>
          <option value="reciprocating">往返</option>
        </select>
      </div>
      <div class="mode-row modal-mode-row">
        <span id="transmissionDirectionLabel" class="label">运动方向</span>
        <div class="direction-control">
          <span id="transmissionReverseText" class="direction-label">反向</span>
          <button id="transmissionDirectionToggle" class="direction-toggle" type="button" onclick="toggleTransmissionDirection()"><span class="knob"></span></button>
          <span id="transmissionForwardText" class="direction-label">正向</span>
        </div>
      </div>
      <div class="modal-ratio-row">
        <span id="transmissionAmountLabel" class="modal-inline-label">负载运动</span>
        <input id="transmissionAmount" type="number" min="0.001" step="0.001" value="360" oninput="updateTransmissionDraft()">
        <select id="transmissionUnit" onchange="updateTransmissionDraft()"></select>
      </div>
      <div class="modal-ratio-row motor">
        <span id="transmissionRevsLabel" class="modal-inline-label">电机旋转</span>
        <input id="transmissionRevs" type="number" min="0.001" step="0.001" value="1" oninput="updateTransmissionDraft()">
        <span class="modal-inline-unit">Rev</span>
      </div>
      <div id="transmissionPeriodRow" class="modal-ratio-row">
        <span id="transmissionPeriodLabel" class="modal-inline-label">周期</span>
        <input id="transmissionPeriod" type="number" min="0.001" step="0.001" value="360" oninput="updateTransmissionDraft()">
        <span id="transmissionPeriodUnit" class="modal-inline-unit">deg</span>
      </div>
      <div id="transmissionForwardRow" class="modal-ratio-row hidden">
        <span id="transmissionForwardLabel" class="modal-inline-label">正向极限</span>
        <input id="transmissionForwardLimit" type="number" step="0.001" value="360" oninput="updateTransmissionDraft()">
        <span id="transmissionForwardUnit" class="modal-inline-unit">deg</span>
      </div>
      <div id="transmissionReverseRow" class="modal-ratio-row hidden">
        <span id="transmissionReverseLabel" class="modal-inline-label">反向极限</span>
        <input id="transmissionReverseLimit" type="number" step="0.001" value="-360" oninput="updateTransmissionDraft()">
        <span id="transmissionReverseUnit" class="modal-inline-unit">deg</span>
      </div>
    </div>
    <div class="modal-actions">
      <button id="transmissionCancelBtn" class="neutral" onclick="closeTransmissionDialog()">取消</button>
      <button id="transmissionSaveBtn" class="blue" onclick="saveTransmissionDialog()">保存</button>
    </div>
  </div>
</div>
		<div id="diagModal" class="modal-shell" onclick="maybeCloseDiagModal(event)">
		  <div class="modal-card" onclick="event.stopPropagation()">
		    <h3 id="diagModalTitle" class="modal-title">运行诊断</h3>
		    <pre id="diagModalBody" class="diag-body"></pre>
		    <div class="modal-actions">
	      <button id="diagCopyBtn" class="neutral" onclick="copyDiagText()">复制</button>
	      <button id="diagCloseBtn" class="blue" onclick="closeDiagModal()">关闭</button>
		    </div>
		  </div>
		</div>
		<div id="motionConfirmModal" class="modal-shell" onclick="maybeCancelMotionConfirm(event)">
		  <div class="modal-card motion-confirm-card" onclick="event.stopPropagation()">
		    <h3 id="motionConfirmTitle" class="modal-title">确认运动</h3>
		    <pre id="motionConfirmBody" class="diag-body motion-confirm-body"></pre>
		    <div class="modal-actions motion-confirm-actions">
		      <button id="motionConfirmCancelBtn" class="neutral" onclick="resolveMotionConfirm(false)">取消</button>
		      <button id="motionConfirmOkBtn" class="blue motion-confirm-primary" onclick="resolveMotionConfirm(true)">确认开始</button>
		    </div>
		  </div>
		</div>
		<div id="touchKeypadModal" class="touch-keypad-shell" onclick="maybeCloseTouchKeypad(event)">
	  <div id="touchKeypadPanel" class="touch-keypad" onclick="event.stopPropagation()">
	    <div id="touchKeypadDragHandle" class="touch-keypad-head">
	      <span class="touch-keypad-grip" aria-hidden="true"></span>
	      <span id="touchKeypadTitle" class="touch-keypad-title">输入数值</span>
	      <button class="touch-keypad-close" type="button" onclick="closeTouchKeypad()" aria-label="Close">×</button>
	    </div>
	    <div id="touchKeypadValue" class="touch-keypad-value">0</div>
	    <div class="touch-keypad-grid">
	      <button class="touch-keypad-key digit" type="button" onclick="touchKeypadPress('7')">7</button>
	      <button class="touch-keypad-key digit" type="button" onclick="touchKeypadPress('8')">8</button>
	      <button class="touch-keypad-key digit" type="button" onclick="touchKeypadPress('9')">9</button>
	      <button class="touch-keypad-key digit" type="button" onclick="touchKeypadPress('4')">4</button>
	      <button class="touch-keypad-key digit" type="button" onclick="touchKeypadPress('5')">5</button>
	      <button class="touch-keypad-key digit" type="button" onclick="touchKeypadPress('6')">6</button>
	      <button class="touch-keypad-key digit" type="button" onclick="touchKeypadPress('1')">1</button>
	      <button class="touch-keypad-key digit" type="button" onclick="touchKeypadPress('2')">2</button>
	      <button class="touch-keypad-key digit" type="button" onclick="touchKeypadPress('3')">3</button>
	      <button id="touchKeypadSignBtn" class="touch-keypad-key utility" type="button" onclick="touchKeypadToggleSign()">±</button>
	      <button class="touch-keypad-key digit" type="button" onclick="touchKeypadPress('0')">0</button>
	      <button id="touchKeypadDecimalBtn" class="touch-keypad-key utility" type="button" onclick="touchKeypadPress('.')">.</button>
	    </div>
	    <div class="touch-keypad-actions">
	      <button id="touchKeypadClearBtn" class="touch-keypad-key destructive" type="button" onclick="touchKeypadClear()">清空</button>
	      <button id="touchKeypadDeleteBtn" class="touch-keypad-key utility" type="button" onclick="touchKeypadBackspace()">退格</button>
	      <button id="touchKeypadDoneBtn" class="touch-keypad-key primary" type="button" onclick="touchKeypadCommit()">确定</button>
	    </div>
	  </div>
	</div>
	<div id="multiPointCyclePopup" class="multi-point-cycle-popup" aria-live="polite">
  <div class="multi-point-cycle-popup-card">
    <div class="multi-point-cycle-popup-head">
      <div class="multi-point-cycle-popup-copy">
        <span id="multiPointCyclePopupTitle" class="multi-point-cycle-popup-title">当前循环</span>
        <strong id="multiPointCyclePopupRow" class="multi-point-cycle-popup-row">P1</strong>
      </div>
      <button id="multiPointCyclePopupClose" class="multi-point-cycle-popup-close" type="button" aria-label="Close" onclick="closeMultiPointCyclePopup()">×</button>
    </div>
    <div class="multi-point-cycle-popup-body">
      <span id="multiPointCyclePopupCurrent" class="multi-point-cycle-popup-current">1</span>
      <span class="multi-point-cycle-popup-slash">/</span>
      <span id="multiPointCyclePopupTotal" class="multi-point-cycle-popup-total">1</span>
    </div>
    <div id="multiPointCyclePopupStatus" class="multi-point-cycle-popup-status">点表运行中</div>
  </div>
</div>
<script>
__MOTION_CURVE_EDITOR_BLOCK__
</script>
<script>
const REV = 8388608;
const AUX_ENCODER_COUNTS_PER_REV = 262144;
const MAX_HOMING_SEARCH_COUNTS = __MAX_HOMING_SEARCH_COUNTS__;
const MAX_SPEED_RPM = __MAX_SPEED_RPM__;
const MAX_ACCEL_RPM_S = __MAX_ACCEL_RPM_S__;
const AXIS_DIR = -1;
const LANG_KEY = 'mctivity_lang';
const API_TOKEN_KEY = 'MCTIVITY_API_TOKEN';
let mockFaultCode = new URLSearchParams(window.location.search).get('mock_fault');
const MOCK_FAULT_OPTIONS = [
  {raw:'0', zh:'READY / 无故障', en:'READY / No fault', kind:'ready'},
  {raw:'0xFFFF', zh:'0xFFFF / 模拟原始码', en:'0xFFFF / Simulated raw code', kind:'sample'}
];
const MODE_LABELS = {
  zh: {position:'单点定位', anti_sway_position:'电子防摇定位', incremental:'增量位移', jog:'点动', point:'点位表', multi_point:'多点定位', homing:'回零', velocity:'速度控制', torque:'转矩控制', gear_cam:'电子齿轮'},
  en: {position:'Point Positioning', anti_sway_position:'Anti-Sway Positioning', incremental:'Incremental Displacement', jog:'Jog', point:'Point Table', multi_point:'Multi-Point Positioning', homing:'Homing', velocity:'Velocity Control', torque:'Torque Control', gear_cam:'Electronic Gearing'}
};
const UI_TEXT = {
  zh: {
    pageTitle:'轴控',
	    axisA:'轴 A',
	    axisB:'轴 B',
	    axisC:'轴 C',
    profile:'装配',
    features:'模块',
    capabilities:'能力',
    warnings:'告警',
    unsupportedCommand:'命令不可用',
    requiredCapability:'缺少能力',
    commandParameterInvalid:'命令参数不可用，请检查数值范围。',
    motionNotReady:'伺服未就绪',
	    motionInterruptedTitle:'运动已中断',
	    motionInterruptedBody:'运动过程中控制器检测到状态异常：{reason}。请检查使能反馈、驱动状态和 EtherCAT 通讯后重试。',
	    motionConfirmCancel:'取消',
	    motionConfirmStart:'确认开始',
	    positionExecutionConfirmTitle:'确认单点定位',
	    positionExecutionConfirm:'将执行单点定位。\n目标位置：{target} {unit}\n本次位移：{distance} {unit}\n请确认现场安全后继续。',
	    unauthorizedTitle:'未授权',
    unauthorizedBody:'API Token 缺失或无效。',
    apiToken:'API Token',
    virtualAxis:'虚拟主轴',
	    motorFeedback:'电机反馈',
	    encoderFeedback:'编码器反馈',
	    position:'位置',
	    torque:'扭矩',
	    speed:'速度',
    control:'控制',
    mode:'模式',
    enable:'使能',
    startStop:'启停',
    status:'状态',
    returnZero:'一键回到零位',
    setZero:'当前位置置零',
	    targetAbs:'目标绝对位置',
	    loadPosition:'负载位置',
	    accel:'加速度',
    relMove:'相对位移',
    moveTime:'运动时间',
    slowerHint:'数值越大越慢',
    move:'移动',
    zeroPanel:'回零',
    softwareZero:'软件零点',
    zeroNote:'当前位置设定不会驱动电机运动，只更新当前位置对应的负载坐标。',
    homingParams:'回零参数',
    homingMethod:'回零方式',
    homingMethodSetCurrent:'在当前位置回零',
    homingMethodTorqueEnd:'末端受阻回零',
    homingSetCurrentPosition:'将当前位置设定为',
    homingTorqueSetPosition:'受阻回零后回退距离',
    homingDirection:'回零方向',
    homingSpeed:'回零速度',
    homingTorqueThreshold:'扭矩阈值',
    homingMaxDistance:'最大搜索距离',
    homingSetCurrentNote:'不会驱动电机运动；应用后，当前位置会被写成设定的负载坐标。',
    homingTorqueNote:'将会驱动电机按设定方向低速移动；受阻后写成对应行程极限，再按设定距离回退，请注意安全。',
	    homingTorqueUnavailable:'当前轴没有可用扭矩反馈，只允许在当前位置回零。',
	    homingSetCurrentConfirm:'当前位置将被设定为 {value} {unit}，是否继续？',
	    homingTorqueConfirm:'将会驱动电机按设定方向低速移动；检测到受阻末端后，受阻末端点会被设定为 {limit} {unit}，随后回退 {backoff} {unit}。请确认现场安全后再开始回零。是否继续？',
	    homingMaxDistanceExceeded:'最大搜索距离超出保护上限。当前传动比下最大为 {value} {unit}。',
	    homingServoNotReady:'末端受阻回零需要伺服已使能并稳定。请先打开使能，确认无故障后再开始。',
	    homingCompleteTitle:'回零完成',
	    homingCompleteBody:'当前位置已设定为 {value} {unit}。',
	    homingTorqueCompleteBody:'受阻末端点已设定为 {limit} {unit}，并已回退到当前位置 {value} {unit}。',
	    homingBlockedNotFoundTitle:'未检测到受阻点',
	    homingBlockedNotFoundBody:'已运动到最大搜索距离 {distance} {unit}，但扭矩未达到阈值。请检查回零方向、扭矩阈值或机械末端。',
	    homingTimeoutTitle:'回零超时',
	    homingTimeoutBody:'在设定时间内未完成回零。请检查回零方向、速度、扭矩阈值和机械状态。',
	    homingCancelledTitle:'回零已中断',
	    homingCancelledBody:'回零过程中控制器检测到状态异常：{reason}。请检查使能反馈、驱动状态和 EtherCAT 通讯后重试。',
	    homingUnhomed:'未回零',
	    homingSetCurrentApply:'确定',
	    homingApply:'应用',
	    homingStart:'开始回零',
	    homingRunning:'回零中',
	    homingRunningTitle:'正在回零',
	    homingRunningBody:'末端受阻回零正在执行，完成或异常时会自动更新提示。',
    velocityJog:'速度点动',
    reverse:'反转',
    stop:'停止',
    forward:'正转',
    torqueCmd:'转矩指令',
    torqueNote:'当前仅暂存指令，未切换到 CST 转矩 PDO 输出。',
    stageWrite:'写入暂存',
	    gearMaster:'主轴',
	    gearSlave:'从轴',
	    pointPositioning:'单点定位',
    pointConfig:'点表配置',
    multiPointParams:'多点定位参数',
    multiPointStart:'起始点表',
    multiPointStep:'点表步数',
    multiPointCycleCount:'循环次数',
    multiPointEdit:'开始修改',
    multiPointWrite:'确定写入',
    multiPointRow:'序号',
    multiPointTarget:'目标位置',
    multiPointDwell:'停留',
    multiPointEnabled:'启用',
    multiPointIdle:'等待点表',
    multiPointRunning:'点表运行中',
    multiPointStopping:'点表停止中',
    multiPointComplete:'点表完成',
    multiPointStopped:'点表已停止',
    multiPointCyclePopupTitle:'当前循环',
    multiPointCyclePopupHint:'点击放大查看当前循环次数',
    posJog:'位置/点动',
    incrementalParams:'增量位移参数',
    speedTorque:'速度/转矩',
    gear:'电子齿轮',
    cam:'电子凸轮',
    defaultRel:'默认相对位移',
    defaultAbs:'默认绝对位置',
    defaultMoveMs:'默认运动时间',
    defaultVel:'默认速度',
    torqueLimit:'转矩上限',
    gearNum:'齿轮比分子',
    gearDen:'齿轮比分母',
    camTable:'凸轮表编号',
    syncPeriod:'同步周期',
    record:'记录',
	    currentTurns:'当前圈数',
	    currentAngle:'当前编码器角度',
	    currentPulses:'编码器脉冲数',
	    singleTurn:'单圈计数',
	    loadTurns:'负载圈数',
	    loadAngle:'负载角度',
	    loadFeedback:'负载反馈',
	    loadPulses:'负载反馈脉冲数',
	    posParams:'定位参数',
	    antiSwayParams:'电子防摇定位参数',
	    antiSwayPositionParams:'定位参数',
    antiSwayPreview:'UI 预览',
    antiSwayMonitor:'摆动监控',
    antiSwayInputReady:'输入数据已就绪',
    antiSwayWaitingPosition:'等待定位到位',
    antiSwayOutOfBand:'摆动偏大',
    antiSwaySettling:'稳定计时中',
    antiSwayStable:'已稳定',
    antiSwayNoSensor:'等待摆角检测',
    antiSwayTarget:'目标位置',
	    antiSwayCurrent:'当前位置',
	    antiSwayAlgorithm:'防摇算法',
	    antiSwayAlgorithmContinuous:'全程防摇',
	    antiSwayAlgorithmTerminal:'终点防摇',
	    antiSwaySensorAxis:'摆角检测',
	    antiSwayRodLength:'摆杆长度',
	    antiSwayCalibratePeriod:'校准周期',
	    antiSwayPeriodCalculated:'计算',
	    antiSwayPeriodMeasured:'实测',
	    antiSwayPeriodCalibratedTitle:'周期校准完成',
	    antiSwayPeriodCalibratedBody:'已根据摆角波形估算自然周期为 {period} s。后续防摇轨迹会优先使用这个实测周期。',
	    antiSwayPeriodCalibrateFailedTitle:'周期校准未完成',
	    antiSwayPeriodCalibrateFailedBody:'当前摆角波形不足或幅度太小。请让摆杆自然摆动一到两次后再校准。',
	    antiSwayAllowAngle:'允许摆角',
	    antiSwayCurrentAngle:'当前摆角',
	    antiSwayPeak:'峰值摆角',
	    antiSwayPeakShort:'峰值',
	    antiSwayResidual:'残余摆角',
	    antiSwayResidualShort:'残余',
	    antiSwayPhase:'相位',
	    antiSwayPhaseNeutral:'接近中位',
	    antiSwayPhasePositiveOut:'正向外摆',
	    antiSwayPhasePositiveReturn:'正向回中',
	    antiSwayPhaseNegativeOut:'反向外摆',
	    antiSwayPhaseNegativeReturn:'反向回中',
	    antiSwayPhaseCrossPositive:'过中向正',
	    antiSwayPhaseCrossNegative:'过中向反',
	    antiSwayPhaseLagOut:'滞后外摆',
	    antiSwayPhaseLagCatch:'滞后追赶',
	    antiSwayPhaseLeadOut:'超前外摆',
	    antiSwayPhaseLeadReturn:'超前回中',
	    antiSwayAngularVelocity:'摆角速度',
	    antiSwaySettle:'预计稳定',
	    antiSwaySettleShort:'稳定',
	    antiSwayRunSummary:'本次记录',
	    antiSwayState:'状态',
	    antiSwayPreviewState:'预览',
	    antiSwayPreviewNote:'显示到位后的摆角稳定判断；实跑后会记录本次峰值和残余摆角。',
	    antiSwayPreviewUnavailable:'电子防摇定位目前是 UI 预览，暂未接入运动控制逻辑。',
	    antiSwayPlan:'轨迹预演',
	    antiSwayPlanPreviewOnly:'仅预演',
	    antiSwayPlanNormal:'普通定位',
	    antiSwayPlanShaped:'防摇定位',
	    antiSwayNaturalPeriod:'自然周期',
	    antiSwayPlanDelay:'预估延时',
	    antiSwayPlanDuration:'预演时长',
	    antiSwayDryRunReadyTitle:'防摇执行干跑已准备',
	    antiSwayDryRunReadyBody:'参数链路已打通，本次没有下发运动命令。自然周期 {period} s，预估延时 +{delay} s。',
	    antiSwayDryRunBlockedTitle:'防摇干跑未就绪',
	    antiSwayDryRunNeedModule:'电子防摇模块未装配。',
	    antiSwayDryRunNeedHoming:'当前轴需要先回零。',
	    antiSwayDryRunNeedEnable:'当前轴需要先使能。',
	    antiSwayDryRunNeedNoFault:'当前轴存在故障。',
	    antiSwayDryRunNeedSensor:'摆角检测轴没有可用反馈。',
	    antiSwayDryRunNeedTarget:'目标位置、速度或加速度不完整。',
	    antiSwayExecutionLocked:'防摇实跑：锁定',
	    antiSwayExecutionUnlocked:'防摇实跑：已解锁',
	    antiSwayExecutionLockedHelp:'当前只会做干跑，不会下发真实运动。',
	    antiSwayExecutionUnlockedHelp:'防摇实跑已解锁，启动前仍会弹窗确认，并按传动软限位保护。',
	    antiSwayExecutionConfirmTitle:'确认防摇定位',
	    antiSwayExecutionConfirm:'将下发防摇定位轨迹。\n目标位置：{target} {unit}\n当前位置：{current} {unit}\n本次位移：{delta} {unit}\n请确认现场安全后继续。',
	    antiSwayPhaseWaitingTitle:'等待防摇相位',
	    antiSwayPhaseWaitingBody:'正在等待摆杆进入目标方向的超前回中窗口，最长约 {time} s。可用启停开关取消。',
	    antiSwayPhaseGateReady:'相位就绪',
	    antiSwayPhaseGateNeutral:'摆角很小',
	    antiSwayPhaseGateTimeout:'相位等待超时',
	    antiSwayExecutionRunningTitle:'防摇执行中',
	    antiSwayExecutionRunningBody:'正在执行连续防摇轨迹。可随时用启停开关停止。',
	    antiSwayExecutionCompleteTitle:'防摇完成',
	    antiSwayExecutionCompleteBody:'已完成防摇定位轨迹，目标位置 {target} {unit}，本次位移 {delta} {unit}。',
	    antiSwayExecutionStoppedTitle:'防摇已停止',
	    antiSwayExecutionStoppedBody:'执行过程已中断，当前位置请以反馈数据为准。',
	    antiSwayTerminalPreviewTitle:'终点防摇尚未接入实跑',
	    antiSwayTerminalPreviewBody:'当前已接入终点防摇算法参数和预演数据，但真实运动控制尚未接入 motiond，因此不会下发运动命令。',
    gearParams:'电子齿轮参数',
    controlSuffix:'控制',
    off:'OFF',
    on:'ON',
    ready:'READY',
    fault:'FAULT',
    reset:'复位',
    standstill:'静止',
    inMotion:'运动中',
    gearing:'齿轮联动',
    moving:'运动',
    idle:'空闲',
    ok:'正常',
    faultServo:'伺服故障',
    faultCode:'错误码',
    mockFaultLabel:'机器原始故障码模拟',
    mockFaultBadge:'本机测试',
    mockFaultHint:'仅本机模拟状态，不下发控制命令。',
    mockFaultCustom:'自定义 raw 码',
    gearLockedPrefix:'无法选择电子齿轮模式，因为',
    gearLockedSuffix:'已经将此轴设为主轴',
    gearUnavailable:'电子齿轮（不可用）',
    transmissionLabel:'传动',
    transmissionModalTitle:'传动设定',
    transmissionTypeFieldLabel:'运动类型',
    transmissionTravelModeLabel:'运动方式',
    transmissionDirectionLabel:'运动方向',
	    transmissionAmountLabel:'负载运动',
	    transmissionRevsLabel:'电机旋转',
	    transmissionEncoderRevsLabel:'编码器旋转',
    transmissionPeriodLabel:'周期',
    transmissionForwardLabel:'正向极限',
    transmissionReverseLabel:'反向极限',
    transmissionCancelBtn:'取消',
    transmissionSaveBtn:'保存',
	    copy:'复制',
	    close:'关闭',
	    touchKeypadTitle:'输入数值',
	    touchKeypadClear:'清空',
	    touchKeypadDelete:'退格',
	    touchKeypadDone:'确定',
	    rotaryType:'旋转',
    linearType:'直线',
    periodicTravel:'周期',
    reciprocatingTravel:'往返',
    forwardDirection:'正方向',
    reverseDirection:'反方向',
	    loadPrefix:'负载 ',
	    motorPrefix:'电机 ',
	    encoderPrefix:'编码器 ',
	    revUnit:'rev'
  },
  en: {
    pageTitle:'Axis Control',
	    axisA:'Axis A',
	    axisB:'Axis B',
	    axisC:'Axis C',
    profile:'Profile',
    features:'Features',
    capabilities:'Caps',
    warnings:'Warn',
    unsupportedCommand:'Unsupported Command',
    requiredCapability:'Required Capability',
    commandParameterInvalid:'Command parameters are invalid. Check numeric limits.',
    motionNotReady:'Servo Not Ready',
	    motionInterruptedTitle:'Motion Interrupted',
	    motionInterruptedBody:'The controller detected an abnormal state during motion: {reason}. Check enable feedback, drive state, and EtherCAT communication before retrying.',
	    motionConfirmCancel:'Cancel',
	    motionConfirmStart:'Confirm Start',
	    positionExecutionConfirmTitle:'Confirm Point Positioning',
	    positionExecutionConfirm:'The axis will run one point positioning move.\nTarget position: {target} {unit}\nMove distance: {distance} {unit}\nConfirm the machine is safe before continuing.',
	    unauthorizedTitle:'Unauthorized',
    unauthorizedBody:'API token is missing or invalid.',
    apiToken:'API Token',
    virtualAxis:'Virtual Master',
	    motorFeedback:'Motor Feedback',
	    encoderFeedback:'Encoder Feedback',
	    position:'Position',
	    torque:'Torque',
	    speed:'Speed',
    control:'Control',
    mode:'Mode',
    enable:'Enable',
    startStop:'Start/Stop',
    status:'Status',
    returnZero:'Return To Zero',
    setZero:'Set Current As Zero',
	    targetAbs:'Absolute Target',
	    loadPosition:'Load Position',
	    accel:'Acceleration',
    relMove:'Relative Displacement',
    moveTime:'Move Time',
    slowerHint:'Higher values move slower',
    move:'Move',
    zeroPanel:'Homing',
    softwareZero:'Software Zero',
    zeroNote:'Set-current does not move the motor; it only updates the load coordinate assigned to the current position.',
    homingParams:'Homing Parameters',
    homingMethod:'Homing Method',
    homingMethodSetCurrent:'Home At Current Position',
    homingMethodTorqueEnd:'Blocked-End Homing',
    homingSetCurrentPosition:'Set current position to',
    homingTorqueSetPosition:'Backoff After Blocked Homing',
    homingDirection:'Homing Direction',
    homingSpeed:'Homing Speed',
    homingTorqueThreshold:'Torque Threshold',
    homingMaxDistance:'Max Search Distance',
    homingSetCurrentNote:'No motor motion is commanded; applying writes the current position as the configured load coordinate.',
    homingTorqueNote:'The motor will move slowly in the selected direction; after the blocked end is detected, it writes the matching travel limit and backs off by the configured distance. Check safety first.',
	    homingTorqueUnavailable:'This axis has no available torque feedback, so only current-position homing is allowed.',
	    homingSetCurrentConfirm:'The current position will be set to {value} {unit}. Continue?',
	    homingTorqueConfirm:'The motor will move slowly in the configured direction; after the blocked endpoint is detected, that endpoint will be set to {limit} {unit}, then the axis will back off {backoff} {unit}. Confirm the area is safe before starting homing. Continue?',
	    homingMaxDistanceExceeded:'Max search distance exceeds the protection limit. The current transmission allows up to {value} {unit}.',
	    homingServoNotReady:'Blocked-end homing requires the servo to be enabled and settled. Enable the servo and confirm there is no fault before starting.',
	    homingCompleteTitle:'Homing Complete',
	    homingCompleteBody:'The current position has been set to {value} {unit}.',
	    homingTorqueCompleteBody:'The blocked endpoint has been set to {limit} {unit}, and the axis has backed off to {value} {unit}.',
	    homingBlockedNotFoundTitle:'Blocked End Not Detected',
	    homingBlockedNotFoundBody:'The axis reached the max search distance of {distance} {unit}, but torque did not reach the threshold. Check homing direction, torque threshold, or the mechanical end.',
	    homingTimeoutTitle:'Homing Timeout',
	    homingTimeoutBody:'Homing did not complete in the configured time. Check homing direction, speed, torque threshold, and mechanical state.',
	    homingCancelledTitle:'Homing Interrupted',
	    homingCancelledBody:'The controller detected an abnormal state during homing: {reason}. Check enable feedback, drive state, and EtherCAT communication before retrying.',
	    homingUnhomed:'Not homed',
	    homingSetCurrentApply:'Confirm',
	    homingApply:'Apply',
    homingStart:'Start Homing',
    homingRunning:'Homing',
    homingRunningTitle:'Homing In Progress',
    homingRunningBody:'Blocked-end homing is running. This notice will update when homing completes or stops abnormally.',
    velocityJog:'Velocity Jog',
    reverse:'Reverse',
    stop:'Stop',
    forward:'Forward',
    torqueCmd:'Torque Command',
    torqueNote:'The current command is staged only and CST torque PDO output is not active.',
    stageWrite:'Stage Command',
	    gearMaster:'Master Axis',
	    gearSlave:'Slave Axis',
	    pointPositioning:'Point Positioning',
    pointConfig:'Point Table',
    multiPointParams:'Multi-Point Parameters',
    multiPointStart:'Start Row',
    multiPointStep:'Rows',
    multiPointCycleCount:'Cycles',
    multiPointEdit:'Edit',
    multiPointWrite:'Write',
    multiPointRow:'Row',
    multiPointTarget:'Target',
    multiPointDwell:'Dwell',
    multiPointEnabled:'Enable',
    multiPointIdle:'Point table idle',
    multiPointRunning:'Point table running',
    multiPointStopping:'Point table stopping',
    multiPointComplete:'Point table complete',
    multiPointStopped:'Point table stopped',
    multiPointCyclePopupTitle:'Current Cycle',
    multiPointCyclePopupHint:'Click to enlarge the current cycle count',
    posJog:'Position / Jog',
    incrementalParams:'Incremental Displacement Parameters',
    speedTorque:'Speed / Torque',
    gear:'Electronic Gearing',
    cam:'Electronic Cam',
    defaultRel:'Default Relative Move',
    defaultAbs:'Default Absolute Target',
    defaultMoveMs:'Default Move Time',
    defaultVel:'Default Speed',
    torqueLimit:'Torque Limit',
    gearNum:'Gear Numerator',
    gearDen:'Gear Denominator',
    camTable:'Cam Table ID',
    syncPeriod:'Sync Period',
    record:'Record',
	    currentTurns:'Turns',
	    currentAngle:'Encoder Angle',
	    currentPulses:'Encoder Pulses',
	    singleTurn:'Single-turn Count',
	    loadTurns:'Load Turns',
	    loadAngle:'Load Angle',
	    loadFeedback:'Load Feedback',
	    loadPulses:'Load Feedback Counts',
	    posParams:'Position Parameters',
	    antiSwayParams:'Anti-Sway Positioning Parameters',
	    antiSwayPositionParams:'Position Parameters',
    antiSwayPreview:'UI Preview',
    antiSwayMonitor:'Sway Monitor',
    antiSwayInputReady:'Input Ready',
    antiSwayWaitingPosition:'Waiting for position',
    antiSwayOutOfBand:'Sway too high',
    antiSwaySettling:'Settling',
    antiSwayStable:'Stable',
    antiSwayNoSensor:'Waiting for sensor',
    antiSwayTarget:'Target',
	    antiSwayCurrent:'Current',
	    antiSwayAlgorithm:'Anti-Sway Algorithm',
	    antiSwayAlgorithmContinuous:'Full-Path',
	    antiSwayAlgorithmTerminal:'Endpoint',
	    antiSwaySensorAxis:'Sway Sensor',
	    antiSwayRodLength:'Rod Length',
	    antiSwayCalibratePeriod:'Calibrate Period',
	    antiSwayPeriodCalculated:'Calc',
	    antiSwayPeriodMeasured:'Measured',
	    antiSwayPeriodCalibratedTitle:'Period Calibrated',
	    antiSwayPeriodCalibratedBody:'The natural period is estimated from the sway waveform as {period} s. Future anti-sway trajectories will prefer this measured period.',
	    antiSwayPeriodCalibrateFailedTitle:'Period Not Calibrated',
	    antiSwayPeriodCalibrateFailedBody:'The current sway waveform is too short or too small. Let the rod swing naturally for one or two cycles, then calibrate again.',
	    antiSwayAllowAngle:'Allowed Angle',
	    antiSwayCurrentAngle:'Current Angle',
	    antiSwayPeak:'Peak Angle',
	    antiSwayPeakShort:'Peak',
	    antiSwayResidual:'Residual Angle',
	    antiSwayResidualShort:'Residual',
	    antiSwayPhase:'Phase',
	    antiSwayPhaseNeutral:'Centered',
	    antiSwayPhasePositiveOut:'Positive Out',
	    antiSwayPhasePositiveReturn:'Positive Return',
	    antiSwayPhaseNegativeOut:'Negative Out',
	    antiSwayPhaseNegativeReturn:'Negative Return',
	    antiSwayPhaseCrossPositive:'Crossing +',
	    antiSwayPhaseCrossNegative:'Crossing -',
	    antiSwayPhaseLagOut:'Lag Out',
	    antiSwayPhaseLagCatch:'Lag Catch',
	    antiSwayPhaseLeadOut:'Lead Out',
	    antiSwayPhaseLeadReturn:'Lead Return',
	    antiSwayAngularVelocity:'Angular Speed',
	    antiSwaySettle:'Estimated Settle',
	    antiSwaySettleShort:'Settle',
	    antiSwayRunSummary:'Run Metrics',
	    antiSwayState:'State',
	    antiSwayPreviewState:'Preview',
	    antiSwayPreviewNote:'Shows post-arrival sway stability; real runs record peak and residual angle.',
	    antiSwayPreviewUnavailable:'Anti-sway positioning is currently a UI preview and is not connected to motion control logic yet.',
	    antiSwayPlan:'Trajectory Preview',
	    antiSwayPlanPreviewOnly:'Preview Only',
	    antiSwayPlanNormal:'Normal',
	    antiSwayPlanShaped:'Anti-Sway',
	    antiSwayNaturalPeriod:'Natural Period',
	    antiSwayPlanDelay:'Estimated Delay',
	    antiSwayPlanDuration:'Preview Time',
	    antiSwayDryRunReadyTitle:'Anti-Sway Dry Run Ready',
	    antiSwayDryRunReadyBody:'The parameter path is connected. No motion command was sent. Natural period {period} s, estimated delay +{delay} s.',
	    antiSwayDryRunBlockedTitle:'Anti-Sway Dry Run Not Ready',
	    antiSwayDryRunNeedModule:'Anti-sway module is not assembled.',
	    antiSwayDryRunNeedHoming:'The current axis must be homed first.',
	    antiSwayDryRunNeedEnable:'The current axis must be enabled first.',
	    antiSwayDryRunNeedNoFault:'The current axis has a fault.',
	    antiSwayDryRunNeedSensor:'The sway sensor axis has no usable feedback.',
	    antiSwayDryRunNeedTarget:'Target position, speed, or acceleration is incomplete.',
	    antiSwayExecutionLocked:'Anti-Sway Run: Locked',
	    antiSwayExecutionUnlocked:'Anti-Sway Run: Unlocked',
	    antiSwayExecutionLockedHelp:'Only dry runs are allowed. No real motion command will be sent.',
	    antiSwayExecutionUnlockedHelp:'Anti-sway real execution is unlocked. The HMI will still ask for confirmation and uses transmission soft limits.',
	    antiSwayExecutionConfirmTitle:'Confirm Anti-Sway Positioning',
	    antiSwayExecutionConfirm:'The axis will run one anti-sway positioning trajectory.\nTarget position: {target} {unit}\nCurrent position: {current} {unit}\nMove delta: {delta} {unit}\nConfirm the machine is safe before continuing.',
	    antiSwayPhaseWaitingTitle:'Waiting for Sway Phase',
	    antiSwayPhaseWaitingBody:'Waiting for the rod to enter the target-direction lead-return window, up to {time} s. Use the start/stop switch to cancel.',
	    antiSwayPhaseGateReady:'Phase Ready',
	    antiSwayPhaseGateNeutral:'Low Sway',
	    antiSwayPhaseGateTimeout:'Phase Wait Timeout',
	    antiSwayExecutionRunningTitle:'Anti-Sway Running',
	    antiSwayExecutionRunningBody:'Running one continuous anti-sway trajectory. Use the start/stop switch to stop at any time.',
	    antiSwayExecutionCompleteTitle:'Anti-Sway Complete',
	    antiSwayExecutionCompleteBody:'Anti-sway positioning trajectory is complete. Target position {target} {unit}; move delta {delta} {unit}.',
	    antiSwayExecutionStoppedTitle:'Anti-Sway Stopped',
	    antiSwayExecutionStoppedBody:'The execution was interrupted. Use live feedback as the current position reference.',
	    antiSwayTerminalPreviewTitle:'Endpoint Anti-Sway Not Connected',
	    antiSwayTerminalPreviewBody:'Endpoint anti-sway parameters and preview data are connected, but real motion control is not connected in motiond yet, so no motion command will be sent.',
    gearParams:'Gear Parameters',
    controlSuffix:'Control',
    off:'OFF',
    on:'ON',
    ready:'READY',
    fault:'FAULT',
    reset:'Reset',
    standstill:'Standstill',
    inMotion:'In Motion',
    gearing:'Gearing',
    moving:'Moving',
    idle:'Idle',
    ok:'OK',
    faultServo:'Servo Fault',
    faultCode:'Error Code',
    mockFaultLabel:'Raw Machine Fault Code Mock',
    mockFaultBadge:'Local test',
    mockFaultHint:'Local simulated status only. No control commands are sent.',
    mockFaultCustom:'Custom raw code',
    gearLockedPrefix:'Electronic gearing is unavailable because ',
    gearLockedSuffix:' is already using this axis as its master',
    gearUnavailable:'Electronic Gearing (Locked)',
    transmissionLabel:'Transmission',
    transmissionModalTitle:'Transmission Setup',
    transmissionTypeFieldLabel:'Motion Type',
    transmissionTravelModeLabel:'Travel Type',
    transmissionDirectionLabel:'Direction',
	    transmissionAmountLabel:'Load Travel',
	    transmissionRevsLabel:'Motor Rotation',
	    transmissionEncoderRevsLabel:'Encoder Rotation',
    transmissionPeriodLabel:'Period',
    transmissionForwardLabel:'Forward Limit',
    transmissionReverseLabel:'Reverse Limit',
    transmissionCancelBtn:'Cancel',
    transmissionSaveBtn:'Save',
	    copy:'Copy',
	    close:'Close',
	    touchKeypadTitle:'Enter Value',
	    touchKeypadClear:'Clear',
	    touchKeypadDelete:'Delete',
	    touchKeypadDone:'Done',
	    rotaryType:'ROTARY',
    linearType:'LINEAR',
    periodicTravel:'Periodic',
    reciprocatingTravel:'Reciprocating',
    forwardDirection:'Forward',
    reverseDirection:'Reverse',
	    loadPrefix:'Load ',
	    motorPrefix:'Motor ',
	    encoderPrefix:'Encoder ',
	    revUnit:'rev'
  }
};
const transmissionUnitSets = {
  rotary: [
    {value:'deg', label:'deg'},
    {value:'rad', label:'rad'}
  ],
  linear: [
    {value:'mm', label:'mm'},
    {value:'cm', label:'cm'},
    {value:'m', label:'m'}
  ]
};
let activeDevice = 'mctivity';
let currentLang = localStorage.getItem(LANG_KEY) === 'en' ? 'en' : 'zh';
const MULTI_POINT_MAX_CYCLES = 1000;
const statusByDevice = {mctivity:null, fv3:null, aux_encoder:null};
const antiSwayInputByDevice = {mctivity:null, fv3:null, aux_encoder:null};
const antiSwayTargetDrag = {active:false, pointerId:null, marker:null};
const antiSwayStabilityByDevice = {
  mctivity: {samples:[], periodSamples:[], inBandSince:0, stable:false, state:'waiting_sensor', lastSensorAxis:''},
  fv3: {samples:[], periodSamples:[], inBandSince:0, stable:false, state:'waiting_sensor', lastSensorAxis:''},
  aux_encoder: {samples:[], periodSamples:[], inBandSince:0, stable:false, state:'waiting_sensor', lastSensorAxis:''}
};
const antiSwayRunMetricsByDevice = {
  mctivity: {active:false, observing:false, samples:[], lastResult:null},
  fv3: {active:false, observing:false, samples:[], lastResult:null},
  aux_encoder: {active:false, observing:false, samples:[], lastResult:null}
};
const feedbackByDevice = {mctivity:null, fv3:null, aux_encoder:null};
const lastSoftZeroByDevice = {mctivity:null, fv3:null, aux_encoder:null};
const incrementalCurveSnapshots = {mctivity:'', fv3:'', aux_encoder:''};
const multiPointStatusByDevice = {mctivity:null, fv3:null, aux_encoder:null};
const motionStateByDevice = {
  mctivity: {latch:false, seenMoving:false, commandAt:0, commandSeq:0, stopRequested:false, gearEngaged:false, gearStoppedLatched:false, movingOffCandidateAt:0, enableVisual:false, enableOffCandidateAt:0, homingPending:false, homingPendingMethod:'', homingWasActive:false, homingNoticeKey:'', motionCancelNoticeKey:''},
  fv3: {latch:false, seenMoving:false, commandAt:0, commandSeq:0, stopRequested:false, gearEngaged:false, gearStoppedLatched:false, movingOffCandidateAt:0, enableVisual:false, enableOffCandidateAt:0, homingPending:false, homingPendingMethod:'', homingWasActive:false, homingNoticeKey:'', motionCancelNoticeKey:''},
  aux_encoder: {latch:false, seenMoving:false, commandAt:0, commandSeq:0, stopRequested:false, gearEngaged:false, gearStoppedLatched:false, movingOffCandidateAt:0, enableVisual:false, enableOffCandidateAt:0, homingPending:false, homingPendingMethod:'', homingWasActive:false, homingNoticeKey:'', motionCancelNoticeKey:''}
};
const deviceProfiles = {
  mctivity: {mode:'position', absPos:0, absSpeedRpm:120, absAccel:300, relDelta:4194304, moveMs:3000, velCps:200000, torqueCmd:0, gearMaster:'fv3', gearMasterRatio:1, gearSlaveRatio:1, homing:defaultHomingState(), incrementalCurve:{mode:'position', targetPosition:0, targetSpeed:0, accel:0, decel:0, dwell:0, blend:'smooth'}, multiPoint:{start:1, step:3, cycleCount:1, editing:false, rows:[{row:1, position:0, speed:120, acceleration:300, dwell:0, enabled:true}, {row:2, position:0, speed:120, acceleration:300, dwell:0, enabled:true}, {row:3, position:0, speed:120, acceleration:300, dwell:0, enabled:true}]}, transmission:{type:'rotary', revs:1, amount:360, unit:'deg', direction:'forward', travelMode:'periodic', period:360, forwardLimit:360, reverseLimit:-360}, points:{1:0, 2:REV/2, 3:REV}},
  fv3: {mode:'position', absPos:0, absSpeedRpm:120, absAccel:300, relDelta:4194304, moveMs:3000, velCps:200000, torqueCmd:0, gearMaster:'mctivity', gearMasterRatio:1, gearSlaveRatio:1, homing:defaultHomingState(), incrementalCurve:{mode:'position', targetPosition:0, targetSpeed:0, accel:0, decel:0, dwell:0, blend:'smooth'}, multiPoint:{start:1, step:3, cycleCount:1, editing:false, rows:[{row:1, position:0, speed:120, acceleration:300, dwell:0, enabled:true}, {row:2, position:0, speed:120, acceleration:300, dwell:0, enabled:true}, {row:3, position:0, speed:120, acceleration:300, dwell:0, enabled:true}]}, transmission:{type:'rotary', revs:1, amount:360, unit:'deg', direction:'forward', travelMode:'periodic', period:360, forwardLimit:360, reverseLimit:-360}, points:{1:0, 2:REV/2, 3:REV}},
  aux_encoder: {mode:'position', countsPerRev:AUX_ENCODER_COUNTS_PER_REV, absPos:0, absSpeedRpm:120, absAccel:300, relDelta:4194304, moveMs:3000, velCps:200000, torqueCmd:0, gearMaster:'virtual', gearMasterRatio:1, gearSlaveRatio:1, softZeroRaw:0, homing:defaultHomingState(), incrementalCurve:{mode:'position', targetPosition:0, targetSpeed:0, accel:0, decel:0, dwell:0, blend:'smooth'}, multiPoint:{start:1, step:3, cycleCount:1, editing:false, rows:[{row:1, position:0, speed:120, acceleration:300, dwell:0, enabled:true}, {row:2, position:0, speed:120, acceleration:300, dwell:0, enabled:true}, {row:3, position:0, speed:120, acceleration:300, dwell:0, enabled:true}]}, transmission:{type:'rotary', revs:1, amount:360, unit:'deg', direction:'forward', travelMode:'periodic', period:360, forwardLimit:360, reverseLimit:-360}, points:{1:0, 2:AUX_ENCODER_COUNTS_PER_REV/2, 3:AUX_ENCODER_COUNTS_PER_REV}}
};
let uiStateSaveTimer = 0;
const lastUiStateSnapshot = {mctivity:'', fv3:'', aux_encoder:''};
let transmissionDraft = null;
const modeUiStateByDevice = {
  mctivity: {pending:null, interacting:false},
  fv3: {pending:null, interacting:false},
  aux_encoder: {pending:null, interacting:false}
};
const modePanelStateByDevice = {
  mctivity: null,
  fv3: null,
  aux_encoder: null
};
const capabilityState = {
  loaded:false,
  profile:'unknown',
  capabilities:new Set(),
  modeMap:{},
  modeHmiModuleMap:{},
  activeFeatures:[],
  enabledFeatureKeys:[],
  featureAssembly:{loaded:{}, skipped:{}},
  featureRegistrySource:'',
  warnings:[],
  generatedAt:'',
  antiSwayExecution:{enabled:false, limitMode:'transmission_soft_limits', strategy:'continuous_zvd_curve'}
};
let diagModalText = '';
let motionConfirmResolver = null;
let touchKeypadTarget = null;
let touchKeypadDraft = '';
let touchKeypadOffset = {x:0, y:0};
let touchKeypadDrag = {active:false, pointerId:null, startX:0, startY:0, originX:0, originY:0};
let touchKeypadIgnoreBackdropUntil = 0;
let multiPointCyclePopupOpen = false;
let modeSelectBlurTimer = 0;
function currentModeUi(device = activeDevice) {
  return modeUiStateByDevice[device];
}
function syncModeSelectDisabled(device = activeDevice) {
  if (!modeSelect || device !== activeDevice) return;
  modeSelect.disabled = false;
}
function axisDisplayName(device) {
  const text = UI_TEXT[currentLang];
  if (device === 'aux_encoder') return text.axisC;
  if (device === 'fv3') return text.axisB;
  if (device === 'mctivity') return text.axisA;
  return text.virtualAxis;
}
function isAuxEncoderDevice(device = activeDevice) {
  return device === 'aux_encoder';
}
function isMotorDevice(device = activeDevice) {
  return device === 'mctivity' || device === 'fv3';
}
function supportsDevice(device) {
  if (device === 'mctivity') return true;
  if (device === 'fv3') {
    return capabilityState.loaded && capabilityState.capabilities.has('axis.device.fv3.access');
  }
  if (device === 'aux_encoder') {
    return capabilityState.loaded && capabilityState.capabilities.has('axis.device.aux_encoder.access');
  }
  return false;
}
function axisDevices() {
  return Object.keys(deviceProfiles).filter(supportsDevice);
}
function motorAxisDevices() {
  return axisDevices().filter(isMotorDevice);
}
function antiSwaySensorAxisOptions(device = activeDevice) {
  return axisDevices().filter(candidate => candidate !== device);
}
function preferredAntiSwaySensorAxis(device = activeDevice) {
  const options = antiSwaySensorAxisOptions(device);
  if (options.includes('aux_encoder')) return 'aux_encoder';
  return options[0] || 'virtual';
}
function preferredGearMaster(device) {
  if (device === 'fv3') return 'mctivity';
  if (device === 'aux_encoder') return 'virtual';
  return supportsDevice('fv3') ? 'fv3' : (supportsDevice('aux_encoder') ? 'aux_encoder' : 'virtual');
}
function refreshGearPanel(device = activeDevice) {
  const profile = currentProfile(device);
  if (!profile.gearMaster || profile.gearMaster === device) {
    profile.gearMaster = preferredGearMaster(device);
  }
  if (gearMasterSelect) {
    gearMasterSelect.value = profile.gearMaster;
  }
  setText('gearSlaveName', axisDisplayName(device));
  updateGearUnitLabels(device);
}
function axisRotationPrefix(device = activeDevice) {
  const text = UI_TEXT[currentLang];
  return isAuxEncoderDevice(device) ? text.encoderPrefix : text.motorPrefix;
}
function transmissionRotationLabel(device = activeDevice) {
  const text = UI_TEXT[currentLang];
  return isAuxEncoderDevice(device) ? text.transmissionEncoderRevsLabel : text.transmissionRevsLabel;
}
function gearUnitForDevice(device) {
  if (device === 'virtual' || !deviceProfiles[device]) return 'rev';
  return normalizedTransmission(currentProfile(device)).unit;
}
function updateGearUnitLabels(device = activeDevice) {
  const profile = currentProfile(device) || {};
  const master = gearMasterSelect ? gearMasterSelect.value : (profile.gearMaster || preferredGearMaster(device));
  setText('gearMasterUnitBadge', gearUnitForDevice(master));
  setText('gearSlaveUnitBadge', gearUnitForDevice(device));
}
function clampGearRatioValue(value) {
  return Math.max(1, Math.min(200, Math.round(Number(value) || 1)));
}
function gearWheelText(value) {
  if (value < 1 || value > 200) return '';
  return fmt(value);
}
function isGearEngaged(device = activeDevice) {
  const motion = currentMotion(device);
  const s = currentStatus(device);
  const fromStatus = Boolean(s && s.control_mode === 'gear_cam' && s.gear_running);
  return Boolean((motion && motion.gearEngaged) || fromStatus);
}
function isGearPanelLocked(device = activeDevice) {
  const profile = currentProfile(device);
  const s = currentStatus(device);
  const inGearMode = Boolean((profile && profile.mode === 'gear_cam') || (s && s.control_mode === 'gear_cam'));
  return inGearMode && isGearEngaged(device);
}
function setGearPanelLocked(locked) {
  const panel = document.getElementById('gearPanelCard');
  const ratio = document.getElementById('gearRatioBlock');
  const master = document.getElementById('gearMasterSelect');
  if (panel) panel.classList.toggle('locked', Boolean(locked));
  if (ratio) ratio.classList.toggle('locked', Boolean(locked));
  if (master) master.disabled = Boolean(locked);
}
const gearDragState = {role:null, lastY:0, acc:0, lastT:0, velocity:0, moved:false, suppressTapUntil:0, inertiaRole:null, inertiaAcc:0, inertiaV:0, inertiaTimer:0};
function renderGearWheel(role) {
  const hidden = role === 'master' ? gearMasterRatio : gearSlaveRatio;
  const current = clampGearRatioValue(hidden.value);
  hidden.value = current;
  const prev2 = current - 2;
  const prev = current - 1;
  const next = current + 1;
  const next2 = current + 2;
  if (role === 'master') {
    setText('gearMasterPrev2', gearWheelText(prev2));
    setText('gearMasterPrev', gearWheelText(prev));
    setText('gearMasterRatioText', fmt(current));
    setText('gearMasterNext', gearWheelText(next));
    setText('gearMasterNext2', gearWheelText(next2));
  } else {
    setText('gearSlavePrev2', gearWheelText(prev2));
    setText('gearSlavePrev', gearWheelText(prev));
    setText('gearSlaveRatioText', fmt(current));
    setText('gearSlaveNext', gearWheelText(next));
    setText('gearSlaveNext2', gearWheelText(next2));
  }
}
function stepGearRatio(role, delta) {
  if (isGearPanelLocked()) return;
  const hidden = role === 'master' ? gearMasterRatio : gearSlaveRatio;
  hidden.value = clampGearRatioValue(Number(hidden.value || 1) + Number(delta || 0));
  updateSliders();
}
function onGearWheel(event, role) {
  event.preventDefault();
  if (isGearPanelLocked()) return;
  stepGearRatio(role, event.deltaY < 0 ? 1 : -1);
}
function onGearTap(event, role) {
  if (isGearPanelLocked()) return;
  if (Date.now() < gearDragState.suppressTapUntil) return;
  const rect = event.currentTarget.getBoundingClientRect();
  const midY = rect.top + rect.height / 2;
  stepGearRatio(role, event.clientY < midY ? 1 : -1);
}
function startGearDrag(event, role) {
  if (isGearPanelLocked()) return;
  if (gearDragState.inertiaTimer) {
    cancelAnimationFrame(gearDragState.inertiaTimer);
    gearDragState.inertiaTimer = 0;
  }
  gearDragState.role = role;
  gearDragState.lastY = event.clientY;
  gearDragState.acc = 0;
  gearDragState.lastT = performance.now();
  gearDragState.velocity = 0;
  gearDragState.moved = false;
}
function moveGearDrag(event) {
  const stepPx = 18;
  let dy = 0;
  let dt = 1;
  let now = 0;
  let instantV = 0;
  if (!gearDragState.role) return;
  dy = event.clientY - gearDragState.lastY;
  now = performance.now();
  dt = Math.max(1, now - gearDragState.lastT);
  instantV = dy / dt;
  gearDragState.velocity = gearDragState.velocity * 0.65 + instantV * 0.35;
  gearDragState.lastT = now;
  gearDragState.lastY = event.clientY;
  gearDragState.acc += dy;
  if (Math.abs(dy) > 2) {
    gearDragState.moved = true;
  }
  while (gearDragState.acc >= stepPx) {
    gearDragState.acc -= stepPx;
    stepGearRatio(gearDragState.role, -1);
  }
  while (gearDragState.acc <= -stepPx) {
    gearDragState.acc += stepPx;
    stepGearRatio(gearDragState.role, 1);
  }
}
function runGearInertia() {
  const stepPx = 18;
  if (!gearDragState.inertiaRole) return;
  gearDragState.inertiaV *= 0.92;
  if (Math.abs(gearDragState.inertiaV) < 0.02) {
    gearDragState.inertiaRole = null;
    gearDragState.inertiaTimer = 0;
    return;
  }
  gearDragState.inertiaAcc += gearDragState.inertiaV * 16;
  while (gearDragState.inertiaAcc >= stepPx) {
    gearDragState.inertiaAcc -= stepPx;
    stepGearRatio(gearDragState.inertiaRole, -1);
  }
  while (gearDragState.inertiaAcc <= -stepPx) {
    gearDragState.inertiaAcc += stepPx;
    stepGearRatio(gearDragState.inertiaRole, 1);
  }
  gearDragState.inertiaTimer = requestAnimationFrame(runGearInertia);
}
function endGearDrag() {
  const role = gearDragState.role;
  const v = gearDragState.velocity;
  const moved = gearDragState.moved;
  gearDragState.role = null;
  gearDragState.acc = 0;
  gearDragState.velocity = 0;
  if (moved) {
    gearDragState.suppressTapUntil = Date.now() + 180;
  }
  if (role && Math.abs(v) >= 0.08) {
    gearDragState.inertiaRole = role;
    gearDragState.inertiaAcc = 0;
    gearDragState.inertiaV = v;
    if (gearDragState.inertiaTimer) {
      cancelAnimationFrame(gearDragState.inertiaTimer);
    }
    gearDragState.inertiaTimer = requestAnimationFrame(runGearInertia);
  }
}
async function apiForDevice(device, payload) {
  if (mockFaultEnabled()) {
    return payload && payload.cmd === 'status' ? mockStatus(device) : {ok:false, error:'mock_read_only'};
  }
  if (isAuxEncoderDevice(device) && (!payload || payload.cmd !== 'status')) {
    return {ok:false, error:'read_only_device'};
  }
  const requestPayload = Object.assign({}, payload, {device});
  const res = await fetch('/api/command', {method:'POST', headers:apiHeaders({'Content-Type':'application/json'}), body: JSON.stringify(requestPayload)});
  return res.json();
}
function modeOption(value) {
  return Array.from(modeSelect.options || []).find(opt => opt.value === value) || null;
}
function supportsCapability(capability) {
  if (!capability) return true;
  if (!capabilityState.loaded) return true;
  return capabilityState.capabilities.has(capability);
}
function modeRequiredCapability(mode) {
  if (capabilityState.modeMap && capabilityState.modeMap[mode]) {
    return capabilityState.modeMap[mode];
  }
  const localMap = {
    position:'axis.mode.position.execute',
    anti_sway_position:'axis.mode.anti_sway_position.input',
    incremental:'axis.mode.incremental.execute',
    jog:'axis.mode.jog.execute',
    point:'axis.mode.point.execute',
    multi_point:'axis.mode.multi_point.execute',
    homing:'axis.mode.homing.execute',
    velocity:'axis.mode.velocity.execute',
    torque:'axis.mode.torque.execute',
    gear_cam:'axis.mode.gear_cam.execute'
  };
  return localMap[mode] || null;
}
function modeRequiredHmiModule(mode) {
  if (capabilityState.modeHmiModuleMap && capabilityState.modeHmiModuleMap[mode]) {
    return capabilityState.modeHmiModuleMap[mode];
  }
  const localMap = {
    position:'feature-hmi-single-point',
    anti_sway_position:'feature-hmi-anti-sway-position',
    incremental:'feature-hmi-incremental',
    jog:'feature-hmi-jog',
    point:'feature-hmi-point',
    multi_point:'feature-hmi-multi-point',
    homing:'feature-hmi-homing',
    velocity:'feature-hmi-velocity',
    torque:'feature-hmi-torque',
    gear_cam:'feature-hmi-electronic-gear'
  };
  return localMap[mode] || null;
}
function supportsHmiModule(moduleId) {
  if (!moduleId) return true;
  if (!capabilityState.loaded) return true;
  return Array.isArray(capabilityState.activeFeatures) && capabilityState.activeFeatures.includes(moduleId);
}
function modeIsAssembled(mode) {
  return supportsCapability(modeRequiredCapability(mode)) && supportsHmiModule(modeRequiredHmiModule(mode));
}
function modeAllowedForDevice(mode, device = activeDevice) {
  if (isAuxEncoderDevice(device)) {
    return mode === 'position' || mode === 'homing';
  }
  return true;
}
function applyCapabilityModeAvailability(device = activeDevice) {
  let changed = false;
  for (const opt of Array.from(modeSelect.options || [])) {
    const assembled = modeIsAssembled(opt.value) && modeAllowedForDevice(opt.value, device);
    const disabled = !assembled;
    opt.hidden = !assembled;
    if (opt.disabled !== disabled) {
      opt.disabled = disabled;
      changed = true;
    }
    const panel = document.getElementById('panel-' + opt.value);
    if (panel) {
      panel.hidden = !assembled;
    }
  }
  const requested = modeSelect.value || 'position';
  if (!modeIsAssembled(requested) || !modeAllowedForDevice(requested, device)) {
    modeSelect.value = 'position';
    currentProfile(device).mode = 'position';
    syncModePanels('position', true);
    changed = true;
  }
  return changed;
}
function renderCapabilitySummary() {
  const warningList = Array.isArray(capabilityState.warnings) ? capabilityState.warnings : [];
  const warningText = warningList.length ? warningList.join('\n') : 'none';
  const featureList = Array.isArray(capabilityState.activeFeatures) ? capabilityState.activeFeatures : [];
  const featureText = featureList.length ? featureList.join('\n') : 'none';
  setText('profileValue', capabilityState.profile || 'unknown');
  setText('featureCountValue', String(featureList.length));
  setText('capabilityCountValue', String(capabilityState.capabilities ? capabilityState.capabilities.size : 0));
  setText('warningCountValue', String(warningList.length));
  const profileValueEl = document.getElementById('profileValue');
  if (profileValueEl) {
    profileValueEl.title = capabilityState.generatedAt ? ('generated_at: ' + capabilityState.generatedAt) : '';
  }
  const warnValueEl = document.getElementById('warningCountValue');
  if (warnValueEl) {
    warnValueEl.title = warningText;
  }
  const warnChipEl = document.getElementById('warningChip');
  if (warnChipEl) {
    warnChipEl.title = warningText;
  }
  const featureValueEl = document.getElementById('featureCountValue');
  if (featureValueEl) {
    featureValueEl.title = featureText;
  }
  const featureChipEl = document.getElementById('featureChip');
  if (featureChipEl) {
    featureChipEl.title = featureText;
  }
}
function showFeatureDetails() {
  const featureList = Array.isArray(capabilityState.activeFeatures) ? capabilityState.activeFeatures : [];
  const enabledList = Array.isArray(capabilityState.enabledFeatureKeys) ? capabilityState.enabledFeatureKeys : [];
  const assembly = capabilityState.featureAssembly && typeof capabilityState.featureAssembly === 'object' ? capabilityState.featureAssembly : {loaded:{}, skipped:{}};
  const loaded = assembly.loaded && typeof assembly.loaded === 'object' ? assembly.loaded : {};
  const skipped = assembly.skipped && typeof assembly.skipped === 'object' ? assembly.skipped : {};
  const lines = [];
  lines.push('profile: ' + (capabilityState.profile || 'unknown'));
  if (capabilityState.featureRegistrySource) {
    lines.push('registry: ' + capabilityState.featureRegistrySource);
  }
  if (capabilityState.generatedAt) {
    lines.push('generated_at: ' + capabilityState.generatedAt);
  }
  lines.push('features(active modules): ' + featureList.length);
  lines.push('features(enabled logic): ' + enabledList.length);
  lines.push('features(skipped logic): ' + Object.keys(skipped).length);
  if (enabledList.length) {
    lines.push('---');
    lines.push('enabled_feature_keys:');
    for (const k of enabledList) lines.push('- ' + String(k));
  }
  const loadedKeys = Object.keys(loaded);
  if (loadedKeys.length) {
    lines.push('---');
    lines.push('loaded_details:');
    for (const key of loadedKeys.sort()) {
      const item = loaded[key] || {};
      const matched = Array.isArray(item.matched_logic_modules) ? item.matched_logic_modules.join(', ') : '';
      lines.push('- ' + key + ' <= ' + matched);
    }
  }
  const skippedKeys = Object.keys(skipped);
  if (skippedKeys.length) {
    lines.push('---');
    lines.push('skipped_details:');
    for (const key of skippedKeys.sort()) {
      const item = skipped[key] || {};
      const reason = item.reason || 'unknown';
      lines.push('- ' + key + ' : ' + reason);
    }
  }
  if (featureList.length) {
    lines.push('---');
    lines.push('active_modules:');
    for (const f of featureList) lines.push('- ' + String(f));
  }
  openDiagModal('Feature Details', lines.join('\n'));
  return false;
}
function showWarningDetails() {
  const warningList = Array.isArray(capabilityState.warnings) ? capabilityState.warnings : [];
  const lines = [];
  lines.push('profile: ' + (capabilityState.profile || 'unknown'));
  if (capabilityState.generatedAt) {
    lines.push('generated_at: ' + capabilityState.generatedAt);
  }
  lines.push('warnings: ' + warningList.length);
  if (warningList.length) {
    lines.push('---');
    for (const w of warningList) {
      lines.push('- ' + String(w));
    }
  }
  openDiagModal('Warning Details', lines.join('\n'));
  return false;
}
function openDiagModal(title, body) {
  diagModalText = String(body || '');
  setText('diagModalTitle', String(title || 'Runtime Diagnostics'));
  setText('diagModalBody', diagModalText);
  diagModal.classList.add('open');
}
function maybeCloseDiagModal(event) {
  if (event.target === diagModal) closeDiagModal();
}
function closeDiagModal() {
  diagModal.classList.remove('open');
}
function copyDiagText() {
  const text = String(diagModalText || '');
  if (!text) return false;
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).catch(() => {});
  }
  return false;
}
function openMotionConfirm(title, body) {
  const modal = document.getElementById('motionConfirmModal');
  const okBtn = document.getElementById('motionConfirmOkBtn');
  if (!modal) return Promise.resolve(window.confirm(String(body || title || 'Confirm motion?')));
  if (motionConfirmResolver) {
    motionConfirmResolver(false);
    motionConfirmResolver = null;
  }
  const text = UI_TEXT[currentLang];
  setText('motionConfirmTitle', String(title || text.motionConfirmStart || '确认开始'));
  setText('motionConfirmBody', String(body || ''));
  setText('motionConfirmCancelBtn', text.motionConfirmCancel || '取消');
  setText('motionConfirmOkBtn', text.motionConfirmStart || '确认开始');
  modal.classList.add('open');
  setTimeout(() => { if (okBtn && okBtn.focus) okBtn.focus(); }, 0);
  return new Promise(resolve => {
    motionConfirmResolver = resolve;
  });
}
function resolveMotionConfirm(ok) {
  const modal = document.getElementById('motionConfirmModal');
  if (modal) modal.classList.remove('open');
  const resolver = motionConfirmResolver;
  motionConfirmResolver = null;
  if (resolver) resolver(Boolean(ok));
  return false;
}
function maybeCancelMotionConfirm(event) {
  const modal = document.getElementById('motionConfirmModal');
  if (event && event.target === modal) resolveMotionConfirm(false);
}
function touchKeypadAllowsSign(input = touchKeypadTarget) {
  return Boolean(input && input.dataset && input.dataset.touchSigned === 'true');
}
function touchKeypadAllowsDecimal(input = touchKeypadTarget) {
  if (!input) return true;
  const step = String(input.getAttribute('step') || '').trim().toLowerCase();
  return step !== '1' && input.getAttribute('inputmode') !== 'numeric';
}
function touchKeypadLabel(input = touchKeypadTarget) {
  const text = UI_TEXT[currentLang];
  if (!input) return text.touchKeypadTitle;
  const field = input.closest('label');
  const label = field && field.querySelector('span') ? field.querySelector('span').textContent.trim() : '';
  const unit = input.nextElementSibling ? input.nextElementSibling.textContent.trim() : '';
  return (label || text.touchKeypadTitle) + (unit ? ' (' + unit + ')' : '');
}
function touchKeypadIsOpen() {
  const modal = document.getElementById('touchKeypadModal');
  return Boolean(modal && modal.classList.contains('open'));
}
function touchKeypadPanel() {
  return document.getElementById('touchKeypadPanel');
}
function setTouchKeypadOffset(x, y, clampToViewport = true) {
  const panel = touchKeypadPanel();
  if (!panel) return;
  let nextX = Number(x) || 0;
  let nextY = Number(y) || 0;
  panel.style.setProperty('--keypad-x', nextX + 'px');
  panel.style.setProperty('--keypad-y', nextY + 'px');
  if (clampToViewport) {
    const margin = 8;
    const rect = panel.getBoundingClientRect();
    if (rect.left < margin) nextX += margin - rect.left;
    if (rect.right > window.innerWidth - margin) nextX -= rect.right - (window.innerWidth - margin);
    if (rect.top < margin) nextY += margin - rect.top;
    if (rect.bottom > window.innerHeight - margin) nextY -= rect.bottom - (window.innerHeight - margin);
    panel.style.setProperty('--keypad-x', nextX + 'px');
    panel.style.setProperty('--keypad-y', nextY + 'px');
  }
  touchKeypadOffset = {x:nextX, y:nextY};
}
function resetTouchKeypadOffset() {
  setTouchKeypadOffset(0, 0, false);
}
function startTouchKeypadDrag(event) {
  const panel = touchKeypadPanel();
  if (!panel || !touchKeypadIsOpen() || (event.target && event.target.closest('button'))) return;
  event.preventDefault();
  event.stopPropagation();
  touchKeypadDrag = {
    active:true,
    pointerId:event.pointerId,
    startX:event.clientX,
    startY:event.clientY,
    originX:touchKeypadOffset.x,
    originY:touchKeypadOffset.y
  };
  panel.classList.add('dragging');
  if (panel.setPointerCapture) {
    try { panel.setPointerCapture(event.pointerId); } catch (err) {}
  }
}
function moveTouchKeypadDrag(event) {
  if (!touchKeypadDrag.active || event.pointerId !== touchKeypadDrag.pointerId) return;
  event.preventDefault();
  setTouchKeypadOffset(
    touchKeypadDrag.originX + (event.clientX - touchKeypadDrag.startX),
    touchKeypadDrag.originY + (event.clientY - touchKeypadDrag.startY)
  );
}
function endTouchKeypadDrag(event) {
  if (!touchKeypadDrag.active || (event && event.pointerId !== touchKeypadDrag.pointerId)) return;
  const panel = touchKeypadPanel();
  if (panel) {
    panel.classList.remove('dragging');
    if (panel.releasePointerCapture && event) {
      try { panel.releasePointerCapture(event.pointerId); } catch (err) {}
    }
  }
  touchKeypadDrag = {active:false, pointerId:null, startX:0, startY:0, originX:touchKeypadOffset.x, originY:touchKeypadOffset.y};
}
function updateTouchKeypadView() {
  const text = UI_TEXT[currentLang];
  const valueEl = document.getElementById('touchKeypadValue');
  const titleEl = document.getElementById('touchKeypadTitle');
  const signBtn = document.getElementById('touchKeypadSignBtn');
  const decimalBtn = document.getElementById('touchKeypadDecimalBtn');
  if (titleEl) titleEl.textContent = touchKeypadLabel();
  if (valueEl) valueEl.textContent = touchKeypadDraft || '0';
  if (signBtn) signBtn.disabled = !touchKeypadAllowsSign();
  if (decimalBtn) decimalBtn.disabled = !touchKeypadAllowsDecimal();
  setText('touchKeypadClearBtn', text.touchKeypadClear);
  setText('touchKeypadDeleteBtn', text.touchKeypadDelete);
  setText('touchKeypadDoneBtn', text.touchKeypadDone);
}
function openTouchKeypadForInput(input) {
  if (!input || input.disabled || input.readOnly) return;
  touchKeypadTarget = input;
  touchKeypadDraft = String(input.value || '0');
  if (touchKeypadDraft === '') touchKeypadDraft = '0';
  touchKeypadIgnoreBackdropUntil = Date.now() + 450;
  updateTouchKeypadView();
  resetTouchKeypadOffset();
  const modal = document.getElementById('touchKeypadModal');
  if (modal) modal.classList.add('open');
  setTimeout(() => {
    try { input.blur(); } catch (err) {}
  }, 0);
}
function maybeCloseTouchKeypad(event) {
  if (event.target !== document.getElementById('touchKeypadModal')) return;
  if (Date.now() < touchKeypadIgnoreBackdropUntil) return;
  closeTouchKeypad();
}
function closeTouchKeypad() {
  endTouchKeypadDrag();
  const modal = document.getElementById('touchKeypadModal');
  if (modal) modal.classList.remove('open');
  touchKeypadTarget = null;
  touchKeypadDraft = '';
}
function touchKeypadPress(char) {
  if (!touchKeypadTarget) return false;
  if (char === '.') {
    if (!touchKeypadAllowsDecimal() || touchKeypadDraft.includes('.')) return false;
    touchKeypadDraft = touchKeypadDraft ? touchKeypadDraft + '.' : '0.';
  } else {
    const digit = String(char).replace(/\D/g, '');
    if (!digit) return false;
    if (touchKeypadDraft === '0') touchKeypadDraft = digit;
    else if (touchKeypadDraft === '-0') touchKeypadDraft = '-' + digit;
    else touchKeypadDraft += digit;
  }
  updateTouchKeypadView();
  return false;
}
function touchKeypadToggleSign() {
  if (!touchKeypadTarget || !touchKeypadAllowsSign()) return false;
  touchKeypadDraft = touchKeypadDraft.startsWith('-') ? touchKeypadDraft.slice(1) : '-' + (touchKeypadDraft || '0');
  updateTouchKeypadView();
  return false;
}
function touchKeypadClear() {
  touchKeypadDraft = '0';
  updateTouchKeypadView();
  return false;
}
function touchKeypadBackspace() {
  if (!touchKeypadDraft || touchKeypadDraft.length <= 1 || (touchKeypadDraft.length === 2 && touchKeypadDraft.startsWith('-'))) {
    touchKeypadDraft = '0';
  } else {
    touchKeypadDraft = touchKeypadDraft.slice(0, -1);
  }
  updateTouchKeypadView();
  return false;
}
function touchKeypadNormalizedValue(input, draft) {
  let value = Number(draft);
  if (!Number.isFinite(value)) value = 0;
  const minRaw = input ? input.getAttribute('min') : null;
  const maxRaw = input ? input.getAttribute('max') : null;
  const min = minRaw === null || minRaw === '' ? NaN : Number(minRaw);
  const max = maxRaw === null || maxRaw === '' ? NaN : Number(maxRaw);
  if (Number.isFinite(min)) value = Math.max(min, value);
  if (Number.isFinite(max)) value = Math.min(max, value);
  if (!touchKeypadAllowsDecimal(input)) return String(Math.round(value));
  return formatMultiPointNumber(value);
}
function touchKeypadCommit() {
  const input = touchKeypadTarget;
  if (!input) return false;
  input.value = touchKeypadNormalizedValue(input, touchKeypadDraft);
  input.dispatchEvent(new Event('input', {bubbles:true}));
  input.dispatchEvent(new Event('change', {bubbles:true}));
  closeTouchKeypad();
  return false;
}
function initTouchKeypad() {
  document.addEventListener('pointerdown', event => {
    const input = event.target && event.target.closest ? event.target.closest('input[data-touch-keypad]') : null;
    if (!input) return;
    event.preventDefault();
    event.stopPropagation();
    openTouchKeypadForInput(input);
  }, {capture:true});
  const dragHandle = document.getElementById('touchKeypadDragHandle');
  if (dragHandle) dragHandle.addEventListener('pointerdown', startTouchKeypadDrag);
  document.addEventListener('pointermove', moveTouchKeypadDrag, {passive:false});
  document.addEventListener('pointerup', endTouchKeypadDrag);
  document.addEventListener('pointercancel', endTouchKeypadDrag);
  window.addEventListener('resize', () => {
    if (touchKeypadIsOpen()) setTouchKeypadOffset(touchKeypadOffset.x, touchKeypadOffset.y);
  });
}
function multiPointRunnerCycleInfo(runner) {
  if (!runner || !(runner.running || runner.state === 'stopping')) return null;
  const row = Math.round(Number(runner.current_row || 0));
  if (!Number.isFinite(row) || row <= 0) return null;
  const current = Math.max(1, Math.round(Number(runner.current_cycle || runner.cycle_count || 1)));
  const total = Math.max(current, Math.round(Number(runner.cycle_total || current)));
  return {
    row,
    rowLabel: 'P' + row,
    current,
    total,
    statusLabel: currentLang === 'zh' ? ('第' + current + '/' + total + '次') : ('Cycle ' + current + '/' + total),
    rowBadge: currentLang === 'zh' ? ('第' + current + '/' + total + '次') : ('C' + current + '/' + total)
  };
}
function renderMultiPointCyclePopup(info = multiPointRunnerCycleInfo(multiPointStatusByDevice[activeDevice])) {
  const popup = document.getElementById('multiPointCyclePopup');
  if (!popup) return;
  if (!multiPointCyclePopupOpen || !info) {
    if (!info) multiPointCyclePopupOpen = false;
    popup.classList.remove('open');
    return;
  }
  popup.classList.add('open');
  setText('multiPointCyclePopupTitle', UI_TEXT[currentLang].multiPointCyclePopupTitle);
  setText('multiPointCyclePopupRow', info.rowLabel);
  setText('multiPointCyclePopupCurrent', String(info.current));
  setText('multiPointCyclePopupTotal', String(info.total));
  setText('multiPointCyclePopupStatus', info.statusLabel);
  const closeBtn = document.getElementById('multiPointCyclePopupClose');
  if (closeBtn) closeBtn.setAttribute('aria-label', UI_TEXT[currentLang].close);
}
function openMultiPointCyclePopup() {
  const info = multiPointRunnerCycleInfo(multiPointStatusByDevice[activeDevice]);
  if (!info) return false;
  multiPointCyclePopupOpen = true;
  renderMultiPointCyclePopup(info);
  return false;
}
function closeMultiPointCyclePopup() {
  multiPointCyclePopupOpen = false;
  const popup = document.getElementById('multiPointCyclePopup');
  if (popup) popup.classList.remove('open');
  return false;
}
function gearMasterHolders(device) {
  return motorAxisDevices().filter(other => other !== device &&
    deviceProfiles[other].mode === 'gear_cam' &&
    deviceProfiles[other].gearMaster === device);
}
function gearLockReason(device) {
  const holders = gearMasterHolders(device);
  if (!holders.length) return '';
  return holders.map(d => axisDisplayName(d)).join(currentLang === 'zh' ? '、' : ', ');
}
function showGearLockedAlert(device) {
  const text = UI_TEXT[currentLang];
  const reason = gearLockReason(device);
  if (!reason) return;
  alert(text.gearLockedPrefix + reason + text.gearLockedSuffix);
}
function isReferencedAsGearMaster(device) {
  return gearMasterHolders(device).length > 0;
}
function applyGearModeAvailability(device = activeDevice) {
  const gearOpt = modeOption('gear_cam');
  const locked = isReferencedAsGearMaster(device);
  if (gearOpt) {
    const modeUi = currentModeUi(device);
    const required = modeRequiredCapability('gear_cam');
    const capBlocked = Boolean(required && !supportsCapability(required));
    const nextDisabled = Boolean(locked || capBlocked);
    const nextText = locked ? UI_TEXT[currentLang].gearUnavailable : modeLabel('gear_cam');
    if (!(device === activeDevice && modeUi && modeUi.interacting)) {
      if (gearOpt.disabled !== nextDisabled) gearOpt.disabled = nextDisabled;
      if (gearOpt.textContent !== nextText) gearOpt.textContent = nextText;
    }
  }
  return locked;
}
function enforceGearConstraints() {
  const changed = [];
  for (const device of motorAxisDevices()) {
    if (isReferencedAsGearMaster(device) && deviceProfiles[device].mode === 'gear_cam') {
      deviceProfiles[device].mode = 'position';
      motionStateByDevice[device].gearEngaged = false;
      motionStateByDevice[device].gearStoppedLatched = false;
      changed.push(device);
    }
  }
  const activeLocked = applyGearModeAvailability(activeDevice);
  if (activeLocked && modeSelect.value === 'gear_cam') {
    modeSelect.value = 'position';
    syncModePanels('position');
  }
  for (const device of changed) {
    if (device === activeDevice) {
      modeSelect.value = 'position';
      syncModePanels('position');
    }
    apiForDevice(device, {cmd:'set_mode', mode:'position'}).catch(() => {});
  }
}
function currentStatus(device = activeDevice) { return statusByDevice[device]; }
function currentMotion(device = activeDevice) { return motionStateByDevice[device]; }
function currentProfile(device = activeDevice) { return deviceProfiles[device]; }
const ANTI_SWAY_ALGORITHMS = ['zvd_continuous', 'zvd_terminal'];
function normalizeAntiSwayAlgorithm(value) {
  const key = String(value || '').trim().toLowerCase();
  if (key === 'zvd_terminal' || key === 'terminal' || key === 'endpoint' || key === 'endpoint_zvd') return 'zvd_terminal';
  return 'zvd_continuous';
}
function antiSwayAlgorithmLabel(key) {
  const text = UI_TEXT[currentLang];
  return normalizeAntiSwayAlgorithm(key) === 'zvd_terminal'
    ? (text.antiSwayAlgorithmTerminal || 'Endpoint')
    : (text.antiSwayAlgorithmContinuous || 'Full-Path');
}
function normalizeAntiSwayState(raw, device = activeDevice) {
  const profile = currentProfile(device) || {};
  const source = Object.assign({
    allowedAngle:3,
    rodLength:520,
    measuredPeriodS:0,
    algorithm:'zvd_continuous',
    speedRpm:profile.absSpeedRpm || 120,
    accelRpmS:profile.absAccel || 300
  }, raw || {});
  const sensorOptions = antiSwaySensorAxisOptions(device);
  const requestedSensor = String(source.sensorAxis || source.sensor_axis || '').trim();
  const sensorAxis = sensorOptions.includes(requestedSensor)
    ? requestedSensor
    : preferredAntiSwaySensorAxis(device);
  return {
    allowedAngle: Math.max(0.1, Math.min(45, Number(source.allowedAngle) || 3)),
    rodLength: Math.max(1, Math.min(100000, Number(source.rodLength) || 520)),
    measuredPeriodS: Number.isFinite(Number(source.measuredPeriodS)) ? Math.max(0, Math.min(10, Number(source.measuredPeriodS))) : 0,
    algorithm: normalizeAntiSwayAlgorithm(source.algorithm),
    speedRpm: Math.max(1, Math.min(MAX_SPEED_RPM, Number(source.speedRpm) || 120)),
    accelRpmS: Math.max(1, Math.min(MAX_ACCEL_RPM_S, Number(source.accelRpmS) || 300)),
    sensorAxis
  };
}
function currentAntiSway(device = activeDevice) {
  const profile = currentProfile(device);
  profile.antiSway = normalizeAntiSwayState(profile.antiSway, device);
  return profile.antiSway;
}
function defaultHomingState() {
  return {method:'set_current', setPosition:0, backoffDistance:0, direction:'reverse', speed:5, torqueThreshold:20, maxDistance:10, transmissionSignature:'', transmissionInvalidated:false};
}
function normalizeHomingState(raw) {
  const source = Object.assign({}, defaultHomingState(), raw || {});
  const method = source.method === 'torque_end' ? 'torque_end' : 'set_current';
  return {
    method,
    setPosition: Number.isFinite(Number(source.setPosition)) ? Number(source.setPosition) : 0,
    backoffDistance: Math.max(0, Math.min(100000000, Number(source.backoffDistance) || 0)),
    direction: source.direction === 'forward' ? 'forward' : 'reverse',
    speed: Math.max(0.001, Math.min(100000000, Number(source.speed) || 5)),
    torqueThreshold: Math.max(1, Math.min(100, Math.round(Number(source.torqueThreshold) || 20))),
    maxDistance: Math.max(0.001, Math.min(100000000, Number(source.maxDistance) || 10)),
    transmissionSignature: typeof source.transmissionSignature === 'string' ? source.transmissionSignature : '',
    transmissionInvalidated: Boolean(source.transmissionInvalidated)
  };
}
function currentHoming(device = activeDevice) {
  const profile = currentProfile(device);
  profile.homing = normalizeHomingState(profile.homing);
  return profile.homing;
}
function markHomingReferenceValid(device = activeDevice) {
  const profile = currentProfile(device);
  const homing = normalizeHomingState(profile.homing);
  homing.transmissionSignature = transmissionSignature(profile);
  homing.transmissionInvalidated = false;
  profile.homing = homing;
  scheduleUiStateSave(device);
}
function invalidateHomingReference(device = activeDevice) {
  const profile = currentProfile(device);
  const homing = normalizeHomingState(profile.homing);
  homing.transmissionSignature = '';
  homing.transmissionInvalidated = true;
  profile.homing = homing;
}
function homingReferenceMatchesTransmission(device = activeDevice, status = currentStatus(device), profile = currentProfile(device)) {
  const homing = normalizeHomingState(profile.homing);
  const signature = transmissionSignature(profile);
  if (status && status.homed && !homing.transmissionSignature && !homing.transmissionInvalidated) {
    homing.transmissionSignature = signature;
    profile.homing = homing;
    scheduleUiStateSave(device);
  }
  return Boolean(status && status.homed && !homing.transmissionInvalidated && homing.transmissionSignature === signature);
}
function axisHomedForCurrentTransmission(device = activeDevice, status = currentStatus(device), profile = currentProfile(device)) {
  return Boolean(status && status.homed && homingReferenceMatchesTransmission(device, status, profile));
}
function homingLimitPositionForDirection(profile = currentProfile(), direction = 'reverse') {
  const tx = normalizedTransmission(profile);
  return direction === 'forward' ? Number(tx.forwardLimit) : Number(tx.reverseLimit);
}
function applyTorqueHomingLimit(profile, homing) {
  if (!homing || homing.method !== 'torque_end') return homing;
  homing.setPosition = homingLimitPositionForDirection(profile, homing.direction);
  return homing;
}
function maxHomingBackoffForProfile(profile = currentProfile()) {
  return maxHomingDistanceForProfile(profile);
}
function homingCanUseTorque(device = activeDevice) {
  const status = currentStatus(device);
  if (!isMotorDevice(device)) return false;
  if (status && Object.prototype.hasOwnProperty.call(status, 'torque_feedback_available')) {
    return Boolean(status.torque_feedback_available);
  }
  return device === 'mctivity' || device === 'fv3';
}
function syncHomingControls(readDom = true) {
  const profile = currentProfile();
  const homing = currentHoming();
  if (readDom) {
    const setEl = document.getElementById('homingSetPosition');
    const torqueSetEl = document.getElementById('homingTorqueSetPosition');
    const directionEl = document.getElementById('homingDirection');
    const speedEl = document.getElementById('homingSpeed');
    const torqueEl = document.getElementById('homingTorqueThreshold');
    const distanceEl = document.getElementById('homingMaxDistance');
    if (directionEl) homing.direction = directionEl.value === 'forward' ? 'forward' : 'reverse';
    if (homing.method === 'torque_end') {
      applyTorqueHomingLimit(profile, homing);
      if (torqueSetEl) homing.backoffDistance = Math.max(0, Number(torqueSetEl.value || 0));
    } else if (setEl) {
      homing.setPosition = Number(setEl.value || 0);
    }
    if (speedEl) homing.speed = Number(speedEl.value || homing.speed);
    if (torqueEl) homing.torqueThreshold = Number(torqueEl.value || homing.torqueThreshold);
    if (distanceEl) homing.maxDistance = Number(distanceEl.value || homing.maxDistance);
  }
  profile.homing = normalizeHomingState(homing);
  return profile.homing;
}
function renderHomingPanel(writeControls = true) {
  const text = UI_TEXT[currentLang];
  const profile = currentProfile();
  const tx = normalizedTransmission(profile);
  const homing = currentHoming();
  const torqueAvailable = homingCanUseTorque(activeDevice);
  if (!torqueAvailable && homing.method === 'torque_end') {
    homing.method = 'set_current';
    profile.homing = normalizeHomingState(homing);
  }
  const setEl = document.getElementById('homingSetPosition');
  const torqueSetEl = document.getElementById('homingTorqueSetPosition');
  const directionEl = document.getElementById('homingDirection');
  const speedEl = document.getElementById('homingSpeed');
  const torqueEl = document.getElementById('homingTorqueThreshold');
  const distanceEl = document.getElementById('homingMaxDistance');
  const maxSearchDistance = maxHomingDistanceForProfile(profile);
  const maxBackoffDistance = maxHomingBackoffForProfile(profile);
  const setTab = document.getElementById('homingTabSetCurrent');
  const torqueTab = document.getElementById('homingTabTorqueEnd');
  const setPanel = document.getElementById('homingPanelSetCurrent');
  const torquePanel = document.getElementById('homingPanelTorqueEnd');
  const isTorque = homing.method === 'torque_end';
  if (isTorque) {
    applyTorqueHomingLimit(profile, homing);
    profile.homing = normalizeHomingState(homing);
  }
  if (setTab) {
    setTab.classList.toggle('active', !isTorque);
    setTab.setAttribute('aria-selected', isTorque ? 'false' : 'true');
  }
  if (torqueTab) {
    torqueTab.classList.toggle('active', isTorque);
    torqueTab.disabled = !torqueAvailable;
    torqueTab.setAttribute('aria-selected', isTorque ? 'true' : 'false');
  }
  if (setPanel) setPanel.classList.toggle('active', !isTorque);
  if (torquePanel) torquePanel.classList.toggle('active', isTorque);
  if (setEl && writeControls && document.activeElement !== setEl) {
    setEl.value = formatMultiPointNumber(homing.setPosition);
  }
  if (torqueSetEl && writeControls) {
    torqueSetEl.value = formatMultiPointNumber(homing.backoffDistance);
  }
  if (torqueSetEl) {
    torqueSetEl.min = '0';
    torqueSetEl.max = String(maxBackoffDistance);
    torqueSetEl.title = '0 - ' + formatMultiPointNumber(maxBackoffDistance) + ' ' + tx.unit;
  }
  if (directionEl) directionEl.value = homing.direction;
  if (speedEl && writeControls && document.activeElement !== speedEl) speedEl.value = formatMultiPointNumber(homing.speed);
  if (torqueEl && writeControls && document.activeElement !== torqueEl) torqueEl.value = String(homing.torqueThreshold);
  if (distanceEl && writeControls && document.activeElement !== distanceEl) distanceEl.value = formatMultiPointNumber(homing.maxDistance);
  if (distanceEl) {
    distanceEl.max = String(maxSearchDistance);
    distanceEl.title = text.homingMaxDistanceExceeded
      .replace('{value}', formatMultiPointNumber(maxSearchDistance))
      .replace('{unit}', tx.unit);
  }
  setText('homingSetPositionUnit', tx.unit);
  setText('homingTorqueSetPositionUnit', tx.unit);
  setText('homingSpeedUnit', transmissionRateUnit(profile, 1));
  setText('homingMaxDistanceUnit', tx.unit);
  setText('homingNote', !torqueAvailable && isTorque ? text.homingTorqueUnavailable : (isTorque ? text.homingTorqueNote : text.homingSetCurrentNote));
  const card = document.querySelector('#panel-homing .homing-card');
  if (card) card.classList.toggle('torque-unavailable', !torqueAvailable);
  updateHomingAxis();
}
function setHomingMethod(method) {
  const profile = currentProfile();
  const homing = syncHomingControls(true);
  const nextMethod = method === 'torque_end' ? 'torque_end' : 'set_current';
  if (nextMethod === 'torque_end' && !homingCanUseTorque(activeDevice)) {
    openDiagModal(modeLabel('homing'), UI_TEXT[currentLang].homingTorqueUnavailable);
    homing.method = 'set_current';
  } else {
    homing.method = nextMethod;
  }
  profile.homing = normalizeHomingState(homing);
  renderHomingPanel(true);
  saveUiState();
}
function onHomingSettingsChange() {
  syncHomingControls(true);
  renderHomingPanel(true);
  saveUiState();
}
function nativeDirectionFromHoming(profile, homing) {
  const loadDirection = homing.direction === 'forward' ? 1 : -1;
  const nativeDelta = nativeCountsFromTransmissionValue(loadDirection, profile) - nativeCountsFromTransmissionValue(0, profile);
  return nativeDelta >= 0 ? 1 : -1;
}
function homingDistanceCounts(profile, distance) {
  const delta = nativeCountsFromTransmissionValue(Number(distance || 0), profile) - nativeCountsFromTransmissionValue(0, profile);
  return Math.max(1, Math.round(Math.abs(delta)));
}
function homingBackoffDistanceCounts(profile, distance) {
  const delta = nativeCountsFromTransmissionValue(Number(distance || 0), profile) - nativeCountsFromTransmissionValue(0, profile);
  return Math.max(0, Math.round(Math.abs(delta)));
}
function homingBackoffPositionCounts(profile, homing) {
  const tx = normalizedTransmission(profile);
  const distance = Math.max(0, Number(homing.backoffDistance || 0));
  const endpoint = homingLimitPositionForDirection(profile, homing.direction);
  const otherLimit = homing.direction === 'forward' ? Number(tx.reverseLimit) : Number(tx.forwardLimit);
  const inward = otherLimit >= endpoint ? 1 : -1;
  const backoffLoad = endpoint + inward * distance;
  return nativeCountsFromTransmissionValue(backoffLoad, profile);
}
function maxHomingDistanceForProfile(profile = currentProfile()) {
  return Math.max(0.001, (MAX_HOMING_SEARCH_COUNTS / REV) * transmissionPerRev(profile));
}
function auxCountsFromLoadValue(value, profile = currentProfile('aux_encoder')) {
  const direction = transmissionDirectionSign(profile);
  const loadRev = Number(value || 0) / (Math.max(0.001, transmissionPerRev(profile)) * direction);
  return Math.round(loadRev * auxEncoderCountsPerRev(null, profile));
}
function defaultIncrementalCurveState() {
  return {mode:'position', targetPosition:0, targetSpeed:0, accel:0, decel:0, dwell:0, blend:'smooth'};
}
function normalizeIncrementalCurveState(raw) {
  const source = Object.assign({}, defaultIncrementalCurveState(), raw || {});
  return {
    mode: source.mode === 'manual' ? 'manual' : 'position',
    targetPosition: Number(source.targetPosition) || 0,
    targetSpeed: Math.max(0, Number(source.targetSpeed) || 0),
    accel: Math.max(0, Number(source.accel) || 0),
    decel: Math.max(0, Number(source.decel) || 0),
    dwell: Math.max(0, Number(source.dwell) || 0),
    blend: source.blend === 'linear' || source.blend === 'aggressive' ? source.blend : 'smooth'
  };
}
function currentIncrementalCurve(device = activeDevice) {
  const profile = currentProfile(device);
  profile.incrementalCurve = normalizeIncrementalCurveState(profile.incrementalCurve);
  return profile.incrementalCurve;
}
function defaultMultiPointState() {
  return {
    start: 1,
    step: 3,
    cycleCount: 1,
    editing: false,
    rows: [
      {row:1, position:0, speed:120, acceleration:300, dwell:0, enabled:true},
      {row:2, position:0, speed:120, acceleration:300, dwell:0, enabled:true},
      {row:3, position:0, speed:120, acceleration:300, dwell:0, enabled:true}
    ]
  };
}
function clampMultiPointCycleCount(value, fallback=1) {
  const numeric = Math.round(Number(value));
  if (Number.isFinite(numeric)) {
    return Math.max(1, Math.min(MULTI_POINT_MAX_CYCLES, numeric));
  }
  return Math.max(1, Math.min(MULTI_POINT_MAX_CYCLES, Math.round(Number(fallback) || 1)));
}
function normalizeMultiPointRow(raw, fallbackRow) {
  const source = Object.assign({}, raw || {});
  const row = Math.max(1, Math.min(255, Math.round(Number(source.row || fallbackRow || 1))));
  const sourceSpeed = Number.isFinite(Number(source.speed)) ? Number(source.speed) : 120;
  const sourceAcceleration = Number.isFinite(Number(source.acceleration)) ? Number(source.acceleration) : 300;
  return {
    row,
    position: Number.isFinite(Number(source.position)) ? Number(source.position) : 0,
    speed: Math.max(0.001, Math.min(100000000, sourceSpeed)),
    acceleration: Math.max(0, Math.min(100000000, sourceAcceleration)),
    dwell: Math.max(0, Math.min(60000, Math.round(Number(source.dwell || 0)))),
    enabled: source.enabled !== false
  };
}
function normalizeMultiPointState(raw) {
  const defaults = defaultMultiPointState();
  const source = Object.assign({}, defaults, raw || {});
  const start = Math.max(1, Math.min(255, Math.round(Number(source.start || 1))));
  const maxStep = Math.max(1, Math.min(64, 255 - start + 1));
  const step = Math.max(1, Math.min(maxStep, Math.round(Number(source.step || defaults.step))));
  const cycleCount = clampMultiPointCycleCount(
    source.cycleCount,
    source.loopMode === 'cycle' ? MULTI_POINT_MAX_CYCLES : defaults.cycleCount
  );
  const sourceRows = Array.isArray(source.rows) ? source.rows : defaults.rows;
  const byRow = new Map();
  sourceRows.forEach((item, idx) => {
    const normalized = normalizeMultiPointRow(item, start + idx);
    byRow.set(normalized.row, normalized);
  });
  const rows = [];
  for (let offset = 0; offset < step; offset += 1) {
    const rowNo = start + offset;
    rows.push(byRow.get(rowNo) || normalizeMultiPointRow({row:rowNo}, rowNo));
  }
  return {start, step, cycleCount, editing:Boolean(source.editing), rows};
}
function currentMultiPoint(device = activeDevice) {
  const profile = currentProfile(device);
  profile.multiPoint = normalizeMultiPointState(profile.multiPoint);
  return profile.multiPoint;
}
function syncMultiPointSettingsFromControls() {
  const mp = currentMultiPoint();
  const startInput = document.getElementById('multiPointStart');
  const stepInput = document.getElementById('multiPointStep');
  const cycleInput = document.getElementById('multiPointCycleCount');
  if (startInput) mp.start = Math.max(1, Math.min(255, Math.round(Number(startInput.value || mp.start))));
  if (stepInput) mp.step = Math.max(1, Math.min(64, Math.round(Number(stepInput.value || mp.step))));
  if (cycleInput) mp.cycleCount = clampMultiPointCycleCount(cycleInput.value, mp.cycleCount);
  currentProfile().multiPoint = normalizeMultiPointState(mp);
  return currentProfile().multiPoint;
}
function syncMultiPointRowsFromInputs() {
  const mp = syncMultiPointSettingsFromControls();
  const byRow = new Map(mp.rows.map(row => [row.row, row]));
  const profile = currentProfile();
  const maxSpeed = maxTransmissionSpeed(profile);
  const maxAccel = maxTransmissionAcceleration(profile);
  document.querySelectorAll('#multiPointTableBody tr').forEach(tr => {
    const rowNo = Number(tr.dataset.row);
    const current = byRow.get(rowNo) || normalizeMultiPointRow({row:rowNo}, rowNo);
    const position = tr.querySelector('[data-field="position"]');
    const speed = tr.querySelector('[data-field="speed"]');
    const acceleration = tr.querySelector('[data-field="acceleration"]');
    const dwell = tr.querySelector('[data-field="dwell"]');
    const enabled = tr.querySelector('[data-field="enabled"]');
    if (position) current.position = Number(position.value || 0);
    if (speed) current.speed = clamp(Number(speed.value || 0.001), 0.001, maxSpeed);
    if (acceleration) current.acceleration = clamp(Number(acceleration.value || 0), 0, maxAccel);
    if (dwell) current.dwell = Math.max(0, Math.min(60000, Math.round(Number(dwell.value || 0))));
    if (enabled) current.enabled = Boolean(enabled.checked);
    byRow.set(rowNo, normalizeMultiPointRow(current, rowNo));
  });
  mp.rows = Array.from(byRow.values()).sort((a, b) => a.row - b.row).filter(row => row.row >= mp.start && row.row < mp.start + mp.step);
  currentProfile().multiPoint = normalizeMultiPointState(mp);
  return currentProfile().multiPoint;
}
function onMultiPointSettingsChange() {
  syncMultiPointSettingsFromControls();
  renderMultiPointPanel(true);
  saveUiState();
}
function onMultiPointRowInput() {
  syncMultiPointRowsFromInputs();
  saveUiState();
}
function renderMultiPointPanel(force=false) {
  const panel = document.getElementById('panel-multi_point');
  if (!panel && !force) return;
  const text = UI_TEXT[currentLang];
  const mp = currentMultiPoint();
  const tx = normalizedTransmission(currentProfile());
  const profile = currentProfile();
  const speedMax = maxTransmissionSpeed(profile);
  const accelMax = maxTransmissionAcceleration(profile);
  const speedStep = multiPointInputStep(speedMax);
  const accelStep = multiPointInputStep(accelMax);
  setText('multiPointStartLabel', text.multiPointStart);
  setText('multiPointStepLabel', text.multiPointStep);
  setText('multiPointCycleLabel', text.multiPointCycleCount);
  setText('multiPointEditBtn', mp.editing ? text.multiPointWrite : text.multiPointEdit);
  setText('multiPointRowHead', text.multiPointRow);
  setText('multiPointTargetHead', text.multiPointTarget + ' (' + tx.unit + ')');
  setText('multiPointSpeedHead', text.speed + ' (' + transmissionRateUnit(profile, 1) + ')');
  setText('multiPointAccelHead', text.accel + ' (' + transmissionRateUnit(profile, 2) + ')');
  setText('multiPointDwellHead', text.multiPointDwell + ' ms');
  setText('multiPointEnableHead', text.multiPointEnabled);
  const startInput = document.getElementById('multiPointStart');
  const stepInput = document.getElementById('multiPointStep');
  const cycleInput = document.getElementById('multiPointCycleCount');
  if (startInput) {
    startInput.value = mp.start;
    startInput.disabled = !mp.editing;
  }
  if (stepInput) {
    stepInput.value = mp.step;
    stepInput.max = String(Math.min(64, 255 - mp.start + 1));
    stepInput.disabled = !mp.editing;
  }
  if (cycleInput) {
    cycleInput.value = String(mp.cycleCount);
    cycleInput.min = '1';
    cycleInput.max = String(MULTI_POINT_MAX_CYCLES);
    cycleInput.disabled = !mp.editing;
  }
  const body = document.getElementById('multiPointTableBody');
  if (body) {
    const readonly = mp.editing ? '' : 'readonly';
    const disabled = mp.editing ? '' : 'disabled';
    body.innerHTML = mp.rows.map(row => `
      <tr data-row="${row.row}">
        <td class="multi-point-row-cell" onclick="openMultiPointCyclePopup()"><strong>P${row.row}</strong><span class="multi-point-cycle-indicator"></span></td>
        <td><input data-field="position" type="number" step="0.001" value="${formatMultiPointNumber(row.position)}" ${readonly} onchange="onMultiPointRowInput()"></td>
        <td><input data-field="speed" type="number" min="0.001" max="${formatMultiPointNumber(speedMax)}" step="${speedStep}" value="${formatMultiPointNumber(row.speed)}" ${readonly} onchange="onMultiPointRowInput()"></td>
        <td><input data-field="acceleration" type="number" min="0" max="${formatMultiPointNumber(accelMax)}" step="${accelStep}" value="${formatMultiPointNumber(row.acceleration)}" ${readonly} onchange="onMultiPointRowInput()"></td>
        <td><input data-field="dwell" type="number" min="0" max="60000" step="100" value="${row.dwell}" ${readonly} onchange="onMultiPointRowInput()"></td>
        <td><input data-field="enabled" type="checkbox" ${row.enabled ? 'checked' : ''} ${disabled} onchange="onMultiPointRowInput()"></td>
      </tr>
    `).join('');
  }
  updateMultiPointAxis();
  renderMultiPointRunner(multiPointStatusByDevice[activeDevice]);
}
function updateMultiPointAxis() {
  const track = document.getElementById('multiPointAxisTrack');
  if (!track) return;
  const axis = track.closest('.multi-point-axis');
  const text = UI_TEXT[currentLang];
  const profile = currentProfile();
  profile.transmission = normalizedTransmission(profile);
  const tx = profile.transmission;
  const bounds = transmissionBounds(profile);
  const status = currentStatus();
  const homed = axisHomedForCurrentTransmission(activeDevice, status, profile);
  const fallback = Number(absPos && absPos.value !== undefined ? absPos.value : profile.absPos || 0);
  const actualLoadPos = status
    ? homingCurrentLoadPosition(activeDevice, status, profile)
    : transmissionValueFromCounts(fallback, profile);
  let displayLoadPos = actualLoadPos;
  if (tx.type !== 'linear' && tx.travelMode === 'periodic') {
    const span = Math.max(0.001, bounds.maxLoad - bounds.minLoad);
    displayLoadPos = ((actualLoadPos - bounds.minLoad) % span + span) % span + bounds.minLoad;
  } else {
    displayLoadPos = clamp(actualLoadPos, bounds.minLoad, bounds.maxLoad);
  }
  const loadSpan = Math.max(0.001, bounds.maxLoad - bounds.minLoad);
  const markerPct = clamp((displayLoadPos - bounds.minLoad) / loadSpan, 0, 1);
  setText('multiPointAxisMin', formatTransmissionScalar(bounds.minLoad, tx.unit, 1));
  setText('multiPointAxisMax', formatTransmissionScalar(bounds.maxLoad, tx.unit, 1));
  setText('multiPointCurrentValue', formatTransmissionScalar(displayLoadPos, tx.unit, 1));
  setText('multiPointUnhomedLabel', text.homingUnhomed);
  if (axis) axis.classList.toggle('unhomed', !homed);
  track.style.setProperty('--marker-pct', String(markerPct));
}
function syncAntiSwayMotionControls() {
  const speedInput = document.getElementById('antiSwaySpeedRpm');
  const accelInput = document.getElementById('antiSwayAccel');
  const anti = currentAntiSway();
  if (speedInput) {
    const min = Number(speedInput.min || 1);
    const max = Number(speedInput.max || MAX_SPEED_RPM);
    anti.speedRpm = clamp(Number(speedInput.value || anti.speedRpm || 120), min, max);
  }
  if (accelInput) {
    const min = Number(accelInput.min || 1);
    const max = Number(accelInput.max || MAX_ACCEL_RPM_S);
    anti.accelRpmS = clamp(Number(accelInput.value || anti.accelRpmS || 300), min, max);
  }
  currentProfile().antiSway = anti;
  renderAntiSwayPanel(false);
  saveUiState();
}
function syncAntiSwayTargetFromInput() {
  const input = document.getElementById('antiSwayTargetInput');
  if (!input || !absPos) return;
  const min = Number(absPos.min || input.min || -1677721600);
  const max = Number(absPos.max || input.max || 1677721600);
  const value = clamp(Number(input.value || absPos.value || 0), Math.min(min, max), Math.max(min, max));
  absPos.value = String(Math.round(value));
  currentProfile().absPos = Number(absPos.value);
  updateSliders();
}
function antiSwayTargetCountsFromClientX(clientX) {
  const track = document.getElementById('antiSwayAxisTrack');
  if (!track || !absPos) return null;
  const profile = currentProfile();
  profile.transmission = normalizedTransmission(profile);
  const bounds = transmissionBounds(profile);
  const rect = track.getBoundingClientRect();
  const trackLeft = rect.left + 18;
  const trackWidth = Math.max(1, rect.width - 36);
  const frac = clamp((Number(clientX) - trackLeft) / trackWidth, 0, 1);
  const loadSpan = Math.max(0.001, bounds.maxLoad - bounds.minLoad);
  const loadValue = bounds.minLoad + frac * loadSpan;
  const rawCounts = countsFromTransmissionValue(loadValue, profile);
  if (!Number.isFinite(rawCounts)) return null;
  const min = Math.min(Number(absPos.min || rawCounts), Number(absPos.max || rawCounts));
  const max = Math.max(Number(absPos.min || rawCounts), Number(absPos.max || rawCounts));
  const step = Math.max(1, Math.abs(Number(absPos.step || 1)) || 1);
  return clamp(Math.round(rawCounts / step) * step, min, max);
}
function setAntiSwayTargetCounts(counts) {
  if (!absPos || counts === null || counts === undefined || !Number.isFinite(Number(counts))) return;
  const targetInput = document.getElementById('antiSwayTargetInput');
  absPos.value = String(Math.round(Number(counts)));
  currentProfile().absPos = Number(absPos.value);
  if (targetInput) targetInput.value = absPos.value;
  updateSliders();
}
function updateAntiSwayTargetFromPointer(event) {
  if (!antiSwayTargetDrag.active || !event) return;
  event.preventDefault();
  const counts = antiSwayTargetCountsFromClientX(event.clientX);
  setAntiSwayTargetCounts(counts);
}
function startAntiSwayTargetDrag(event) {
  if (!event || (event.button !== undefined && event.button !== 0)) return false;
  event.preventDefault();
  event.stopPropagation();
  antiSwayTargetDrag.active = true;
  antiSwayTargetDrag.pointerId = event.pointerId;
  antiSwayTargetDrag.marker = event.currentTarget;
  if (antiSwayTargetDrag.marker) {
    antiSwayTargetDrag.marker.classList.add('dragging');
    if (antiSwayTargetDrag.marker.setPointerCapture && event.pointerId !== undefined) {
      try { antiSwayTargetDrag.marker.setPointerCapture(event.pointerId); } catch (_) {}
    }
  }
  updateAntiSwayTargetFromPointer(event);
  return false;
}
function endAntiSwayTargetDrag(event) {
  if (!antiSwayTargetDrag.active) return;
  const marker = antiSwayTargetDrag.marker;
  if (marker) {
    marker.classList.remove('dragging');
    if (marker.releasePointerCapture && antiSwayTargetDrag.pointerId !== null && antiSwayTargetDrag.pointerId !== undefined) {
      try { marker.releasePointerCapture(antiSwayTargetDrag.pointerId); } catch (_) {}
    }
  }
  antiSwayTargetDrag.active = false;
  antiSwayTargetDrag.pointerId = null;
  antiSwayTargetDrag.marker = null;
  saveUiState();
}
function onAntiSwaySettingsChange(commit=true) {
  const anti = currentAntiSway();
  const algorithmSelect = document.getElementById('antiSwayAlgorithm');
  if (algorithmSelect && algorithmSelect.value) {
    anti.algorithm = normalizeAntiSwayAlgorithm(algorithmSelect.value);
  }
  const sensorSelect = document.getElementById('antiSwaySensorAxis');
  if (sensorSelect && sensorSelect.value) {
    anti.sensorAxis = sensorSelect.value;
  }
  const limitInput = document.getElementById('antiSwayLimitInput');
  if (limitInput) {
    anti.allowedAngle = Math.max(0.1, Math.min(45, Number(limitInput.value || anti.allowedAngle || 3)));
  }
  const rodInput = document.getElementById('antiSwayRodInput');
  if (rodInput) {
    anti.rodLength = Math.max(1, Math.min(100000, Number(rodInput.value || anti.rodLength || 520)));
  }
  currentProfile().antiSway = anti;
  renderAntiSwayPanel(true);
  if (commit) {
    saveUiState(activeDevice, false);
    persistUiState(activeDevice, {updateAntiSwaySettings:true}).catch(err => console.error(err));
  }
}
function syncAntiSwayAlgorithmOptions(anti = currentAntiSway()) {
  const select = document.getElementById('antiSwayAlgorithm');
  if (!select) return normalizeAntiSwayAlgorithm(anti && anti.algorithm);
  const selected = normalizeAntiSwayAlgorithm(anti && anti.algorithm);
  const optionSignature = ANTI_SWAY_ALGORITHMS.join('|') + '|' + currentLang;
  if (select.dataset.options !== optionSignature) {
    select.innerHTML = '';
    for (const key of ANTI_SWAY_ALGORITHMS) {
      const option = document.createElement('option');
      option.value = key;
      option.textContent = antiSwayAlgorithmLabel(key);
      select.appendChild(option);
    }
    select.dataset.options = optionSignature;
  }
  select.value = selected;
  anti.algorithm = selected;
  return selected;
}
function openAntiSwayLimitEditor(event) {
  if (event) {
    event.preventDefault();
    event.stopPropagation();
  }
  const input = document.getElementById('antiSwayLimitInput');
  if (input) openTouchKeypadForInput(input);
  return false;
}
function handleAntiSwayLimitKey(event) {
  if (!event || (event.key !== 'Enter' && event.key !== ' ')) return true;
  return openAntiSwayLimitEditor(event);
}
function openAntiSwayRodEditor(event) {
  if (event) {
    event.preventDefault();
    event.stopPropagation();
  }
  const input = document.getElementById('antiSwayRodInput');
  if (input) openTouchKeypadForInput(input);
  return false;
}
function handleAntiSwayRodKey(event) {
  if (!event || (event.key !== 'Enter' && event.key !== ' ')) return true;
  return openAntiSwayRodEditor(event);
}
function syncAntiSwaySensorAxisOptions(anti = currentAntiSway()) {
  const select = document.getElementById('antiSwaySensorAxis');
  if (!select) return null;
  const options = antiSwaySensorAxisOptions(activeDevice);
  const nextValue = options.includes(anti.sensorAxis) ? anti.sensorAxis : preferredAntiSwaySensorAxis(activeDevice);
  if (select.dataset.options !== options.join('|')) {
    select.innerHTML = '';
    for (const device of options) {
      const option = document.createElement('option');
      option.value = device;
      option.textContent = axisDisplayName(device);
      select.appendChild(option);
    }
    select.dataset.options = options.join('|');
  }
  select.disabled = options.length === 0;
  select.value = nextValue;
  anti.sensorAxis = nextValue;
  return nextValue;
}
function antiSwaySensorDisplayValue(sensorAxis, status, profile) {
  if (!sensorAxis || !status || !profile) return '--';
  const tx = normalizedTransmission(profile);
  if (sensorAxis === 'aux_encoder') {
    return formatTransmissionScalar(auxDisplayLoadPosition(status, profile), tx.unit, 2);
  }
  return formatTransmissionScalar(homingCurrentLoadPosition(sensorAxis, status, profile), tx.unit, 2);
}
function antiSwaySensorAngleDeg(sensorAxis, status, profile) {
  if (!sensorAxis || !status || !profile) return null;
  const tx = normalizedTransmission(profile);
  const value = sensorAxis === 'aux_encoder'
    ? auxDisplayLoadPosition(status, profile)
    : homingCurrentLoadPosition(sensorAxis, status, profile);
  if (!Number.isFinite(value)) return null;
  if (tx.unit === 'rad') return value * 180 / Math.PI;
  if (tx.unit === 'rev') return value * 360;
  return value;
}
function antiSwayTipOffsetMm(angleDeg, rodLengthMm) {
  const angleRad = Number(angleDeg || 0) * Math.PI / 180;
  return Math.sin(angleRad) * Number(rodLengthMm || 0);
}
function antiSwayNaturalPeriodS(rodLengthMm, measuredPeriodS=0) {
  const measured = Number(measuredPeriodS || 0);
  if (Number.isFinite(measured) && measured >= 0.05 && measured <= 10) return measured;
  const lengthM = Math.max(0.001, Number(rodLengthMm || 520) / 1000);
  const effectiveLengthM = lengthM * (2 / 3);
  return 2 * Math.PI * Math.sqrt(effectiveLengthM / 9.80665);
}
function antiSwayZvdImpulses(periodS) {
  const period = Math.max(0.001, Number(periodS || 0));
  return [
    {time:0, amplitude:0.25},
    {time:period / 2, amplitude:0.5},
    {time:period, amplitude:0.25}
  ];
}
function antiSwayBasePositionAt(t, start, target, speed, accel, minDurationS=0) {
  const distance = Math.abs(Number(target) - Number(start));
  const direction = Number(target) >= Number(start) ? 1 : -1;
  const vmax = Math.max(0.001, Number(speed || 0));
  let a = Math.max(0.001, Number(accel || 0));
  if (!Number.isFinite(distance) || distance <= 0.000001) {
    return {position:Number(target) || 0, duration:0};
  }
  const accelTimeAtVmax = vmax / a;
  const accelDistanceAtVmax = 0.5 * a * accelTimeAtVmax * accelTimeAtVmax;
  let tAccel = accelTimeAtVmax;
  let tCruise = 0;
  let vPeak = vmax;
  let useSmoothBase = false;
  if (2 * accelDistanceAtVmax >= distance) {
    tAccel = Math.sqrt(distance / a);
    vPeak = a * tAccel;
  } else {
    tCruise = (distance - 2 * accelDistanceAtVmax) / vmax;
  }
  let duration = 2 * tAccel + tCruise;
  const minDuration = Math.max(0, Number(minDurationS || 0));
  const smoothVelocityDuration = 1.875 * distance / vmax;
  const smoothAccelDuration = Math.sqrt(5.773502691896258 * distance / a);
  const smoothDuration = Math.max(minDuration, smoothVelocityDuration, smoothAccelDuration);
  if (Number.isFinite(smoothDuration) && smoothDuration > 0) {
    duration = smoothDuration;
    useSmoothBase = true;
  }
  const time = clamp(Number(t || 0), 0, duration);
  if (useSmoothBase) {
    const x = duration <= 0 ? 1 : clamp(time / duration, 0, 1);
    const smooth = x * x * x * (10 + x * (-15 + 6 * x));
    return {position:Number(start) + direction * distance * smooth, duration};
  }
  let travelled = 0;
  if (time <= tAccel) {
    travelled = 0.5 * a * time * time;
  } else if (time <= tAccel + tCruise) {
    travelled = 0.5 * a * tAccel * tAccel + vPeak * (time - tAccel);
  } else {
    const td = time - tAccel - tCruise;
    travelled = 0.5 * a * tAccel * tAccel + vPeak * tCruise + vPeak * td - 0.5 * a * td * td;
  }
  return {position:Number(start) + direction * clamp(travelled, 0, distance), duration};
}
function antiSwayTrapezoidPositionAt(t, start, target, speed, accel) {
  const distance = Math.abs(Number(target) - Number(start));
  const direction = Number(target) >= Number(start) ? 1 : -1;
  const vmax = Math.max(0.001, Number(speed || 0));
  const a = Math.max(0.001, Number(accel || 0));
  if (!Number.isFinite(distance) || distance <= 0.000001) {
    return {position:Number(target) || 0, duration:0};
  }
  const accelTimeAtVmax = vmax / a;
  const accelDistanceAtVmax = 0.5 * a * accelTimeAtVmax * accelTimeAtVmax;
  let tAccel = accelTimeAtVmax;
  let tCruise = 0;
  let vPeak = vmax;
  if (2 * accelDistanceAtVmax >= distance) {
    tAccel = Math.sqrt(distance / a);
    vPeak = a * tAccel;
  } else {
    tCruise = (distance - 2 * accelDistanceAtVmax) / vmax;
  }
  const duration = 2 * tAccel + tCruise;
  const time = clamp(Number(t || 0), 0, duration);
  let travelled = 0;
  if (time <= tAccel) {
    travelled = 0.5 * a * time * time;
  } else if (time <= tAccel + tCruise) {
    travelled = 0.5 * a * tAccel * tAccel + vPeak * (time - tAccel);
  } else {
    const td = time - tAccel - tCruise;
    travelled = 0.5 * a * tAccel * tAccel + vPeak * tCruise + vPeak * td - 0.5 * a * td * td;
  }
  return {position:Number(start) + direction * clamp(travelled, 0, distance), duration};
}
function antiSwayTerminalEndpointPositionAt(t, start, target, speed, accel, periodS) {
  const distance = Math.abs(Number(target) - Number(start));
  const direction = Number(target) >= Number(start) ? 1 : -1;
  const vmax = Math.max(0.001, Number(speed || 0));
  const a = Math.max(0.001, Number(accel || 0));
  const period = Math.max(0.001, Number(periodS || 0));
  if (!Number.isFinite(distance) || distance <= 0.000001) {
    return {position:Number(target) || 0, duration:0, periods:1, decelStartS:0};
  }
  let periods = Math.max(
    1,
    Math.ceil(distance / (vmax * period)),
    Math.ceil(Math.sqrt(distance / (a * period * period)))
  );
  let decelStartS = periods * period;
  let vPeak = distance / decelStartS;
  let tAccel = vPeak / a;
  while (tAccel > decelStartS && periods < 10000) {
    periods += 1;
    decelStartS = periods * period;
    vPeak = distance / decelStartS;
    tAccel = vPeak / a;
  }
  const tCruise = Math.max(0, decelStartS - tAccel);
  const duration = decelStartS + tAccel;
  const time = clamp(Number(t || 0), 0, duration);
  let travelled = 0;
  if (time <= tAccel) {
    travelled = 0.5 * a * time * time;
  } else if (time <= decelStartS) {
    travelled = 0.5 * a * tAccel * tAccel + vPeak * (time - tAccel);
  } else {
    const remaining = duration - time;
    travelled = distance - 0.5 * a * remaining * remaining;
  }
  return {
    position:Number(start) + direction * clamp(travelled, 0, distance),
    duration,
    periods,
    decelStartS
  };
}
function buildAntiSwayPreviewPlan(snapshot) {
  if (!snapshot || !snapshot.command || !snapshot.controlAxis || !snapshot.controlAxis.load) return null;
  const start = Number(snapshot.controlAxis.load.position);
  const target = Number(snapshot.command.targetPosition);
  const speed = Number(snapshot.command.speed);
  const accel = Number(snapshot.command.acceleration);
  if (!Number.isFinite(start) || !Number.isFinite(target) || !Number.isFinite(speed) || !Number.isFinite(accel)) return null;
  const algorithm = normalizeAntiSwayAlgorithm(snapshot.antiSway && snapshot.antiSway.algorithm);
  const terminalMode = algorithm === 'zvd_terminal';
  const periodS = antiSwayNaturalPeriodS(snapshot.antiSway && snapshot.antiSway.rodLengthMm, snapshot.antiSway && snapshot.antiSway.measuredPeriodS);
  const impulses = antiSwayZvdImpulses(periodS);
  const normalPositionAt = (t) => antiSwayTrapezoidPositionAt(t, start, target, speed, accel);
  const basePositionAt = (t) => antiSwayBasePositionAt(t, start, target, speed, accel, periodS);
  const terminalPositionAt = (t) => antiSwayTerminalEndpointPositionAt(t, start, target, speed, accel, periodS);
  const normalDurationS = normalPositionAt(0).duration;
  const baseDurationS = terminalMode ? terminalPositionAt(0).duration : basePositionAt(0).duration;
  const shaperDelayS = terminalMode ? 0 : impulses[impulses.length - 1].time;
  const shapedDurationS = terminalMode ? baseDurationS : baseDurationS + shaperDelayS;
  const sampleCount = 42;
  const normal = [];
  const shaped = [];
  for (let i = 0; i < sampleCount; i++) {
    const frac = sampleCount <= 1 ? 0 : i / (sampleCount - 1);
    const t = shapedDurationS * frac;
    normal.push({t, position:normalPositionAt(t).position});
    let shapedPosition = terminalMode ? terminalPositionAt(t).position : 0;
    if (!terminalMode) {
      for (const impulse of impulses) {
        shapedPosition += impulse.amplitude * basePositionAt(t - impulse.time).position;
      }
    }
    shaped.push({t, position:shapedPosition});
  }
  return {
    algorithm,
    mode:terminalMode ? 'terminal_endpoint' : 'full_path',
    baseProfile:terminalMode ? 'period_matched_trapezoid' : 'smoothstep5',
    naturalPeriodS:periodS,
    shaperDelayS,
    baseDurationS,
    normalDurationS,
    shapedDurationS,
    addedTimeS:Math.max(0, shapedDurationS - normalDurationS),
    impulses:terminalMode ? [] : impulses,
    normal,
    shaped,
    motionConnected:false
  };
}
function antiSwayPlanPath(points, minValue, maxValue) {
  if (!Array.isArray(points) || !points.length) return '';
  const span = Math.max(0.000001, maxValue - minValue);
  const timeMax = Math.max(0.001, points[points.length - 1].t || 0);
  return points.map((point, index) => {
    const x = 4 + clamp(Number(point.t || 0) / timeMax, 0, 1) * 412;
    const y = 74 - clamp((Number(point.position) - minValue) / span, 0, 1) * 62;
    return (index === 0 ? 'M' : 'L') + x.toFixed(1) + ' ' + y.toFixed(1);
  }).join(' ');
}
function renderAntiSwayPlan(plan) {
  const normalPath = document.getElementById('antiSwayPlanNormalPath');
  const shapedPath = document.getElementById('antiSwayPlanShapedPath');
  if (!normalPath || !shapedPath) return;
  if (!plan || !Array.isArray(plan.normal) || !Array.isArray(plan.shaped)) {
    normalPath.setAttribute('d', 'M4 74 L416 74');
    shapedPath.setAttribute('d', 'M4 74 L416 74');
    setText('antiSwayNaturalPeriodValue', '--');
    setText('antiSwayPlanDelayValue', '--');
    setText('antiSwayPlanDurationValue', '--');
    return;
  }
  const values = plan.normal.concat(plan.shaped).map(point => Number(point.position)).filter(Number.isFinite);
  const minValue = values.length ? Math.min(...values) : 0;
  const maxValue = values.length ? Math.max(...values) : 1;
  normalPath.setAttribute('d', antiSwayPlanPath(plan.normal, minValue, maxValue));
  shapedPath.setAttribute('d', antiSwayPlanPath(plan.shaped, minValue, maxValue));
  setText('antiSwayNaturalPeriodValue', formatMultiPointNumber(plan.naturalPeriodS, 2) + ' s');
  setText('antiSwayPlanDelayValue', '+' + formatMultiPointNumber(plan.addedTimeS, 2) + ' s');
  setText('antiSwayPlanDurationValue', formatMultiPointNumber(plan.shapedDurationS, 2) + ' s');
}
function estimateAntiSwayPeriodFromSamples(samples) {
  const data = (Array.isArray(samples) ? samples : [])
    .map(sample => ({t:Number(sample.t), angle:Number(sample.angle)}))
    .filter(sample => Number.isFinite(sample.t) && Number.isFinite(sample.angle))
    .sort((a, b) => a.t - b.t);
  if (data.length < 12) return null;
  const angles = data.map(sample => sample.angle);
  const mean = angles.reduce((sum, value) => sum + value, 0) / angles.length;
  const minAngle = Math.min(...angles);
  const maxAngle = Math.max(...angles);
  const amplitude = (maxAngle - minAngle) * 0.5;
  if (!Number.isFinite(amplitude) || amplitude < 0.05) return null;
  const crossings = [];
  for (let i = 1; i < data.length; i++) {
    const prev = data[i - 1];
    const next = data[i];
    const y0 = prev.angle - mean;
    const y1 = next.angle - mean;
    if (y0 === y1) continue;
    const up = y0 <= 0 && y1 > 0;
    const down = y0 >= 0 && y1 < 0;
    if (!up && !down) continue;
    const frac = Math.abs(y0) / Math.max(0.000001, Math.abs(y0) + Math.abs(y1));
    const t = prev.t + (next.t - prev.t) * frac;
    const last = crossings[crossings.length - 1];
    if (last && t - last.t < 120) continue;
    crossings.push({t, direction:up ? 'up' : 'down'});
  }
  const candidates = [];
  for (let i = 1; i < crossings.length; i++) {
    const halfPeriod = (crossings[i].t - crossings[i - 1].t) / 1000;
    const period = halfPeriod * 2;
    if (period >= 0.2 && period <= 5) candidates.push(period);
  }
  for (const direction of ['up', 'down']) {
    const same = crossings.filter(crossing => crossing.direction === direction);
    for (let i = 1; i < same.length; i++) {
      const period = (same[i].t - same[i - 1].t) / 1000;
      if (period >= 0.2 && period <= 5) candidates.push(period);
    }
  }
  if (!candidates.length) return null;
  candidates.sort((a, b) => a - b);
  const median = candidates[Math.floor(candidates.length / 2)];
  const close = candidates.filter(value => Math.abs(value - median) <= Math.max(0.12, median * 0.18));
  const used = close.length ? close : candidates;
  const periodS = used.reduce((sum, value) => sum + value, 0) / used.length;
  if (!Number.isFinite(periodS) || periodS < 0.2 || periodS > 5) return null;
  return {periodS, amplitudeDeg:amplitude, crossingCount:crossings.length, sampleCount:data.length};
}
function calibrateAntiSwayPeriodFromWave() {
  const text = UI_TEXT[currentLang];
  const state = antiSwayStabilityByDevice[activeDevice] || {};
  const estimate = estimateAntiSwayPeriodFromSamples(state.periodSamples || state.samples);
  if (!estimate) {
    openDiagModal(text.antiSwayPeriodCalibrateFailedTitle, text.antiSwayPeriodCalibrateFailedBody);
    return false;
  }
  const anti = currentAntiSway();
  anti.measuredPeriodS = estimate.periodS;
  currentProfile().antiSway = anti;
  saveUiState(activeDevice, false);
  persistUiState(activeDevice, {updateAntiSwayPeriod:true}).catch(err => console.error(err));
  renderAntiSwayPanel(true);
  openDiagModal(
    text.antiSwayPeriodCalibratedTitle,
    text.antiSwayPeriodCalibratedBody.replace('{period}', formatMultiPointNumber(estimate.periodS, 3))
  );
  return false;
}
function antiSwayPositionArrived(snapshot, profile, status) {
  if (!snapshot || !profile || !status) return false;
  if (status.moving) return false;
  const target = Number(snapshot.command && snapshot.command.targetPosition);
  const current = Number(snapshot.controlAxis && snapshot.controlAxis.load && snapshot.controlAxis.load.position);
  if (!Number.isFinite(target) || !Number.isFinite(current)) return false;
  const span = Math.max(0.001, Math.abs(transmissionBounds(profile).maxLoad - transmissionBounds(profile).minLoad));
  const tolerance = Math.max(0.01, span * 0.001);
  return Math.abs(current - target) <= tolerance;
}
function antiSwayStateLabel(state) {
  const text = UI_TEXT[currentLang];
  if (state === 'stable') return text.antiSwayStable;
  if (state === 'settling') return text.antiSwaySettling;
  if (state === 'out_of_band') return text.antiSwayOutOfBand;
  if (state === 'waiting_position') return text.antiSwayWaitingPosition;
  return text.antiSwayNoSensor;
}
function antiSwayEstimateAngularVelocity(samples, now = Date.now()) {
  const data = (Array.isArray(samples) ? samples : [])
    .map(sample => ({t:Number(sample.t), angle:Number(sample.angle)}))
    .filter(sample => Number.isFinite(sample.t) && Number.isFinite(sample.angle))
    .filter(sample => now - sample.t <= 700)
    .sort((a, b) => a.t - b.t);
  if (data.length < 2) return null;
  const first = data[0];
  const last = data[data.length - 1];
  const dt = (last.t - first.t) / 1000;
  if (!Number.isFinite(dt) || dt < 0.08) return null;
  return (last.angle - first.angle) / dt;
}
function antiSwayPhaseInfo(snapshot, angleDeg, angularVelocityDegS) {
  const text = UI_TEXT[currentLang];
  if (!Number.isFinite(angleDeg)) {
    return {label:'--', angularVelocityDegS:null, motionSign:0};
  }
  const allowed = Number((snapshot && snapshot.antiSway && snapshot.antiSway.allowedAngleDeg) || currentAntiSway().allowedAngle || 3);
  const angleEps = Math.max(0.05, Math.min(0.5, Math.abs(allowed) * 0.12));
  const velocity = Number.isFinite(angularVelocityDegS) ? Number(angularVelocityDegS) : 0;
  const velocityEps = Math.max(0.12, angleEps * 1.6);
  const current = Number(snapshot && snapshot.controlAxis && snapshot.controlAxis.load && snapshot.controlAxis.load.position);
  const target = Number(snapshot && snapshot.command && snapshot.command.targetPosition);
  const moveDelta = Number.isFinite(current) && Number.isFinite(target) ? target - current : 0;
  const moveSign = Math.abs(moveDelta) > 0.001 ? Math.sign(moveDelta) : 0;
  const signedAngle = angleDeg;
  const signedVelocity = velocity;
  let label = text.antiSwayPhaseNeutral || 'Centered';
  if (Math.abs(signedAngle) <= angleEps) {
    if (signedVelocity > velocityEps) {
      label = moveSign ? (text.antiSwayPhaseCrossPositive || 'Crossing +') : (text.antiSwayPhaseCrossPositive || 'Crossing +');
    } else if (signedVelocity < -velocityEps) {
      label = moveSign ? (text.antiSwayPhaseCrossNegative || 'Crossing -') : (text.antiSwayPhaseCrossNegative || 'Crossing -');
    }
  } else {
    if (signedAngle < 0) {
      label = signedVelocity > velocityEps
        ? (text.antiSwayPhaseLagCatch || 'Lag Catch')
        : (text.antiSwayPhaseLagOut || 'Lag Out');
    } else {
      label = signedVelocity < -velocityEps
        ? (text.antiSwayPhaseLeadReturn || 'Lead Return')
        : (text.antiSwayPhaseLeadOut || 'Lead Out');
    }
  }
  return {label, angularVelocityDegS:velocity, motionSign:moveSign, signedAngle, signedVelocity};
}
function renderAntiSwayWave(state, allowedAngle) {
  const wave = document.getElementById('antiSwayWave');
  const decay = document.getElementById('antiSwayDecay');
  const band = document.getElementById('antiSwayBand');
  if (!wave || !decay) return;
  const samples = state && Array.isArray(state.samples) ? state.samples : [];
  const limit = Math.max(0.1, Number(allowedAngle || 3));
  const scale = Math.max(limit * 2, ...samples.map(sample => Math.abs(Number(sample.angle) || 0)), 1);
  const yForAngle = angle => clamp(75 - Number(angle || 0) / scale * 58, 15, 135);
  const now = Date.now();
  const points = samples.length
    ? samples.map(sample => {
      const age = clamp((now - sample.t) / 3000, 0, 1);
      const x = 4 + (1 - age) * 412;
      return x.toFixed(1) + ',' + yForAngle(sample.angle).toFixed(1);
    }).join(' ')
    : '4,75 416,75';
  wave.setAttribute('points', points);
  decay.setAttribute('points', samples.length ? points : '');
  if (band) {
    const bandTop = yForAngle(limit);
    const bandBottom = yForAngle(-limit);
    band.setAttribute('y', String(Math.min(bandTop, bandBottom).toFixed(1)));
    band.setAttribute('height', String(Math.abs(bandBottom - bandTop).toFixed(1)));
  }
}
function updateAntiSwayStability(snapshot, angleDeg) {
  const state = antiSwayStabilityByDevice[activeDevice];
  const now = Date.now();
  const anti = snapshot && snapshot.antiSway ? snapshot.antiSway : currentAntiSway();
  const profile = currentProfile(activeDevice);
  const controlStatus = currentStatus(activeDevice);
  const sensorAxis = anti.sensorAxis || '';
  if (state.lastSensorAxis !== sensorAxis) {
    state.samples = [];
    state.periodSamples = [];
    state.inBandSince = 0;
    state.stable = false;
    state.lastSensorAxis = sensorAxis;
  }
  if (!Number.isFinite(angleDeg)) {
    state.samples = [];
    state.periodSamples = [];
    state.inBandSince = 0;
    state.stable = false;
    state.state = 'waiting_sensor';
    return state;
  }
  const lastSample = state.samples[state.samples.length - 1];
  if (!lastSample || now - lastSample.t >= 60 || Math.abs(Number(lastSample.angle) - angleDeg) >= 0.001) {
    state.samples.push({t:now, angle:angleDeg});
    if (!Array.isArray(state.periodSamples)) state.periodSamples = [];
    state.periodSamples.push({t:now, angle:angleDeg});
  }
  state.samples = state.samples.filter(sample => now - sample.t <= 3000);
  state.periodSamples = (state.periodSamples || []).filter(sample => now - sample.t <= 12000);
  state.angularVelocityDegS = antiSwayEstimateAngularVelocity(state.samples, now);
  state.phase = antiSwayPhaseInfo(snapshot, angleDeg, state.angularVelocityDegS);
  const arrived = antiSwayPositionArrived(snapshot, profile, controlStatus);
  const inBand = Math.abs(angleDeg) <= Number(anti.allowedAngle || 0);
  if (!arrived) {
    state.inBandSince = 0;
    state.stable = false;
    state.state = 'waiting_position';
  } else if (!inBand) {
    state.inBandSince = 0;
    state.stable = false;
    state.state = 'out_of_band';
  } else {
    if (!state.inBandSince) state.inBandSince = now;
    state.stable = now - state.inBandSince >= 900;
    state.state = state.stable ? 'stable' : 'settling';
  }
  state.arrived = arrived;
  state.inBand = inBand;
  state.currentAngleDeg = angleDeg;
  state.tipOffsetMm = antiSwayTipOffsetMm(angleDeg, anti.rodLengthMm || anti.rodLength);
  const absAngles = state.samples.map(sample => Math.abs(sample.angle));
  state.peakAngleDeg = absAngles.length ? Math.max(...absAngles) : Math.abs(angleDeg);
  state.residualAngleDeg = Math.abs(angleDeg);
  state.settleProgressMs = state.inBandSince ? Math.min(900, now - state.inBandSince) : 0;
  updateAntiSwayRunMetrics(snapshot, state, angleDeg, now);
  return state;
}
function resetAntiSwayRunMetrics(device = activeDevice, snapshot = null) {
  const state = antiSwayStabilityByDevice[device] || {};
  const angle = Number(state.currentAngleDeg);
  antiSwayRunMetricsByDevice[device] = {
    active:true,
    observing:true,
    startedAt:Date.now(),
    motionDoneAt:0,
    completedAt:0,
    baselineAngleDeg:Number.isFinite(angle) ? angle : null,
    peakAngleDeg:0,
    residualAngleDeg:null,
    settleMs:null,
    samples:[],
    targetDeltaCounts:Math.abs(antiSwayTargetDeltaCounts(snapshot)),
    lastResult:null
  };
}
function updateAntiSwayRunMetrics(snapshot, stability, angleDeg, now = Date.now(), device = activeDevice) {
  const metrics = antiSwayRunMetricsByDevice[device];
  if (!metrics || (!metrics.active && !metrics.observing)) return metrics;
  if (!Number.isFinite(angleDeg)) return metrics;
  if (!Number.isFinite(Number(metrics.baselineAngleDeg))) metrics.baselineAngleDeg = angleDeg;
  const relative = Number(angleDeg) - Number(metrics.baselineAngleDeg || 0);
  const absolute = Math.abs(relative);
  metrics.peakAngleDeg = Math.max(Number(metrics.peakAngleDeg || 0), absolute);
  metrics.residualAngleDeg = absolute;
  metrics.samples.push({t:now, angle:angleDeg, relative});
  metrics.samples = metrics.samples.filter(sample => now - sample.t <= 12000);
  if (!metrics.settleMs && metrics.motionDoneAt && stability && stability.state === 'stable') {
    metrics.settleMs = now - metrics.motionDoneAt;
  }
  return metrics;
}
function markAntiSwayMotionDone(device = activeDevice) {
  const metrics = antiSwayRunMetricsByDevice[device];
  if (!metrics) return;
  metrics.active = false;
  metrics.observing = true;
  metrics.motionDoneAt = Date.now();
}
function finishAntiSwayRunMetrics(device = activeDevice) {
  const metrics = antiSwayRunMetricsByDevice[device];
  if (!metrics) return null;
  metrics.active = false;
  metrics.observing = false;
  metrics.completedAt = Date.now();
  metrics.lastResult = {
    completedAt:metrics.completedAt,
    durationMs:metrics.startedAt ? metrics.completedAt - metrics.startedAt : 0,
    peakAngleDeg:Number(metrics.peakAngleDeg || 0),
    residualAngleDeg:Number(metrics.residualAngleDeg || 0),
    settleMs:metrics.settleMs,
    sampleCount:Array.isArray(metrics.samples) ? metrics.samples.length : 0,
    targetDeltaCounts:Number(metrics.targetDeltaCounts || 0)
  };
  window.mctivityLastAntiSwayRunMetrics = metrics.lastResult;
  return metrics.lastResult;
}
function currentAntiSwayRunMetrics(device = activeDevice) {
  const metrics = antiSwayRunMetricsByDevice[device];
  if (!metrics) return null;
  if (metrics.active || metrics.observing) return metrics;
  return metrics.lastResult || null;
}
function antiSwayRunMetricsSummary(metrics) {
  if (!metrics) return '';
  const text = UI_TEXT[currentLang];
  const peak = Number.isFinite(Number(metrics.peakAngleDeg)) ? formatMultiPointNumber(metrics.peakAngleDeg, 2) + ' deg' : '--';
  const residual = Number.isFinite(Number(metrics.residualAngleDeg)) ? formatMultiPointNumber(metrics.residualAngleDeg, 2) + ' deg' : '--';
  const settle = Number.isFinite(Number(metrics.settleMs)) ? formatMultiPointNumber(Number(metrics.settleMs) / 1000, 1) + ' s' : '--';
  return (text.antiSwayRunSummary || 'Run metrics') + '：' +
    (text.antiSwayPeakShort || 'Peak') + ' ' + peak + '，' +
    (text.antiSwayResidualShort || 'Residual') + ' ' + residual + '，' +
    (text.antiSwaySettleShort || 'Settle') + ' ' + settle + '。';
}
async function syncAntiSwayInputWithBackend(snapshot) {
  if (!snapshot || !snapshot.ready || !snapshot.ready.assembled) return snapshot;
  const payload = {
    cmd: 'anti_sway_input',
    sensor_axis: snapshot.antiSway.sensorAxis,
    current_counts: Math.round(antiSwaySnapshotCurrentCounts(snapshot)),
    target_counts: Math.round(Number(snapshot.command.targetCounts || 0)),
    speed_rpm: Math.round(Number(snapshot.command.speedRpm || 0)),
    acceleration_rpm_s: Math.round(Number(snapshot.command.accelerationRpmS || 0)),
    rod_length_mm: Number(snapshot.antiSway.rodLengthMm || 520),
    natural_period_s: Number(snapshot.antiSway.naturalPeriodS || antiSwayNaturalPeriodS(snapshot.antiSway.rodLengthMm, snapshot.antiSway.measuredPeriodS)),
    allowed_angle_deg: Number(snapshot.antiSway.allowedAngleDeg || 3),
      algorithm: normalizeAntiSwayAlgorithm(snapshot.antiSway.algorithm)
  };
  try {
    const data = await apiForDevice(snapshot.controlAxis.device, payload);
    if (data && data.ok && data.anti_sway_input) {
      snapshot.backend = data.anti_sway_input;
      antiSwayInputByDevice[snapshot.controlAxis.device] = snapshot;
      window.mctivityLastAntiSwayInput = snapshot;
    }
  } catch (err) {
    console.error(err);
  }
  return snapshot;
}
function antiSwayExecutionConfig() {
  const cfg = capabilityState.antiSwayExecution || {};
  return {
    enabled:Boolean(cfg.enabled),
    limitMode:String(cfg.limitMode || 'transmission_soft_limits'),
    strategy:String(cfg.strategy || 'continuous_zvd_curve')
  };
}
function renderAntiSwayExecutionBadge() {
  const badge = document.getElementById('antiSwayPreviewBadge');
  if (!badge) return;
  const text = UI_TEXT[currentLang];
  const enabled = antiSwayExecutionConfig().enabled;
  badge.textContent = enabled ? text.antiSwayExecutionUnlocked : text.antiSwayExecutionLocked;
  badge.title = enabled ? text.antiSwayExecutionUnlockedHelp : text.antiSwayExecutionLockedHelp;
  badge.classList.toggle('execution-unlocked', enabled);
  badge.classList.toggle('execution-locked', !enabled);
}
function antiSwayDebugEnabled() {
  try {
    const params = new URLSearchParams(window.location.search || '');
    return params.get('anti_sway_debug') === '1' || params.get('debug_anti_sway') === '1';
  } catch (_) {
    return false;
  }
}
function renderAntiSwayDebugPanels() {
  const show = antiSwayDebugEnabled();
  document.querySelectorAll('[data-anti-sway-debug-panel]').forEach(panel => {
    panel.hidden = !show;
  });
}
function antiSwayCurrentCounts(device = activeDevice) {
  const status = currentStatus(device);
  const statusPos = status ? Number(status.pos) : NaN;
  return Number.isFinite(statusPos) ? axisCounts(statusPos) : 0;
}
function antiSwaySnapshotCurrentCounts(snapshot, device = activeDevice) {
  const snapshotCounts = Number(snapshot && snapshot.controlAxis && snapshot.controlAxis.raw && snapshot.controlAxis.raw.positionCounts);
  return Number.isFinite(snapshotCounts) ? snapshotCounts : antiSwayCurrentCounts(device);
}
function antiSwayTargetDeltaCounts(snapshot) {
  const currentCounts = antiSwaySnapshotCurrentCounts(snapshot);
  const targetCounts = Number(snapshot && snapshot.command ? snapshot.command.targetCounts : 0);
  return targetCounts - currentCounts;
}
function antiSwayMoveDirectionSign(snapshot, profile = currentProfile()) {
  const currentLoad = Number(snapshot && snapshot.controlAxis && snapshot.controlAxis.load && snapshot.controlAxis.load.position);
  const targetLoad = Number(snapshot && snapshot.command && snapshot.command.targetPosition);
  if (Number.isFinite(currentLoad) && Number.isFinite(targetLoad)) {
    const deltaLoad = targetLoad - currentLoad;
    if (Math.abs(deltaLoad) > 0.000001) return Math.sign(deltaLoad);
    return 0;
  }
  const currentCounts = antiSwaySnapshotCurrentCounts(snapshot);
  const targetCounts = Number(snapshot && snapshot.command ? snapshot.command.targetCounts : 0);
  const currentFallback = transmissionValueFromCounts(currentCounts, profile);
  const targetFallback = transmissionValueFromCounts(targetCounts, profile);
  const deltaFallback = targetFallback - currentFallback;
  return Math.abs(deltaFallback) > 0.000001 ? Math.sign(deltaFallback) : 0;
}
function antiSwayExecutionMessage(template, snapshot, profile = currentProfile()) {
  const tx = normalizedTransmission(profile);
  const target = Number(snapshot && snapshot.command && snapshot.command.targetPosition);
  const current = Number(snapshot && snapshot.controlAxis && snapshot.controlAxis.load && snapshot.controlAxis.load.position);
  const delta = Number.isFinite(target) && Number.isFinite(current) ? target - current : 0;
  const deltaText = (delta > 0 ? '+' : '') + formatMultiPointNumber(delta, 3);
  return String(template || '')
    .replace('{target}', formatMultiPointNumber(target, 3))
    .replace('{current}', formatMultiPointNumber(current, 3))
    .replace('{delta}', deltaText)
    .replace('{distance}', formatMultiPointNumber(Math.abs(delta), 3))
    .split('{unit}').join(tx.unit);
}
function singlePointExecutionMessage(targetCounts = Number(absPos && absPos.value || 0), profile = currentProfile()) {
  const text = UI_TEXT[currentLang];
  const tx = normalizedTransmission(profile);
  const targetPosition = transmissionValueFromCounts(targetCounts, profile);
  const currentLoad = homingCurrentLoadPosition(activeDevice, currentStatus(), profile);
  const distance = Number.isFinite(Number(currentLoad)) ? Math.abs(targetPosition - Number(currentLoad)) : 0;
  return String(text.positionExecutionConfirm || '')
    .replace('{target}', formatMultiPointNumber(targetPosition, 3))
    .replace('{distance}', formatMultiPointNumber(distance, 3))
    .split('{unit}').join(tx.unit);
}
function antiSwayRunPayload(snapshot, dryRun = true) {
  const currentCounts = Math.round(antiSwaySnapshotCurrentCounts(snapshot));
  return {
    cmd:'anti_sway_run',
    sensor_axis:snapshot.antiSway.sensorAxis,
    current_counts:currentCounts,
    target_counts:Math.round(Number(snapshot.command.targetCounts || 0)),
    speed_rpm:Math.round(Number(snapshot.command.speedRpm || 0)),
    acceleration_rpm_s:Math.round(Number(snapshot.command.accelerationRpmS || 0)),
    rod_length_mm:Number(snapshot.antiSway.rodLengthMm || 520),
    natural_period_s:Number(snapshot.antiSway.naturalPeriodS || antiSwayNaturalPeriodS(snapshot.antiSway.rodLengthMm, snapshot.antiSway.measuredPeriodS)),
    allowed_angle_deg:Number(snapshot.antiSway.allowedAngleDeg || 3),
    algorithm:normalizeAntiSwayAlgorithm(snapshot.antiSway.algorithm),
    dry_run:Boolean(dryRun)
  };
}
function antiSwayUsesTerminalPhaseGate(snapshot, runRequest) {
  const strategy = String(runRequest && runRequest.execution_strategy || '');
  const command = runRequest && runRequest.curve_command ? runRequest.curve_command : {};
  return normalizeAntiSwayAlgorithm(snapshot && snapshot.antiSway && snapshot.antiSway.algorithm) === 'zvd_terminal' ||
    strategy.indexOf('terminal') >= 0 ||
    String(command.cmd || '').indexOf('terminal_anti_sway') >= 0;
}
function antiSwayPhaseGateInfo(snapshot, stability) {
  const text = UI_TEXT[currentLang];
  const moveSign = antiSwayMoveDirectionSign(snapshot);
  const angle = Number(stability && stability.currentAngleDeg);
  const velocity = Number(stability && stability.angularVelocityDegS);
  const allowed = Number((snapshot && snapshot.antiSway && snapshot.antiSway.allowedAngleDeg) || currentAntiSway().allowedAngle || 3);
  const neutralAngle = 0.5;
  const staticOffsetAngle = 1.2;
  const leadAngle = Math.max(0.15, Math.min(1.2, Math.abs(allowed) * 0.35));
  const velocityEps = 3.0;
  if (!moveSign) {
    return {ready:true, reason:'bypass', label:text.antiSwayPhaseGateNeutral || 'Low Sway'};
  }
  if (!Number.isFinite(angle) || !Number.isFinite(velocity)) {
    return {ready:false, reason:'waiting_samples', label:'--'};
  }
  const now = Date.now();
  const recentSamples = (Array.isArray(stability && stability.samples) ? stability.samples : [])
    .map(sample => ({t:Number(sample.t), angle:Number(sample.angle)}))
    .filter(sample => Number.isFinite(sample.t) && Number.isFinite(sample.angle) && now - sample.t <= 900);
  const recentMin = recentSamples.length ? Math.min(...recentSamples.map(sample => sample.angle)) : angle;
  const recentMax = recentSamples.length ? Math.max(...recentSamples.map(sample => sample.angle)) : angle;
  const recentSwingAmplitude = Math.abs(recentMax - recentMin) / 2;
  const relativeAngle = angle * moveSign;
  const relativeVelocity = velocity * moveSign;
  const lowAbsoluteAngle = Math.abs(angle) <= neutralAngle && Math.abs(velocity) <= velocityEps;
  const lowRecentSwing = recentSamples.length >= 3 &&
    Math.abs(angle) <= staticOffsetAngle &&
    recentSwingAmplitude <= neutralAngle &&
    Math.abs(velocity) <= velocityEps;
  const neutral = lowAbsoluteAngle || lowRecentSwing;
  const leadReturning = relativeAngle >= leadAngle && relativeVelocity <= -velocityEps;
  const leadCrossing = relativeAngle > -neutralAngle && relativeAngle <= leadAngle && relativeVelocity <= -velocityEps;
  const ready = neutral || leadReturning || leadCrossing;
  let label = text.antiSwayPhaseGateReady || 'Phase Ready';
  if (neutral) label = text.antiSwayPhaseGateNeutral || 'Low Sway';
  if (!ready) label = stability && stability.phase && stability.phase.label ? stability.phase.label : '--';
  return {
    ready,
    reason:neutral ? 'neutral' : (ready ? 'lead_return' : 'waiting'),
    label,
    angleDeg:angle,
    angularVelocityDegS:velocity,
    recentSwingAmplitudeDeg:recentSwingAmplitude,
    relativeAngleDeg:relativeAngle,
    relativeVelocityDegS:relativeVelocity,
    moveSign
  };
}
async function waitAntiSwayTerminalPhaseGate(snapshot, runRequest, commandSeq) {
  if (!antiSwayUsesTerminalPhaseGate(snapshot, runRequest)) return {ready:true, skipped:true};
  const text = UI_TEXT[currentLang];
  const motionState = currentMotion();
  let lastGate = null;
  for (let i = 0; i < 3; i += 1) {
    await refreshAntiSwaySensorStatus().catch(err => console.error(err));
    lastGate = antiSwayPhaseGateInfo(snapshot, antiSwayStabilityByDevice[activeDevice] || {});
    if (lastGate && lastGate.ready) {
      window.mctivityLastAntiSwayPhaseGate = lastGate;
      return Object.assign({waitedMs:i * 80}, lastGate);
    }
    if (i < 2) await antiSwaySleep(80);
  }
  const periodS = Number(
    (runRequest && runRequest.anti_sway_plan && runRequest.anti_sway_plan.natural_period_s) ||
    (snapshot && snapshot.antiSwayPlan && snapshot.antiSwayPlan.naturalPeriodS) ||
    (snapshot && snapshot.antiSway && snapshot.antiSway.naturalPeriodS) ||
    1.2
  );
  const timeoutMs = Math.max(700, Math.min(2600, Number(periodS || 1.2) * 1250));
  const startedAt = Date.now();
  openDiagModal(
    text.antiSwayPhaseWaitingTitle || modeLabel('anti_sway_position'),
    String(text.antiSwayPhaseWaitingBody || '').replace('{time}', formatMultiPointNumber(timeoutMs / 1000, 2))
  );
  while (Date.now() - startedAt <= timeoutMs) {
    if (commandSeq !== motionState.commandSeq || motionState.stopRequested) return {ready:false, stopped:true};
    await refreshAntiSwaySensorStatus().catch(err => console.error(err));
    const state = antiSwayStabilityByDevice[activeDevice] || {};
    lastGate = antiSwayPhaseGateInfo(snapshot, state);
    state.phaseGate = lastGate;
    window.mctivityLastAntiSwayPhaseGate = lastGate;
    renderAntiSwayPanel(false);
    if (lastGate.ready) return Object.assign({waitedMs:Date.now() - startedAt}, lastGate);
    await antiSwaySleep(60);
  }
  return Object.assign({
    ready:true,
    timeout:true,
    waitedMs:Date.now() - startedAt,
    label:text.antiSwayPhaseGateTimeout || 'Phase Wait Timeout'
  }, lastGate || {});
}
function antiSwayRunBlockers(snapshot, realExecution = false) {
  const text = UI_TEXT[currentLang];
  const status = currentStatus();
  const profile = currentProfile();
  const blockers = [];
  if (!modeIsAssembled('anti_sway_position')) blockers.push(text.antiSwayDryRunNeedModule);
  if (!status || !axisHomedForCurrentTransmission(activeDevice, status, profile)) blockers.push(text.antiSwayDryRunNeedHoming);
  if (!status || !(status.enabled || status.servo_request)) blockers.push(text.antiSwayDryRunNeedEnable);
  if (status && status.fault) blockers.push(text.antiSwayDryRunNeedNoFault);
  if (!snapshot || !snapshot.ready || !snapshot.ready.sensorAxisValid || !snapshot.ready.sensorAxisReady) blockers.push(text.antiSwayDryRunNeedSensor);
  if (
    !snapshot ||
    !Number.isFinite(Number(snapshot.command && snapshot.command.targetCounts)) ||
    !Number.isFinite(Number(snapshot.command && snapshot.command.speedRpm)) ||
    !Number.isFinite(Number(snapshot.command && snapshot.command.accelerationRpmS)) ||
    Number(snapshot.command.speedRpm) <= 0 ||
    Number(snapshot.command.accelerationRpmS) <= 0
  ) {
    blockers.push(text.antiSwayDryRunNeedTarget);
  }
  return blockers;
}
function antiSwaySegmentSchedule(runRequest, snapshot) {
  const command = (runRequest && runRequest.command) || {};
  const currentCounts = Number.isFinite(Number(command.current_counts)) ? Number(command.current_counts) : antiSwaySnapshotCurrentCounts(snapshot);
  const targetCounts = Number.isFinite(Number(command.target_counts)) ? Number(command.target_counts) : Number(snapshot.command.targetCounts || 0);
  const segments = Array.isArray(runRequest && runRequest.segments) ? runRequest.segments : [];
  const plan = (runRequest && runRequest.anti_sway_plan) || (snapshot && snapshot.antiSwayPlan) || {};
  const impulses = Array.isArray(plan.impulses) ? plan.impulses : [];
  const defaultPeriod = Number(plan.natural_period_s || antiSwayNaturalPeriodS(snapshot && snapshot.antiSway && snapshot.antiSway.rodLengthMm, snapshot && snapshot.antiSway && snapshot.antiSway.measuredPeriodS));
  const schedule = segments
    .map((segment, index) => {
      const target = Number(segment && segment.target_counts);
      if (!Number.isFinite(target)) return null;
      const impulse = impulses[index] || {};
      const impulseTime = Number.isFinite(Number(impulse.time_s)) ? Number(impulse.time_s) : Number(impulse.time || 0);
      const time = Number.isFinite(Number(segment && segment.impulse_time_s))
        ? Number(segment.impulse_time_s)
        : (Number.isFinite(impulseTime) ? impulseTime : (index === 0 ? 0 : defaultPeriod * index / Math.max(1, segments.length - 1)));
      return {
        index:Number(segment && segment.index) || index + 1,
        targetCounts:Math.round(target),
        startCounts:Number.isFinite(Number(segment && segment.start_counts)) ? Number(segment.start_counts) : null,
        impulseTimeS:Math.max(0, time),
        weight:Number(segment && segment.weight) || Number(impulse.amplitude) || 0
      };
    })
    .filter(Boolean);
  if (schedule.length) return schedule;
  const delta = targetCounts - currentCounts;
  const fallbackImpulses = impulses.length ? impulses : antiSwayZvdImpulses(defaultPeriod);
  let cumulative = 0;
  return fallbackImpulses.map((impulse, index) => {
    const weight = Number(impulse.amplitude || 0);
    const impulseTime = Number.isFinite(Number(impulse.time_s)) ? Number(impulse.time_s) : Number(impulse.time || 0);
    cumulative += weight;
    return {
      index:index + 1,
      targetCounts:index === fallbackImpulses.length - 1 ? Math.round(targetCounts) : Math.round(currentCounts + delta * cumulative),
      startCounts:null,
      impulseTimeS:Math.max(0, impulseTime),
      weight
    };
  });
}
function antiSwaySegmentTargets(runRequest, snapshot) {
  return antiSwaySegmentSchedule(runRequest, snapshot).map(segment => segment.targetCounts);
}
function antiSwayMotionPayload(targetCounts, snapshot, currentCountsForTiming=null) {
  return Object.assign({
    cmd:'move_abs',
    pos:axisCounts(targetCounts),
    move_ms:absoluteMoveMs(targetCounts, currentCountsForTiming, snapshot.command.speedRpm),
    speed_rpm:Math.round(Number(snapshot.command.speedRpm || absSpeedRpm.value || 0)),
    acceleration_rpm_s:Math.round(Number(snapshot.command.accelerationRpmS || absAccel.value || 0))
  }, currentMotionBoundsPayload());
}
function antiSwayContinuousMotionPayload(snapshot, runRequest) {
  const command = (runRequest && runRequest.curve_command) || {};
  const plan = (runRequest && runRequest.anti_sway_plan) || (snapshot && snapshot.antiSwayPlan) || {};
  const targetCounts = Number.isFinite(Number(command.target_counts))
    ? Number(command.target_counts)
    : Number(snapshot && snapshot.command && snapshot.command.targetCounts || 0);
  const speedRpm = Number.isFinite(Number(command.speed_rpm))
    ? Number(command.speed_rpm)
    : Number(snapshot && snapshot.command && snapshot.command.speedRpm || 0);
  const accelRpmS = Number.isFinite(Number(command.acceleration_rpm_s))
    ? Number(command.acceleration_rpm_s)
    : Number(snapshot && snapshot.command && snapshot.command.accelerationRpmS || 0);
  const naturalPeriodMs = Number.isFinite(Number(command.natural_period_ms))
    ? Number(command.natural_period_ms)
    : Number(plan.natural_period_s || antiSwayNaturalPeriodS(snapshot && snapshot.antiSway && snapshot.antiSway.rodLengthMm, snapshot && snapshot.antiSway && snapshot.antiSway.measuredPeriodS)) * 1000;
  return Object.assign({
    cmd:String(command.cmd || 'anti_sway_curve_abs'),
    pos:axisCounts(Math.round(targetCounts)),
    speed_rpm:Math.round(speedRpm),
    acceleration_rpm_s:Math.round(accelRpmS),
    natural_period_ms:Math.max(50, Math.min(10000, Math.round(naturalPeriodMs)))
  }, currentMotionBoundsPayload());
}
function antiSwaySleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}
async function antiSwayWaitUntil(commandSeq, startedAt, dueMs) {
  const motionState = currentMotion();
  while (Date.now() < startedAt + dueMs) {
    if (commandSeq !== motionState.commandSeq || motionState.stopRequested) return false;
    await antiSwaySleep(Math.min(80, Math.max(0, startedAt + dueMs - Date.now())));
  }
  return commandSeq === motionState.commandSeq && !motionState.stopRequested;
}
async function observeAntiSwayAfterMotion(commandSeq, durationMs = 2500) {
  const motionState = currentMotion();
  const endAt = Date.now() + Math.max(0, Number(durationMs || 0));
  while (Date.now() < endAt) {
    if (commandSeq !== motionState.commandSeq || motionState.stopRequested) return false;
    await refreshAntiSwaySensorStatus().catch(err => console.error(err));
    await antiSwaySleep(100);
  }
  return commandSeq === motionState.commandSeq && !motionState.stopRequested;
}
async function waitAntiSwaySegmentSettled(commandSeq, targetCounts, timeoutMs = 60000) {
  const motionState = currentMotion();
  const startedAt = Date.now();
  let seenMoving = false;
  let stableSince = 0;
  while (Date.now() - startedAt < timeoutMs) {
    if (commandSeq !== motionState.commandSeq || motionState.stopRequested) return false;
    const data = await api({cmd:'status'}).catch(err => ({ok:false, error:String(err)}));
    const status = (data && data.status) || currentStatus();
    if (status && status.moving) {
      seenMoving = true;
      stableSince = 0;
    } else if (status && (seenMoving || Date.now() - startedAt > 800)) {
      const currentCounts = axisCounts(Number(status.pos || 0));
      const toleranceCounts = Math.max(2048, REV / 2000);
      if (seenMoving || Math.abs(currentCounts - Number(targetCounts)) <= toleranceCounts) {
        if (!stableSince) stableSince = Date.now();
        if (Date.now() - stableSince > 250) return true;
      }
    }
    await antiSwaySleep(120);
  }
  throw new Error('anti-sway segment timeout');
}
async function executeAntiSwayContinuousCurve(snapshot, runRequest) {
  const text = UI_TEXT[currentLang];
  const motionState = currentMotion();
  const commandSeq = ++motionState.commandSeq;
  const finalTarget = Math.round(Number(snapshot && snapshot.command && snapshot.command.targetCounts || 0));
  const curvePayload = antiSwayContinuousMotionPayload(snapshot, runRequest);
  motionState.stopRequested = false;
  motionState.latch = true;
  motionState.seenMoving = false;
  motionState.gearStoppedLatched = false;
  motionState.motionCancelNoticeKey = '';
  motionState.commandAt = Date.now();
  renderMotionToggle(true, text.antiSwayPhaseWaitingTitle || text.antiSwayExecutionRunningTitle);
  try {
    const phaseGate = await waitAntiSwayTerminalPhaseGate(snapshot, runRequest, commandSeq);
    if (commandSeq !== motionState.commandSeq || motionState.stopRequested || (phaseGate && phaseGate.stopped)) {
      openDiagModal(text.antiSwayExecutionStoppedTitle, text.antiSwayExecutionStoppedBody);
      return {ok:false, stopped:true};
    }
    snapshot.phaseGate = phaseGate;
    resetAntiSwayRunMetrics(activeDevice, snapshot);
    renderMotionToggle(true, text.antiSwayExecutionRunningTitle);
    openDiagModal(text.antiSwayExecutionRunningTitle, text.antiSwayExecutionRunningBody);
    window.mctivityLastAntiSwayCurvePayload = curvePayload;
    const moveResult = await api(curvePayload);
    window.mctivityLastAntiSwayCurveResult = moveResult;
    if (commandSeq !== motionState.commandSeq || motionState.stopRequested) {
      openDiagModal(text.antiSwayExecutionStoppedTitle, text.antiSwayExecutionStoppedBody);
      return {ok:false, stopped:true};
    }
    if (!moveResult || !moveResult.ok) throw new Error((moveResult && (moveResult.message || moveResult.error)) || 'anti-sway curve failed');
    const settled = await waitAntiSwaySegmentSettled(commandSeq, finalTarget);
    if (!settled) {
      openDiagModal(text.antiSwayExecutionStoppedTitle, text.antiSwayExecutionStoppedBody);
      return {ok:false, stopped:true};
    }
    markAntiSwayMotionDone(activeDevice);
    const observed = await observeAntiSwayAfterMotion(commandSeq, 2500);
    if (!observed) {
      finishAntiSwayRunMetrics(activeDevice);
      openDiagModal(text.antiSwayExecutionStoppedTitle, text.antiSwayExecutionStoppedBody);
      return {ok:false, stopped:true};
    }
    const runMetrics = finishAntiSwayRunMetrics(activeDevice);
    if (commandSeq === motionState.commandSeq) {
      motionState.latch = false;
      motionState.seenMoving = false;
      renderMotionToggle(false);
      openDiagModal(
        text.antiSwayExecutionCompleteTitle,
        antiSwayExecutionMessage(text.antiSwayExecutionCompleteBody, snapshot, currentProfile()) +
          (runMetrics ? '\n' + antiSwayRunMetricsSummary(runMetrics) : '')
      );
    }
    return {ok:true, anti_sway_execution:{strategy:String(runRequest && runRequest.execution_strategy || 'continuous_zvd_curve'), payload:curvePayload, metrics:runMetrics}};
  } catch (err) {
    if (commandSeq === motionState.commandSeq) {
      finishAntiSwayRunMetrics(activeDevice);
      motionState.latch = false;
      motionState.seenMoving = false;
      renderMotionToggle(false);
      openDiagModal(modeLabel('anti_sway_position'), err.message || String(err));
    }
    console.error(err);
    return {ok:false, error:String(err && err.message || err)};
  }
}
async function executeAntiSwaySegments(snapshot, runRequest) {
  const text = UI_TEXT[currentLang];
  const motionState = currentMotion();
  const commandSeq = ++motionState.commandSeq;
  const schedule = antiSwaySegmentSchedule(runRequest, snapshot);
  const targets = schedule.map(segment => segment.targetCounts);
  const command = (runRequest && runRequest.command) || {};
  let segmentStartCounts = Number.isFinite(Number(command.current_counts))
    ? Number(command.current_counts)
    : antiSwaySnapshotCurrentCounts(snapshot);
  motionState.stopRequested = false;
  motionState.latch = true;
  motionState.seenMoving = false;
  motionState.gearStoppedLatched = false;
  motionState.motionCancelNoticeKey = '';
  motionState.commandAt = Date.now();
  resetAntiSwayRunMetrics(activeDevice, snapshot);
  renderMotionToggle(true, text.antiSwayExecutionRunningTitle);
  openDiagModal(text.antiSwayExecutionRunningTitle, text.antiSwayExecutionRunningBody);
  try {
    const startedAt = Date.now();
    window.mctivityLastAntiSwaySchedule = schedule;
    window.mctivityLastAntiSwayMovePayloads = [];
    for (const segment of schedule) {
      if (commandSeq !== motionState.commandSeq || motionState.stopRequested) {
        openDiagModal(text.antiSwayExecutionStoppedTitle, text.antiSwayExecutionStoppedBody);
        return {ok:false, stopped:true};
      }
      const readyToSend = await antiSwayWaitUntil(commandSeq, startedAt, Number(segment.impulseTimeS || 0) * 1000);
      if (!readyToSend) {
        openDiagModal(text.antiSwayExecutionStoppedTitle, text.antiSwayExecutionStoppedBody);
        return {ok:false, stopped:true};
      }
      const roundedTargetCounts = Math.round(segment.targetCounts);
      const startForTiming = Number.isFinite(Number(segment.startCounts)) ? Number(segment.startCounts) : segmentStartCounts;
      const movePayload = antiSwayMotionPayload(roundedTargetCounts, snapshot, startForTiming);
      window.mctivityLastAntiSwayMovePayload = movePayload;
      window.mctivityLastAntiSwayMovePayloads.push({segment, payload:movePayload, sentAt:Date.now() - startedAt});
      const moveResult = await api(movePayload);
      window.mctivityLastAntiSwayMoveResult = moveResult;
      if (commandSeq !== motionState.commandSeq || motionState.stopRequested) {
        openDiagModal(text.antiSwayExecutionStoppedTitle, text.antiSwayExecutionStoppedBody);
        return {ok:false, stopped:true};
      }
      if (!moveResult || !moveResult.ok) throw new Error((moveResult && (moveResult.message || moveResult.error)) || 'anti-sway move_abs failed');
      segmentStartCounts = roundedTargetCounts;
    }
    const finalTarget = targets.length ? targets[targets.length - 1] : Number(snapshot.command.targetCounts || 0);
    const settled = await waitAntiSwaySegmentSettled(commandSeq, finalTarget);
    if (!settled) {
      openDiagModal(text.antiSwayExecutionStoppedTitle, text.antiSwayExecutionStoppedBody);
      return {ok:false, stopped:true};
    }
    markAntiSwayMotionDone(activeDevice);
    const observed = await observeAntiSwayAfterMotion(commandSeq, 2500);
    if (!observed) {
      finishAntiSwayRunMetrics(activeDevice);
      openDiagModal(text.antiSwayExecutionStoppedTitle, text.antiSwayExecutionStoppedBody);
      return {ok:false, stopped:true};
    }
    const runMetrics = finishAntiSwayRunMetrics(activeDevice);
    if (commandSeq === motionState.commandSeq) {
      motionState.latch = false;
      motionState.seenMoving = false;
      renderMotionToggle(false);
      openDiagModal(
        text.antiSwayExecutionCompleteTitle,
        antiSwayExecutionMessage(text.antiSwayExecutionCompleteBody, snapshot, currentProfile()) +
          (runMetrics ? '\n' + antiSwayRunMetricsSummary(runMetrics) : '')
      );
    }
    return {ok:true, anti_sway_execution:{strategy:antiSwayExecutionConfig().strategy, schedule, targets, metrics:runMetrics}};
  } catch (err) {
    if (commandSeq === motionState.commandSeq) {
      finishAntiSwayRunMetrics(activeDevice);
      motionState.latch = false;
      motionState.seenMoving = false;
      renderMotionToggle(false);
      openDiagModal(modeLabel('anti_sway_position'), err.message || String(err));
    }
    console.error(err);
    return {ok:false, error:String(err && err.message || err)};
  }
}
async function startAntiSwayRun() {
  const text = UI_TEXT[currentLang];
  await refreshAntiSwaySensorStatus().catch(err => console.error(err));
  const snapshot = buildAntiSwayInputSnapshot(activeDevice);
  if (snapshot && snapshot.command) snapshot.command.currentCounts = antiSwaySnapshotCurrentCounts(snapshot);
  renderAntiSwayPanel(true);
  const realExecution = Boolean(antiSwayExecutionConfig().enabled);
  const blockers = antiSwayRunBlockers(snapshot, realExecution);
  if (blockers.length) {
    openDiagModal(text.antiSwayDryRunBlockedTitle, blockers.map(item => '- ' + item).join('\n'));
    return {ok:false, blocked:true, blockers};
  }
  const modeResult = await api({cmd:'set_mode', mode:'anti_sway_position'});
  if (!modeResult || !modeResult.ok) return modeResult || {ok:false};
  const runPayload = antiSwayRunPayload(snapshot, !realExecution);
  window.mctivityLastAntiSwayRunPayload = runPayload;
  const result = await api(runPayload);
  window.mctivityLastAntiSwayRun = result;
  if (result && result.ok) {
    const plan = result.anti_sway_plan || (result.anti_sway_run_request && result.anti_sway_run_request.anti_sway_plan) || snapshot.antiSwayPlan || {};
    if (realExecution) {
      const message = antiSwayExecutionMessage(text.antiSwayExecutionConfirm, snapshot, currentProfile());
      const confirmed = await openMotionConfirm(text.antiSwayExecutionConfirmTitle || modeLabel('anti_sway_position'), message);
      if (!confirmed) return {ok:false, cancelled:true};
      const request = result.anti_sway_run_request || {};
      const strategy = String(request.execution_strategy || antiSwayExecutionConfig().strategy || '');
      if (strategy === 'continuous_zvd_curve' || strategy === 'terminal_endpoint_curve' || strategy === 'terminal_zvd_curve') {
        return executeAntiSwayContinuousCurve(snapshot, request);
      }
      return executeAntiSwaySegments(snapshot, request);
    }
    const body = text.antiSwayDryRunReadyBody
      .replace('{period}', formatMultiPointNumber(Number(plan.natural_period_s || 0), 3))
      .replace('{delay}', formatMultiPointNumber(Number(plan.shaper_delay_s || plan.addedTimeS || 0), 3));
    openDiagModal(text.antiSwayDryRunReadyTitle, body);
  }
  return result;
}
function antiSwayDryRunBlockers(snapshot) {
  return antiSwayRunBlockers(snapshot, false);
}
function startAntiSwayDryRun() {
  return startAntiSwayRun();
}
function antiSwayAxisInputSnapshot(device, status = currentStatus(device), profile = currentProfile(device)) {
  if (!device || !profile) return null;
  const tx = normalizedTransmission(profile);
  const bounds = transmissionBounds(profile);
  const rawCounts = status ? Number(status.pos || 0) : Number(profile.absPos || 0);
  const nativeCounts = isAuxEncoderDevice(device) ? rawCounts : axisCounts(rawCounts);
  const loadPosition = status
    ? (isAuxEncoderDevice(device) ? auxDisplayLoadPosition(status, profile) : homingCurrentLoadPosition(device, status, profile))
    : transmissionValueFromCounts(rawCounts, profile);
  const countsPerRev = isAuxEncoderDevice(device) ? auxEncoderCountsPerRev(status, profile) : REV;
  return {
    device,
    label: axisDisplayName(device),
    kind: isAuxEncoderDevice(device) ? 'encoder' : 'motor',
    available: supportsDevice(device),
    operational: Boolean(status && (status.operational || status.wc_complete || status.al_state === 8)),
    homed: isAuxEncoderDevice(device) ? Boolean(status && status.homed) : axisHomedForCurrentTransmission(device, status, profile),
    raw: {
      positionCounts: Number.isFinite(nativeCounts) ? nativeCounts : null,
      statusPosition: Number.isFinite(rawCounts) ? rawCounts : null,
      countsPerRev
    },
    load: {
      position: Number.isFinite(loadPosition) ? loadPosition : null,
      unit: tx.unit,
      bounds: {min: bounds.minLoad, max: bounds.maxLoad},
      direction: tx.direction,
      transmissionPerRev: transmissionPerRev(profile)
    }
  };
}
function buildAntiSwayInputSnapshot(device = activeDevice) {
  const profile = currentProfile(device);
  if (!profile) return null;
  profile.transmission = normalizedTransmission(profile);
  const anti = currentAntiSway(device);
  const sensorOptions = antiSwaySensorAxisOptions(device);
  if (!sensorOptions.includes(anti.sensorAxis)) {
    anti.sensorAxis = preferredAntiSwaySensorAxis(device);
  }
  const sensorAxis = anti.sensorAxis;
  const controlStatus = currentStatus(device);
  const sensorStatus = sensorAxis && supportsDevice(sensorAxis) ? currentStatus(sensorAxis) : null;
  const sensorProfile = sensorAxis && supportsDevice(sensorAxis) ? currentProfile(sensorAxis) : null;
  const targetCounts = Number(absPos && device === activeDevice ? absPos.value : profile.absPos || 0);
  const speedRpm = Number(anti.speedRpm || profile.absSpeedRpm || 0);
  const accelRpmS = Number(anti.accelRpmS || profile.absAccel || 0);
  const targetLoad = transmissionValueFromCounts(targetCounts, profile);
  const speedLoad = Math.max(0, speedRpm) * transmissionPerRev(profile) / 60;
  const accelLoad = Math.max(0, accelRpmS) * transmissionPerRev(profile) / 60;
  const naturalPeriodS = antiSwayNaturalPeriodS(anti.rodLength, anti.measuredPeriodS);
  const tx = normalizedTransmission(profile);
  const snapshot = {
    version: 'anti_sway_input.v1',
    mode: 'anti_sway_position',
    generatedAt: new Date().toISOString(),
    controlAxis: antiSwayAxisInputSnapshot(device, controlStatus, profile),
    sensorAxis: antiSwayAxisInputSnapshot(sensorAxis, sensorStatus, sensorProfile),
    command: {
      targetCounts,
      targetPosition: targetLoad,
      speedRpm,
      accelerationRpmS: accelRpmS,
      speed: speedLoad,
      acceleration: accelLoad,
      units: {
        position: tx.unit,
        speed: transmissionRateUnit(profile, 1),
        acceleration: transmissionRateUnit(profile, 2)
      }
    },
    antiSway: {
      algorithm: normalizeAntiSwayAlgorithm(anti.algorithm),
      rodLengthMm: anti.rodLength,
      measuredPeriodS: Number(anti.measuredPeriodS || 0),
      naturalPeriodS,
      periodSource: Number(anti.measuredPeriodS || 0) >= 0.05 ? 'measured' : 'calculated',
      allowedAngleDeg: anti.allowedAngle,
      sensorAxis
    },
    ready: {
      assembled: modeIsAssembled('anti_sway_position'),
      controlAxisReady: Boolean(controlStatus),
      sensorAxisReady: Boolean(sensorStatus && sensorProfile),
      sensorAxisValid: Boolean(sensorAxis && sensorAxis !== device && sensorOptions.includes(sensorAxis)),
      motionConnected: false
    }
  };
  snapshot.antiSwayPlan = buildAntiSwayPreviewPlan(snapshot);
  antiSwayInputByDevice[device] = snapshot;
  window.mctivityLastAntiSwayInput = snapshot;
  return snapshot;
}
function renderAntiSwayPanel(force=false) {
  const track = document.getElementById('antiSwayAxisTrack');
  if (!track) return;
  const axis = document.getElementById('antiSwayAxis');
  const text = UI_TEXT[currentLang];
  renderAntiSwayExecutionBadge();
  const profile = currentProfile();
  const anti = currentAntiSway();
  profile.transmission = normalizedTransmission(profile);
  const tx = profile.transmission;
  const bounds = transmissionBounds(profile);
  const status = currentStatus();
  const homed = axisHomedForCurrentTransmission(activeDevice, status, profile);
  const fallbackCounts = Number(absPos && absPos.value !== undefined ? absPos.value : profile.absPos || 0);
  const currentLoad = status
    ? homingCurrentLoadPosition(activeDevice, status, profile)
    : transmissionValueFromCounts(fallbackCounts, profile);
  const targetLoad = transmissionValueFromCounts(fallbackCounts, profile);
  const loadSpan = Math.max(0.001, bounds.maxLoad - bounds.minLoad);
  const boundedCurrent = clamp(currentLoad, bounds.minLoad, bounds.maxLoad);
  const boundedTarget = clamp(targetLoad, bounds.minLoad, bounds.maxLoad);
  const currentPct = clamp((boundedCurrent - bounds.minLoad) / loadSpan, 0, 1);
  const targetPct = clamp((boundedTarget - bounds.minLoad) / loadSpan, 0, 1);
  const zeroPct = clamp((0 - bounds.minLoad) / loadSpan, 0, 1);
  const speedLoad = Math.max(0, Number(anti.speedRpm || 0)) * transmissionPerRev(profile) / 60;
  const accelLoad = Math.max(0, Number(anti.accelRpmS || 0)) * transmissionPerRev(profile) / 60;
  const speedInput = document.getElementById('antiSwaySpeedRpm');
  const accelInput = document.getElementById('antiSwayAccel');
  const targetInput = document.getElementById('antiSwayTargetInput');
  if (targetInput && absPos) {
    targetInput.min = absPos.min;
    targetInput.max = absPos.max;
    targetInput.step = absPos.step;
    if (String(targetInput.value) !== String(absPos.value)) targetInput.value = absPos.value;
  }
  if (speedInput) {
    speedInput.min = '1';
    speedInput.max = String(MAX_SPEED_RPM);
    speedInput.step = '1';
    if (String(speedInput.value) !== String(anti.speedRpm)) speedInput.value = String(anti.speedRpm);
  }
  if (accelInput) {
    accelInput.min = '1';
    accelInput.max = String(MAX_ACCEL_RPM_S);
    accelInput.step = '1';
    if (String(accelInput.value) !== String(anti.accelRpmS)) accelInput.value = String(anti.accelRpmS);
  }
  const limitInput = document.getElementById('antiSwayLimitInput');
  const allowedAngleText = formatMultiPointNumber(anti.allowedAngle, 1);
  if (limitInput && document.activeElement !== limitInput && String(limitInput.value) !== allowedAngleText) {
    limitInput.value = allowedAngleText;
  }
  const sensorAxis = syncAntiSwaySensorAxisOptions(anti);
  syncAntiSwayAlgorithmOptions(anti);
  const sensorStatus = sensorAxis && supportsDevice(sensorAxis) ? currentStatus(sensorAxis) : null;
  const sensorProfile = sensorAxis && supportsDevice(sensorAxis) ? currentProfile(sensorAxis) : null;
  const inputSnapshot = buildAntiSwayInputSnapshot(activeDevice);
  if (force) {
    syncAntiSwayInputWithBackend(inputSnapshot).catch(err => console.error(err));
  }
  const swayAngle = antiSwaySensorDisplayValue(sensorAxis, sensorStatus, sensorProfile);
  const swayAngleDeg = antiSwaySensorAngleDeg(sensorAxis, sensorStatus, sensorProfile);
  const stability = updateAntiSwayStability(inputSnapshot, swayAngleDeg);

  if (axis) axis.classList.toggle('unhomed', !homed);
  track.style.setProperty('--marker-pct', String(currentPct));
  track.style.setProperty('--target-frac', String(targetPct));
  track.style.setProperty('--zero-pct', (zeroPct * 100).toFixed(2) + '%');
  setText('antiSwayAxisMin', formatTransmissionScalar(bounds.minLoad, tx.unit, 1));
  setText('antiSwayAxisMax', formatTransmissionScalar(bounds.maxLoad, tx.unit, 1));
  setText('antiSwayTargetLabel', formatTransmissionScalar(targetLoad, tx.unit, 1));
  setText('antiSwayCurrentLabel', formatTransmissionScalar(currentLoad, tx.unit, 1));
  setText('antiSwayUnhomedLabel', text.homingUnhomed);
  setText('antiSwaySpeedValue', formatMultiPointNumber(speedLoad, 3) + ' ' + transmissionRateUnit(profile, 1));
  setText('antiSwayAccelValue', formatMultiPointNumber(accelLoad, 3) + ' ' + transmissionRateUnit(profile, 2));
  setText('antiSwayRodValue', formatMultiPointNumber(anti.rodLength, 1) + ' mm');
  setText('antiSwayCalibratePeriodTitle', text.antiSwayCalibratePeriod);
  const measuredPeriod = Number(anti.measuredPeriodS || 0);
  const periodSource = measuredPeriod >= 0.05 ? text.antiSwayPeriodMeasured : text.antiSwayPeriodCalculated;
  const periodValue = antiSwayNaturalPeriodS(anti.rodLength, measuredPeriod);
  setText('antiSwayMeasuredPeriodValue', periodSource + ' ' + formatMultiPointNumber(periodValue, 3) + ' s');
  const rodInput = document.getElementById('antiSwayRodInput');
  if (rodInput && document.activeElement !== rodInput) rodInput.value = formatMultiPointNumber(anti.rodLength, 1);
  setText('antiSwayChartCurrent', text.antiSwayCurrentAngle + ' ' + swayAngle);
  const runMetrics = currentAntiSwayRunMetrics(activeDevice);
  const runMetricsLive = runMetrics && (runMetrics.active || runMetrics.observing);
  const runMetricsPeak = runMetrics && Number.isFinite(Number(runMetrics.peakAngleDeg)) ? Number(runMetrics.peakAngleDeg) : null;
  const runMetricsResidual = runMetrics && Number.isFinite(Number(runMetrics.residualAngleDeg)) ? Number(runMetrics.residualAngleDeg) : null;
  const runMetricsSettleMs = runMetrics && Number.isFinite(Number(runMetrics.settleMs)) ? Number(runMetrics.settleMs) : null;
  const phase = stability.phase || antiSwayPhaseInfo(inputSnapshot, swayAngleDeg, stability.angularVelocityDegS);
  const phaseVelocityText = phase && Number.isFinite(Number(phase.angularVelocityDegS))
    ? formatMultiPointNumber(Number(phase.angularVelocityDegS), 1) + ' deg/s'
    : '--';
  setText('antiSwayPeakValue', runMetricsPeak !== null ? formatMultiPointNumber(runMetricsPeak, 2) + ' deg' : (Number.isFinite(stability.peakAngleDeg) ? formatMultiPointNumber(stability.peakAngleDeg, 2) + ' deg' : '--'));
  setText('antiSwayResidualValue', runMetricsResidual !== null ? formatMultiPointNumber(runMetricsResidual, 2) + ' deg' : (Number.isFinite(stability.residualAngleDeg) ? formatMultiPointNumber(stability.residualAngleDeg, 2) + ' deg' : '--'));
  setText('antiSwayPhaseValue', phase && phase.label ? phase.label : '--');
  const phaseValue = document.getElementById('antiSwayPhaseValue');
  if (phaseValue) phaseValue.title = (text.antiSwayAngularVelocity || 'Angular Speed') + ' ' + phaseVelocityText;
  setText('antiSwaySettleValue', runMetricsSettleMs !== null ? formatMultiPointNumber(runMetricsSettleMs / 1000, 1) + ' s' : (stability.state === 'stable' ? text.antiSwayStable : (stability.inBandSince ? Math.ceil((900 - stability.settleProgressMs) / 100) / 10 + ' s' : '--')));
  setText('antiSwayStateValue', antiSwayStateLabel(stability.state));
  const runNote = runMetrics ? antiSwayRunMetricsSummary(runMetrics) : '';
  const phaseNote = phase && phase.label && phase.label !== '--'
    ? ' · ' + (text.antiSwayPhase || 'Phase') + ' ' + phase.label + ' · ' + phaseVelocityText
    : '';
  const gateNote = stability.phaseGate && stability.phaseGate.label
    ? ' · ' + (text.antiSwayPhaseWaitingTitle || 'Phase Gate') + ' ' + stability.phaseGate.label
    : '';
  setText('antiSwayPreviewNote', runMetricsLive ? runNote : (runNote || (antiSwayStateLabel(stability.state) + (Number.isFinite(stability.tipOffsetMm) ? ' · ' + formatMultiPointNumber(stability.tipOffsetMm, 2) + ' mm' : '') + phaseNote + gateNote)));
  renderAntiSwayPlan(inputSnapshot && inputSnapshot.antiSwayPlan);
  renderAntiSwayWave(stability, anti.allowedAngle);
  setText('antiSwayChartTop', '+' + allowedAngleText + ' deg');
  setText('antiSwayChartZero', '0');
  setText('antiSwayChartBottom', '-' + allowedAngleText + ' deg');
}
function homingCurrentLoadPosition(device = activeDevice, status = currentStatus(device), profile = currentProfile(device)) {
  if (!status) return null;
  if (isAuxEncoderDevice(device)) {
    return auxLoadValueFromCounts(Number(status.pos || 0), profile);
  }
  return transmissionValueFromCounts(axisCounts(Number(status.pos || 0)), profile);
}
function updateHomingAxis() {
  const axis = document.getElementById('homingAxis');
  const track = document.getElementById('homingAxisTrack');
  if (!axis || !track) return;
  const text = UI_TEXT[currentLang];
  const profile = currentProfile();
  profile.transmission = normalizedTransmission(profile);
  const tx = profile.transmission;
  const bounds = transmissionBounds(profile);
  const minLoad = Math.min(bounds.minLoad, bounds.maxLoad);
  const maxLoad = Math.max(bounds.minLoad, bounds.maxLoad);
  const loadSpan = Math.max(0.001, maxLoad - minLoad);
  const status = currentStatus();
  const actualLoadPos = homingCurrentLoadPosition(activeDevice, status, profile);
  const tolerance = Math.max(0.000001, loadSpan * 0.000001);
  const inRange = Number.isFinite(actualLoadPos) && actualLoadPos >= minLoad - tolerance && actualLoadPos <= maxLoad + tolerance;
  const isHomed = axisHomedForCurrentTransmission(activeDevice, status, profile) && inRange;
  const displayLoadPos = isHomed ? clamp(actualLoadPos, minLoad, maxLoad) : minLoad;
  const markerPct = clamp((displayLoadPos - minLoad) / loadSpan, 0, 1);
  axis.classList.toggle('unhomed', !isHomed);
  setText('homingAxisMin', formatTransmissionScalar(minLoad, tx.unit, 1));
  setText('homingAxisMax', formatTransmissionScalar(maxLoad, tx.unit, 1));
  setText('homingCurrentValue', formatTransmissionScalar(displayLoadPos, tx.unit, 1));
  setText('homingUnhomedLabel', text.homingUnhomed);
  track.style.setProperty('--marker-pct', String(markerPct));
}
function multiPointPayloadRows() {
  const mp = syncMultiPointRowsFromInputs();
  const bounds = currentMotionBoundsPayload();
  const profile = currentProfile();
  return mp.rows.filter(row => row.enabled).map(row => {
    const nativeCounts = countsFromTransmissionValue(row.position, profile);
    return Object.assign({
      row: row.row,
      pos: Math.round(nativeCounts),
      speed_rpm: motorRpmFromTransmissionSpeed(row.speed, profile),
      acceleration_rpm_s: motorRpmSFromTransmissionAcceleration(row.acceleration, profile),
      dwell_ms: row.dwell,
      enabled: row.enabled
    }, bounds);
  });
}
async function writeMultiPointTable(showAlert=false) {
  const mp = syncMultiPointRowsFromInputs();
  const rows = multiPointPayloadRows();
  if (!rows.length) {
    throw new Error(UI_TEXT[currentLang].multiPointIdle);
  }
  const result = await api({
    cmd:'point_table_write',
    start:mp.start,
    step:mp.step,
    cycle_count:mp.cycleCount,
    rows
  });
  if (!result.ok) throw new Error(result.error || 'point_table_write failed');
  multiPointStatusByDevice[activeDevice] = result.point_table_runner || null;
  renderMultiPointRunner(multiPointStatusByDevice[activeDevice]);
  if (showAlert) openDiagModal(modeLabel('multi_point'), result.message || UI_TEXT[currentLang].multiPointComplete);
  return result;
}
async function toggleMultiPointEdit() {
  const mp = currentMultiPoint();
  if (!mp.editing) {
    mp.editing = true;
    renderMultiPointPanel(true);
    saveUiState();
    return false;
  }
  try {
    await writeMultiPointTable(false);
    currentMultiPoint().editing = false;
    renderMultiPointPanel(true);
    saveUiState();
  } catch (err) {
    openDiagModal(modeLabel('multi_point'), err.message || String(err));
  }
  return false;
}
function renderMultiPointRunner(runner) {
  const text = UI_TEXT[currentLang];
  let statusText = text.multiPointIdle;
  let activeRow = null;
  const cycleInfo = multiPointRunnerCycleInfo(runner);
  if (runner && runner.state === 'stopping') {
    activeRow = runner.current_row;
    statusText = text.multiPointStopping + (activeRow ? ' P' + activeRow : '') + (cycleInfo ? ' ' + cycleInfo.statusLabel : '');
  } else if (runner && runner.running) {
    activeRow = runner.current_row;
    statusText = text.multiPointRunning + (activeRow ? ' P' + activeRow : '') + (cycleInfo ? ' ' + cycleInfo.statusLabel : '');
  } else if (runner && runner.state === 'complete') {
    statusText = text.multiPointComplete;
  } else if (runner && runner.state === 'stopped') {
    statusText = text.multiPointStopped;
  } else if (runner && runner.state === 'error') {
    statusText = runner.error || runner.message || 'point table error';
  }
  setText('multiPointStatus', statusText);
  const statusButton = document.getElementById('multiPointStatus');
  if (statusButton) {
    statusButton.disabled = !cycleInfo;
    statusButton.classList.toggle('clickable', Boolean(cycleInfo));
    statusButton.title = cycleInfo ? text.multiPointCyclePopupHint : '';
  }
  document.querySelectorAll('#multiPointTableBody tr').forEach(tr => {
    const isActive = activeRow !== null && Number(tr.dataset.row) === Number(activeRow);
    tr.classList.toggle('running-row', isActive);
    const rowCell = tr.querySelector('.multi-point-row-cell');
    if (rowCell) rowCell.classList.toggle('clickable', isActive && Boolean(cycleInfo));
    const cycleEl = tr.querySelector('.multi-point-cycle-indicator');
    if (cycleEl) {
      cycleEl.textContent = isActive && cycleInfo ? cycleInfo.rowBadge : '';
    }
  });
  renderMultiPointCyclePopup(cycleInfo);
  if (isMultiPointModeSelected()) {
    const motionState = currentMotion();
    if (runner && runner.running) {
      renderMotionToggle(true, runner.state === 'stopping' ? text.multiPointStopping : text.multiPointRunning);
    } else if (runner && ['complete', 'stopped', 'error'].includes(runner.state)) {
      motionState.latch = false;
      motionState.seenMoving = false;
      renderMotionToggle(false);
    }
  }
}
async function refreshMultiPointStatus() {
  if (!modeIsAssembled('multi_point')) return null;
  const result = await api({cmd:'point_table_status'});
  if (result && result.ok) {
    multiPointStatusByDevice[activeDevice] = result.point_table_runner || null;
    renderMultiPointRunner(multiPointStatusByDevice[activeDevice]);
  }
  return result;
}
async function refreshAntiSwaySensorStatus() {
  if (!modeSelect || modeSelect.value !== 'anti_sway_position') return null;
  const sensorAxis = currentAntiSway().sensorAxis;
  if (!sensorAxis || sensorAxis === activeDevice || !supportsDevice(sensorAxis)) return null;
  try {
    const result = await apiForDevice(sensorAxis, {cmd:'status'});
    if (result && result.ok && result.status) render(result.status);
    return result;
  } catch (err) {
    console.error(err);
  }
  return null;
}
let incrementalEditor = null;
let incrementalEditorDevice = '';
let incrementalEditorLanguage = '';
function normalizedTransmission(profile = currentProfile()) {
  const source = profile && profile.transmission ? profile.transmission : profile;
  const tx = Object.assign({type:'rotary', revs:1, amount:360, unit:'deg', direction:'forward', travelMode:'periodic', period:null, forwardLimit:null, reverseLimit:null}, source || {});
  tx.type = tx.type === 'linear' ? 'linear' : 'rotary';
  tx.revs = Math.max(0.001, Number(tx.revs) || 1);
  tx.amount = Math.max(0.001, Number(tx.amount) || (tx.type === 'linear' ? 1 : 360));
  tx.direction = tx.direction === 'reverse' ? 'reverse' : 'forward';
  tx.travelMode = tx.type === 'linear' ? 'reciprocating' : (tx.travelMode === 'reciprocating' ? 'reciprocating' : 'periodic');
  tx.period = Math.max(0.001, Number(tx.period) || tx.amount);
  tx.forwardLimit = Number.isFinite(Number(tx.forwardLimit)) ? Number(tx.forwardLimit) : tx.amount;
  tx.reverseLimit = Number.isFinite(Number(tx.reverseLimit)) ? Number(tx.reverseLimit) : -tx.amount;
  const options = transmissionUnitSets[tx.type] || transmissionUnitSets.rotary;
  if (!options.some(opt => opt.value === tx.unit)) {
    tx.unit = options[0].value;
  }
  return tx;
}
function transmissionSignatureNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) ? Number(number.toFixed(6)) : 0;
}
function transmissionSignature(profile = currentProfile()) {
  const tx = normalizedTransmission(profile);
  return JSON.stringify({
    type:tx.type,
    revs:transmissionSignatureNumber(tx.revs),
    amount:transmissionSignatureNumber(tx.amount),
    unit:tx.unit,
    direction:tx.direction,
    travelMode:tx.travelMode,
    period:transmissionSignatureNumber(tx.period),
    forwardLimit:transmissionSignatureNumber(tx.forwardLimit),
    reverseLimit:transmissionSignatureNumber(tx.reverseLimit)
  });
}
function transmissionPerRev(profile = currentProfile()) {
  const tx = normalizedTransmission(profile);
  return tx.amount / tx.revs;
}
function transmissionDirectionSign(profile = currentProfile()) {
  const tx = normalizedTransmission(profile);
  return tx.direction === 'reverse' ? -1 : 1;
}
function transmissionValueFromCounts(counts, profile = currentProfile()) {
  return rev(axisCounts(counts)) * transmissionPerRev(profile) * transmissionDirectionSign(profile);
}
function countsFromTransmissionValue(value, profile = currentProfile()) {
  const tx = normalizedTransmission(profile);
  const direction = tx.direction === 'reverse' ? -1 : 1;
  const motorRev = Number(value) / (transmissionPerRev(tx) * direction);
  return Math.round((motorRev * REV) / AXIS_DIR);
}
function nativeCountsFromTransmissionValue(value, profile = currentProfile()) {
  return axisCounts(countsFromTransmissionValue(value, profile));
}
function transmissionRateUnit(profile = currentProfile(), order = 1) {
  const tx = normalizedTransmission(profile);
  return tx.unit + '/s' + (order === 2 ? '²' : '');
}
function maxTransmissionSpeed(profile = currentProfile()) {
  return Math.max(0.001, MAX_SPEED_RPM * transmissionPerRev(profile) / 60);
}
function maxTransmissionAcceleration(profile = currentProfile()) {
  return Math.max(0.001, MAX_ACCEL_RPM_S * transmissionPerRev(profile) / 60);
}
function motorRpmFromTransmissionSpeed(value, profile = currentProfile()) {
  const perRev = Math.max(0.001, transmissionPerRev(profile));
  return Math.max(1, Math.min(MAX_SPEED_RPM, Math.round(Math.abs(Number(value) || 0) * 60 / perRev)));
}
function motorRpmSFromTransmissionAcceleration(value, profile = currentProfile()) {
  const perRev = Math.max(0.001, transmissionPerRev(profile));
  return Math.max(0, Math.min(MAX_ACCEL_RPM_S, Math.round(Math.abs(Number(value) || 0) * 60 / perRev)));
}
function formatMultiPointNumber(value, digits = 3) {
  const number = Number(value) || 0;
  if (Math.abs(number) >= 100) return number.toFixed(1).replace(/\.0$/, '');
  if (Math.abs(number) >= 10) return number.toFixed(2).replace(/0+$/, '').replace(/\.$/, '');
  return number.toFixed(digits).replace(/0+$/, '').replace(/\.$/, '');
}
function multiPointInputStep(maxValue) {
  if (maxValue <= 1) return 0.001;
  if (maxValue <= 10) return 0.01;
  if (maxValue <= 100) return 0.1;
  return 1;
}
function transmissionBounds(profile = currentProfile()) {
  const tx = normalizedTransmission(profile);
  let minLoad = 0;
  let maxLoad = tx.period;
  if (tx.travelMode === 'reciprocating') {
    minLoad = Math.min(tx.reverseLimit, tx.forwardLimit);
    maxLoad = Math.max(tx.reverseLimit, tx.forwardLimit);
  }
  return {
    minLoad,
    maxLoad,
    minCounts: countsFromTransmissionValue(minLoad, tx),
    maxCounts: countsFromTransmissionValue(maxLoad, tx)
  };
}
function transmissionHasMotionBounds(profile = currentProfile()) {
  const tx = normalizedTransmission(profile);
  return tx.type === 'linear' || tx.travelMode === 'reciprocating';
}
function transmissionMotionBounds(profile = currentProfile()) {
  if (!transmissionHasMotionBounds(profile)) return null;
  return transmissionBounds(profile);
}
function formatTransmissionValue(value, digits = 1) {
  const n = Math.abs(Number(value)) < 0.0005 ? 0 : Number(value);
  return (n > 0 ? '+' : '') + n.toFixed(digits);
}
function formatTransmissionScalar(value, unit, digits = 1) {
  return formatTransmissionValue(value, digits) + ' ' + unit;
}
function formatMotorRevScalar(counts, digits = 3) {
  const motorRev = rev(axisCounts(counts));
  return (motorRev > 0 ? '+' : '') + motorRev.toFixed(digits) + ' rev';
}
function syncTransmissionSummary(device = activeDevice) {
  const tx = normalizedTransmission(currentProfile(device));
  const text = UI_TEXT[currentLang];
  setText('transmissionLabel', text.transmissionLabel);
  setText('transmissionTypeLabel', tx.type === 'linear' ? text.linearType : text.rotaryType);
  setText('transmissionLoadSummary', text.loadPrefix + transmissionPerRev(tx).toFixed(1) + ' ' + tx.unit);
  setText('transmissionMotorSummary', axisRotationPrefix(device) + '1 rev');
  setText('transmissionRevsLabel', transmissionRotationLabel(device));
}
function refillTransmissionUnitOptions(type, preferred) {
  const options = transmissionUnitSets[type] || transmissionUnitSets.rotary;
  transmissionUnit.innerHTML = options.map(opt => '<option value="' + opt.value + '">' + opt.label + '</option>').join('');
  transmissionUnit.value = options.some(opt => opt.value === preferred) ? preferred : options[0].value;
}
function syncTransmissionTravelFields() {
  const isLinear = transmissionType.value === 'linear';
  if (isLinear) transmissionTravelMode.value = 'reciprocating';
  transmissionTravelMode.disabled = isLinear;
  const isPeriodic = !isLinear && transmissionTravelMode.value !== 'reciprocating';
  transmissionPeriodRow.classList.toggle('hidden', !isPeriodic);
  transmissionForwardRow.classList.toggle('hidden', isPeriodic);
  transmissionReverseRow.classList.toggle('hidden', isPeriodic);
  const unit = transmissionUnit.value || 'deg';
  setText('transmissionPeriodUnit', unit);
  setText('transmissionForwardUnit', unit);
  setText('transmissionReverseUnit', unit);
}
function openTransmissionDialog() {
  const tx = normalizedTransmission(currentProfile());
  transmissionDraft = Object.assign({}, tx);
  transmissionType.value = transmissionDraft.type;
  refillTransmissionUnitOptions(transmissionDraft.type, transmissionDraft.unit);
  transmissionTravelMode.value = transmissionDraft.travelMode;
  setTransmissionDirectionValue(transmissionDraft.direction);
  transmissionRevs.value = transmissionDraft.revs;
  transmissionAmount.value = transmissionDraft.amount;
  transmissionPeriod.value = transmissionDraft.period;
  transmissionForwardLimit.value = transmissionDraft.forwardLimit;
  transmissionReverseLimit.value = transmissionDraft.reverseLimit;
  updateTransmissionDraft();
  transmissionModal.classList.add('open');
}
function maybeCloseTransmissionDialog(event) {
  if (event.target === transmissionModal) closeTransmissionDialog();
}
function closeTransmissionDialog() {
  transmissionModal.classList.remove('open');
}
function onTransmissionTypeChange() {
  const nextType = transmissionType.value === 'linear' ? 'linear' : 'rotary';
  refillTransmissionUnitOptions(nextType, null);
  if (nextType === 'linear') {
    transmissionTravelMode.value = 'reciprocating';
  }
  if (nextType === 'rotary' && Number(transmissionAmount.value || 0) === 1) {
    transmissionAmount.value = 360;
  }
  if (Number(transmissionPeriod.value || 0) === 1 && nextType === 'rotary') {
    transmissionPeriod.value = 360;
  }
  updateTransmissionDraft();
}
function onTransmissionTravelModeChange() {
  syncTransmissionTravelFields();
  updateTransmissionDraft();
}
function transmissionDirectionValue() {
  const btn = document.getElementById('transmissionDirectionToggle');
  if (!btn) return 'forward';
  return btn.classList.contains('reverse') ? 'reverse' : 'forward';
}
function setTransmissionDirectionValue(direction) {
  const btn = document.getElementById('transmissionDirectionToggle');
  if (!btn) return;
  const reverse = direction === 'reverse';
  btn.classList.toggle('reverse', reverse);
}
function toggleTransmissionDirection() {
  setTransmissionDirectionValue(transmissionDirectionValue() === 'reverse' ? 'forward' : 'reverse');
  updateTransmissionDraft();
  return false;
}
function parseTransmissionNumber(raw, fallback) {
  const text = String(raw == null ? '' : raw).trim();
  if (!text || text === '-' || text === '+') return Number(fallback);
  const n = Number(text);
  return Number.isFinite(n) ? n : Number(fallback);
}
function updateTransmissionDraft() {
  const type = transmissionType.value === 'linear' ? 'linear' : 'rotary';
  const prev = normalizedTransmission(transmissionDraft || currentProfile());
  transmissionDraft = normalizedTransmission({
    type,
    revs: Math.max(0.001, parseTransmissionNumber(transmissionRevs.value, prev.revs || 1)),
    amount: Math.max(0.001, parseTransmissionNumber(transmissionAmount.value, prev.amount || (type === 'linear' ? 1 : 360))),
    unit: transmissionUnit.value,
    direction: transmissionDirectionValue(),
    travelMode: type === 'linear' ? 'reciprocating' : (transmissionTravelMode.value === 'reciprocating' ? 'reciprocating' : 'periodic'),
    period: Math.max(0.001, parseTransmissionNumber(transmissionPeriod.value, prev.period || (type === 'linear' ? 1 : 360))),
    forwardLimit: parseTransmissionNumber(transmissionForwardLimit.value, prev.forwardLimit),
    reverseLimit: parseTransmissionNumber(transmissionReverseLimit.value, prev.reverseLimit)
  });
  if (document.activeElement !== transmissionRevs) transmissionRevs.value = transmissionDraft.revs;
  if (document.activeElement !== transmissionAmount) transmissionAmount.value = transmissionDraft.amount;
  if (document.activeElement !== transmissionPeriod) transmissionPeriod.value = transmissionDraft.period;
  if (document.activeElement !== transmissionForwardLimit) transmissionForwardLimit.value = transmissionDraft.forwardLimit;
  if (document.activeElement !== transmissionReverseLimit) transmissionReverseLimit.value = transmissionDraft.reverseLimit;
  transmissionTravelMode.value = transmissionDraft.travelMode;
  setTransmissionDirectionValue(transmissionDraft.direction);
  syncTransmissionTravelFields();
}
function saveTransmissionDialog() {
  const profile = currentProfile();
  const beforeSignature = transmissionSignature(profile);
  updateTransmissionDraft();
  profile.transmission = Object.assign({}, transmissionDraft);
  if (beforeSignature !== transmissionSignature(profile)) {
    invalidateHomingReference(activeDevice);
  }
  closeTransmissionDialog();
  updateSliders();
  if (modeSelect && modeSelect.value === 'multi_point') renderMultiPointPanel(true);
  if (modeSelect && modeSelect.value === 'homing') renderHomingPanel(true);
}
async function persistUiState(device = activeDevice, options = {}) {
  if (device === activeDevice) saveUiState(device, false);
  try {
    const res = await fetch('/api/ui_state', {
      method:'POST',
      headers:apiHeaders({'Content-Type':'application/json'}),
      body: JSON.stringify({
        device,
        state: currentProfile(device),
        update_anti_sway_period:Boolean(options && options.updateAntiSwayPeriod),
        update_anti_sway_settings:Boolean(options && options.updateAntiSwaySettings)
      })
    });
    const data = await res.json();
    showApiError(data);
  } catch (err) {
    console.error(err);
  }
}
function scheduleUiStateSave(device = activeDevice) {
  if (uiStateSaveTimer) clearTimeout(uiStateSaveTimer);
  uiStateSaveTimer = setTimeout(() => {
    uiStateSaveTimer = 0;
    persistUiState(device);
  }, 180);
}
function saveUiState(device = activeDevice) {
  const profile = currentProfile(device);
  profile.transmission = normalizedTransmission(profile);
  profile.antiSway = normalizeAntiSwayState(profile.antiSway, device);
  profile.incrementalCurve = storeIncrementalCurveState(device, profile.incrementalCurve, false);
  profile.multiPoint = device === activeDevice && !isAuxEncoderDevice(device) ? syncMultiPointRowsFromInputs() : normalizeMultiPointState(profile.multiPoint);
  const shouldReadHomingControls = device === activeDevice && modeSelect && modeSelect.value === 'homing';
  profile.homing = shouldReadHomingControls ? syncHomingControls(true) : normalizeHomingState(profile.homing);
  profile.absPos = Number(absPos.value);
  profile.absSpeedRpm = Number(absSpeedRpm.value);
  profile.absAccel = Number(absAccel.value);
  profile.relDelta = Number(relDelta.value);
  profile.moveMs = Number(moveMs.value);
  profile.velCps = Number(velCps.value);
  profile.torqueCmd = Number(torqueCmd.value);
  profile.gearMaster = gearMasterSelect.value;
  profile.gearMasterRatio = Number(gearMasterRatio.value);
  profile.gearSlaveRatio = Number(gearSlaveRatio.value);
  profile.mode = modeSelect.value || 'position';
  if (isAuxEncoderDevice(device)) {
    profile.mode = profile.mode === 'homing' && modeIsAssembled('homing') ? 'homing' : 'position';
    profile.gearMaster = 'virtual';
    profile.softZeroRaw = Number(profile.softZeroRaw || 0) || 0;
  }
  const snapshot = JSON.stringify(profile);
  const changed = lastUiStateSnapshot[device] !== snapshot;
  lastUiStateSnapshot[device] = snapshot;
  if (changed && (arguments.length < 2 || arguments[1] !== false)) {
    scheduleUiStateSave(device);
  }
}
function loadUiState(device = activeDevice) {
  const profile = currentProfile(device);
  profile.transmission = normalizedTransmission(profile);
  profile.antiSway = normalizeAntiSwayState(profile.antiSway, device);
  profile.incrementalCurve = storeIncrementalCurveState(device, profile.incrementalCurve, false);
  profile.multiPoint = normalizeMultiPointState(profile.multiPoint);
  profile.homing = normalizeHomingState(profile.homing);
  absPos.value = profile.absPos;
  absSpeedRpm.value = profile.absSpeedRpm;
  absAccel.value = profile.absAccel;
  relDelta.value = profile.relDelta;
  moveMs.value = profile.moveMs;
  velCps.value = profile.velCps;
  torqueCmd.value = profile.torqueCmd;
  gearMasterRatio.value = profile.gearMasterRatio || 1;
  gearSlaveRatio.value = profile.gearSlaveRatio || 1;
  if (profile.gearMaster && profile.gearMaster !== device && (profile.gearMaster === 'virtual' || supportsDevice(profile.gearMaster))) {
    gearMasterSelect.value = profile.gearMaster;
  } else {
    gearMasterSelect.value = preferredGearMaster(device);
    profile.gearMaster = gearMasterSelect.value;
  }
  if (isAuxEncoderDevice(device)) {
    profile.mode = profile.mode === 'homing' && modeIsAssembled('homing') ? 'homing' : 'position';
    profile.gearMaster = 'virtual';
    gearMasterSelect.value = 'virtual';
  }
  modeSelect.value = profile.mode || 'position';
  syncModeSelectDisabled(device);
  refreshGearPanel(device);
  if (device === activeDevice) renderMultiPointPanel(true);
}
async function hydrateUiStateFromServer() {
  try {
    const res = await fetch('/api/ui_state', {headers:apiHeaders()});
    const data = await res.json();
    showApiError(data);
    if (!data || !data.ok || !data.state || !data.state.devices) return;
    for (const device of axisDevices()) {
      if (!data.state.devices[device]) continue;
      Object.assign(deviceProfiles[device], data.state.devices[device]);
      deviceProfiles[device].transmission = normalizedTransmission(deviceProfiles[device]);
      deviceProfiles[device].antiSway = normalizeAntiSwayState(deviceProfiles[device].antiSway, device);
      deviceProfiles[device].incrementalCurve = normalizeIncrementalCurveState(deviceProfiles[device].incrementalCurve);
      deviceProfiles[device].multiPoint = normalizeMultiPointState(deviceProfiles[device].multiPoint);
      deviceProfiles[device].homing = normalizeHomingState(deviceProfiles[device].homing);
      if (isAuxEncoderDevice(device)) {
        deviceProfiles[device].mode = deviceProfiles[device].mode === 'homing' && modeIsAssembled('homing') ? 'homing' : 'position';
        deviceProfiles[device].gearMaster = 'virtual';
        deviceProfiles[device].softZeroRaw = Number(deviceProfiles[device].softZeroRaw || 0) || 0;
      }
      incrementalCurveSnapshots[device] = JSON.stringify(deviceProfiles[device].incrementalCurve);
    }
  } catch (err) {
    console.error(err);
  }
}
function fmt(n) { return Math.round(Number(n)).toLocaleString('en-US'); }
function rev(n) { return Number(n) / REV; }
function revText(n, digits=3) {
  const value = Number(n);
  const prefix = value > 0 ? '+' : '';
  return prefix + value.toFixed(digits) + ' rev';
}
function signedNumber(value, digits=1) {
  const n = Math.abs(Number(value)) < 0.05 ? 0 : Number(value);
  return (n > 0 ? '+' : '') + n.toFixed(digits);
}
function targetParts(counts) {
  const value = Number(counts) / REV;
  const turns = Math.trunc(value);
  const angle = (value - turns) * 360;
  return {turns, angle};
}
function axisCounts(n) { return Number(n) * AXIS_DIR; }
function phaseCounts(n) { return ((axisCounts(n) % REV) + REV) % REV; }
function deg(n) { return phaseCounts(n) / REV * 360; }
function continuousDeg(n) { return axisCounts(n) / REV * 360; }
function rpm(delta, ms) { return Math.abs(rev(delta)) / (Number(ms) / 1000) * 60; }
function absoluteMoveMs(target, currentOverride=null, speedOverride=null) {
  const status = currentStatus();
  const statusPos = status ? Number(status.pos) : NaN;
  const targetCounts = Number(target);
  let current = Number(currentOverride);
  if (!Number.isFinite(current)) {
    current = Number.isFinite(statusPos) ? axisCounts(statusPos) : 0;
  }
  const speed = Math.max(1, Number(speedOverride || (absSpeedRpm && absSpeedRpm.value) || 1));
  if (!Number.isFinite(targetCounts) || !Number.isFinite(current) || !Number.isFinite(speed)) return 500;
  const distance = Math.abs(targetCounts - current);
  const ms = distance === 0 ? 500 : Math.round(distance / (speed * REV / 60) * 1000);
  return Math.min(60000, Math.max(200, ms));
}
function motionPayload(target) {
  return Object.assign({
    cmd:'move_abs',
    pos:axisCounts(target),
    move_ms:absoluteMoveMs(target),
    speed_rpm:Number(absSpeedRpm.value || 0),
    acceleration_rpm_s:Number(absAccel.value || 0)
  }, currentMotionBoundsPayload());
}
function currentMotionBoundsPayload(profile = currentProfile()) {
  const bounds = transmissionMotionBounds(profile);
  if (!bounds) return {};
  const minNative = axisCounts(bounds.minCounts);
  const maxNative = axisCounts(bounds.maxCounts);
  return {
    min_pos: Math.min(minNative, maxNative),
    max_pos: Math.max(minNative, maxNative)
  };
}
function storeIncrementalCurveState(device, nextState, shouldSchedule = true) {
  const normalized = normalizeIncrementalCurveState(nextState);
  const snapshot = JSON.stringify(normalized);
  currentProfile(device).incrementalCurve = normalized;
  if (incrementalCurveSnapshots[device] !== snapshot) {
    incrementalCurveSnapshots[device] = snapshot;
    if (shouldSchedule) {
      scheduleUiStateSave(device);
    }
  }
  return normalized;
}
function buildIncrementalAxisContext(device = activeDevice) {
  const profile = currentProfile(device);
  const tx = normalizedTransmission(profile);
  const bounds = currentMotionBoundsPayload(profile);
  const status = currentStatus(device);
  const timeUnit = 's';
  return {
    countsPerRev: REV,
    userUnitsPerRev: transmissionPerRev(tx),
    positionUnit: tx.unit,
    speedUnit: `${tx.unit}/${timeUnit}`,
    accelUnit: `${tx.unit}/${timeUnit}²`,
    decelUnit: `${tx.unit}/${timeUnit}²`,
    jerkUnit: `${tx.unit}/${timeUnit}³`,
    dwellUnit: timeUnit,
    timeUnit,
    language: currentLang,
    direction: tx.direction,
    axisSign: 1,
    currentPositionCounts: status ? Number(status.pos || 0) : axisCounts(Number(profile.absPos || 0)),
    minPositionCounts: bounds.min_pos !== undefined ? Number(bounds.min_pos) : undefined,
    maxPositionCounts: bounds.max_pos !== undefined ? Number(bounds.max_pos) : undefined
  };
}
function destroyIncrementalEditor() {
  if (incrementalEditor && typeof incrementalEditor.destroy === 'function') {
    incrementalEditor.destroy();
  }
  incrementalEditor = null;
  incrementalEditorDevice = '';
  incrementalEditorLanguage = '';
}
function syncIncrementalEditor(forceRemount = false) {
  const host = document.getElementById('incrementalProfileHost');
  const selectedMode = (modeSelect && modeSelect.value) || currentProfile().mode || 'position';
  const editorApi = window.MctivityMotionCurveEditor && window.MctivityMotionCurveEditor.single;
  if (!host || selectedMode !== 'incremental' || !editorApi) {
    if (selectedMode !== 'incremental') {
      destroyIncrementalEditor();
    }
    return null;
  }
  const device = activeDevice;
  const initialParams = currentIncrementalCurve(device);
  const axisContext = buildIncrementalAxisContext(device);
  const needRemount = forceRemount || !incrementalEditor || incrementalEditorDevice !== device || incrementalEditorLanguage !== currentLang;
  if (needRemount) {
    destroyIncrementalEditor();
    incrementalEditor = editorApi.mount(host, {
      language: currentLang,
      hostMode: 'embedded',
      initialParams,
      axisContext,
      onChange(result) {
        storeIncrementalCurveState(device, result && result.params, true);
      }
    });
    incrementalEditorDevice = device;
    incrementalEditorLanguage = currentLang;
    return incrementalEditor;
  }
  incrementalEditor.setParams(initialParams);
  incrementalEditor.setAxisContext(axisContext);
  return incrementalEditor;
}
function currentIncrementalCommandProfile() {
  const editor = syncIncrementalEditor(false);
  if (!editor || typeof editor.getCommandProfile !== 'function') {
    return null;
  }
  return editor.getCommandProfile(buildIncrementalAxisContext(activeDevice));
}
function clamp(value, min, max) { return Math.max(min, Math.min(max, value)); }
function updateFeedbackDial(handId, valueId, value, maxAbs, digits, unit) {
  const hand = document.getElementById(handId);
  const valueEl = document.getElementById(valueId);
  if (!hand || !valueEl) return;
  const bounded = clamp(Math.abs(Number(value) || 0), 0, maxAbs);
  const degrees = 240 + (bounded / maxAbs) * 240;
  hand.style.transform = 'rotate(' + degrees.toFixed(1) + 'deg)';
  valueEl.textContent = signedNumber(value, digits);
  const unitEl = valueEl.nextElementSibling;
  if (unitEl && unit) unitEl.textContent = unit;
}
function torqueFeedbackPercent(status) {
  if (!status) return 0;
  if (Object.prototype.hasOwnProperty.call(status, 'torque_feedback_percent')) {
    return Number(status.torque_feedback_percent || 0);
  }
  const raw = Object.prototype.hasOwnProperty.call(status, 'torque_feedback_raw')
    ? Number(status.torque_feedback_raw || 0)
    : Number(status.torque_feedback ?? status.torque_actual ?? 0);
  return raw / 10;
}
function getApiToken() {
  return sessionStorage.getItem(API_TOKEN_KEY) || '';
}
function setApiToken(value) {
  const token = String(value || '').trim();
  if (token) sessionStorage.setItem(API_TOKEN_KEY, token);
  else sessionStorage.removeItem(API_TOKEN_KEY);
}
function apiHeaders(extra = {}) {
  const headers = Object.assign({}, extra);
  const token = getApiToken();
  if (token) headers['X-MCTIVITY-Token'] = token;
  return headers;
}
function initApiTokenInput() {
  const input = document.getElementById('apiTokenInput');
  if (!input) return;
  input.value = getApiToken();
  input.addEventListener('input', () => setApiToken(input.value));
  input.addEventListener('change', () => hydrateUiStateFromServer().catch(err => console.error(err)));
}
function showApiError(data) {
  const text = UI_TEXT[currentLang];
  if (!data || data.ok) return;
  if (data.error === 'unauthorized') {
    openDiagModal(text.unauthorizedTitle, text.unauthorizedBody);
    return;
  }
  if (data.error === 'unsupported_command') {
    if (data.required_capability) {
      openDiagModal(text.unsupportedCommand, text.requiredCapability + ': ' + data.required_capability);
    } else {
      openDiagModal(text.unsupportedCommand, data.message || text.commandParameterInvalid);
    }
    return;
  }
  if (data.error === 'motion_not_ready' || String(data.error || '').includes('servo is not ready')) {
    openDiagModal(text.motionNotReady, data.message || text.homingServoNotReady);
  }
}
function normalizeMockFaultRaw(value) {
  const raw = String(value ?? '').trim();
  if (!raw) return '';
  if (/^(ready|ok|none)$/i.test(raw)) return '0';
  const numeric = Number(raw);
  if (Number.isFinite(numeric)) return hex4(numeric);
  const hex = raw.match(/^0x([0-9a-f]+)$/i);
  if (hex) return hex4(parseInt(hex[1], 16));
  return raw.toUpperCase();
}
function mockFaultEnabled() {
  return mockFaultCode !== null;
}
function currentMockFaultOption() {
  if (!mockFaultEnabled()) return null;
  const normalized = normalizeMockFaultRaw(mockFaultCode);
  return MOCK_FAULT_OPTIONS.find(option => normalizeMockFaultRaw(option.raw) === normalized) || null;
}
function mockFaultLabel(option) {
  if (!option) {
    const raw = normalizeMockFaultRaw(mockFaultCode);
    return UI_TEXT[currentLang].mockFaultCustom + ' ' + (raw || '--');
  }
  return option[currentLang] || option.zh || option.en || option.raw;
}
function refreshMockFaultPanel() {
  const panel = document.getElementById('mockFaultPanel');
  const select = document.getElementById('mockFaultSelect');
  if (!panel || !select) return;
  const text = UI_TEXT[currentLang];
  panel.hidden = !mockFaultEnabled();
  setText('mockFaultLabel', text.mockFaultLabel);
  setText('mockFaultBadge', text.mockFaultBadge);
  setText('mockFaultHint', text.mockFaultHint);
  if (panel.hidden) return;
  const selectedRaw = normalizeMockFaultRaw(mockFaultCode);
  const optionValues = new Set();
  select.innerHTML = '';
  MOCK_FAULT_OPTIONS.forEach((option) => {
    const value = normalizeMockFaultRaw(option.raw);
    optionValues.add(value);
    const item = document.createElement('option');
    item.value = value;
    item.textContent = mockFaultLabel(option);
    item.selected = value === selectedRaw;
    select.appendChild(item);
  });
  const currentValue = selectedRaw;
  if (!optionValues.has(currentValue) && selectedRaw) {
    const custom = document.createElement('option');
    custom.value = currentValue;
    custom.textContent = mockFaultLabel(null);
    custom.selected = true;
    select.appendChild(custom);
  }
}
function setMockFaultUrl(raw) {
  mockFaultCode = raw;
  const params = new URLSearchParams(window.location.search);
  params.set('mock_fault', raw);
  const query = params.toString();
  window.history.replaceState(null, '', window.location.pathname + (query ? '?' + query : '') + window.location.hash);
}
function changeMockFaultCode() {
  const select = document.getElementById('mockFaultSelect');
  if (!select) return false;
  setMockFaultUrl(String(select.value || '0'));
  refreshMockFaultPanel();
  api({cmd:'status'}).catch(err => console.error(err));
  return false;
}
function syncMockFaultFromUrl() {
  const params = new URLSearchParams(window.location.search);
  mockFaultCode = params.get('mock_fault');
  refreshMockFaultPanel();
  api({cmd:'status'}).catch(err => console.error(err));
}
function mockFaultValue() {
  if (!mockFaultEnabled()) return null;
  const option = currentMockFaultOption();
  const raw = String((option && option.raw) || mockFaultCode || '').trim();
  if (!raw || /^(0|ready|ok|none)$/i.test(raw)) return 0;
  const numeric = Number(raw);
  if (Number.isFinite(numeric)) return numeric & 0xffff;
  const hex = raw.match(/^0x([0-9a-f]+)$/i);
  if (hex) return parseInt(hex[1], 16) & 0xffff;
  return 0xffff;
}
function mockStatus(device = activeDevice) {
  const err = mockFaultValue();
  if (err === null) return null;
  const fault = err !== 0;
  const option = currentMockFaultOption();
  return {
    ok:true,
    status:{
      device,
      enabled:false,
      servo_request:false,
      moving:false,
      gear_running:false,
      fault,
      settle_cycles:0,
      al_state:8,
      operational:1,
      wc:1,
      wc_complete:true,
      cw:0,
      sw:fault ? 0x0008 : 0x0040,
      err,
      mode:8,
      control_mode:'position',
      pos_raw:0,
      pos:0,
      target_raw:0,
      target:0,
      following_error:0,
      soft_zero_raw:0,
      jog_velocity_cps:0,
      torque_cmd:0,
      torque_feedback:0,
      torque_feedback_available:isMotorDevice(device),
      homed:false,
      cycles:0,
      last_command:'mock_status',
      mock_fault:true,
      mock_fault_raw:hex4(err),
      mock_fault_kind:(option && option.kind) || 'custom',
      mock_fault_label:(option && mockFaultLabel(option)) || '',
      message:fault ? 'mock fault status' : 'mock ready status'
    }
  };
}
async function api(payload) {
  if ((payload && payload.cmd) === 'status') {
    const mocked = mockStatus(activeDevice);
    if (mocked) {
      render(mocked.status);
      return mocked;
    }
    const res = await fetch('/api/status?device=' + encodeURIComponent(activeDevice), {headers:apiHeaders()});
    const data = await res.json();
    if (data.ok && data.status) render(data.status);
    showApiError(data);
    return data;
  }
  if (mockFaultEnabled()) {
    return {ok:false, error:'mock_read_only'};
  }
  if (isAuxEncoderDevice(activeDevice)) {
    return {ok:false, error:'read_only_device'};
  }
  const requestPayload = Object.assign({}, payload, {device: activeDevice});
  const res = await fetch('/api/command', {method:'POST', headers:apiHeaders({'Content-Type':'application/json'}), body: JSON.stringify(requestPayload)});
  const data = await res.json();
  if (data.ok && data.status) render(data.status);
  showApiError(data);
  return data;
}
function cls(el, good) { el.className = 'value ' + (good ? 'good' : 'bad'); }
function setText(id, text) { const el = document.getElementById(id); if (el) el.textContent = text; }
function modeLabel(mode) {
  return (MODE_LABELS[currentLang] && MODE_LABELS[currentLang][mode]) || mode;
}
function refreshModeOptions() {
  for (const opt of Array.from(modeSelect.options || [])) {
    if (opt.value === 'gear_cam' && opt.disabled) {
      opt.textContent = UI_TEXT[currentLang].gearUnavailable;
    } else {
      opt.textContent = modeLabel(opt.value);
    }
  }
  applyCapabilityModeAvailability(activeDevice);
}
function refreshGearMasterOptions() {
  if (!gearMasterSelect || !gearMasterSelect.options) return;
  gearMasterSelect.options[0].textContent = axisDisplayName('mctivity');
  gearMasterSelect.options[1].textContent = axisDisplayName('fv3');
  gearMasterSelect.options[1].disabled = !supportsDevice('fv3');
  gearMasterSelect.options[1].hidden = !supportsDevice('fv3');
  if (gearMasterSelect.options[2]) {
    gearMasterSelect.options[2].textContent = axisDisplayName('aux_encoder');
    gearMasterSelect.options[2].disabled = !supportsDevice('aux_encoder');
    gearMasterSelect.options[2].hidden = !supportsDevice('aux_encoder');
  }
  if (gearMasterSelect.options[3]) gearMasterSelect.options[3].textContent = UI_TEXT[currentLang].virtualAxis;
}
function applyDeviceUiMode(device = activeDevice) {
  const text = UI_TEXT[currentLang];
  const aux = isAuxEncoderDevice(device);
  document.body.classList.toggle('device-aux-encoder', aux);
  setText('feedbackPanelTitle', aux ? text.encoderFeedback : text.motorFeedback);
  const feedbackLabels = document.querySelectorAll('.feedback-metric .label');
  if (aux) {
    if (feedbackLabels[0]) feedbackLabels[0].textContent = text.loadTurns;
    if (feedbackLabels[1]) feedbackLabels[1].textContent = text.loadAngle;
    if (feedbackLabels[2]) feedbackLabels[2].textContent = text.loadPulses;
    if (feedbackLabels[3]) feedbackLabels[3].textContent = text.singleTurn;
    setText('positionParamTitle', text.loadPosition);
  } else {
    if (feedbackLabels[0]) feedbackLabels[0].textContent = text.currentTurns;
    if (feedbackLabels[1]) feedbackLabels[1].textContent = text.currentAngle;
    if (feedbackLabels[2]) feedbackLabels[2].textContent = text.currentPulses;
    if (feedbackLabels[3]) feedbackLabels[3].textContent = text.singleTurn;
    setText('positionParamTitle', text.targetAbs);
  }
  const absInput = document.getElementById('absPos');
  if (absInput) {
    absInput.tabIndex = aux ? -1 : 0;
    absInput.setAttribute('aria-readonly', aux ? 'true' : 'false');
  }
  if (aux) {
    const auxMode = currentProfile(device).mode === 'homing' && modeIsAssembled('homing') ? 'homing' : 'position';
    currentProfile(device).mode = auxMode;
    if (modeSelect) modeSelect.value = auxMode;
    syncModePanels(auxMode, true);
  }
}
function refreshStaticText() {
  const text = UI_TEXT[currentLang];
  document.documentElement.lang = currentLang === 'zh' ? 'zh-CN' : 'en';
  document.body.classList.toggle('lang-en', currentLang === 'en');
  document.body.classList.toggle('lang-zh', currentLang === 'zh');
  document.title = text.pageTitle;
  setText('pageTitle', text.pageTitle);
  setText('tabMonitorBtn', text.axisA);
  setText('tabConfigBtn', text.axisB);
  setText('tabEncoderBtn', text.axisC);
  setText('protocolChip', 'EtherCAT');
  setText('profileLabel', text.profile);
  setText('featureCountLabel', text.features);
  setText('capabilityCountLabel', text.capabilities);
  setText('warningCountLabel', text.warnings);
  const langToggleBtn = document.getElementById('langToggleBtn');
  const langZhBtn = document.getElementById('langZhBtn');
  const langEnBtn = document.getElementById('langEnBtn');
  const apiTokenInput = document.getElementById('apiTokenInput');
  if (apiTokenInput) {
    apiTokenInput.placeholder = text.apiToken;
    apiTokenInput.setAttribute('aria-label', text.apiToken);
    apiTokenInput.title = text.apiToken;
  }
  if (langToggleBtn) {
    langToggleBtn.setAttribute('aria-label', currentLang === 'zh' ? '语言' : 'Language');
    langToggleBtn.title = currentLang === 'zh' ? '语言' : 'Language';
  }
  if (langZhBtn) langZhBtn.classList.toggle('active', currentLang === 'zh');
  if (langEnBtn) langEnBtn.classList.toggle('active', currentLang === 'en');
  applyDeviceUiMode(activeDevice);
  const feedbackTitles = document.querySelectorAll('.feedback-card .feedback-title');
  if (feedbackTitles[0]) feedbackTitles[0].textContent = text.position;
  if (feedbackTitles[1]) feedbackTitles[1].textContent = text.torque;
  if (feedbackTitles[2]) feedbackTitles[2].textContent = text.speed;
  const feedbackLabels = document.querySelectorAll('.feedback-metric .label');
  if (feedbackLabels[0]) feedbackLabels[0].textContent = text.currentTurns;
  if (feedbackLabels[1]) feedbackLabels[1].textContent = text.currentAngle;
  if (feedbackLabels[2]) feedbackLabels[2].textContent = text.currentPulses;
  if (feedbackLabels[3]) feedbackLabels[3].textContent = text.singleTurn;
  const controlTitle = document.querySelector('.middle-stack .card h2');
  if (controlTitle) controlTitle.textContent = text.control;
  const modeLabelNode = document.querySelector('.mode-row .label');
  if (modeLabelNode) modeLabelNode.textContent = text.mode;
  setText('transmissionLabel', text.transmissionLabel);
  setText('transmissionModalTitle', text.transmissionModalTitle);
  setText('transmissionTypeFieldLabel', text.transmissionTypeFieldLabel);
  setText('transmissionTravelModeLabel', text.transmissionTravelModeLabel);
  setText('transmissionDirectionLabel', text.transmissionDirectionLabel);
  setText('transmissionReverseText', text.reverseDirection);
  setText('transmissionForwardText', text.forwardDirection);
  setText('transmissionAmountLabel', text.transmissionAmountLabel);
  setText('transmissionRevsLabel', transmissionRotationLabel(activeDevice));
  setText('transmissionPeriodLabel', text.transmissionPeriodLabel);
  setText('transmissionForwardLabel', text.transmissionForwardLabel);
  setText('transmissionReverseLabel', text.transmissionReverseLabel);
  setText('transmissionCancelBtn', text.transmissionCancelBtn);
  setText('transmissionSaveBtn', text.transmissionSaveBtn);
  setText('diagCopyBtn', text.copy);
  setText('diagCloseBtn', text.close);
  const transmissionTypeSelect = document.getElementById('transmissionType');
  if (transmissionTypeSelect && transmissionTypeSelect.options[0]) transmissionTypeSelect.options[0].textContent = text.rotaryType;
  if (transmissionTypeSelect && transmissionTypeSelect.options[1]) transmissionTypeSelect.options[1].textContent = text.linearType;
  const transmissionTravelSelect = document.getElementById('transmissionTravelMode');
  if (transmissionTravelSelect && transmissionTravelSelect.options[0]) transmissionTravelSelect.options[0].textContent = text.periodicTravel;
  if (transmissionTravelSelect && transmissionTravelSelect.options[1]) transmissionTravelSelect.options[1].textContent = text.reciprocatingTravel;
  const toggleTitles = document.querySelectorAll('.toggle-title');
  if (toggleTitles[0]) toggleTitles[0].textContent = text.enable;
  if (toggleTitles[1]) toggleTitles[1].textContent = text.startStop;
  if (toggleTitles[2]) toggleTitles[2].textContent = text.status;
  const positionTitles = document.querySelectorAll('#panel-position .slider-title');
  if (positionTitles[0]) positionTitles[0].textContent = text.targetAbs;
  const positionLabels = document.querySelectorAll('#panel-position .vertical-slider label');
  if (positionLabels[0]) positionLabels[0].textContent = text.speed;
  if (positionLabels[1]) positionLabels[1].textContent = text.accel;
  setText('antiSwayPositionParamsTitle', text.antiSwayPositionParams);
  renderAntiSwayExecutionBadge();
  renderAntiSwayDebugPanels();
  setText('antiSwayMonitorBadge', text.antiSwayInputReady || text.antiSwayPreview);
  setText('antiSwayMonitorTitle', text.antiSwayMonitor);
  setText('antiSwayPlanTitle', text.antiSwayPlan);
  setText('antiSwayPlanState', text.antiSwayPlanPreviewOnly);
  setText('antiSwayPlanNormalLabel', text.antiSwayPlanNormal);
  setText('antiSwayPlanShapedLabel', text.antiSwayPlanShaped);
  setText('antiSwayNaturalPeriodTitle', text.antiSwayNaturalPeriod);
  setText('antiSwayPlanDelayTitle', text.antiSwayPlanDelay);
  setText('antiSwayPlanDurationTitle', text.antiSwayPlanDuration);
  setText('antiSwaySpeedTitle', text.speed);
  setText('antiSwayAccelTitle', text.accel);
  setText('antiSwayAlgorithmTitle', text.antiSwayAlgorithm);
  setText('antiSwaySensorAxisTitle', text.antiSwaySensorAxis);
  setText('antiSwayRodTitle', text.antiSwayRodLength);
  setText('antiSwayLimitTitle', text.antiSwayAllowAngle);
  setText('antiSwayAngleTitle', text.antiSwayCurrentAngle);
  setText('antiSwayPeakTitle', text.antiSwayPeakShort || text.antiSwayPeak);
  setText('antiSwayResidualTitle', text.antiSwayResidualShort || text.antiSwayResidual);
  setText('antiSwayPhaseTitle', text.antiSwayPhase);
  setText('antiSwaySettleTitle', text.antiSwaySettleShort || text.antiSwaySettle);
  setText('antiSwayStateTitle', text.antiSwayState);
  setText('antiSwayPreviewNote', text.antiSwayPreviewNote);
  setText('antiSwayStateValue', text.antiSwayPreviewState);
  const antiSwaySensorAxis = document.getElementById('antiSwaySensorAxis');
  if (antiSwaySensorAxis) antiSwaySensorAxis.dataset.options = '';
  const targetUnit = document.querySelector('.target-unit');
  if (targetUnit) targetUnit.textContent = normalizedTransmission(currentProfile()).unit;
  const jogTitles = document.querySelectorAll('#panel-jog .slider-title');
  if (jogTitles[0]) jogTitles[0].textContent = text.relMove;
  if (jogTitles[1]) jogTitles[1].textContent = text.moveTime;
  const jogMeta = document.querySelectorAll('#panel-jog .meta span');
  if (jogMeta[1]) jogMeta[1].textContent = text.slowerHint;
  const jogButton = document.querySelector('#panel-jog button.blue');
  if (jogButton) jogButton.textContent = text.move;
  document.querySelectorAll('#panel-point .point-actions button').forEach(btn => btn.textContent = text.move);
  renderMultiPointPanel(true);
  setText('homingTabSetCurrent', text.homingMethodSetCurrent);
  setText('homingTabTorqueEnd', text.homingMethodTorqueEnd);
  setText('homingSetCurrentPositionLabel', text.homingSetCurrentPosition);
  setText('homingSetCurrentApply', text.homingSetCurrentApply);
  setText('homingTorqueSetPositionLabel', text.homingTorqueSetPosition);
  setText('homingDirectionLabel', text.homingDirection);
  setText('homingSpeedLabel', text.homingSpeed);
  setText('homingTorqueLabel', text.homingTorqueThreshold);
  setText('homingMaxDistanceLabel', text.homingMaxDistance);
  const homingDirection = document.getElementById('homingDirection');
  if (homingDirection && homingDirection.options[0]) homingDirection.options[0].textContent = text.reverseDirection;
  if (homingDirection && homingDirection.options[1]) homingDirection.options[1].textContent = text.forwardDirection;
  renderHomingPanel(true);
  const velocityTitle = document.querySelector('#panel-velocity .slider-title');
  if (velocityTitle) velocityTitle.textContent = text.velocityJog;
  const velocityButtons = document.querySelectorAll('#panel-velocity .control-row button');
  if (velocityButtons[0]) velocityButtons[0].textContent = text.reverse;
  if (velocityButtons[1]) velocityButtons[1].textContent = text.stop;
  if (velocityButtons[2]) velocityButtons[2].textContent = text.forward;
  const torqueTitle = document.querySelector('#panel-torque .slider-title');
  if (torqueTitle) torqueTitle.textContent = text.torqueCmd;
  const torqueNote = document.querySelector('#panel-torque .control-note');
  if (torqueNote) torqueNote.textContent = text.torqueNote;
  const torqueButton = document.querySelector('#panel-torque button.neutral');
  if (torqueButton) torqueButton.textContent = text.stageWrite;
  const gearLabels = document.querySelectorAll('.gear-field .label');
  if (gearLabels[0]) gearLabels[0].textContent = text.gearMaster;
  if (gearLabels[1]) gearLabels[1].textContent = text.gearSlave;
  const configTitles = document.querySelectorAll('#tabConfig h2');
  if (configTitles[0]) configTitles[0].textContent = text.pointPositioning;
  if (configTitles[1]) configTitles[1].textContent = text.pointConfig;
  const cardTitles = document.querySelectorAll('#tabConfig .param-card h3');
  if (cardTitles[0]) cardTitles[0].textContent = text.posJog;
  if (cardTitles[1]) cardTitles[1].textContent = text.speedTorque;
  if (cardTitles[2]) cardTitles[2].textContent = text.gear;
  if (cardTitles[3]) cardTitles[3].textContent = text.cam;
  const configLabels = document.querySelectorAll('#tabConfig .param-card label');
  if (configLabels[0]) configLabels[0].childNodes[0].nodeValue = text.defaultRel;
  if (configLabels[1]) configLabels[1].childNodes[0].nodeValue = text.defaultAbs;
  if (configLabels[2]) configLabels[2].childNodes[0].nodeValue = text.defaultMoveMs + ' ms';
  if (configLabels[3]) configLabels[3].childNodes[0].nodeValue = text.defaultVel + ' cnt/s';
  if (configLabels[4]) configLabels[4].childNodes[0].nodeValue = text.torqueLimit + ' %';
  if (configLabels[5]) configLabels[5].childNodes[0].nodeValue = text.gearNum;
  if (configLabels[6]) configLabels[6].childNodes[0].nodeValue = text.gearDen;
  if (configLabels[7]) configLabels[7].childNodes[0].nodeValue = text.camTable;
  if (configLabels[8]) configLabels[8].childNodes[0].nodeValue = text.syncPeriod + ' ms';
  document.querySelectorAll('#tabConfig .point-actions button').forEach(btn => btn.textContent = text.record);
  refreshModeOptions();
  refreshGearMasterOptions();
  refreshMockFaultPanel();
  renderCapabilitySummary();
  applyDeviceUiMode(activeDevice);
}
function applyLanguage() {
  refreshStaticText();
  refreshGearPanel(activeDevice);
  syncModeSelectDisabled(activeDevice);
  syncModePanels((currentProfile(activeDevice) && currentProfile(activeDevice).mode) || modeSelect.value || 'position', true);
  syncIncrementalEditor(true);
  updateSliders();
  const status = currentStatus();
  if (status) render(status);
}
function closeLanguageMenu() {
  const dropdown = document.getElementById('langDropdown');
  const button = document.getElementById('langToggleBtn');
  if (dropdown) dropdown.classList.remove('open');
  if (button) button.setAttribute('aria-expanded', 'false');
}
function toggleLanguageMenu(event) {
  if (event) event.stopPropagation();
  const dropdown = document.getElementById('langDropdown');
  const button = document.getElementById('langToggleBtn');
  if (!dropdown || !button) return false;
  const nextOpen = !dropdown.classList.contains('open');
  dropdown.classList.toggle('open', nextOpen);
  button.setAttribute('aria-expanded', nextOpen ? 'true' : 'false');
  return false;
}
function setLanguage(lang) {
  if (lang !== 'zh' && lang !== 'en') return false;
  currentLang = lang;
  localStorage.setItem(LANG_KEY, currentLang);
  closeLanguageMenu();
  applyLanguage();
  return false;
}
function hex4(value) { return '0x' + (Number(value) & 0xffff).toString(16).toUpperCase().padStart(4, '0'); }
function faultDisplay(status) {
  const text = UI_TEXT[currentLang];
  if (status && status.device === 'aux_encoder') {
    const alarm = Number(status.alarm_status || 0);
    const warning = Number(status.warning_status || 0);
    const healthy = Boolean(status.operational && status.wc_complete && alarm === 0);
    return {
      code: hex4(alarm || warning),
      name: healthy ? text.ok : text.fault
    };
  }
  return {
    code: hex4(status && status.err),
    name: status && status.fault ? text.fault : text.ok
  };
}
function normalizeAuxEncoderStatus(raw) {
  const profile = currentProfile('aux_encoder');
  const rawCounts = Number(raw.position_raw ?? raw.position_value ?? raw.pos_raw ?? 0) || 0;
  const countsPerRev = auxEncoderCountsPerRev(raw, profile);
  const softZeroRaw = Number(profile.softZeroRaw || 0) || 0;
  const alarm = Number(raw.alarm_status || 0) || 0;
  const warning = Number(raw.warning_status || 0) || 0;
  const healthy = Boolean(raw.operational && raw.wc_complete && alarm === 0);
  return Object.assign({}, raw, {
    device:'aux_encoder',
    enabled:false,
    servo_request:false,
    moving:false,
    gear_running:false,
    fault:!healthy,
    settle_cycles:0,
    cw:0,
    sw:0,
    err:alarm || warning || 0,
    mode:'--',
    control_mode:'position',
    counts_per_rev:countsPerRev,
    pos_raw:rawCounts,
    pos:rawCounts - softZeroRaw,
    target_raw:rawCounts,
    target:rawCounts - softZeroRaw,
    following_error:0,
    soft_zero_raw:softZeroRaw,
    jog_velocity_cps:Number(raw.speed_32 || 0) || 0,
    torque_cmd:0,
    torque_feedback:0,
    homed:Boolean(softZeroRaw),
    last_command:'status',
    message:raw.message || (healthy ? 'auxiliary encoder feedback active' : 'auxiliary encoder status warning')
  });
}
function auxEncoderCountsPerRev(status = null, profile = currentProfile('aux_encoder')) {
  const value = Number((status && (status.counts_per_rev ?? status.countsPerRev)) ?? profile.countsPerRev ?? AUX_ENCODER_COUNTS_PER_REV);
  return Number.isFinite(value) && value > 0 ? value : AUX_ENCODER_COUNTS_PER_REV;
}
function auxEncoderRev(counts, status = null, profile = currentProfile('aux_encoder')) {
  return Number(counts || 0) / auxEncoderCountsPerRev(status, profile);
}
function auxEncoderPhaseCounts(counts, status = null, profile = currentProfile('aux_encoder')) {
  const countsPerRev = auxEncoderCountsPerRev(status, profile);
  return ((Number(counts || 0) % countsPerRev) + countsPerRev) % countsPerRev;
}
function auxLoadValueFromCounts(counts, profile = currentProfile('aux_encoder')) {
  return auxEncoderRev(counts, null, profile) * transmissionPerRev(profile) * transmissionDirectionSign(profile);
}
function auxDisplayLoadPosition(status, profile = currentProfile('aux_encoder')) {
  const tx = normalizedTransmission(profile);
  const bounds = transmissionBounds(profile);
  let value = auxLoadValueFromCounts(Number(status && status.pos || 0), profile);
  if (tx.type !== 'linear' && tx.travelMode === 'periodic') {
    const span = Math.max(0.001, bounds.maxLoad - bounds.minLoad);
    value = ((value - bounds.minLoad) % span + span) % span + bounds.minLoad;
  } else {
    value = clamp(value, bounds.minLoad, bounds.maxLoad);
  }
  return value;
}
function renderAuxEncoderFeedback(status, speedFeedbackRpm) {
  const profile = currentProfile('aux_encoder');
  const tx = normalizedTransmission(profile);
  const countsPerRev = auxEncoderCountsPerRev(status, profile);
  const loadValue = auxLoadValueFromCounts(Number(status.pos || 0), profile);
  const displayLoad = auxDisplayLoadPosition(status, profile);
  const loadTurns = tx.type === 'linear'
    ? loadValue / Math.max(0.001, transmissionPerRev(profile))
    : loadValue / Math.max(0.001, tx.period || tx.amount);
  setText('encoderTurns', loadTurns.toFixed(3) + ' rev');
  setText('encoderAngle', formatTransmissionScalar(displayLoad, tx.unit, 1));
  setText('encoderSingleTurn', fmt(auxEncoderPhaseCounts(status.pos, status, profile)) + ' cnt');
  setText('encoderPulses', fmt(Number(status.pos || 0)) + ' cnt');
  const encoderHand = document.getElementById('encoderHand');
  if (encoderHand) encoderHand.style.transform = 'rotate(' + (Number(status.pos || 0) / countsPerRev * 360).toFixed(1) + 'deg)';
  updateFeedbackDial('speedHand', 'speedGaugeValue', speedFeedbackRpm / 1000, 3, 1, 'krpm');
  updateFeedbackDial('torqueHand', 'torqueGaugeValue', 0, 100, 1, '%');
}
function renderAuxEncoderPositionPanel(status) {
  const profile = currentProfile('aux_encoder');
  const tx = normalizedTransmission(profile);
  const bounds = transmissionBounds(profile);
  const homed = axisHomedForCurrentTransmission('aux_encoder', status, profile);
  const displayLoad = auxDisplayLoadPosition(status, profile);
  const loadSpan = Math.max(0.001, bounds.maxLoad - bounds.minLoad);
  const markerPct = clamp((displayLoad - bounds.minLoad) / loadSpan, 0, 1);
  const positionAxis = document.querySelector('.position-axis');
  if (positionAxis) positionAxis.classList.toggle('unhomed', !homed);
  const marker = document.getElementById('currentPositionMarker');
  if (marker) marker.style.setProperty('--marker-pct', String(markerPct));
  setText('positionUnhomedLabel', UI_TEXT[currentLang].homingUnhomed);
  setText('axisMinRev', formatTransmissionScalar(bounds.minLoad, tx.unit, 1));
  setText('axisMaxRev', formatTransmissionScalar(bounds.maxLoad, tx.unit, 1));
  setText('targetRevBig', formatTransmissionValue(displayLoad, 1));
  setText('targetAngleBig', tx.type === 'linear' ? '' : 'Encoder ' + auxEncoderRev(status.pos, status, profile).toFixed(3) + ' rev');
  const targetUnit = document.querySelector('.target-unit');
  if (targetUnit) targetUnit.textContent = tx.unit;
  const speedLoad = Math.max(0, Number(absSpeedRpm.value || 0)) * transmissionPerRev(profile) / 60;
  const accelLoad = Math.max(0, Number(absAccel.value || 0)) * transmissionPerRev(profile) / 60;
  setText('absSpeedText', formatMultiPointNumber(speedLoad, 3) + ' ' + transmissionRateUnit(profile, 1));
  setText('absAccelText', formatMultiPointNumber(accelLoad, 3) + ' ' + transmissionRateUnit(profile, 2));
}
function setAuxEncoderPosition(setPosition = 0) {
  const status = currentStatus('aux_encoder');
  const profile = currentProfile('aux_encoder');
  const rawCounts = Number(status ? (status.pos_raw ?? status.position_raw ?? 0) : 0) || 0;
  const desiredCounts = auxCountsFromLoadValue(setPosition, profile);
  profile.softZeroRaw = rawCounts - desiredCounts;
  profile.homing = normalizeHomingState(profile.homing);
  profile.homing.transmissionSignature = transmissionSignature(profile);
  profile.homing.transmissionInvalidated = false;
  const normalized = normalizeAuxEncoderStatus(Object.assign({}, status || {}, {position_raw:rawCounts}));
  statusByDevice.aux_encoder = normalized;
  saveUiState('aux_encoder');
  render(normalized);
  return Promise.resolve({ok:true, status:normalized});
}
function setAuxEncoderZero() {
  return setAuxEncoderPosition(0);
}
function showTab(name) {
  switchAxis(name === 'encoder' ? 'aux_encoder' : (name === 'config' ? 'fv3' : 'mctivity'));
}
function switchAxis(deviceName) {
  try {
    saveUiState();
  } catch (err) {
    console.error('saveUiState before axis switch failed', err);
  }
  const monitorPanel = document.getElementById('tabMonitor');
  const configPanel = document.getElementById('tabConfig');
  const monitorBtn = document.getElementById('tabMonitorBtn');
  const configBtn = document.getElementById('tabConfigBtn');
  const encoderBtn = document.getElementById('tabEncoderBtn');
  activeDevice = supportsDevice(deviceName) ? deviceName : 'mctivity';
  if (monitorPanel) monitorPanel.classList.add('active');
  if (configPanel) configPanel.classList.remove('active');
  if (monitorBtn) monitorBtn.classList.toggle('active', activeDevice === 'mctivity');
  if (configBtn) configBtn.classList.toggle('active', activeDevice === 'fv3');
  if (encoderBtn) encoderBtn.classList.toggle('active', activeDevice === 'aux_encoder');
  applyDeviceUiMode(activeDevice);
  loadUiState();
  applyDeviceUiMode(activeDevice);
  enforceGearConstraints();
  feedbackByDevice[activeDevice] = null;
  syncModePanels(modeSelect.value || 'position', true);
  syncIncrementalEditor(true);
  setGearPanelLocked(isGearPanelLocked(activeDevice));
  updateSliders();
  const cachedStatus = currentStatus(activeDevice);
  if (cachedStatus) {
    render(cachedStatus);
  }
  api({cmd:'status'}).catch(() => {});
}
function bindAxisSwitchButtons() {
  const bindings = [
    ['tabMonitorBtn', 'mctivity'],
    ['tabConfigBtn', 'fv3'],
    ['tabEncoderBtn', 'aux_encoder'],
  ];
  bindings.forEach(([id, device]) => {
    const button = document.getElementById(id);
    if (!button) return;
    button.removeAttribute('onclick');
    const activate = event => {
      if (event) {
        event.preventDefault();
        event.stopPropagation();
      }
      if (supportsDevice(device)) switchAxis(device);
    };
    button.addEventListener('click', activate);
    button.addEventListener('touchend', activate, {passive:false});
  });
}
function refreshDeviceTabs() {
  const configBtn = document.getElementById('tabConfigBtn');
  const encoderBtn = document.getElementById('tabEncoderBtn');
  const fv3Enabled = supportsDevice('fv3');
  if (configBtn) {
    configBtn.hidden = !fv3Enabled;
    configBtn.disabled = !fv3Enabled;
    configBtn.setAttribute('aria-hidden', fv3Enabled ? 'false' : 'true');
  }
  if (encoderBtn) {
    encoderBtn.hidden = !supportsDevice('aux_encoder');
    encoderBtn.disabled = !supportsDevice('aux_encoder');
    encoderBtn.setAttribute('aria-hidden', supportsDevice('aux_encoder') ? 'false' : 'true');
  }
  if (!fv3Enabled && activeDevice === 'fv3') {
    activeDevice = 'mctivity';
  }
  if (!supportsDevice(activeDevice)) {
    activeDevice = 'mctivity';
  }
  applyDeviceUiMode(activeDevice);
}
function renderMotionToggle(active, activeText) {
  const motionIndicator = document.getElementById('motionIndicator');
  const motionText = document.getElementById('motionIndicatorText');
  const text = UI_TEXT[currentLang];
  if (!motionIndicator || !motionText) return;
  motionIndicator.classList.toggle('motion-on', Boolean(active));
  motionText.textContent = active ? (activeText || text.inMotion) : text.standstill;
}
function homingActiveFromStatus(status) {
  const state = String((status && status.homing_state) || '').toLowerCase();
  return Boolean(
    status &&
    (status.homing_active || state === 'search' || state === 'decel' || state === 'abort' || state === 'backoff' ||
      (status.control_mode === 'homing' && status.moving))
  );
}
function homingTerminalKind(status) {
  const message = String((status && status.message) || '').toLowerCase();
  if (message.includes('homing complete') && message.includes('backed off')) return 'torque_complete';
  if (message.includes('homing complete') || message.includes('current position set')) return 'complete';
  if (message.includes('exceeded max search distance')) return 'max_distance';
  if (message.includes('homing timeout')) return 'timeout';
  if (message.includes('homing cancelled')) return 'cancelled';
  return '';
}
function homingDialogContent(kind, status, device = activeDevice) {
  const text = UI_TEXT[currentLang];
  const profile = currentProfile(device);
  const tx = normalizedTransmission(profile);
  const motionState = currentMotion(device);
  const rawMessage = String((status && status.message) || '');
  const message = rawMessage.toLowerCase();
  const loadPosition = homingCurrentLoadPosition(device, status, profile);
  const currentValue = formatTransmissionValue(Number.isFinite(loadPosition) ? loadPosition : 0, 3);
  const maxDistance = formatMultiPointNumber((profile.homing && profile.homing.maxDistance) || maxHomingDistanceForProfile(profile));
  const homing = currentHoming(device);
  const endpoint = homingLimitPositionForDirection(profile, homing.direction);
  const endpointValue = formatTransmissionValue(endpoint, 3);
  if (kind === 'complete') {
    const setCurrentValue = Number(motionState && motionState.homingSetCurrentValue);
    const setCurrentUnit = (motionState && motionState.homingSetCurrentUnit) || tx.unit;
    const usesSubmittedSetpoint = message.includes('current position set') && Number.isFinite(setCurrentValue);
    const completeValue = usesSubmittedSetpoint ? formatTransmissionValue(setCurrentValue, 3) : currentValue;
    const completeUnit = usesSubmittedSetpoint ? setCurrentUnit : tx.unit;
    return {
      title:text.homingCompleteTitle,
      body:text.homingCompleteBody.replace('{value}', completeValue).replace('{unit}', completeUnit)
    };
  }
  if (kind === 'torque_complete') {
    return {
      title:text.homingCompleteTitle,
      body:text.homingTorqueCompleteBody
        .replace('{limit}', endpointValue)
        .replaceAll('{unit}', tx.unit)
        .replace('{value}', currentValue)
    };
  }
  if (kind === 'max_distance') {
    return {
      title:text.homingBlockedNotFoundTitle,
      body:text.homingBlockedNotFoundBody.replace('{distance}', maxDistance).replace('{unit}', tx.unit)
    };
  }
  if (kind === 'timeout') {
    return {title:text.homingTimeoutTitle, body:text.homingTimeoutBody};
  }
  if (kind === 'cancelled') {
    const parts = rawMessage.split(/homing cancelled:\s*/i);
    const reason = (parts.length > 1 ? parts.slice(1).join(' ') : rawMessage).trim() || (currentLang === 'zh' ? '未知原因' : 'unknown reason');
    return {
      title:text.homingCancelledTitle,
      body:text.homingCancelledBody.replace('{reason}', reason)
    };
  }
  return null;
}
function syncPositionTargetToCurrentStatus(status, device = activeDevice) {
  if (device !== activeDevice || !isMotorDevice(device) || !status || !absPos) return;
  const profile = currentProfile(device);
  const bounds = transmissionBounds(profile);
  const minCounts = Math.min(bounds.minCounts, bounds.maxCounts);
  const maxCounts = Math.max(bounds.minCounts, bounds.maxCounts);
  const currentCounts = clamp(axisCounts(Number(status.pos || 0)), minCounts, maxCounts);
  absPos.value = String(currentCounts);
  profile.absPos = currentCounts;
}
function maybeSyncPositionTargetAfterCoordinateChange(status, device = activeDevice) {
  if (!isMotorDevice(device) || !status || !Object.prototype.hasOwnProperty.call(status, 'soft_zero_raw')) return;
  const softZero = Number(status.soft_zero_raw);
  if (!Number.isFinite(softZero)) return;
  const previous = lastSoftZeroByDevice[device];
  lastSoftZeroByDevice[device] = softZero;
  if (!axisHomedForCurrentTransmission(device, status, currentProfile(device))) return;
  if (previous === null || previous !== softZero) {
    syncPositionTargetToCurrentStatus(status, device);
  }
}
function maybeShowHomingCompletionNotice(status, device = activeDevice) {
  if (!isMotorDevice(device)) return;
  const motionState = currentMotion(device);
  const active = homingActiveFromStatus(status);
  if (active) {
    motionState.homingWasActive = true;
    return;
  }
  const kind = homingTerminalKind(status);
  const pendingMethod = String(motionState.homingPendingMethod || '');
  const allowInstantNotice = pendingMethod === 'set_current';
  const shouldNotify = Boolean(kind && (motionState.homingWasActive || (motionState.homingPending && allowInstantNotice)));
  if (kind && !shouldNotify && motionState.homingPending && pendingMethod === 'torque_end' && !motionState.homingWasActive) {
    return;
  }
  if (shouldNotify) {
    if (kind === 'complete' || kind === 'torque_complete') {
      markHomingReferenceValid(device);
      syncPositionTargetToCurrentStatus(status, device);
    }
    const key = [kind, String(status && status.message || '')].join('|');
    if (motionState.homingNoticeKey !== key) {
      const dialog = homingDialogContent(kind, status, device);
      if (dialog) openDiagModal(dialog.title, dialog.body);
      motionState.homingNoticeKey = key;
    }
  }
  motionState.homingPending = false;
  motionState.homingPendingMethod = '';
  motionState.homingWasActive = false;
  motionState.homingSetCurrentValue = null;
  motionState.homingSetCurrentUnit = '';
}
function maybeShowMotionInterruptedNotice(status, device = activeDevice) {
  if (!isMotorDevice(device) || !status) return;
  const motionState = currentMotion(device);
  const rawMessage = String(status.message || '');
  if (!rawMessage.toLowerCase().includes('motion cancelled')) return;
  const recentCommand = motionState.latch || motionState.seenMoving || (Date.now() - Number(motionState.commandAt || 0) < 5000);
  if (!recentCommand) return;
  const key = [device, rawMessage].join('|');
  if (motionState.motionCancelNoticeKey === key) return;
  const parts = rawMessage.split(/motion cancelled:\s*/i);
  const reason = (parts.length > 1 ? parts.slice(1).join(' ') : rawMessage).trim() || (currentLang === 'zh' ? '未知原因' : 'unknown reason');
  const text = UI_TEXT[currentLang];
  openDiagModal(text.motionInterruptedTitle, text.motionInterruptedBody.replace('{reason}', reason));
  motionState.motionCancelNoticeKey = key;
  motionState.latch = false;
  motionState.seenMoving = false;
}
function syncModePanels(mode, force=false) {
  const active = modeIsAssembled(mode || 'position') ? (mode || 'position') : 'position';
  if (!force && modePanelStateByDevice[activeDevice] === active) return;
  modePanelStateByDevice[activeDevice] = active;
  const text = UI_TEXT[currentLang];
  const activeModeCard = document.querySelector('.active-mode-card');
  for (const key of Object.keys(MODE_LABELS.zh)) {
    const panel = document.getElementById('panel-' + key);
    if (panel) panel.classList.toggle('active', key === active);
  }
  if (activeModeCard) {
    activeModeCard.classList.toggle('incremental-scroll', active === 'incremental');
    activeModeCard.classList.toggle('multi-point-scroll', active === 'multi_point');
    activeModeCard.classList.toggle('anti-sway-scroll', active === 'anti_sway_position');
  }
  if (active === 'position') {
    setText('modePanelTitle', text.posParams);
  } else if (active === 'anti_sway_position') {
    setText('modePanelTitle', text.antiSwayParams);
  } else if (active === 'incremental') {
    setText('modePanelTitle', text.incrementalParams);
  } else if (active === 'multi_point') {
    setText('modePanelTitle', text.multiPointParams);
  } else if (active === 'homing') {
    setText('modePanelTitle', text.homingParams);
  } else if (active === 'gear_cam') {
    setText('modePanelTitle', text.gearParams);
  } else {
    setText('modePanelTitle', modeLabel(active) + ' ' + text.controlSuffix);
  }
  syncIncrementalEditor(active === 'incremental');
  if (active === 'anti_sway_position') renderAntiSwayPanel(true);
  if (active === 'multi_point') renderMultiPointPanel(true);
  if (active === 'homing') renderHomingPanel(true);
}
function updateGearMaster() {
  if (isGearPanelLocked()) return;
  const profile = currentProfile();
  if (gearMasterSelect.value === activeDevice) {
    gearMasterSelect.value = preferredGearMaster(activeDevice);
  }
  profile.gearMaster = gearMasterSelect.value;
  enforceGearConstraints();
  updateSliders();
}
function render(s) {
  const text = UI_TEXT[currentLang];
  const device = s && s.device === 'aux_encoder' ? 'aux_encoder' : (s && s.device === 'fv3' ? 'fv3' : 'mctivity');
  if (device === 'aux_encoder') {
    s = normalizeAuxEncoderStatus(s || {});
  }
  statusByDevice[device] = s;
  if (device !== activeDevice) {
    if (modeSelect && modeSelect.value === 'anti_sway_position') renderAntiSwayPanel(false);
    return;
  }
  applyDeviceUiMode(device);
  const motionState = currentMotion(device);
  const now = Date.now();
  let speedFeedback = 0;
  const feedbackSample = feedbackByDevice[device];
  if (feedbackSample && now > feedbackSample.t) {
    const countsPerRev = isAuxEncoderDevice(device) ? auxEncoderCountsPerRev(s) : REV;
    const deltaCounts = isAuxEncoderDevice(device)
      ? Number(s.pos) - feedbackSample.pos
      : axisCounts(Number(s.pos) - feedbackSample.pos);
    speedFeedback = deltaCounts / countsPerRev / ((now - feedbackSample.t) / 1000) * 60;
  }
  feedbackByDevice[device] = {pos:Number(s.pos) || 0, t:now};
  if (isAuxEncoderDevice(device) && Number(s.speed_32 || 0) !== 0) {
    speedFeedback = Number(s.speed_32 || 0) / auxEncoderCountsPerRev(s) * 60;
  }
  updateFeedbackDial('speedHand', 'speedGaugeValue', speedFeedback / 1000, 3, 1, 'krpm');
  updateFeedbackDial('torqueHand', 'torqueGaugeValue', torqueFeedbackPercent(s), 100, 1, '%');
  let e = document.getElementById('enabled');
  if (e) { e.textContent = s.enabled ? text.on : text.off; cls(e, s.enabled); }
  const toggle = document.getElementById('enableToggle');
  const toggleText = document.getElementById('enableToggleText');
  const enableRawOn = Boolean(s.servo_request || s.enabled);
  if (enableRawOn) {
    motionState.enableVisual = true;
    motionState.enableOffCandidateAt = 0;
  } else if (s.fault) {
    motionState.enableVisual = false;
    motionState.enableOffCandidateAt = 0;
  } else if (!motionState.enableOffCandidateAt) {
    motionState.enableOffCandidateAt = now;
  } else if (now - motionState.enableOffCandidateAt > 800) {
    motionState.enableVisual = false;
  }
  const enableVisualOn = Boolean(motionState.enableVisual);
  if (toggle && toggleText) {
    toggle.classList.toggle('on', enableVisualOn);
    toggleText.textContent = enableVisualOn ? text.on : text.off;
  }
  if (s.moving) {
    motionState.seenMoving = true;
    motionState.movingOffCandidateAt = 0;
  } else {
    motionState.stopRequested = false;
    if (!motionState.movingOffCandidateAt) motionState.movingOffCandidateAt = now;
  }
  if (motionState.latch && !s.moving && (motionState.seenMoving || now - motionState.commandAt > 1200)) {
    if (motionState.movingOffCandidateAt && now - motionState.movingOffCandidateAt > 600) {
      motionState.latch = false;
    }
  }
  if (s.control_mode === 'gear_cam' && typeof s.gear_running !== 'undefined') {
    motionState.gearEngaged = Boolean(s.gear_running);
  } else if (s.control_mode !== 'gear_cam' && !motionState.latch) {
    motionState.gearEngaged = false;
    motionState.gearStoppedLatched = false;
  }
  const gearEngaged = isGearEngaged(device) && s.control_mode === 'gear_cam';
  const multiPointRunning = isMultiPointRunnerRunning(device);
  const homingRunning = homingActiveFromStatus(s);
  maybeSyncPositionTargetAfterCoordinateChange(s, device);
  maybeShowHomingCompletionNotice(s, device);
  maybeShowMotionInterruptedNotice(s, device);
  const forceGearStandstill = Boolean(motionState.gearStoppedLatched && !gearEngaged);
  if (forceGearStandstill && !s.moving) {
    motionState.gearStoppedLatched = false;
  }
  renderMotionToggle(
    gearEngaged || multiPointRunning || homingRunning || (!forceGearStandstill && motionState.latch),
    gearEngaged ? text.gearing : (multiPointRunning ? text.multiPointRunning : (homingRunning ? text.homingRunning : ''))
  );
  setGearPanelLocked(gearEngaged);
  const faultIndicator = document.getElementById('faultIndicator');
  const faultText = document.getElementById('faultIndicatorText');
  const faultButton = document.getElementById('faultIndicatorButton');
  if (faultIndicator && faultText && faultButton) {
    const faultView = faultDisplay(s);
    faultIndicator.classList.toggle('fault-on', Boolean(s.fault));
    faultText.textContent = s.fault ? text.fault : text.ready;
    setText('faultCodeText', faultView.code);
    setText('faultNameText', faultView.name);
    faultButton.setAttribute('aria-label', s.fault ? text.fault : text.ready);
    faultButton.textContent = s.fault ? text.reset : '';
    faultButton.classList.toggle('fault', Boolean(s.fault));
    faultButton.disabled = isAuxEncoderDevice(device) || !s.fault;
  }
  let m = document.getElementById('moving'); if (m) { m.textContent = s.moving ? text.moving : text.idle; m.className = 'value info'; }
  let f = document.getElementById('fault'); if (f) { f.textContent = s.fault ? text.fault : text.ok; cls(f, !s.fault); }
  setText('pos', fmt(s.pos)); setText('target', fmt(s.target)); setText('follow', fmt(s.following_error));
  setText('op', '0x' + Number(s.al_state).toString(16) + ' / ' + s.operational);
  setText('wc', s.wc + (s.wc_complete ? ' complete' : ''));
  setText('mode', s.mode);
  const hmiMode = hmiModeFromStatus(device, s.control_mode);
  setText('controlModeView', modeLabel(hmiMode) || hmiMode || '--');
  setText('velocityView', fmt(s.jog_velocity_cps || 0));
  setText('torqueView', String(s.torque_cmd || 0) + '%');
  if (isAuxEncoderDevice(device)) {
    const auxMode = currentProfile(device).mode === 'homing' && modeIsAssembled('homing') ? 'homing' : 'position';
    currentProfile(device).mode = auxMode;
    if (modeSelect) modeSelect.value = auxMode;
    syncModePanels(auxMode);
    renderAuxEncoderFeedback(s, speedFeedback);
    updateSliders();
    renderAuxEncoderPositionPanel(s);
    return;
  }
  const modeUi = currentModeUi(device);
  if (!modeUi.interacting) {
    if (modeUi.pending && (s.control_mode === modeUi.pending || hmiMode === modeUi.pending)) {
      modeUi.pending = null;
      syncModeSelectDisabled(device);
    }
    if (modeSelect && device === activeDevice && !modeUi.pending && hmiMode && modeSelect.value !== hmiMode) {
      modeSelect.value = hmiMode;
    }
    if (!modeUi.pending && hmiMode) {
      currentProfile(device).mode = hmiMode;
    }
    syncModePanels(modeUi.pending || hmiMode || currentProfile(device).mode || 'position');
    enforceGearConstraints();
  }
  if (device === activeDevice && incrementalEditor) {
    incrementalEditor.setAxisContext(buildIncrementalAxisContext(device));
  }
  const a = deg(s.pos);
  setText('encoderAngle', a.toFixed(1) + ' deg');
  setText('encoderSingleTurn', fmt(phaseCounts(s.pos)) + ' cnt');
  setText('encoderTurns', rev(axisCounts(s.pos)).toFixed(3) + ' rev');
  setText('encoderPulses', fmt(axisCounts(s.pos)) + ' cnt');
  const encoderHand = document.getElementById('encoderHand');
  if (encoderHand) encoderHand.style.transform = 'rotate(' + continuousDeg(s.pos) + 'deg)';
  updateSliders();
}
function updateSliders() {
  saveUiState();
  const profile = currentProfile();
  profile.transmission = normalizedTransmission(profile);
  const tx = profile.transmission;
  const bounds = transmissionBounds(profile);
  const rangeMin = Math.min(bounds.minCounts, bounds.maxCounts);
  const rangeMax = Math.max(bounds.minCounts, bounds.maxCounts);
  if (String(absPos.min) !== String(rangeMin)) absPos.min = String(rangeMin);
  if (String(absPos.max) !== String(rangeMax)) absPos.max = String(rangeMax);
  absPos.value = String(clamp(Number(absPos.value), rangeMin, rangeMax));
  profile.absPos = Number(absPos.value);
  const rel = Number(relDelta.value), abs = Number(absPos.value), ms = Number(moveMs.value);
  const vel = Number(velCps.value), tq = Number(torqueCmd.value);
  const speed = Number(absSpeedRpm.value), accel = Number(absAccel.value);
  const gearSlaveName = axisDisplayName(activeDevice);
  const status = currentStatus();
  const current = status ? axisCounts(Number(status.pos)) : abs;
  const isLinear = tx.type === 'linear';
  const targetValue = transmissionValueFromCounts(abs, profile);
  const relValue = transmissionValueFromCounts(rel, profile);
  syncTransmissionSummary(activeDevice);
  setText('relText', formatTransmissionScalar(relValue, tx.unit, 1));
  setText('relRev', axisRotationPrefix(activeDevice) + formatMotorRevScalar(rel, 3));
  setText('relDeg', UI_TEXT[currentLang].loadPrefix + formatTransmissionScalar(relValue, tx.unit, 1));
  setText('relRpm', rpm(rel, ms).toFixed(1) + ' rpm');
  setText('absText', formatTransmissionScalar(targetValue, tx.unit, 1));
  setText('targetRevBig', formatTransmissionValue(targetValue, 1));
  setText('targetAngleBig', isLinear ? '' : axisRotationPrefix(activeDevice) + formatMotorRevScalar(abs, 3));
  const targetUnit = document.querySelector('.target-unit');
  if (targetUnit) targetUnit.textContent = tx.unit;
  setText('axisMinRev', formatTransmissionScalar(bounds.minLoad, tx.unit, 1));
  setText('axisMaxRev', formatTransmissionScalar(bounds.maxLoad, tx.unit, 1));
  const targetReadout = document.querySelector('.target-readout');
  if (targetReadout) targetReadout.classList.toggle('linear-mode', isLinear);
  const positionAxis = document.querySelector('.position-axis');
  const homed = axisHomedForCurrentTransmission(activeDevice, status, profile);
  if (positionAxis) {
    positionAxis.classList.toggle('linear-mode', isLinear);
    positionAxis.classList.toggle('unhomed', !homed);
  }
  setText('positionUnhomedLabel', UI_TEXT[currentLang].homingUnhomed);
  const currentPositionMarker = document.getElementById('currentPositionMarker');
	  if (currentPositionMarker) {
	    const currentLoadPos = transmissionValueFromCounts(current, profile);
	    const loadSpan = Math.max(0.001, bounds.maxLoad - bounds.minLoad);
	    const markerPct = clamp((currentLoadPos - bounds.minLoad) / loadSpan, 0, 1);
	    currentPositionMarker.style.setProperty('--marker-pct', String(markerPct));
	    setText('currentPositionValue', formatTransmissionScalar(currentLoadPos, tx.unit, 1));
	  }
  updateMultiPointAxis();
  if (modeSelect && modeSelect.value === 'homing') renderHomingPanel(false);
  if (modeSelect && modeSelect.value === 'anti_sway_position') renderAntiSwayPanel(false);
  const speedLoad = Math.max(0, speed) * transmissionPerRev(profile) / 60;
  const accelLoad = Math.max(0, accel) * transmissionPerRev(profile) / 60;
  setText('absSpeedText', formatMultiPointNumber(speedLoad, 3) + ' ' + transmissionRateUnit(profile, 1));
  setText('absAccelText', formatMultiPointNumber(accelLoad, 3) + ' ' + transmissionRateUnit(profile, 2));
  renderGearWheel('master');
  renderGearWheel('slave');
  updateGearUnitLabels(activeDevice);
  setText('gearSlaveName', gearSlaveName);
  setText('msText', fmt(ms) + ' ms');
  setText('velText', fmt(vel) + ' cnt/s'); setText('torqueText', tq + '%');
  const points = profile.points;
  for (const k of [1,2,3]) {
    const pointText = formatTransmissionScalar(transmissionValueFromCounts(points[k], profile), tx.unit, 1);
    setText('p' + k, pointText);
    setText('p' + k + 'Quick', pointText);
  }
}
function applyConfig() {
  relDelta.value = cfgRel.value; absPos.value = cfgAbs.value; moveMs.value = cfgMs.value; velCps.value = cfgVel.value;
  absSpeedRpm.value = Math.max(1, Math.min(MAX_SPEED_RPM, Math.round(Number(cfgVel.value) * 60 / REV)));
  torqueCmd.min = -Number(cfgTorqueLimit.value); torqueCmd.max = Number(cfgTorqueLimit.value);
  updateSliders();
}
function cmd(name) {
  if (isAuxEncoderDevice(activeDevice)) {
    if (name === 'set_zero' || name === 'home') return setAuxEncoderZero();
    return Promise.resolve({ok:false, error:'read_only_device'});
  }
  return api({cmd:name});
}
function toggleEnable() {
  if (isAuxEncoderDevice(activeDevice)) return Promise.resolve(false);
  const status = currentStatus();
  const motionState = currentMotion();
  if (!(status && status.servo_request)) {
    saveUiState();
    motionState.enableVisual = true;
    motionState.enableOffCandidateAt = 0;
  } else {
    motionState.enableVisual = false;
    motionState.enableOffCandidateAt = 0;
  }
  return cmd(status && status.servo_request ? 'disable' : 'enable');
}
function gearPayload(cmdName) {
  return {
    cmd: cmdName,
    master: gearMasterSelect.value,
    master_ratio: Number(gearMasterRatio.value || 1),
    slave_ratio: Number(gearSlaveRatio.value || 1)
  };
}
function isGearModeSelected() {
  const status = currentStatus();
  return (modeSelect && modeSelect.value === 'gear_cam') || (status && status.control_mode === 'gear_cam');
}
function hmiModeFromStatus(device = activeDevice, controlMode = '') {
  const physicalMode = controlMode || 'position';
  const profile = currentProfile(device);
  const profileMode = profile && profile.mode;
  const selectedMode = device === activeDevice && modeSelect ? modeSelect.value : profileMode;
  if (
    physicalMode === 'position' &&
    modeIsAssembled('anti_sway_position') &&
    (selectedMode === 'anti_sway_position' || profileMode === 'anti_sway_position')
  ) {
    return 'anti_sway_position';
  }
  if (
    physicalMode === 'position' &&
    modeIsAssembled('homing') &&
    (selectedMode === 'homing' || profileMode === 'homing')
  ) {
    return 'homing';
  }
  if (
    physicalMode === 'position' &&
    modeIsAssembled('multi_point') &&
    (selectedMode === 'multi_point' || profileMode === 'multi_point' || isMultiPointRunnerRunning(device))
  ) {
    return 'multi_point';
  }
  return physicalMode;
}
function isMultiPointModeSelected() {
  const status = currentStatus();
  return (modeSelect && modeSelect.value === 'multi_point') || hmiModeFromStatus(activeDevice, status && status.control_mode) === 'multi_point';
}
function isHomingModeSelected() {
  const status = currentStatus();
  return (modeSelect && modeSelect.value === 'homing') || hmiModeFromStatus(activeDevice, status && status.control_mode) === 'homing';
}
function isMultiPointRunnerRunning(device = activeDevice) {
  const runner = multiPointStatusByDevice[device];
  return Boolean(runner && (runner.running || runner.state === 'stopping'));
}
function setMode() {
  const requested = modeSelect.value;
  if (isAuxEncoderDevice(activeDevice)) {
    const auxMode = requested === 'homing' && modeIsAssembled('homing') ? 'homing' : 'position';
    currentProfile().mode = auxMode;
    if (modeSelect) modeSelect.value = auxMode;
    syncModePanels(auxMode, true);
    saveUiState();
    return Promise.resolve(false);
  }
  const modeUi = currentModeUi();
  const previous = (currentProfile() && currentProfile().mode) || (currentStatus() && currentStatus().control_mode) || 'position';
  if (requested === 'gear_cam' && applyGearModeAvailability(activeDevice)) {
    showGearLockedAlert(activeDevice);
    modeSelect.value = previous;
    syncModePanels(previous);
    return Promise.resolve(false);
  }
  if (requested !== 'gear_cam') {
    currentMotion().gearEngaged = false;
    currentMotion().gearStoppedLatched = false;
    setGearPanelLocked(false);
  }
  currentProfile().mode = requested;
  modeUi.pending = requested;
  modeUi.interacting = false;
  syncModeSelectDisabled(activeDevice);
  syncModePanels(requested, true);
  return api({cmd:'set_mode', mode:requested}).then(data => {
    if (!(data && data.ok)) {
      throw new Error((data && data.error) || 'set_mode failed');
    }
    if (data.status && data.status.control_mode === requested) {
      modeUi.pending = null;
      syncModeSelectDisabled(activeDevice);
    }
    enforceGearConstraints();
    return data;
  }).catch(err => {
    modeUi.pending = null;
    currentProfile().mode = previous;
    modeSelect.value = previous;
    syncModeSelectDisabled(activeDevice);
    syncModePanels(previous, true);
    enforceGearConstraints();
    console.error(err);
    return false;
  });
}
function resetFault(event) {
  if (event) {
    event.preventDefault();
    event.stopPropagation();
  }
  const motionState = currentMotion();
  if (!(currentStatus() && currentStatus().fault)) return false;
  if (isAuxEncoderDevice(activeDevice)) return false;
  motionState.commandSeq += 1;
  motionState.stopRequested = false;
  motionState.latch = false;
  motionState.seenMoving = false;
  motionState.gearEngaged = false;
  motionState.gearStoppedLatched = false;
  renderMotionToggle(false);
  setGearPanelLocked(false);
  api({cmd:'fault_reset'}).catch(err => console.error(err));
  return false;
}
function stopMotion() {
  if (isAuxEncoderDevice(activeDevice)) return Promise.resolve(false);
  const motionState = currentMotion();
  motionState.commandSeq += 1;
  if (motionState.stopRequested) return false;
  motionState.stopRequested = true;
  motionState.latch = false;
  motionState.seenMoving = false;
  motionState.gearStoppedLatched = false;
  renderMotionToggle(Boolean(currentStatus() && currentStatus().moving) || isGearEngaged(), isGearEngaged() ? UI_TEXT[currentLang].gearing : '');
  if (isGearModeSelected()) {
    return api(gearPayload('gear_stop'))
      .then(data => {
        motionState.stopRequested = false;
        motionState.gearEngaged = false;
        motionState.gearStoppedLatched = true;
        motionState.latch = false;
        motionState.seenMoving = false;
        renderMotionToggle(false);
        setGearPanelLocked(false);
        return data;
      })
      .catch(err => {
        motionState.stopRequested = false;
        renderMotionToggle(Boolean(currentStatus() && currentStatus().moving) || isGearEngaged(), isGearEngaged() ? UI_TEXT[currentLang].gearing : '');
        console.error(err);
        return false;
      });
  }
  if (isMultiPointModeSelected()) {
    return api({cmd:'point_table_stop'})
      .then(data => {
        motionState.stopRequested = false;
        motionState.latch = false;
        motionState.seenMoving = false;
        multiPointStatusByDevice[activeDevice] = data.point_table_runner || null;
        renderMultiPointRunner(multiPointStatusByDevice[activeDevice]);
        return data;
      })
      .catch(err => {
        motionState.stopRequested = false;
        renderMotionToggle(Boolean(currentStatus() && currentStatus().moving));
        console.error(err);
        return false;
      });
  }
  if (isHomingModeSelected()) {
    return api({cmd:'homing_stop', deceleration_rpm_s:Number(absAccel.value || 0)})
      .then(data => {
        motionState.stopRequested = false;
        motionState.latch = false;
        motionState.seenMoving = false;
        return data;
      })
      .catch(err => {
        motionState.stopRequested = false;
        renderMotionToggle(Boolean(currentStatus() && currentStatus().moving));
        console.error(err);
        return false;
      });
  }
  return api({cmd:'stop', deceleration_rpm_s:Number(absAccel.value || 0)})
    .then(data => {
      const moving = Boolean(data && data.status && data.status.moving);
      if (!moving) {
        motionState.stopRequested = false;
        motionState.gearEngaged = false;
        renderMotionToggle(false);
        setGearPanelLocked(false);
      }
      return data;
    })
    .catch(err => {
      motionState.stopRequested = false;
      renderMotionToggle(Boolean(currentStatus() && currentStatus().moving) || isGearEngaged(), isGearEngaged() ? UI_TEXT[currentLang].gearing : '');
      console.error(err);
      return false;
    });
}
async function startHoming() {
  const profile = currentProfile();
  const homing = syncHomingControls(true);
  applyTorqueHomingLimit(profile, homing);
  profile.homing = normalizeHomingState(homing);
  renderHomingPanel(true);
  const text = UI_TEXT[currentLang];
  const tx = normalizedTransmission(profile);
  const setValueText = formatMultiPointNumber(homing.setPosition);
  if (homing.method === 'torque_end') {
    const maxSearchDistance = maxHomingDistanceForProfile(profile);
    const maxBackoffDistance = maxHomingBackoffForProfile(profile);
    if (Number(homing.backoffDistance || 0) > maxBackoffDistance + 1e-9) {
      const message = text.homingMaxDistanceExceeded
        .replace('{value}', formatMultiPointNumber(maxBackoffDistance))
        .replace('{unit}', tx.unit);
      openDiagModal(modeLabel('homing'), message);
      return {ok:false, blocked:true};
    }
    if (Number(homing.maxDistance || 0) > maxSearchDistance + 1e-9) {
      const message = text.homingMaxDistanceExceeded
        .replace('{value}', formatMultiPointNumber(maxSearchDistance))
        .replace('{unit}', tx.unit);
      openDiagModal(modeLabel('homing'), message);
      return {ok:false, blocked:true};
    }
    const status = currentStatus();
    if (!status || !status.enabled || status.fault || Number(status.settle_cycles || 0) > 0) {
      openDiagModal(text.motionNotReady, text.homingServoNotReady);
      return {ok:false, blocked:true};
    }
  }
  if (homing.method === 'set_current') {
    const message = text.homingSetCurrentConfirm
      .replace('{value}', setValueText)
      .replace('{unit}', tx.unit);
    if (!window.confirm(message)) return {ok:false, cancelled:true};
  } else {
    const backoffText = formatMultiPointNumber(homing.backoffDistance);
    const message = text.homingTorqueConfirm
      .replace('{limit}', setValueText)
      .replace('{backoff}', backoffText)
      .split('{unit}').join(tx.unit);
    if (!window.confirm(message)) return {ok:false, cancelled:true};
  }
  if (isAuxEncoderDevice(activeDevice)) {
    if (homing.method === 'torque_end') {
      openDiagModal(modeLabel('homing'), text.homingTorqueUnavailable);
      return false;
    }
    return setAuxEncoderPosition(homing.setPosition);
  }
  if (homing.method === 'set_current') {
    const motionState = currentMotion();
    motionState.homingPending = true;
    motionState.homingPendingMethod = 'set_current';
    motionState.homingWasActive = false;
    motionState.homingNoticeKey = '';
    motionState.homingSetCurrentValue = Number(homing.setPosition);
    motionState.homingSetCurrentUnit = tx.unit;
    return api({cmd:'homing_set_current', position:nativeCountsFromTransmissionValue(homing.setPosition, profile)});
  }
  if (!homingCanUseTorque(activeDevice)) {
    openDiagModal(modeLabel('homing'), text.homingTorqueUnavailable);
    return false;
  }
  const direction = nativeDirectionFromHoming(profile, homing);
  const maxDistance = homingDistanceCounts(profile, homing.maxDistance);
  const motionState = currentMotion();
  motionState.homingPending = true;
  motionState.homingPendingMethod = 'torque_end';
  motionState.homingWasActive = false;
  motionState.homingNoticeKey = '';
  motionState.homingSetCurrentValue = null;
  motionState.homingSetCurrentUnit = '';
  const payload = {
    cmd:'homing_start_torque',
    direction,
    speed_rpm:motorRpmFromTransmissionSpeed(homing.speed, profile),
    torque_threshold:homing.torqueThreshold,
    set_position:nativeCountsFromTransmissionValue(homing.setPosition, profile),
    backoff_distance:homingBackoffDistanceCounts(profile, homing.backoffDistance),
    backoff_position:homingBackoffPositionCounts(profile, homing),
    max_distance:maxDistance,
    deceleration_rpm_s:Number(absAccel.value || 300),
    timeout_ms:30000,
    torque_hold_ms:1
  };
  motionState.stopRequested = false;
  motionState.latch = true;
  motionState.seenMoving = false;
  motionState.commandAt = Date.now();
  renderMotionToggle(true, UI_TEXT[currentLang].homingRunning);
  openDiagModal(text.homingRunningTitle, text.homingRunningBody);
  return api(payload);
}
function startHomingFromPanel() {
  startHoming().then(result => {
    if (result && (result.cancelled || result.blocked || result.ok === false)) {
      const motionState = currentMotion();
      motionState.latch = false;
      motionState.seenMoving = false;
      motionState.homingPending = false;
      motionState.homingPendingMethod = '';
      motionState.homingWasActive = false;
      motionState.homingSetCurrentValue = null;
      motionState.homingSetCurrentUnit = '';
      renderMotionToggle(false);
    }
  }).catch(err => {
    const motionState = currentMotion();
    motionState.latch = false;
    motionState.seenMoving = false;
    motionState.homingPending = false;
    motionState.homingPendingMethod = '';
    motionState.homingWasActive = false;
    motionState.homingSetCurrentValue = null;
    motionState.homingSetCurrentUnit = '';
    renderMotionToggle(false);
    console.error(err);
    openDiagModal(modeLabel('homing'), err.message || String(err));
  });
  return false;
}
async function startSinglePointMotion() {
  if (isAuxEncoderDevice(activeDevice)) return false;
  const motionState = currentMotion();
  if (motionState.stopRequested) return false;
  if (isMultiPointRunnerRunning() || motionState.latch || (currentStatus() && currentStatus().moving)) {
    return stopMotion();
  }
  if (modeSelect && modeSelect.value === 'anti_sway_position') {
    return startAntiSwayRun().catch(err => {
      console.error(err);
      openDiagModal(modeLabel('anti_sway_position'), err.message || String(err));
      return false;
    });
  }
  if (!modeSelect || modeSelect.value === 'position') {
    const confirmed = await openMotionConfirm(
      UI_TEXT[currentLang].positionExecutionConfirmTitle || modeLabel('position'),
      singlePointExecutionMessage(Number(absPos && absPos.value || 0), currentProfile())
    );
    if (!confirmed) return {ok:false, cancelled:true};
  }
  const commandSeq = ++motionState.commandSeq;
  motionState.stopRequested = false;
  motionState.latch = true;
  motionState.seenMoving = false;
  motionState.gearStoppedLatched = false;
  motionState.motionCancelNoticeKey = '';
  motionState.commandAt = Date.now();
  renderMotionToggle(true, isGearModeSelected() ? UI_TEXT[currentLang].gearing : '');
  try {
    if (isGearModeSelected()) {
      if (modeSelect && modeSelect.value !== 'gear_cam') {
        modeSelect.value = 'gear_cam';
      }
      syncModePanels('gear_cam');
      motionState.gearEngaged = false;
      setGearPanelLocked(false);
      const modeResult = await api({cmd:'set_mode', mode:'gear_cam'});
      if (!modeResult.ok) throw new Error(modeResult.error || 'set_mode gear_cam failed');
      if (commandSeq !== motionState.commandSeq || motionState.stopRequested) return false;
      const cfgResult = await api(gearPayload('gear_config'));
      if (!cfgResult.ok) throw new Error(cfgResult.error || 'gear_config failed');
      if (commandSeq !== motionState.commandSeq || motionState.stopRequested) return false;
      const startResult = await api(gearPayload('gear_start'));
      if (!startResult.ok) throw new Error(startResult.error || 'gear_start failed');
      motionState.gearEngaged = true;
      renderMotionToggle(true, UI_TEXT[currentLang].gearing);
      setGearPanelLocked(true);
      return startResult;
    }
    motionState.gearEngaged = false;
    setGearPanelLocked(false);
	    if (modeSelect && modeSelect.value === 'homing') {
	      const homingResult = await startHoming();
	      if (commandSeq !== motionState.commandSeq || motionState.stopRequested) return false;
	      if (homingResult && homingResult.cancelled) {
	        motionState.latch = false;
	        motionState.seenMoving = false;
	        renderMotionToggle(false);
	        return false;
	      }
	      if (!(homingResult && homingResult.ok)) throw new Error((homingResult && homingResult.error) || 'homing failed');
	      if (currentHoming().method !== 'torque_end') {
        motionState.latch = false;
        renderMotionToggle(false);
      }
      return homingResult;
    }
    if (modeSelect && modeSelect.value === 'multi_point') {
      const mp = currentMultiPoint();
      if (mp.editing) {
        throw new Error(UI_TEXT[currentLang].multiPointWrite);
      }
      const modeResult = await api({cmd:'set_mode', mode:'multi_point'});
      if (!modeResult.ok) throw new Error(modeResult.error || 'set_mode multi_point failed');
      if (commandSeq !== motionState.commandSeq || motionState.stopRequested) return false;
      const writeResult = await writeMultiPointTable(false);
      if (!writeResult.ok) throw new Error(writeResult.error || 'point_table_write failed');
      if (commandSeq !== motionState.commandSeq || motionState.stopRequested) return false;
      const runResult = await api({cmd:'point_table_run', cycle_count:mp.cycleCount});
      if (commandSeq !== motionState.commandSeq || motionState.stopRequested) return false;
      if (!runResult.ok) throw new Error(runResult.error || 'point_table_run failed');
      multiPointStatusByDevice[activeDevice] = runResult.point_table_runner || null;
      renderMultiPointRunner(multiPointStatusByDevice[activeDevice]);
      return runResult;
    }
    if (modeSelect && modeSelect.value === 'incremental') {
      syncIncrementalEditor(false);
      const curveProfile = currentIncrementalCommandProfile();
      if (!curveProfile || !curveProfile.command) {
        throw new Error('incremental profile editor is unavailable');
      }
      if (!curveProfile.valid) {
        openDiagModal(modeLabel('incremental'), (curveProfile.errors || []).join('\n') || 'incremental profile is not executable');
        throw new Error('incremental profile is not executable');
      }
      const modeResult = await api({cmd:'set_mode', mode:'incremental'});
      if (!modeResult.ok) throw new Error(modeResult.error || 'set_mode incremental failed');
      if (commandSeq !== motionState.commandSeq || motionState.stopRequested) return false;
      const moveResult = await api(curveProfile.command);
      if (commandSeq !== motionState.commandSeq || motionState.stopRequested) return false;
      if (!moveResult.ok) throw new Error(moveResult.error || 'move_curve_rel failed');
      return moveResult;
    }
    if (modeSelect && modeSelect.value !== 'position') {
      modeSelect.value = 'position';
      syncModePanels('position');
    }
    const modeResult = await api({cmd:'set_mode', mode:'position'});
    if (!modeResult.ok) throw new Error(modeResult.error || 'set_mode failed');
    if (commandSeq !== motionState.commandSeq || motionState.stopRequested) return false;
    if (commandSeq !== motionState.commandSeq || motionState.stopRequested) return false;
    const moveResult = await api(motionPayload(Number(absPos.value)));
    if (commandSeq !== motionState.commandSeq || motionState.stopRequested) return false;
    if (!moveResult.ok) throw new Error(moveResult.error || 'move_abs failed');
  } catch (err) {
    if (commandSeq !== motionState.commandSeq) return false;
    motionState.latch = false;
    motionState.seenMoving = false;
    motionState.gearEngaged = false;
    setGearPanelLocked(false);
    renderMotionToggle(false);
    console.error(err);
  }
}
function moveAbs() { return api(motionPayload(Number(absPos.value))); }
function returnZero() {
  if (isAuxEncoderDevice(activeDevice)) return Promise.resolve(false);
  absPos.value = 0;
  updateSliders();
  return api(motionPayload(0));
}
function moveRel() {
  if (isAuxEncoderDevice(activeDevice)) return Promise.resolve(false);
  return api(Object.assign({
    cmd:'move_rel',
    delta:Number(relDelta.value),
    move_ms:Number(moveMs.value),
    speed_rpm:Number(absSpeedRpm.value || 0),
    acceleration_rpm_s:Number(absAccel.value || 0)
  }, currentMotionBoundsPayload()));
}
function jogVelocity(v) {
  if (isAuxEncoderDevice(activeDevice)) return Promise.resolve(false);
  return api({cmd:'jog_velocity', velocity:v});
}
function sendTorque() {
  if (isAuxEncoderDevice(activeDevice)) return Promise.resolve(false);
  return api({cmd:'torque_cmd', torque:Number(torqueCmd.value)});
}
function savePoint(n) {
  if (isAuxEncoderDevice(activeDevice)) return false;
  if (currentStatus()) { currentProfile().points[n] = axisCounts(Number(currentStatus().pos)); updateSliders(); }
}
function gotoPoint(n) {
  if (isAuxEncoderDevice(activeDevice)) return Promise.resolve(false);
  absPos.value = currentProfile().points[n];
  updateSliders();
  return api(motionPayload(Number(absPos.value)));
}
function lockViewport() {
  document.addEventListener('gesturestart', e => e.preventDefault(), {passive:false});
  document.addEventListener('touchmove', e => {
    if (
	      !e.target.closest('input[type=range]') &&
	      !e.target.closest('.gear-wheel') &&
	      !e.target.closest('.touch-keypad') &&
	      !e.target.closest('.active-mode-card.incremental-scroll') &&
      !e.target.closest('.active-mode-card.multi-point-scroll') &&
      !e.target.closest('.active-mode-card.anti-sway-scroll')
    ) e.preventDefault();
  }, {passive:false});
  document.addEventListener('dblclick', e => e.preventDefault(), {passive:false});
}
function tryFullscreen() {
  if (mockFaultEnabled()) return;
  const el = document.documentElement;
  if (!document.fullscreenElement && el.requestFullscreen) el.requestFullscreen().catch(() => {});
}
syncModePanels('position');
lockViewport();
initTouchKeypad();
document.addEventListener('pointermove', updateAntiSwayTargetFromPointer, {passive:false});
document.addEventListener('pointerup', endAntiSwayTargetDrag);
document.addEventListener('pointercancel', endAntiSwayTargetDrag);
document.addEventListener('pointerdown', tryFullscreen, {once:true});
const langToggleBtn = document.getElementById('langToggleBtn');
const langDropdown = document.getElementById('langDropdown');
bindAxisSwitchButtons();
if (langToggleBtn) {
  langToggleBtn.addEventListener('click', toggleLanguageMenu);
}
document.addEventListener('pointerdown', event => {
  if (!langDropdown || !langToggleBtn) return;
  if (langDropdown.contains(event.target) || langToggleBtn.contains(event.target)) return;
  closeLanguageMenu();
});
if (modeSelect) {
  modeSelect.addEventListener('pointerdown', () => { currentModeUi().interacting = true; });
  modeSelect.addEventListener('focus', () => { currentModeUi().interacting = true; });
  modeSelect.addEventListener('blur', () => {
    const modeUi = currentModeUi();
    clearTimeout(modeSelectBlurTimer);
    modeSelectBlurTimer = setTimeout(() => {
      modeUi.interacting = false;
      applyGearModeAvailability(activeDevice);
      syncModeSelectDisabled(activeDevice);
      const selected = modeSelect.value;
      if (selected && modeIsAssembled(selected) && selected !== currentProfile().mode) {
        setMode();
        return;
      }
      const status = currentStatus();
      const hmiMode = status ? hmiModeFromStatus(activeDevice, status.control_mode) : '';
      syncModePanels((modeUi.pending || selected || hmiMode || currentProfile().mode || 'position'), true);
    }, 120);
  });
}
const warningChip = document.getElementById('warningChip');
if (warningChip) {
  warningChip.addEventListener('click', showWarningDetails);
  warningChip.addEventListener('keydown', e => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      showWarningDetails();
    }
  });
}
const featureChip = document.getElementById('featureChip');
if (featureChip) {
  featureChip.addEventListener('click', showFeatureDetails);
  featureChip.addEventListener('keydown', e => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      showFeatureDetails();
    }
  });
}
initApiTokenInput();
refreshMockFaultPanel();
window.addEventListener('popstate', syncMockFaultFromUrl);
document.addEventListener('keydown', e => {
  if (touchKeypadIsOpen()) {
    if (/^[0-9]$/.test(e.key)) {
      e.preventDefault();
      touchKeypadPress(e.key);
      return;
    }
    if (e.key === '.' || e.key === ',') {
      e.preventDefault();
      touchKeypadPress('.');
      return;
    }
    if (e.key === '-' || e.key === '+') {
      e.preventDefault();
      touchKeypadToggleSign();
      return;
    }
    if (e.key === 'Backspace') {
      e.preventDefault();
      touchKeypadBackspace();
      return;
    }
    if (e.key === 'Enter') {
      e.preventDefault();
      touchKeypadCommit();
      return;
    }
  }
  if (e.key === 'Escape') {
    closeTouchKeypad();
    resolveMotionConfirm(false);
    closeDiagModal();
    closeTransmissionDialog();
  }
});
async function bootstrapUi() {
  try {
    const res = await fetch('/api/capabilities');
    const data = await res.json();
    if (data && data.ok) {
      capabilityState.loaded = true;
      capabilityState.profile = data.profile || 'unknown';
      capabilityState.capabilities = new Set(Array.isArray(data.capabilities) ? data.capabilities : []);
      capabilityState.modeMap = (data.mode_capability_map && typeof data.mode_capability_map === 'object') ? data.mode_capability_map : {};
      capabilityState.modeHmiModuleMap = (data.mode_hmi_module_map && typeof data.mode_hmi_module_map === 'object') ? data.mode_hmi_module_map : {};
      capabilityState.activeFeatures = Array.isArray(data.active_features) ? data.active_features : [];
      capabilityState.enabledFeatureKeys = Array.isArray(data.enabled_feature_keys) ? data.enabled_feature_keys : [];
      capabilityState.featureAssembly = (data.feature_assembly && typeof data.feature_assembly === 'object') ? data.feature_assembly : {loaded:{}, skipped:{}};
      capabilityState.featureRegistrySource = data.feature_registry_source || '';
      capabilityState.warnings = Array.isArray(data.warnings) ? data.warnings : [];
      capabilityState.generatedAt = data.generated_at || '';
      const antiSwayExecution = (data.anti_sway_execution && typeof data.anti_sway_execution === 'object') ? data.anti_sway_execution : {};
      capabilityState.antiSwayExecution = {
        enabled:Boolean(antiSwayExecution.enabled),
        limitMode:String(antiSwayExecution.limit_mode || 'transmission_soft_limits'),
        strategy:String(antiSwayExecution.strategy || 'continuous_zvd_curve')
      };
    }
  } catch (err) {
    console.error(err);
  }
  refreshDeviceTabs();
  await hydrateUiStateFromServer();
  loadUiState('mctivity');
  applyCapabilityModeAvailability('mctivity');
  enforceGearConstraints();
  applyLanguage();
  setInterval(() => api({cmd:'status'}).catch(() => {}), 300);
  setInterval(() => {
    if ((modeSelect && modeSelect.value === 'multi_point') || isMultiPointRunnerRunning(activeDevice)) {
      refreshMultiPointStatus().catch(err => console.error(err));
    }
  }, 700);
  setInterval(() => {
    if (modeSelect && modeSelect.value === 'anti_sway_position') {
      refreshAntiSwaySensorStatus().catch(err => console.error(err));
    }
  }, 100);
  api({cmd:'status'}).catch(() => {});
}
bootstrapUi();
</script>
</body>
</html>
"""


def motiond_command(payload, port=MOTIOND_PORT):
    line = json.dumps(payload, separators=(",", ":")) + "\n"
    with socket.create_connection((MOTIOND_HOST, port), timeout=1.0) as sock:
        sock.sendall(line.encode("utf-8"))
        data = b""
        while not data.endswith(b"\n"):
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
    if not data:
        return {"ok": False, "error": "motion daemon returned no data"}
    return json.loads(data.decode("utf-8"))


def _default_ui_state():
    return {"devices": {}}


def _finite_float(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _normalize_ui_device_state(raw):
    if not isinstance(raw, dict):
        return None
    normalized = {}
    numeric_fields = (
        "absPos",
        "absSpeedRpm",
        "absAccel",
        "relDelta",
        "moveMs",
        "velCps",
        "torqueCmd",
        "gearMasterRatio",
        "gearSlaveRatio",
        "softZeroRaw",
    )
    for field in numeric_fields:
        if field in raw:
            value = _finite_float(raw[field])
            if value is not None:
                normalized[field] = value
    if "mode" in raw and isinstance(raw["mode"], str):
        normalized["mode"] = raw["mode"]
    if "gearMaster" in raw and isinstance(raw["gearMaster"], str):
        normalized["gearMaster"] = raw["gearMaster"]
    anti_sway = raw.get("antiSway")
    if isinstance(anti_sway, dict):
        normalized_anti = {}
        sensor_axis = anti_sway.get("sensorAxis") or anti_sway.get("sensor_axis")
        algorithm = anti_sway.get("algorithm")
        if isinstance(sensor_axis, str):
            normalized_anti["sensorAxis"] = sensor_axis[:64]
        if isinstance(algorithm, str):
            normalized_anti["algorithm"] = algorithm[:64]
        for key in ("allowedAngle", "rodLength", "measuredPeriodS", "speedRpm", "accelRpmS"):
            if key in anti_sway:
                value = _finite_float(anti_sway[key])
                if value is not None:
                    normalized_anti[key] = value
        if normalized_anti:
            normalized["antiSway"] = normalized_anti
    points = raw.get("points")
    if isinstance(points, dict):
        normalized_points = {}
        for key, value in points.items():
            number = _finite_float(value)
            if number is not None:
                normalized_points[str(key)] = number
        if normalized_points:
            normalized["points"] = normalized_points
    tx = raw.get("transmission")
    if isinstance(tx, dict):
        normalized_tx = {}
        for key in ("type", "unit", "travelMode", "direction"):
            if key in tx and isinstance(tx[key], str):
                normalized_tx[key] = tx[key]
        for key in ("revs", "amount", "period", "forwardLimit", "reverseLimit"):
            if key in tx:
                value = _finite_float(tx[key])
                if value is not None:
                    normalized_tx[key] = value
        if normalized_tx:
            normalized["transmission"] = normalized_tx
    incremental_curve = raw.get("incrementalCurve")
    if isinstance(incremental_curve, dict):
        normalized_curve = {}
        mode = incremental_curve.get("mode")
        blend = incremental_curve.get("blend")
        if isinstance(mode, str):
            normalized_curve["mode"] = mode
        if isinstance(blend, str):
            normalized_curve["blend"] = blend
        for key in ("targetPosition", "targetSpeed", "accel", "decel", "dwell"):
            if key in incremental_curve:
                value = _finite_float(incremental_curve[key])
                if value is not None:
                    normalized_curve[key] = value
        if normalized_curve:
            normalized["incrementalCurve"] = normalized_curve
    homing = raw.get("homing")
    if isinstance(homing, dict):
        normalized_homing = {}
        method = homing.get("method")
        direction = homing.get("direction")
        if isinstance(method, str):
            normalized_homing["method"] = "torque_end" if method == "torque_end" else "set_current"
        if isinstance(direction, str):
            normalized_homing["direction"] = "forward" if direction == "forward" else "reverse"
        signature = homing.get("transmissionSignature")
        if isinstance(signature, str):
            normalized_homing["transmissionSignature"] = signature[:512]
        if "transmissionInvalidated" in homing:
            normalized_homing["transmissionInvalidated"] = bool(homing["transmissionInvalidated"])
        for key in ("setPosition", "backoffDistance", "speed", "torqueThreshold", "maxDistance"):
            if key in homing:
                value = _finite_float(homing[key])
                if value is not None:
                    normalized_homing[key] = value
        if normalized_homing:
            normalized["homing"] = normalized_homing
    multi_point = raw.get("multiPoint")
    if isinstance(multi_point, dict):
        normalized_multi = {}
        for key in ("start", "step"):
            if key in multi_point:
                value = _finite_float(multi_point[key])
                if value is not None:
                    normalized_multi[key] = int(value)
        cycle_count = _finite_float(multi_point.get("cycleCount"))
        if cycle_count is not None:
            normalized_multi["cycleCount"] = max(1, min(MAX_POINT_TABLE_CYCLES, int(cycle_count)))
        else:
            loop_mode = multi_point.get("loopMode")
            if isinstance(loop_mode, str) and loop_mode in ("single", "cycle"):
                normalized_multi["cycleCount"] = MAX_POINT_TABLE_CYCLES if loop_mode == "cycle" else 1
        if "editing" in multi_point:
            normalized_multi["editing"] = bool(multi_point["editing"])
        rows = multi_point.get("rows")
        if isinstance(rows, list):
            normalized_rows = []
            for item in rows[:MAX_POINT_TABLE_ROWS]:
                if not isinstance(item, dict):
                    continue
                row = {}
                for key in ("row", "position", "speed", "acceleration", "dwell"):
                    if key in item:
                        value = _finite_float(item[key])
                        if value is not None:
                            row[key] = value
                if "enabled" in item:
                    row["enabled"] = bool(item["enabled"])
                if row:
                    normalized_rows.append(row)
            if normalized_rows:
                normalized_multi["rows"] = normalized_rows
        if normalized_multi:
            normalized["multiPoint"] = normalized_multi
    return normalized


def load_ui_state():
    with _ui_state_lock:
        if not os.path.exists(UI_STATE_PATH):
            return _default_ui_state()
        try:
            with open(UI_STATE_PATH, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            return _default_ui_state()
        if not isinstance(data, dict):
            return _default_ui_state()
        devices = data.get("devices")
        if not isinstance(devices, dict):
            return _default_ui_state()
        normalized = {"devices": {}}
        for device in ("mctivity", "fv3", "aux_encoder"):
            if device not in devices:
                continue
            device_state = _normalize_ui_device_state(devices[device])
            if device_state:
                normalized["devices"][device] = device_state
        return normalized


def save_ui_state(
    device,
    state,
    allow_anti_sway_period_update=False,
    allow_anti_sway_settings_update=False,
):
    normalized_state = _normalize_ui_device_state(state)
    if device not in ("mctivity", "fv3", "aux_encoder") or normalized_state is None:
        raise ValueError("invalid ui state payload")
    with _ui_state_lock:
        merged = load_ui_state()
        existing_state = merged.get("devices", {}).get(device, {})
        existing_anti = existing_state.get("antiSway", {}) if isinstance(existing_state, dict) else {}
        incoming_anti = normalized_state.get("antiSway", {})
        merged_anti = dict(existing_anti) if isinstance(existing_anti, dict) else {}
        if isinstance(incoming_anti, dict):
            merged_anti.update(incoming_anti)
        if not allow_anti_sway_settings_update:
            for key in ("sensorAxis", "algorithm", "allowedAngle", "rodLength"):
                if key in existing_anti:
                    merged_anti[key] = existing_anti[key]
                else:
                    merged_anti.pop(key, None)
        if not allow_anti_sway_period_update:
            existing_period = _finite_float(existing_anti.get("measuredPeriodS")) if isinstance(existing_anti, dict) else None
            if existing_period is not None:
                merged_anti["measuredPeriodS"] = existing_period
            else:
                merged_anti.pop("measuredPeriodS", None)
        if merged_anti:
            normalized_state["antiSway"] = merged_anti
        merged["devices"][device] = normalized_state
        directory = os.path.dirname(UI_STATE_PATH)
        if directory:
            os.makedirs(directory, exist_ok=True)
        tmp_path = UI_STATE_PATH + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as fh:
            json.dump(merged, fh, ensure_ascii=False, indent=2, allow_nan=False)
        os.replace(tmp_path, UI_STATE_PATH)


_fv3_cache = None
_fv3_cache_ts = 0.0
_fv3_cycles = 0
_fv3_lock = threading.RLock()
_fv3_soft_zero_raw = 0
_fv3_last_command = "status"
_fv3_message = "FV3 monitor ready"


def _run_text(cmd):
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=1.5, check=False)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip() or "command failed")
    return result.stdout


def _read_sdo_int(position, data_type, index_hex, subindex=0):
    text = _run_text(
        [
            "ethercat",
            "upload",
            "-p",
            str(position),
            "--type",
            data_type,
            index_hex,
            str(subindex),
        ]
    )
    match = re.search(r"(-?\d+)\s*$", text.strip())
    if not match:
        raise RuntimeError(f"unexpected upload output: {text.strip()}")
    return int(match.group(1))


def _read_fv3_state():
    text = _run_text(["ethercat", "slave", "-p", str(FV3_SLAVE_POSITION)])
    match = re.search(r"State:\s+([A-Z]+)", text)
    if not match:
        match = re.search(r"\b(INIT|PREOP|SAFEOP|OP)\b", text)
    if not match:
        return "UNKNOWN"
    return match.group(1)


def _write_sdo(position, data_type, index_hex, value, subindex=0):
    _run_text(
        [
            "ethercat",
            "download",
            "-p",
            str(position),
            "--type",
            data_type,
            index_hex,
            str(subindex),
            "--",
            str(int(value)),
        ]
    )


def _invalidate_fv3_cache():
    global _fv3_cache_ts
    _fv3_cache_ts = 0.0


def _rpm_to_counts_s(rpm):
    return max(1, int(abs(float(rpm)) * 8388608.0 / 60.0))


def _rpm_s_to_counts_s2(rpm_s):
    return max(1, int(abs(float(rpm_s)) * 8388608.0 / 60.0))


def _mode_name(mode):
    mapping = {
        1: "position",
        3: "velocity",
        4: "torque",
        6: "homing",
        8: "position",
        9: "velocity",
        10: "torque",
    }
    return mapping.get(mode, "position")


def _fv3_mode_code_from_name(mode_name):
    name = str(mode_name or "").strip().lower()
    if name in ("position", "incremental", "jog", "point", "multi_point"):
        # FV3 single-axis motion uses PP trigger sequence.
        return 1
    if name == "gear_cam":
        # Electronic gearing stays in CSP.
        return 8
    if name == "homing":
        # mctivity homing is controller-managed and does not use CiA402 native homing mode.
        return 8
    if name == "velocity":
        return 9
    if name == "torque":
        return 10
    return 8


_VALID_FV3_MODES = {
    "position",
    "incremental",
    "jog",
    "point",
    "multi_point",
    "homing",
    "velocity",
    "torque",
    "gear_cam",
}


def fv3_status():
    return motiond_command({"cmd": "status", "device": "fv3"})


def aux_encoder_status():
    return motiond_command({"cmd": "status", "device": "aux_encoder"})


def _apply_fv3_profile_from_payload(payload):
    speed_rpm = int(float(payload.get("speed_rpm", 0) or 0))
    accel_rpm_s = int(float(payload.get("acceleration_rpm_s", 0) or 0))
    if speed_rpm > 0:
        _write_sdo(FV3_SLAVE_POSITION, "uint32", "0x6081", _rpm_to_counts_s(speed_rpm))
    if accel_rpm_s > 0:
        accel = _rpm_s_to_counts_s2(accel_rpm_s)
        _write_sdo(FV3_SLAVE_POSITION, "uint32", "0x6083", accel)
        _write_sdo(FV3_SLAVE_POSITION, "uint32", "0x6084", accel)


def fv3_command(payload):
    payload2 = dict(payload)
    cmd = str(payload2.get("cmd", "status")).strip().lower()
    if cmd == "set_mode":
        mode_name = str(payload2.get("mode", "position")).strip().lower()
        if mode_name not in _VALID_FV3_MODES:
            return {"ok": False, "error": "unsupported_fv3_mode", "mode": mode_name}
    clean = {"cmd": cmd, "device": "fv3"}
    for key, value in payload2.items():
        if key not in ("cmd", "device"):
            clean[key] = value
    return motiond_command(clean)


def _transport_command(device, payload):
    """
    Transport layer for axis commands.
    Keeps protocol behavior unchanged while dispatching by logical feature.
    """
    if device not in ("mctivity", "fv3"):
        return {"ok": False, "error": "unsupported_device"}
    payload2 = dict(payload)
    payload2.pop("device", None)
    if device == "fv3":
        return fv3_command(payload2)
    return motiond_command(payload2)


def _wait_motion_ready(device, timeout_sec=3.0, poll_sec=0.05):
    end_ts = time.time() + max(0.1, float(timeout_sec))
    last_status = {}
    while time.time() <= end_ts:
        rsp = _transport_command(device, {"cmd": "status"})
        last_status = rsp.get("status", {}) if isinstance(rsp, dict) else {}
        if rsp.get("ok") and not last_status.get("fault"):
            if last_status.get("enabled") and int(last_status.get("settle_cycles") or 0) == 0:
                return True, None
        time.sleep(max(0.01, float(poll_sec)))
    enabled = bool(last_status.get("enabled"))
    fault = bool(last_status.get("fault"))
    settle = int(last_status.get("settle_cycles") or 0)
    return False, f"servo is not ready for motion (enabled={enabled}, fault={fault}, settle_cycles={settle})"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def send_json(self, obj, status=200):
        try:
            body = json.dumps(obj, ensure_ascii=False, allow_nan=False).encode("utf-8")
        except (TypeError, ValueError):
            status = 500
            body = json.dumps({"ok": False, "error": "invalid_json_response"}, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _is_authorized(self):
        if not API_TOKEN:
            return is_loopback_host(WEB_HOST) and is_loopback_host(self.client_address[0])
        header_token = self.headers.get("X-MCTIVITY-Token", "").strip()
        if header_token and hmac.compare_digest(header_token.encode("utf-8"), API_TOKEN.encode("utf-8")):
            return True
        auth = self.headers.get("Authorization", "").strip()
        return bool(auth) and hmac.compare_digest(auth.encode("utf-8"), f"Bearer {API_TOKEN}".encode("utf-8"))

    def _require_authorized(self):
        if self._is_authorized():
            return True
        self.send_json({"ok": False, "error": "unauthorized"}, 401)
        return False

    def _require_json_content_type(self):
        ctype = self.headers.get("Content-Type", "")
        media_type = ctype.split(";", 1)[0].strip().lower()
        if media_type != "application/json":
            self.send_json({"ok": False, "error": "unsupported_media_type"}, 415)
            return False
        return True

    def _require_allowed_host(self):
        host = self.headers.get("Host", "").strip()
        name = _split_host_header(host)
        if not name or name not in _allowed_host_names():
            self.send_json({"ok": False, "error": "forbidden_host"}, 403)
            return False
        return True

    def _require_same_origin(self):
        origin = self.headers.get("Origin", "").strip()
        if not origin:
            return True
        host = self.headers.get("Host", "").strip()
        if not host:
            self.send_json({"ok": False, "error": "forbidden_origin"}, 403)
            return False
        host_name = _split_host_header(host)
        port = _port_from_host_header(host)
        allowed = {f"http://{host}".rstrip("/")}
        if host_name in ("127.0.0.1", "localhost", "::1"):
            allowed.add(f"http://127.0.0.1:{port}")
            allowed.add(f"http://localhost:{port}")
            allowed.add(f"http://[::1]:{port}")
        if origin.rstrip("/") not in allowed:
            self.send_json({"ok": False, "error": "forbidden_origin"}, 403)
            return False
        return True

    def _read_json_body(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_json({"ok": False, "error": "invalid_content_length"}, 400)
            return None
        if length < 0:
            self.send_json({"ok": False, "error": "invalid_content_length"}, 400)
            return None
        if length > MAX_REQUEST_BYTES:
            self.send_json({"ok": False, "error": "payload_too_large", "max_bytes": MAX_REQUEST_BYTES}, 413)
            return None
        body = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(body.decode("utf-8"))
        except Exception:
            self.send_json({"ok": False, "error": "invalid_json"}, 400)
            return None
        if not isinstance(payload, dict):
            self.send_json({"ok": False, "error": "invalid_json_object"}, 400)
            return None
        return payload

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        device = (query.get("device", ["mctivity"])[0] or "mctivity").lower()
        if not self._require_allowed_host():
            return
        if path == "/" or path == "/index.html":
            try:
                curve_block = MOTION_CURVE_EDITOR_ASSET_PATH.read_text(encoding="utf-8")
            except Exception:
                curve_block = ""
            body = (
                HTML.replace("__MOTION_CURVE_EDITOR_BLOCK__", curve_block)
                .replace("__MAX_HOMING_SEARCH_COUNTS__", str(MAX_HOMING_SEARCH_COUNTS))
                .replace("__MAX_SPEED_RPM__", str(MAX_SPEED_RPM))
                .replace("__MAX_ACCEL_RPM_S__", str(MAX_ACCEL_RPM_S))
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/assets/motion-curve-editor.js":
            try:
                body = MOTION_CURVE_EDITOR_ASSET_PATH.read_bytes()
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, 503)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/assets/logo.png":
            try:
                body = (ASSETS_ROOT / "logo.png").read_bytes()
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, 503)
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path.startswith("/assets/"):
            try:
                asset_root = ASSETS_ROOT.resolve()
                asset_path = (ASSETS_ROOT / path[len("/assets/") :].lstrip("/")).resolve()
                if asset_root not in asset_path.parents and asset_path != asset_root:
                    self.send_error(403)
                    return
                body = asset_path.read_bytes()
            except FileNotFoundError:
                self.send_error(404)
                return
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, 503)
                return
            content_type = mimetypes.guess_type(str(asset_path))[0] or "application/octet-stream"
            if asset_path.suffix == ".js":
                content_type = "application/javascript; charset=utf-8"
            elif asset_path.suffix == ".css":
                content_type = "text/css; charset=utf-8"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/api/status":
            if not self._require_authorized():
                return
            device = _normalize_device(device)
            if device is None:
                self.send_json({"ok": False, "error": "unsupported_device"}, 400)
                return
            try:
                if device == "fv3":
                    self.send_json(fv3_status())
                elif device == "aux_encoder":
                    self.send_json(aux_encoder_status())
                else:
                    self.send_json(motiond_command({"cmd": "status"}))
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, 503)
        elif path == "/api/capabilities":
            self.send_json(capability_manifest())
        elif path == "/api/health/modular":
            manifest = capability_manifest()
            warnings = manifest.get("warnings", [])
            self.send_json(
                {
                    "ok": True,
                    "status": "healthy" if not warnings else "degraded",
                    "profile": manifest.get("profile"),
                    "feature_count": len(manifest.get("active_features", [])),
                    "capability_count": len(manifest.get("capabilities", [])),
                    "warning_count": len(warnings),
                    "warnings": warnings,
                    "generated_at": manifest.get("generated_at"),
                }
            )
        elif path == "/api/ui_state":
            if not self._require_authorized():
                return
            try:
                self.send_json({"ok": True, "state": load_ui_state()})
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, 503)
        else:
            self.send_error(404)

    def do_POST(self):
        path = urlparse(self.path).path
        if path not in ("/api/command", "/api/ui_state"):
            self.send_error(404)
            return
        if not self._require_allowed_host():
            return
        if not self._require_same_origin():
            return
        if not self._require_json_content_type():
            return
        if not self._require_authorized():
            return
        payload = self._read_json_body()
        if payload is None:
            return
        try:
            if path == "/api/ui_state":
                device = _normalize_device(payload.get("device", "mctivity"))
                if device is None:
                    self.send_json({"ok": False, "error": "unsupported_device"}, 400)
                    return
                state = payload.get("state", {})
                save_ui_state(
                    device,
                    state,
                    allow_anti_sway_period_update=bool(payload.get("update_anti_sway_period")),
                    allow_anti_sway_settings_update=bool(payload.get("update_anti_sway_settings")),
                )
                self.send_json({"ok": True, "state": load_ui_state()})
            else:
                cmd = _normalize_command_name(payload)
                if cmd is None:
                    self.send_json({"ok": False, "error": "unsupported_command"}, 400)
                    return
                if cmd == "set_mode":
                    mode_name = str(payload.get("mode", "")).strip().lower()
                    if mode_name not in _VALID_MODES:
                        self.send_json({"ok": False, "error": "unsupported_mode", "mode": mode_name}, 400)
                        return
                device = _normalize_device(payload.get("device", "mctivity"))
                if device is None:
                    self.send_json({"ok": False, "error": "unsupported_device"}, 400)
                    return
                if device == "aux_encoder":
                    if cmd == "status":
                        self.send_json(aux_encoder_status())
                    else:
                        self.send_json({"ok": False, "error": "read_only_device"}, 400)
                    return
                raw_payload = payload
                payload = _sanitize_command_payload(payload, device)
                if payload is None:
                    detail = _sanitize_rejection_detail(raw_payload, device)
                    self.send_json({"ok": False, "error": "unsupported_command", **detail}, 400)
                    return
                enabled, required = _command_is_enabled(payload)
                if not enabled:
                    self.send_json(
                        {
                            "ok": False,
                            "error": "unsupported_command",
                            "required_capability": required,
                            "capabilities": sorted(_CAPABILITY_SET),
                        },
                        400,
                    )
                    return
                status = feature_dispatch_axis_command(
                    device,
                    payload,
                    _transport_command,
                    adapter=ProtocolAdapter(
                        wait_motion_ready_fn=_wait_motion_ready,
                        apply_fv3_profile_fn=_apply_fv3_profile_from_payload,
                        fv3_set_mode_fn=lambda mode_name: _write_sdo(
                            FV3_SLAVE_POSITION,
                            "int8",
                            "0x6060",
                            _fv3_mode_code_from_name(mode_name),
                        ),
                        fv3_force_csp_fn=lambda: _write_sdo(FV3_SLAVE_POSITION, "int8", "0x6060", 8),
                    ),
                    enabled_feature_keys=_ENABLED_FEATURE_KEYS,
                )
                self.send_json(status, 400 if not status.get("ok") else 200)
        except Exception as exc:
            self.send_json({"ok": False, "error": str(exc)}, 503)


if __name__ == "__main__":
    validate_web_access(WEB_HOST, API_TOKEN)
    server = ThreadingHTTPServer((WEB_HOST, WEB_PORT), Handler)
    print(f"Motion HMI listening on http://{WEB_HOST}:{WEB_PORT}")
    server.serve_forever()
