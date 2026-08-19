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
    if len(axis_devices) > 1:
        raise ProfileRuntimeError("motiond launcher supports at most one assembled axis_device")

    launch_env = dict(source_env)
    device = axis_devices[0] if axis_devices else None
    expected_topology = str(device.get("topology")) if device else "legacy-dual"
    if expected_topology not in {"legacy-dual", "axis-d-uservo", "axis-d-uservo-pv"}:
        raise ProfileRuntimeError(f"unsupported motiond topology: {expected_topology!r}")
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
    if expected_topology == "axis-d-uservo-pv":
        expected_contract = {
            "topology": "axis-d-uservo-pv",
            "vendor_id": "0x00666999",
            "product_code": "0x00004806",
            "revision": "0x00000001",
            "physical_position": 0,
            "cycle_ns": 1_000_000,
            "rxpdo_profile": "0x1601",
            "txpdo_profile": "0x1A01",
            "rxpdo": ["0x6040:00/16", "0x6060:00/8", "0x60ff:00/32", "0x60fe:01/32"],
            "txpdo": ["0x6041:00/16", "0x6061:00/8", "0x606c:00/32", "0x60fd:00/32"],
        }
        for key, expected in expected_contract.items():
            if device.get(key) != expected:
                raise ProfileRuntimeError(
                    f"Uservo PV runtime contract mismatch for {key}: expected {expected!r}, got {device.get(key)!r}"
                )
        if device.get("ethercat_mode") != "pv":
            raise ProfileRuntimeError("Uservo PV profile must declare native PV mode")
        if int(device.get("ethercat_mode_code", 0)) != 3:
            raise ProfileRuntimeError("Uservo PV profile must use CiA 402 mode 3")
        if int(device["default_decel_rpm_s"]) != int(device["stop_decel_rpm_s"]):
            raise ProfileRuntimeError(
                "Uservo PV uses 0x6084 for both deceleration and stop; profile values must match"
            )
        launch_env.update(
            {
                "MCTIVITY_PV_TARGET_SPEED_RPM": str(device["default_speed_rpm"]),
                "MCTIVITY_PV_MAX_SPEED_RPM": str(device["max_speed_rpm"]),
                "MCTIVITY_PV_ACCEL_RPM_S": str(device["default_accel_rpm_s"]),
                "MCTIVITY_PV_DECEL_RPM_S": str(device["default_decel_rpm_s"]),
                "MCTIVITY_PV_STOP_DECEL_RPM_S": str(device["stop_decel_rpm_s"]),
            }
        )
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
    ]
    return {
        "profile": runtime["profile"],
        "device": device,
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
