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

## Execution and Stop State

The HMI execution flag covers anti-sway curve requests as well as non-dry preparation. It does not constrain the separate local daemon protocol or standalone tools.

Multi-point cancellation retains an unconfirmed-stop state when a stop fails or feedback is unavailable. New motion and table clear/restart remain blocked on that axis; explicit stop retry and disable remain available. An accepted stop request is not confirmation of standstill. This state is process-local: do not restart the HMI to bypass it. Communication failures can prevent a software stop; independent hardware emergency stop and limits remain necessary.

The supplied daemon's motion-feedback contract requires a valid `moving` flag, `wc_complete` and `operational` both true (or integer 1), and a uint32 `cycles` counter. Missing/malformed fields, an invalid bus/slave state, a counter rollback, a response taking over one second, or a counter not advancing for one second cannot confirm stop. Two stationary samples with a forward cycle-counter advance are required; uint32 wrap is supported. The same validity/freshness checks guard multi-point row completion. A successful local daemon RPC alone is insufficient. Alternative adapters must provide this contract, not synthesize successful flags for unavailable feedback.

Multi-point browser responses are bound to the request's axis and ordered per axis. A mutating request invalidates older polls, and polling pauses while a mutation is pending. Missing runner data does not clear the existing state. Switching axes during startup cancels remaining unsent setup/run steps; it does not stop motion that has already been submitted. This UI sequencing is not a substitute for the server-side cancellation guard or multi-client arbitration.

Browser fault-preview sessions keep configuration edits in memory and block device commands and configuration writes. Entering or leaving preview requires a fresh page load; do not attempt to reuse preview state for machine operation.

## Secrets and Publishing

- Keep passwords, tokens, personal paths, host identities, private addresses, runtime state, logs, and site configuration out of Git and release archives.
- The preflight scans source content and prohibited filenames. Optional private deny terms may be supplied through `MCTIVITY_RELEASE_DENY_TERMS` as a JSON string array in the local environment; never put actual values in scripts, fixtures, CI YAML, or reports.
- `python3 scripts/release_content_check.py --history <base-ref>` additionally checks every intervening snapshot and commit metadata. Reports contain rule names and locations, not matched values.
- Heuristic checks cannot guarantee that no sensitive information exists. Review images, binary metadata, personal identities, Git author metadata, tags, branches, release assets, and old history separately.
- Publish a reviewed branch and an archive of its tree, not a whole working directory or a Git bundle of all refs. Deleting a secret in the latest commit does not remove it from earlier commits.
- If a credential was publicly exposed, revoke/rotate it and assess access. History cleanup alone does not invalidate copies. Rotation on an operating machine must be coordinated with its operator.

Do not include live credentials or site logs in public bug reports. Arrange a private reporting channel with the repository owner before sending sensitive details.
