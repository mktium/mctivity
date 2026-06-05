#!/usr/bin/env bash
set -euo pipefail

python3 -m py_compile mctivity_hmi/*.py

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
