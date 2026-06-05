#!/usr/bin/env python3
import json
import os

_DEFAULT_FEATURE_REGISTRY = {
    "position": {
        "logic_modules": {
            "feature-logic-single-point",
            "feature-logic-jog",
            "feature-logic-point",
            "feature-logic-homing",
        },
        "commands": {"move_abs", "move_rel"},
        "modes": {"position", "jog", "point", "homing"},
    },
    "incremental": {
        "logic_modules": {"feature-logic-incremental"},
        "commands": {"move_curve_rel"},
        "modes": {"incremental"},
    },
    "velocity": {
        "logic_modules": {"feature-logic-velocity"},
        "commands": {"jog_velocity"},
        "modes": {"velocity"},
    },
    "torque": {
        "logic_modules": {"feature-logic-torque"},
        "commands": {"torque_cmd"},
        "modes": {"torque"},
    },
    "gear_cam": {
        "logic_modules": {"feature-logic-electronic-gear"},
        "commands": {"gear_config", "gear_start", "gear_stop"},
        "modes": {"gear_cam"},
    },
}


def _normalize_feature_registry(raw, warnings):
    if not isinstance(raw, dict):
        warnings.append("feature_registry_invalid_root")
        return None
    normalized = {}
    command_owner = {}
    mode_owner = {}
    for key, spec in raw.items():
        if not isinstance(key, str) or not isinstance(spec, dict):
            warnings.append(f"feature_registry_invalid_entry:{key}")
            continue
        feature_key = str(key).strip()
        if not feature_key:
            warnings.append("feature_registry_empty_feature_key")
            continue
        logic_modules = set(str(x).strip() for x in spec.get("logic_modules", []) if isinstance(x, str) and str(x).strip())
        commands = set(str(x).strip().lower() for x in spec.get("commands", []) if isinstance(x, str) and str(x).strip())
        modes = set(str(x).strip().lower() for x in spec.get("modes", []) if isinstance(x, str) and str(x).strip())
        normalized[feature_key] = {
            "logic_modules": logic_modules,
            "commands": commands,
            "modes": modes,
        }
        if not logic_modules:
            warnings.append(f"feature_registry_empty_logic_modules:{feature_key}")
        if not commands:
            warnings.append(f"feature_registry_empty_commands:{feature_key}")
        if not modes:
            warnings.append(f"feature_registry_empty_modes:{feature_key}")
        for cmd in commands:
            owner = command_owner.get(cmd)
            if owner and owner != feature_key:
                warnings.append(f"feature_registry_command_conflict:{cmd}:{owner}:{feature_key}")
            else:
                command_owner[cmd] = feature_key
        for mode in modes:
            owner = mode_owner.get(mode)
            if owner and owner != feature_key:
                warnings.append(f"feature_registry_mode_conflict:{mode}:{owner}:{feature_key}")
            else:
                mode_owner[mode] = feature_key
    return normalized


def _load_feature_registry():
    warnings = []
    registry_path = os.environ.get(
        "MCTIVITY_FEATURE_REGISTRY_PATH",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "feature_registry.json"),
    )
    try:
        with open(registry_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        normalized = _normalize_feature_registry(data, warnings)
        if normalized:
            return normalized, warnings, registry_path
        warnings.append("feature_registry_empty_after_normalize")
    except Exception as exc:
        warnings.append(f"feature_registry_load_failed:{registry_path}:{exc}")
    fallback_warnings = []
    fallback = _normalize_feature_registry(_DEFAULT_FEATURE_REGISTRY, fallback_warnings) or {}
    warnings.extend(fallback_warnings)
    warnings.append("feature_registry_fallback_default")
    return fallback, warnings, "builtin-default"


FEATURE_REGISTRY, FEATURE_REGISTRY_WARNINGS, FEATURE_REGISTRY_SOURCE = _load_feature_registry()


def get_feature_registry_warnings():
    return list(FEATURE_REGISTRY_WARNINGS)


def get_feature_registry_source():
    return FEATURE_REGISTRY_SOURCE


def resolve_enabled_feature_keys(active_module_ids):
    active = set(active_module_ids or [])
    enabled = set()
    for feature_key, spec in FEATURE_REGISTRY.items():
        if active & set(spec.get("logic_modules", set())):
            enabled.add(feature_key)
    return enabled


def describe_feature_assembly(active_module_ids):
    active = set(active_module_ids or [])
    loaded = {}
    skipped = {}
    for feature_key, spec in FEATURE_REGISTRY.items():
        logic_modules = set(spec.get("logic_modules", set()))
        matched = sorted(active & logic_modules)
        if matched:
            loaded[feature_key] = {
                "matched_logic_modules": matched,
                "candidate_logic_modules": sorted(logic_modules),
            }
        else:
            skipped[feature_key] = {
                "reason": "no_logic_module_matched",
                "candidate_logic_modules": sorted(logic_modules),
            }
    return {"loaded": loaded, "skipped": skipped}
