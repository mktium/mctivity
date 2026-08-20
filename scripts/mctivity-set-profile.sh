#!/usr/bin/env sh
set -eu

SERVICE_NAME="${SERVICE_NAME:-mctivity-hmi.service}"
ROOT="${MCTIVITY_ROOT:-/opt/mctivity}"
PROFILE_NAME="${1:-standard}"
AXIS_ENV_DIR="/etc/mctivity"
AXIS_ENV_FILE="${AXIS_ENV_DIR}/axis.env"
HMI_ENV_FILE="${AXIS_ENV_DIR}/hmi.env"
BACKUP_ROOT="${MCTIVITY_BACKUP_ROOT:-/var/backups/mctivity}"

case "${PROFILE_NAME}" in
  minimal|standard|full|axis-d-uservo|axis-d-uservo-pv|axis-de-uservo-pv)
    ;;
  *)
    echo "invalid profile: ${PROFILE_NAME}" >&2
    echo "usage: $0 {minimal|standard|full|axis-d-uservo|axis-d-uservo-pv|axis-de-uservo-pv}" >&2
    exit 2
    ;;
esac

mkdir -p "${AXIS_ENV_DIR}"
backup_dir="${BACKUP_ROOT}/profile-$(date -u +%Y%m%dT%H%M%SZ)-$$"
mkdir -p "${backup_dir}"
: >"${backup_dir}/manifest.tsv"
for path in \
  "${AXIS_ENV_FILE}" \
  "${HMI_ENV_FILE}" \
  /etc/systemd/system/mctivity-motiond.service \
  /etc/systemd/system/mctivity-hmi.service \
  /etc/systemd/system/mctivity-motiond.service.d/10-axis-d-realtime.conf; do
  if [ -e "${path}" ]; then
    saved="$(printf '%s' "${path}" | tr '/' '_')"
    cp -a "${path}" "${backup_dir}/${saved}"
    printf 'present\t%s\t%s\n' "${path}" "${saved}" >>"${backup_dir}/manifest.tsv"
  else
    printf 'absent\t%s\t-\n' "${path}" >>"${backup_dir}/manifest.tsv"
  fi
done
readlink -f "${ROOT}" >"${backup_dir}/active-release.txt" 2>/dev/null || true
cat >"${backup_dir}/rollback.sh" <<'EOF'
#!/usr/bin/env sh
set -eu
backup_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
tab="$(printf '\t')"
while IFS="$tab" read -r state target saved; do
  if [ "$state" = present ]; then
    cp -a "${backup_dir}/${saved}" "$target"
  else
    rm -f "$target"
  fi
done <"${backup_dir}/manifest.tsv"
systemctl daemon-reload
echo "configuration restored; restart services only after the inhibited read-only gate is ready"
EOF
chmod 0755 "${backup_dir}/rollback.sh"
axis_tmp="$(mktemp "${AXIS_ENV_DIR}/axis.env.XXXXXX")"
if [ "${PROFILE_NAME}" = "axis-d-uservo" ] || [ "${PROFILE_NAME}" = "axis-d-uservo-pv" ] || [ "${PROFILE_NAME}" = "axis-de-uservo-pv" ]; then
  cat > "${axis_tmp}" <<EOF
MCTIVITY_TOPOLOGY=${PROFILE_NAME}
MCTIVITY_PROFILE=${PROFILE_NAME}
MCTIVITY_COMMISSIONING_INHIBIT=1
MCTIVITY_REQUIRE_REALTIME=1
EOF
  mkdir -p /etc/systemd/system/mctivity-motiond.service.d
  install -m 0644 "${ROOT}/systemd/mctivity-motiond-axis-d-realtime.conf" \
    /etc/systemd/system/mctivity-motiond.service.d/10-axis-d-realtime.conf
else
  cat > "${axis_tmp}" <<EOF
MCTIVITY_TOPOLOGY=legacy-dual
MCTIVITY_PROFILE=${PROFILE_NAME}
MCTIVITY_COMMISSIONING_INHIBIT=0
MCTIVITY_REQUIRE_REALTIME=0
EOF
  rm -f /etc/systemd/system/mctivity-motiond.service.d/10-axis-d-realtime.conf
fi
install -m 0644 "${axis_tmp}" "${AXIS_ENV_FILE}"
rm -f "${axis_tmp}"

hmi_tmp="$(mktemp "${AXIS_ENV_DIR}/hmi.env.XXXXXX")"
if [ -f "${HMI_ENV_FILE}" ]; then
  awk -v profile="${PROFILE_NAME}" '
    BEGIN { replaced = 0 }
    /^MCTIVITY_PROFILE=/ { if (!replaced) print "MCTIVITY_PROFILE=" profile; replaced = 1; next }
    { print }
    END { if (!replaced) print "MCTIVITY_PROFILE=" profile }
  ' "${HMI_ENV_FILE}" >"${hmi_tmp}"
else
  printf 'MCTIVITY_PROFILE=%s\n' "${PROFILE_NAME}" >"${hmi_tmp}"
fi
install -m 0644 "${hmi_tmp}" "${HMI_ENV_FILE}"
rm -f "${hmi_tmp}"

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

echo "backup: ${backup_dir}"
echo "rollback config: ${backup_dir}/rollback.sh (does not restart services)"
