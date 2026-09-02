#!/usr/bin/env bash
set -euo pipefail

export PYTHONDONTWRITEBYTECODE=1
python3 - <<'PY'
import ast
from pathlib import Path
for path in Path("mctivity_hmi").glob("*.py"):
    ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
print("python syntax ok")
PY
python3 -m unittest discover -s tests -p 'test_*.py' -v

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

if command -v node >/dev/null 2>&1; then
  node --check mctivity_hmi/motion_curve_editor_block.js
  while IFS= read -r js_file; do
    node --check "$js_file"
  done < <(find mctivity_hmi/assets -name '*.js' -type f | sort)
  node tests/raw_fault_status_test.js
  node tests/mock_state_isolation_test.js
  node tests/multi_point_ui_race_test.js
  node --check tests/browser_fault_smoke.js
  node --check tests/browser_multi_point_races.js
  tmp_dir="$(mktemp -d "${TMPDIR:-/tmp}/mctivity-inline-js.XXXXXX")"
  tmp_js="$tmp_dir/inline.js"
  trap 'rm -rf "$tmp_dir"' EXIT
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
else
  echo "Node.js is required for release JavaScript checks." >&2
  exit 1
fi

bad_files="$(
  find . -path ./.git -prune -o \( \
    -name "__MACOSX" -o \
    -name "._*" -o \
    -name ".DS_Store" -o \
    -name ".env" -o \
    -name ".env.*" -o \
    -name "*.pem" -o \
    -name "*.key" -o \
    -name "*.p12" -o \
    -name "*.pfx" -o \
    -name "*.bak" -o \
    -name "*.log" -o \
    -name "*.pid" -o \
    -name "mctivity_hmi_state.json" \
  \) -print
)"
if [ -n "$bad_files" ]; then
  printf '%s\n' "$bad_files"
  exit 1
fi

python3 scripts/release_content_check.py

echo "release preflight ok"
