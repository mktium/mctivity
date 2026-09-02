#!/usr/bin/env sh
set -eu

BASE_URL="${BASE_URL:-http://127.0.0.1:2015}"

python3 - "$BASE_URL" <<'PY'
import json
import sys
import urllib.error
import urllib.request

base = sys.argv[1].rstrip("/")
caps_url = base + "/api/capabilities"
health_url = base + "/api/health/modular"
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

def get_json(url):
    try:
        with opener.open(url, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        print(f"mctivity HMI is not reachable at {url}: {exc}", file=sys.stderr)
        print("Start mctivity-hmi.service or set BASE_URL to the running HMI URL.", file=sys.stderr)
        raise SystemExit(1)

caps = get_json(caps_url)
health = get_json(health_url)

print("capabilities.ok:", caps.get("ok"))
print("profile:", caps.get("profile"))
print("active_features:", len(caps.get("active_features", [])))
print("enabled_feature_keys:", ",".join(caps.get("enabled_feature_keys", [])))
print("feature_registry_source:", caps.get("feature_registry_source"))

assembly = caps.get("feature_assembly") or {}
loaded = assembly.get("loaded") or {}
skipped = assembly.get("skipped") or {}
print("assembly.loaded:", ",".join(sorted(loaded.keys())))
print("assembly.skipped:", ",".join(sorted(skipped.keys())))

warnings = caps.get("warnings", [])
print("warnings.count:", len(warnings))
for w in warnings:
    print("warning:", w)

print("health.status:", health.get("status"))
print("health.warning_count:", health.get("warning_count"))
print("generated_at:", caps.get("generated_at"))
PY
