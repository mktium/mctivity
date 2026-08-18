#!/usr/bin/env sh
set -eu

SERVICE_NAME="${SERVICE_NAME:-mctivity-hmi.service}"
ROOT="${MCTIVITY_ROOT:-/opt/mctivity}"
PROFILE_NAME="${1:-standard}"
AXIS_ENV_DIR="/etc/mctivity"
AXIS_ENV_FILE="${AXIS_ENV_DIR}/axis.env"

case "${PROFILE_NAME}" in
  minimal|standard|full|axis-d-uservo)
    ;;
  *)
    echo "invalid profile: ${PROFILE_NAME}" >&2
    echo "usage: $0 {minimal|standard|full|axis-d-uservo}" >&2
    exit 2
    ;;
esac

mkdir -p "${AXIS_ENV_DIR}"
if [ "${PROFILE_NAME}" = "axis-d-uservo" ]; then
  cat > "${AXIS_ENV_FILE}" <<EOF
MCTIVITY_TOPOLOGY=axis-d-uservo
MCTIVITY_PROFILE=axis-d-uservo
MCTIVITY_COMMISSIONING_INHIBIT=1
MCTIVITY_REQUIRE_REALTIME=1
EOF
  mkdir -p /etc/systemd/system/mctivity-motiond.service.d
  install -m 0644 "${ROOT}/systemd/mctivity-motiond-axis-d-realtime.conf" \
    /etc/systemd/system/mctivity-motiond.service.d/10-axis-d-realtime.conf
else
  cat > "${AXIS_ENV_FILE}" <<EOF
MCTIVITY_TOPOLOGY=legacy-dual
MCTIVITY_PROFILE=${PROFILE_NAME}
MCTIVITY_COMMISSIONING_INHIBIT=0
MCTIVITY_REQUIRE_REALTIME=0
EOF
  rm -f /etc/systemd/system/mctivity-motiond.service.d/10-axis-d-realtime.conf
fi
chmod 0644 "${AXIS_ENV_FILE}"

systemctl daemon-reload
systemctl restart mctivity-motiond.service "${SERVICE_NAME}"
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
