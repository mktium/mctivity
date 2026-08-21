#!/usr/bin/env bash
set -euo pipefail

python3 -m py_compile mctivity_hmi/*.py
python3 -m py_compile scripts/mctivity-axis-d-stability.py
python3 scripts/mctivity-axis-profile-verify.py
python3 mctivity_hmi/test_profile_runtime.py
python3 mctivity_hmi/test_velocity_profile.py
python3 mctivity_hmi/test_dual_uservo_hmi.py
python3 mctivity_hmi/test_dual_uservo_gear_hmi.py
python3 scripts/test_motiond_launch.py
python3 scripts/mctivity-motiond-launch.py --profile axis-d-uservo-pv --dump >/dev/null
python3 scripts/mctivity-motiond-launch.py --profile axis-de-uservo-pv --dump >/dev/null
python3 scripts/mctivity-motiond-launch.py --profile axis-de-uservo-gear --dump >/dev/null
for profile in minimal standard full axis-d-uservo; do
  python3 scripts/mctivity-motiond-launch.py --profile "$profile" --dump >/dev/null
done
make -C mctivity_pdo_monitor test

python3 - <<'PY'
import json
from pathlib import Path

for path in sorted(Path(".").rglob("*.json")):
    if ".git" in path.parts:
        continue
    json.loads(path.read_text(encoding="utf-8"))
print("json ok")
PY

for script in scripts/*.sh; do
  bash -n "$script"
done

if command -v systemd-analyze >/dev/null 2>&1 && command -v systemctl >/dev/null 2>&1; then
  ethercat_unit="$(systemctl show -p FragmentPath --value ethercat.service 2>/dev/null || true)"
  if [ -n "$ethercat_unit" ] && [ -f "$ethercat_unit" ]; then
    systemd-analyze verify \
      "$ethercat_unit" \
      systemd/mctivity-motiond.service \
      systemd/mctivity-hmi.service
  else
    echo "systemd verify skipped: ethercat.service fragment is unavailable"
  fi
fi

if command -v node >/dev/null 2>&1; then
  node --check mctivity_hmi/motion_curve_editor_block.js
  tmp_js="$(mktemp "${TMPDIR:-/tmp}/mctivity-inline-js.XXXXXX.js")"
  trap 'rm -f "$tmp_js"' EXIT
  python3 - "$tmp_js" <<'PY'
from pathlib import Path
import sys

source = Path("mctivity_hmi/mctivity_hmi.py").read_text(encoding="utf-8")
marker = "<script>\nconst REV"
start = source.index(marker) + len("<script>\n")
end = source.index("</script>", start)
Path(sys.argv[1]).write_text(source[start:end], encoding="utf-8")
PY
  node --check "$tmp_js"
fi

find . -name "__pycache__" -type d -not -path "./.git/*" -exec rm -rf {} +
find . -name "*.pyc" -type f -not -path "./.git/*" -exec rm -f {} +

bad_files="$(
  find . -path ./.git -prune -o \( \
    -name "__MACOSX" -o \
    -name "._*" -o \
    -name ".DS_Store" -o \
    -name "__pycache__" -o \
    -name "*.pyc" \
  \) -print
)"
if [ -n "$bad_files" ]; then
  printf '%s\n' "$bad_files"
  exit 1
fi

echo "release preflight ok"
