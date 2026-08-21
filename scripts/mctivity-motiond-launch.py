#!/usr/bin/env python3
"""Load the selected module profile, then exec mctivity_motiond."""

import argparse
import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "mctivity_hmi"))

from profile_runtime import ProfileRuntimeError, build_module_runtime  # noqa: E402


def resolve_launch_environment(profile_name=None, profile_path=None, modules_root=None, environ=None):
    source_env = dict(os.environ if environ is None else environ)
    profile_name = profile_name or source_env.get("MCTIVITY_PROFILE", "standard")
    profile_path = Path(
        profile_path
        or source_env.get("MCTIVITY_PROFILE_PATH", ROOT / "profiles" / f"{profile_name}.json")
    )
    modules_root = Path(modules_root or source_env.get("MCTIVITY_MODULES_ROOT", ROOT / "modules"))
    runtime = build_module_runtime(profile_path, modules_root, strict=True)
    if runtime["profile"] != profile_name:
        raise ProfileRuntimeError(
            f"profile name mismatch: selected {profile_name!r}, file declares {runtime['profile']!r}"
        )
    axis_devices = runtime.get("axis_devices", [])
    if len(axis_devices) > 2:
        raise ProfileRuntimeError("motiond launcher supports at most two assembled axis_devices")

    launch_env = dict(source_env)
    device = axis_devices[0] if axis_devices else None
    expected_topology = str(device.get("topology")) if device else "legacy-dual"
    if expected_topology not in {
        "legacy-dual",
        "axis-d-uservo",
        "axis-d-uservo-pv",
        "axis-de-uservo-pv",
        "axis-de-uservo-gear",
    }:
        raise ProfileRuntimeError(f"unsupported motiond topology: {expected_topology!r}")
    if any(str(item.get("topology")) != expected_topology for item in axis_devices):
        raise ProfileRuntimeError("all assembled axes must declare the same topology")
    selected_topology = source_env.get("MCTIVITY_TOPOLOGY", expected_topology)
    if selected_topology != expected_topology:
        raise ProfileRuntimeError(
            f"profile/topology mismatch: profile requires {expected_topology!r}, got {selected_topology!r}"
        )
    launch_env["MCTIVITY_PROFILE"] = profile_name
    launch_env["MCTIVITY_TOPOLOGY"] = expected_topology

    if device:
        launch_env["MCTIVITY_AXIS_COUNTS_PER_REV"] = str(device["counts_per_rev"])
        launch_env.setdefault(
            "MCTIVITY_COMMISSIONING_INHIBIT",
            "1" if device.get("commissioning_inhibit_default", False) else "0",
        )
        launch_env.setdefault("MCTIVITY_REQUIRE_REALTIME", "1")
    if expected_topology in {"axis-d-uservo-pv", "axis-de-uservo-pv"}:
        expected_contract = {
            "vendor_id": "0x00666999",
            "product_code": "0x00004806",
            "revision": "0x00000001",
            "cycle_ns": 1_000_000,
            "rxpdo_profile": "0x1601",
            "txpdo_profile": "0x1A01",
            "rxpdo": ["0x6040:00/16", "0x6060:00/8", "0x60ff:00/32", "0x60fe:01/32"],
            "txpdo": ["0x6041:00/16", "0x6061:00/8", "0x606c:00/32", "0x60fd:00/32"],
        }
        for item in axis_devices:
            for key, expected in expected_contract.items():
                if item.get(key) != expected:
                    raise ProfileRuntimeError(
                        f"Uservo PV runtime contract mismatch for {key}: expected {expected!r}, got {item.get(key)!r}"
                    )
            if item.get("ethercat_mode") != "pv":
                raise ProfileRuntimeError("Uservo PV profile must declare native PV mode")
            if int(item.get("ethercat_mode_code", 0)) != 3:
                raise ProfileRuntimeError("Uservo PV profile must use CiA 402 mode 3")
            if int(item["default_decel_rpm_s"]) != int(item["stop_decel_rpm_s"]):
                raise ProfileRuntimeError(
                    "Uservo PV uses 0x6084 for both deceleration and stop; profile values must match"
                )
        if expected_topology == "axis-d-uservo-pv":
            expected_instances = [("D", "mctivity", 0)]
        else:
            expected_instances = [("D", "mctivity", 0), ("E", "mctivity_e", 1)]
        actual_instances = [
            (str(item.get("logical_axis")), str(item.get("transport_device")), int(item.get("physical_position", -1)))
            for item in axis_devices
        ]
        if actual_instances != expected_instances:
            raise ProfileRuntimeError(
                f"Uservo PV axis instances mismatch: expected {expected_instances!r}, got {actual_instances!r}"
            )
        if expected_topology == "axis-d-uservo-pv":
            launch_env.update(
                {
                    "MCTIVITY_PV_TARGET_SPEED_RPM": str(device["default_speed_rpm"]),
                    "MCTIVITY_PV_MAX_SPEED_RPM": str(device["max_speed_rpm"]),
                    "MCTIVITY_PV_ACCEL_RPM_S": str(device["default_accel_rpm_s"]),
                    "MCTIVITY_PV_DECEL_RPM_S": str(device["default_decel_rpm_s"]),
                    "MCTIVITY_PV_STOP_DECEL_RPM_S": str(device["stop_decel_rpm_s"]),
                }
            )
        else:
            launch_env["MCTIVITY_USERVO_AXIS_COUNT"] = "2"
            for item in axis_devices:
                axis_name = str(item["logical_axis"]).upper()
                prefix = f"MCTIVITY_AXIS_{axis_name}"
                launch_env.update(
                    {
                        f"{prefix}_COUNTS_PER_REV": str(item["counts_per_rev"]),
                        f"{prefix}_PV_TARGET_SPEED_RPM": str(item["default_speed_rpm"]),
                        f"{prefix}_PV_MAX_SPEED_RPM": str(item["max_speed_rpm"]),
                        f"{prefix}_PV_ACCEL_RPM_S": str(item["default_accel_rpm_s"]),
                        f"{prefix}_PV_DECEL_RPM_S": str(item["default_decel_rpm_s"]),
                        f"{prefix}_PV_STOP_DECEL_RPM_S": str(item["stop_decel_rpm_s"]),
                    }
                )
    if expected_topology == "axis-de-uservo-gear":
        expected_contract = {
            "vendor_id": "0x00666999",
            "product_code": "0x00004806",
            "revision": "0x00000001",
            "cycle_ns": 1_000_000,
            "rxpdo_profile": "0x1600",
            "txpdo_profile": "0x1A00",
            "rxpdo": ["0x6040:00/16", "0x6060:00/8", "0x607a:00/32", "0x60fe:01/32"],
            "txpdo": ["0x6041:00/16", "0x6061:00/8", "0x6064:00/32", "0x60fd:00/32"],
        }
        for item in axis_devices:
            for key, expected in expected_contract.items():
                if item.get(key) != expected:
                    raise ProfileRuntimeError(
                        f"Uservo CSP runtime contract mismatch for {key}: expected {expected!r}, got {item.get(key)!r}"
                    )
            if item.get("ethercat_mode") != "csp" or int(item.get("ethercat_mode_code", 0)) != 8:
                raise ProfileRuntimeError("Uservo gear profile must declare CiA 402 CSP mode code 8")
            if int(item.get("gear_following_error_limit_counts", 0)) != 200:
                raise ProfileRuntimeError("Uservo gear profile must use a 200-count default following-error limit")
            if int(item.get("gear_max_ratio", 0)) != 200:
                raise ProfileRuntimeError("Uservo gear profile must use a 200:1 maximum ratio")
        expected_instances = [("D", "mctivity", 0), ("E", "mctivity_e", 1)]
        actual_instances = [
            (str(item.get("logical_axis")), str(item.get("transport_device")), int(item.get("physical_position", -1)))
            for item in axis_devices
        ]
        if actual_instances != expected_instances:
            raise ProfileRuntimeError(
                f"Uservo gear axis instances mismatch: expected {expected_instances!r}, got {actual_instances!r}"
            )
        launch_env["MCTIVITY_USERVO_AXIS_COUNT"] = "2"
        launch_env["MCTIVITY_GEAR_FOLLOWING_ERROR_LIMIT_COUNTS"] = str(
            device["gear_following_error_limit_counts"]
        )
        launch_env["MCTIVITY_GEAR_MAX_RATIO"] = str(device["gear_max_ratio"])
        for item in axis_devices:
            axis_name = str(item["logical_axis"]).upper()
            launch_env[f"MCTIVITY_AXIS_{axis_name}_COUNTS_PER_REV"] = str(item["counts_per_rev"])
            launch_env[f"MCTIVITY_AXIS_{axis_name}_MAX_SPEED_RPM"] = str(item["max_speed_rpm"])
    return runtime, device, launch_env


def public_dump(runtime, device, launch_env):
    keys = [
        "MCTIVITY_PROFILE",
        "MCTIVITY_TOPOLOGY",
        "MCTIVITY_COMMISSIONING_INHIBIT",
        "MCTIVITY_REQUIRE_REALTIME",
        "MCTIVITY_AXIS_COUNTS_PER_REV",
        "MCTIVITY_PV_TARGET_SPEED_RPM",
        "MCTIVITY_PV_MAX_SPEED_RPM",
        "MCTIVITY_PV_ACCEL_RPM_S",
        "MCTIVITY_PV_DECEL_RPM_S",
        "MCTIVITY_PV_STOP_DECEL_RPM_S",
        "MCTIVITY_USERVO_AXIS_COUNT",
        "MCTIVITY_GEAR_FOLLOWING_ERROR_LIMIT_COUNTS",
        "MCTIVITY_GEAR_MAX_RATIO",
    ]
    for axis_name in ("D", "E"):
        keys.extend(
            [
                f"MCTIVITY_AXIS_{axis_name}_COUNTS_PER_REV",
                f"MCTIVITY_AXIS_{axis_name}_PV_TARGET_SPEED_RPM",
                f"MCTIVITY_AXIS_{axis_name}_PV_MAX_SPEED_RPM",
                f"MCTIVITY_AXIS_{axis_name}_PV_ACCEL_RPM_S",
                f"MCTIVITY_AXIS_{axis_name}_PV_DECEL_RPM_S",
                f"MCTIVITY_AXIS_{axis_name}_PV_STOP_DECEL_RPM_S",
                f"MCTIVITY_AXIS_{axis_name}_COUNTS_PER_REV",
                f"MCTIVITY_AXIS_{axis_name}_MAX_SPEED_RPM",
            ]
        )
    return {
        "profile": runtime["profile"],
        "device": device,
        "devices": runtime.get("axis_devices", []),
        "environment": {key: launch_env[key] for key in keys if key in launch_env},
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile")
    parser.add_argument("--profile-path")
    parser.add_argument("--modules-root")
    parser.add_argument("--dump", action="store_true", help="print resolved non-secret parameters and exit")
    parser.add_argument("binary", nargs="?", default=str(ROOT / "mctivity_pdo_monitor" / "mctivity_motiond"))
    args = parser.parse_args()

    try:
        runtime, device, launch_env = resolve_launch_environment(
            profile_name=args.profile,
            profile_path=args.profile_path,
            modules_root=args.modules_root,
        )
    except Exception as exc:
        print(f"motiond profile resolution failed: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc

    if args.dump:
        print(json.dumps(public_dump(runtime, device, launch_env), sort_keys=True))
        return
    os.execvpe(args.binary, [args.binary], launch_env)


if __name__ == "__main__":
    main()
