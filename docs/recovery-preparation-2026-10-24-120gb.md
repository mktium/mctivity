# Larger replacement disk preparation — 2026-10-24

## Current acceptance

A frozen source capture, checksum-verified transfer, capacity expansion and
independent offline validation are complete. The replacement is for the same
machine: filesystem and partition UUIDs, network identity and default realtime
kernel are preserved. Physical writing, full read-back verification and safe
ejection completed on 2026-10-26 at 13:09 China Standard Time.
Replacement-disk boot remains pending operator acceptance.

| Component | Bytes |
| --- | ---: |
| Original source disk | 60,022,480,896 |
| Replacement disk and candidate | 120,034,123,776 |
| EFI partition, unchanged size | 1,023,410,176 |
| Expanded root filesystem | 115,907,493,888 |
| Relocated swap partition | 3,101,687,808 |

The source HMI was restored and checked after thaw. Fieldbus/motion services
remain inactive. The original source disk is retained. The operator explicitly
authorized overwriting the new replacement disk without a target backup.

## Evidence and deployment gate

- Frozen allocated-block capture and each transferred component passed size and
  SHA-256 checks; this is not a backup of deleted/free-space data.
- The private source tmpfs was unmounted after verified transfer; temporary
  capture scripts were removed by the checksum-gated cleanup unit.
- Root passed forced full preen before expansion and full read-only checks
  afterward. EFI passed its read-only FAT check after ordinary dirty-bit clear.
- Both target GPT copies, partition payload hashes, x86-64 fallback EFI and the
  swap header against an independent `mkswap` reference passed.
- The final root UUID matches its existing `fstab` and fixed RT GRUB entry.
- Thirteen unit tests pass; a disposable 4 KiB ext4 fixture also passed frozen
  capture, conversion, forced preen, expansion from 128 to 256 MiB, full check
  and original-file content verification.
- The connected USB disk matches the recorded pre-write identity. A new private
  deployment plan and launcher passed check-only mode without opening raw disks.

The operator launched the prepared privileged writer locally. It verified the
complete source image, rechecked the target identity, wrote and read back all
120,034,123,776 bytes, and safely ejected the disk. The full read-back SHA-256
matches the independently validated candidate:

`0a1a08b689ef54e10cb4f28b80480de0d04056f9da59ca1a7a76b584db3f7013`

The private final compressed archive passed full decompression, size and raw
SHA checks. Its compressed SHA was rechecked before removing redundant raw
candidate/root images and derived EFI build copies. The original compressed
source capture, final compressed image, manifests and audit receipts remain
in private local storage outside cloud sync and this public repository.

The disk is ready to install into the original machine, preserving its identity.
Actual boot, network access, HMI restoration and expanded filesystem capacity
still require operator acceptance. Keep the original disk available for rollback;
fieldbus/motion commissioning remains outside this disk-writing acceptance.

See [the source capture workflow](frozen-root-replacement.md) and
[the guarded writer guide](disk-image-recovery.md).
