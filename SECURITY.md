# Security and Operation

This is a laboratory motion-control demo, not a certified safety controller. A clean source scan or passing tests do not establish that machinery is safe to operate.

## Network Boundary

- The HMI binds to loopback by default. Token-free access is permitted only for a loopback bind and loopback client.
- Non-loopback binding requires a random API token of at least 32 characters. Length/diversity checks reject obvious weak configuration, but cannot prove randomness.
- The configured token is required for status, commands, and UI-state reads/writes. Page assets and capability/health metadata are intentionally public.
- Allowed Host and same-origin checks remain enabled. Do not weaken them to resolve deployment problems.
- The built-in server uses plain HTTP. A token is not encryption. Use an isolated trusted control network; any TLS proxy requires a separate origin/header integration review before deployment.
- Shared tokens provide no per-user roles, audit identity, lockout, rate limiting, or multi-operator arbitration. Do not expose this service to the internet or untrusted clients.
- Browser session storage contains the token. Clear it after shared-console use, and do not load untrusted scripts/extensions into the control browser.

## Local Control Boundary

The supplied motion daemon listens on loopback. Its local command protocol is not authenticated. Local users and processes with access to it must be trusted; do not port-forward it. `mctivity_ctl.py` bypasses HMI authorization, capabilities, and parameter validation. Restrict OS access and use the dedicated service account with only the required EtherCAT device permissions.

`make motion-test-tools` builds three standalone hardware tests. They require `--confirm-motion` before any hardware access. After confirmation they can enable/move a real drive, may use hardcoded topology/defaults, and bypass the HMI. Never run them alongside the motion daemon or on an unprepared load. Confirm independent emergency stop, drive limits, clearance, and topology first. The confirmation flag does not enforce those conditions.

## Secrets and Publishing

- Keep passwords, tokens, personal paths, host identities, private addresses, runtime state, logs, and site configuration out of Git and release archives.
- The preflight scans source content and prohibited filenames. Optional private deny terms may be supplied through `MCTIVITY_RELEASE_DENY_TERMS` as a JSON string array in the local environment; never put actual values in scripts, fixtures, CI YAML, or reports.
- `python3 scripts/release_content_check.py --history <base-ref>` additionally checks every intervening snapshot and commit metadata. Reports contain rule names and locations, not matched values.
- Heuristic checks cannot guarantee that no sensitive information exists. Review images, binary metadata, personal identities, Git author metadata, tags, branches, release assets, and old history separately.
- Publish a reviewed branch and an archive of its tree, not a whole working directory or a Git bundle of all refs. Deleting a secret in the latest commit does not remove it from earlier commits.
- If a credential was publicly exposed, revoke/rotate it and assess access. History cleanup alone does not invalidate copies. Rotation on an operating machine must be coordinated with its operator.

Do not include live credentials or site logs in public bug reports. Arrange a private reporting channel with the repository owner before sending sensitive details.
