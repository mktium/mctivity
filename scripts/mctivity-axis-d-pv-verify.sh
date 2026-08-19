#!/usr/bin/env bash
set -euo pipefail

MCTIVITY_EXPECT_PROFILE=axis-d-uservo-pv \
  exec "$(dirname "$0")/mctivity-kiosk-verify.sh" "$@"
