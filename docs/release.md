# Release Guide

The current release candidate is `v1.4.0-preview.3`. Full behavior, safety notes, topology, and limitations are documented in [../RELEASE_NOTES.md](../RELEASE_NOTES.md).

## Technical Changes Since v1.2.0

- multi-point positioning and controlled sequence stop
- current-position and torque-obstruction homing with backoff
- Axis C auxiliary encoder feedback
- Axis C as an electronic-gear master
- full-path ZVD and endpoint period-matched anti-sway positioning
- persistent anti-sway settings and measured period
- direction/unit consistency fixes across positioning, homing, and gearing
- touch-oriented HMI and motion confirmation updates

## Release Boundary

- Source/preview release for controlled laboratory evaluation.
- Runtime UI state, logs, backups, credentials, and site-specific deployment values are excluded.
- Anti-sway remains open-loop; encoder feedback does not perform real-time trajectory correction.
- Vendor-specific diagnostic code, data, detail dialogs, and manual-derived troubleshooting documentation are not included.
- Basic fault flags, raw error codes, manual reset, and existing control protections remain. No manufacturer manuals are distributed.

## Preflight

```bash
./scripts/mctivity-release-preflight.sh
python3 scripts/release_content_check.py --history origin/main
git diff --check
```

Run the preflight from the repository root or from an extracted source archive. The archive supports current-file checks only; review history separately in the repository.

Optional browser regression with Playwright 1.62.1 available to Node.js:

```bash
MCTIVITY_BROWSER_CHANNEL=chrome node tests/browser_fault_smoke.js
```

Omit the channel variable to use Playwright's installed Chromium. This test launches an isolated loopback fixture, intercepts device APIs, blocks hardware backend calls, and checks raw fault display, explicit reset, unconfirmed-stop retry, delayed multi-point responses across axis switches, same-axis response ordering, startup cancellation, and preview configuration isolation at 1920x1080 and 390x844. It does not connect to a controller.

On a matching IgH EtherCAT development target:

```bash
cd mctivity_pdo_monitor
make clean
make
```

Verify every selected profile, review the safety notes, and use a release branch before tagging or merging.

## Publication Gate

This candidate excludes vendor-specific fault diagnosis. Review the exact source tree and every commit that will be published, not only the final diff: a removed dataset must not survive in an intermediate commit or release attachment. The release content check rejects excluded package paths. Keep private backups outside the repository and do not publish them.

The project owner confirmed company ownership of the program code and logo, and in-house creation of the architecture diagrams, on 2026-09-02. This ownership-confirmation item is recorded in [Notices](../NOTICE.md); it does not clear third-party material. Review [Security](../SECURITY.md) and inspect the exact branch history and commit author metadata as well as its current files. Old review branches, tags, release archives, and other remote feature branches need their own audit; a clean candidate does not clear them.

Create a source archive from the exact reviewed commit using `git archive`, not from the working directory. Do not push all branches/tags or publish a Git bundle. Record the commit and archive digest. Hardware validation from a previous snapshot must remain labeled historical; this laboratory demo may document outstanding motion tests without calling them passed.
