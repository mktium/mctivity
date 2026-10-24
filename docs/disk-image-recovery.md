# Offline disk image recovery

These tools prepare and check an existing system backup. They do not upgrade the
application to the version of the source checkout. A historical image restores
its historical Debian and mctivity deployment.

Keep device identities, full images, rollback backups, detailed logs and site
configuration in a private archive outside this public repository. Commit only
generic tools and documentation. Preserve the original compressed image and its
checksum; retain separately named derivatives for subsequent disks.

## Supported layout

`tools/recovery/adapt_image.py` supports a gzip-compressed raw disk image with:

- 512-byte logical sectors and a standard 128-entry GPT;
- partition 1: EFI system partition, starting at or after 1 MiB;
- partition 2: Linux root filesystem;
- partition 3: Linux swap v1, 4096-byte pages, no bad-page map.

It fits a smaller disk by shrinking only the final swap partition. EFI/root data,
partition starts, GUIDs and filesystem UUIDs remain unchanged. It rebuilds both
GPT copies and the protective MBR, and reinitializes swap while preserving its
UUID and label. It refuses unsupported layouts or a disk too small for the
unchanged root filesystem plus swap. Do not truncate a disk image with `dd`.

The macOS writer requires an image whose length exactly matches the new disk.
For a subsequent disk of another capacity, prepare and validate a matching image;
do not bypass the size check. J1800 alone does not establish firmware support:
the target motherboard must support the image's 64-bit UEFI boot mode.

## Prepare an image

Use a local working directory with enough space for the raw sparse image, its
root check copy, a full rollback backup and retained compressed derivatives.
Do not place temporary raw images in a synchronizing cloud folder.

```sh
python3 tools/recovery/adapt_image.py \
  --source /path/to/original.img.gz --sha256 ORIGINAL_COMPRESSED_SHA256 \
  --target-bytes EXACT_DISK_BYTES \
  --output /path/to/stage/adapted.img \
  --root-copy /path/to/stage/root.img \
  --manifest /path/to/stage/manifest.json
```

The output manifest records original and resulting geometry, payload checksums
and the new whole-image checksum. The input is opened read-only. Existing output
files are refused. Failed preparations are not eligible for deployment.

## Independent Linux validation

Build the isolated checker from the pinned Debian base:

```sh
docker build -t mctivity-recovery-check tools/recovery
docker run --rm --network none --read-only --tmpfs /tmp:rw,size=64m \
  --mount type=bind,src=/absolute/stage,dst=/work,readonly \
  --mount type=bind,src=/absolute/repo/tools/recovery,dst=/tools,readonly \
  --entrypoint python3 mctivity-recovery-check /tools/validate_image.py /work
```

Required checks:

1. Both GPT header/array CRCs, boundaries, non-overlap and secondary GPT location.
2. EFI and root payloads matching the manifest, including the root check copy.
3. Read-only `e2fsck -f -n` and `fsck.fat -n` with successful status.
4. A valid x86-64 `EFI/BOOT/BOOTX64.EFI` fallback executable.
5. Swap header byte equality with an independent util-linux `mkswap` reference.

`--other-checks-only` is a diagnostic mode that omits the root filesystem check.
It explicitly does not approve a deployment. A gzip/SHA match alone does not
establish filesystem consistency.

### Images captured while the source was running

A live raw backup can contain pending journal transactions and an EFI dirty
flag. First reproduce recovery on separate disposable partition copies; never
repair the original backup in place. `e2fsck -p` models normal noninteractive
filesystem recovery; `e2fsck -E journal_only` limits a diagnostic to journal
replay. Save their complete output and follow with read-only checks. Do not use
`-y` as a way to accept arbitrary repairs.

If a clean deployment derivative is needed, obtain explicit approval when this
changes an agreed requirement to preserve root-partition bytes. Preserve the
original and the unmodified-payload derivative. After independently checking the
recovered root and EFI copies, create a new candidate:

```sh
python3 tools/recovery/prepare_clean_image.py \
  --source-folder /path/to/stage \
  --recovered-root /path/to/recovered/root.img \
  --recovered-efi /path/to/recovered/efi.img \
  --output-folder /path/to/clean-candidate \
  --acknowledge-filesystem-metadata-changes
```

This clones the parent and patches only differing 4096-byte blocks within the
two selected partitions. It records parent/original/new hashes and block counts.
It neither changes application configuration nor resizes the root partition.
Run the full independent checker again against the new candidate. A successful
check of a disposable recovery copy is not a substitute for checking the final
image that will actually be written.

## Guarded macOS disk deployment

Prepare a private JSON plan for each physical disk. It contains:

- `device`, such as `/dev/diskN`, with no default;
- `expected_info`: exact `diskutil info -plist` identity fields, including USB
  device-tree path, model, capacity, logical sector size and external/whole flags;
- `expected_partitions`: canonical original partition types, sizes and GUIDs;
- absolute `image`, `validation`, and `output_dir` paths;
- `image_bytes` and `image_sha256`;
- `filesystem_metadata_recovery_approved: true` if applicable.

The validation receipt must identify the same image SHA-256 and set all required
checks to true (`gpt`, `root_matches_manifest`, `root_e2fsck_readonly`,
`efi_matches_manifest`, `efi_fat_readonly`, `x64_fallback_efi`,
`swap_mkswap_reference`). Record tool/container versions and the validation log
hash with the receipt. Do not create a passing receipt for a failed checker.

Check without opening or unmounting the disk:

```sh
/usr/bin/python3 -I /absolute/repo/tools/recovery/macos_restore.py \
  --plan /private/job-plan.json --confirm-device /dev/diskN
```

After reviewing the identity and validation, run the same command with
administrator authentication and `--execute`:

```sh
sudo /usr/bin/python3 -I /absolute/repo/tools/recovery/macos_restore.py \
  --plan /private/job-plan.json --confirm-device /dev/diskN --execute
```

The job verifies the candidate, unmounts the target without forcing it, makes a
compressed full-disk rollback backup, and fully decompresses/verifies that backup
before writing. It rechecks the device, original partition GUIDs, and first MiB
before the first write. It then writes all image bytes, syncs, compares every byte
read back, checks the whole-disk SHA-256, records the resulting GPT, and ejects.

`restore-status.json` and `restore-events.jsonl` record the PID, phase, byte counts,
backup hashes and read-back result. Monitor these files read-only at sensible
intervals. `completed` with `readback_verified` and `safely_ejected` is the offline
completion gate. A failed or interrupted write leaves the target unapproved;
preserve the verified rollback backup and inspect the recorded phase before any
retry. A job lock prevents accidental reuse of the same job directory.

For rollback, preserve `target-before.img.gz`, its compressed/raw SHA-256 and the
original geometry/identities. Before a rollback, re-identify the connected disk,
fully verify the backup and prepare a separate reviewed restore operation. Do
not point the Linux validation receipt at a Windows rollback image or reuse the
original disk plan after the partition table has changed.

## On-machine acceptance and archive

After ejecting, install the disk in the target PC and select 64-bit UEFI boot.
If needed select the internal disk's `EFI/BOOT/BOOTX64.EFI`. Verify Debian starts,
partitions mount, and the expected image version is installed. Check hostname/IP
conflicts and actual physical NIC names before configuring EtherCAT or starting
motion services. Keep motion disabled during commissioning.

Archive source/derivative checksums, manifests, recovery approval if needed,
target rollback backup, tool commit, validation logs, deployment events and the
short operator instructions. Keep offline deployment and physical boot/runtime
acceptance as separate statuses. Retain the reusable images for the next disk.

## Tool checks

```sh
python3 -m unittest discover -s tools/recovery -p 'test_*.py' -v
git diff --check
```

Tests cover geometry/CRC rejection, payload preservation, wrong target identity,
required validation/confirmation, and isolated derivative patching. They never
open a physical disk.
