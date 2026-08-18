#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
MCTIVITY_EXPECT_PROFILE=axis-d-uservo exec "${SCRIPT_DIR}/mctivity-kiosk-verify.sh"
