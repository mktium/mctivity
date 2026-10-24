# Offline recovery preparation — 2026-10-24

## Scope and current status

Prepare the historical mctivity 1.0 / Debian 13 backup for a smaller replacement
SSD connected to the operator's Mac through a USB enclosure. This restores the
backup version; it does not install the application revision in this checkout.

**Image preparation and offline validation are complete. Physical disk writing,
read-back comparison and on-machine boot acceptance are pending.**
The guarded deployment requires local administrator authentication before it
can read or write the raw USB disk.

## Geometry and retained images

| Item | Bytes |
| --- | ---: |
| Original disk image | 32,017,047,552 |
| Replacement disk / prepared image | 31,675,383,808 |
| Original swap partition | 1,657,798,656 |
| Adapted swap partition | 1,315,962,880 |

The EFI and root partition starts and sizes are unchanged. Both GPT copies and
the protective MBR have been adapted to the exact replacement capacity. Swap was
reinitialized with its original filesystem UUID so the existing `fstab` remains
consistent.

Three whole-disk image variants are retained privately with checksums:

1. Original compressed backup.
2. Capacity-adapted image retaining the original filesystem state.
3. Clean capacity-adapted derivative after specifically approved routine
   filesystem recovery.

Retain all three for subsequent disks. Re-identify each new physical disk and
check its exact capacity before selecting or generating a matching derivative.

## Source filesystem finding and approved recovery

The source backup had pending ext4 journal recovery and an EFI dirty flag. Its
compressed checksum and gzip integrity were valid; those checks alone did not
establish filesystem consistency.

Recovery was first reproduced on disposable copies. Normal `e2fsck -p` journal
recovery completed successfully, followed by a clean `e2fsck -f -n` check. EFI
dirty-flag recovery was likewise checked on a separate copy, followed by a clean
`fsck.fat -n` check. Explicit approval was obtained before incorporating these
results into a new deployment candidate.

Relative to the unmodified-payload adapted image, the clean derivative changes
19 root-partition 4096-byte blocks and one EFI 4096-byte block. Parent, original
partition and resulting partition hashes, plus complete recovery logs, are
retained in the private archive. The original images were not edited.

## Completed validation

- Eleven unit tests pass for geometry/CRC rejection, payload preservation,
  device identity and validation gates, and isolated derivative patching.
- Both GPT tables/headers validate with no geometry problems.
- Final EFI/root payloads match the clean candidate's manifest.
- Final root check copy passes `e2fsck -f -n` without unresolved errors.
- Final EFI check copy passes `fsck.fat -n`.
- The removable-media fallback EFI executable is x86-64.
- The swap header matches independently generated util-linux `mkswap` output.
- Root, EFI and swap UUID references in `fstab` remain consistent.
- The retained clean compressed image fully decompresses to the recorded raw
  image size and SHA-256.
- The exact original target partition identities still match the deployment
  plan; check-only execution opens no raw disk and performs no unmount/write.

The checker uses a pinned Debian 13 base with e2fsprogs 1.47.2, dosfstools 4.2 and
util-linux 2.41.5. Private receipts identify the built checker image and tool
commit. Full images, disk GUIDs, device paths and detailed site records are not
published in this repository.

## Target-backup policy update

The initial backup attempt was stopped at the operator's request, before any
target writes. The replacement disk was declared disposable and target backup
was explicitly waived. The next guarded job uses a new directory and the
matching plan waiver plus `--skip-backup`. Device identity, image validation and
full write/read-back acceptance remain required. Original and derived system
images are retained; an incomplete cancelled target backup is not a rollback
image.

## Remaining acceptance gates

1. Start the guarded job with local administrator authentication.
2. Verify the recorded no-backup waiver and target identity immediately before writing.
3. Write the checked clean candidate and compare every byte read back.
4. Record the resulting GPT and safely eject the disk.
5. Install it in the target PC, verify 64-bit UEFI boot, then check network and
   EtherCAT bindings with motion disabled.

Do not mark the deployment complete until the physical write/read-back gates
pass. Keep actual boot and runtime acceptance separate from offline success.
See [the recovery guide](disk-image-recovery.md) for the guarded workflow.

## Automated follow-up

A separate non-root `monitor_restore.py --watch` process reads the current job
status every 60 seconds and writes an atomic summary. It never opens the target
disk or restarts the writer. It exits on completion, failure, disappearance of
the writer, or progress older than 180 seconds. A thread heartbeat checks the
summary every five minutes, stays quiet during normal progress, and performs
final archive/documentation work or reports a problem. Stop the heartbeat at a
terminal outcome. Physical boot acceptance remains a separate operator step.
