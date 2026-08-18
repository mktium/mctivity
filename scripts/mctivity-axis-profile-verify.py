#!/usr/bin/env python3
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
PROFILES = ROOT / "profiles"
MODULES = ROOT / "modules"
REQUIRED_DEVICE_FIELDS = {
    "logical_axis",
    "transport_device",
    "topology",
    "vendor_id",
    "product_code",
    "physical_position",
    "cycle_ns",
    "counts_per_rev",
    "max_position_revolutions",
    "default_relative_revolutions",
    "position_step_counts",
    "default_speed_rpm",
    "max_speed_rpm",
    "default_velocity_counts_s",
    "max_velocity_counts_s",
    "default_accel_rpm_s",
    "max_accel_rpm_s",
    "commissioning_inhibit_default",
    "rxpdo",
    "txpdo",
}


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def manifest_path(module_id):
    return MODULES / module_id.replace("-", "/") / "module.json"


def main():
    checked = 0
    for profile_path in sorted(PROFILES.glob("*.json")):
        profile = load_json(profile_path)
        axis_devices = []
        for module_id in profile.get("modules", []):
            path = manifest_path(module_id)
            if not path.is_file():
                raise SystemExit(f"missing module manifest: {module_id}: {path}")
            manifest = load_json(path)
            if manifest.get("type") == "axis_device":
                axis_devices.append(manifest.get("device"))
        for device in axis_devices:
            if not isinstance(device, dict):
                raise SystemExit(f"invalid axis device in {profile_path.name}")
            missing = sorted(REQUIRED_DEVICE_FIELDS - set(device))
            if missing:
                raise SystemExit(f"axis device missing fields in {profile_path.name}: {','.join(missing)}")
            if int(device["counts_per_rev"]) <= 0 or int(device["cycle_ns"]) <= 0:
                raise SystemExit(f"axis device scale/cycle invalid in {profile_path.name}")
            if not device["rxpdo"] or not device["txpdo"]:
                raise SystemExit(f"axis device PDO list empty in {profile_path.name}")
            checked += 1
        if profile.get("profile") == "axis-d-uservo" and len(axis_devices) != 1:
            raise SystemExit("axis-d-uservo profile must contain exactly one axis device")
        if profile.get("profile") == "axis-d-uservo":
            modules = set(profile.get("modules", []))
            if {"feature-logic-velocity", "feature-hmi-velocity"} & modules:
                raise SystemExit("axis-d-uservo must not expose velocity mode without a 0x60ff target-velocity PDO")
            device = axis_devices[0]
            if not (0 < float(device["default_relative_revolutions"]) <= float(device["max_position_revolutions"])):
                raise SystemExit("axis-d-uservo relative default exceeds its position envelope")
            if not (0 < int(device["default_speed_rpm"]) <= int(device["max_speed_rpm"])):
                raise SystemExit("axis-d-uservo speed defaults invalid")
            if not (0 < int(device["default_velocity_counts_s"]) <= int(device["max_velocity_counts_s"])):
                raise SystemExit("axis-d-uservo velocity-count defaults invalid")
            if not (0 < int(device["default_accel_rpm_s"]) <= int(device["max_accel_rpm_s"])):
                raise SystemExit("axis-d-uservo acceleration defaults invalid")
            expected_identity = {
                "vendor_id": "0x00666999",
                "product_code": "0x00004806",
                "revision": "0x00000001",
                "cycle_ns": 1000000,
                "counts_per_rev": 10000,
                "commissioning_inhibit_default": True,
            }
            for key, expected in expected_identity.items():
                if device.get(key) != expected:
                    raise SystemExit(
                        f"axis-d-uservo official identity/timing mismatch for {key}: "
                        f"expected {expected!r}, got {device.get(key)!r}"
                    )
            expected_rxpdo = ["0x6040:00/16", "0x6060:00/8", "0x607a:00/32", "0x60fe:01/32"]
            expected_txpdo = ["0x6041:00/16", "0x6061:00/8", "0x6064:00/32", "0x60fd:00/32"]
            if device.get("rxpdo") != expected_rxpdo or device.get("txpdo") != expected_txpdo:
                raise SystemExit("axis-d-uservo PDOs do not match XActant-E-XML-6120R Uservo defaults")
    if checked < 1:
        raise SystemExit("no axis_device module found")
    print(f"axis profile ok: {checked} device profile(s)")


if __name__ == "__main__":
    main()
