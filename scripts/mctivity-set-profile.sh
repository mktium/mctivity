#!/usr/bin/env sh
set -eu

SERVICE_NAME="${SERVICE_NAME:-mctivity-hmi.service}"
PROFILE_NAME="${1:-standard}"
DROPIN_DIR="/etc/systemd/system/${SERVICE_NAME}.d"
DROPIN_FILE="${DROPIN_DIR}/profile.conf"

case "${PROFILE_NAME}" in
  minimal|standard|full)
    ;;
  *)
    echo "invalid profile: ${PROFILE_NAME}" >&2
    echo "usage: $0 {minimal|standard|full}" >&2
    exit 2
    ;;
esac

mkdir -p "${DROPIN_DIR}"
cat > "${DROPIN_FILE}" <<EOF
[Service]
Environment=MCTIVITY_PROFILE=${PROFILE_NAME}
EOF

systemctl daemon-reload
systemctl restart "${SERVICE_NAME}"
sleep 1
systemctl --no-pager --full status "${SERVICE_NAME}" | sed -n '1,20p'

python3 - <<'PY'
import json
import urllib.request

url = "http://127.0.0.1:2015/api/capabilities"
try:
    with urllib.request.urlopen(url, timeout=3) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
except Exception as exc:
    print("capabilities_check_failed:", exc)
    raise SystemExit(1)

print("profile:", payload.get("profile"))
print("features:", len(payload.get("active_features", [])))
print("caps:", len(payload.get("capabilities", [])))
print("warnings:", len(payload.get("warnings", [])))
print("generated_at:", payload.get("generated_at"))
PY

