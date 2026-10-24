# Replacing a disk from a small running ext4 system

This workflow captures allocated filesystem contents and metadata, not deleted
files or unused disk sectors. It produces a bootable whole-disk candidate from
the source GPT, complete EFI partition, root clone and regenerated swap header.
Keep captures and final images in private local storage: they include machine
credentials. Do not place them in the public repository or a cloud-synced
maintenance folder.

## Source capture and protection

`capture_frozen_root.py` is deliberately limited to the reviewed `/dev/sda`
EFI/ext4-root/swap layout. It requires root, the exact source capacity, sufficient
RAM and swap margin, an unused `/run` directory and explicit `--execute`.
Motion and EtherCAT services must already be stopped. It preserves whether the
HMI was active, stops it temporarily, remounts EFI read-only, then captures EFI
and source geometry into a private tmpfs mount.

The root is frozen with `FIFREEZE`; `e2image -aQf` copies its allocated blocks
and metadata into RAM. Parent timeout is 240 seconds. An independent watchdog
thaws root after 300 seconds or unexpected parent exit and attempts to restore
EFI/HMI. A watchdog-triggered capture is invalid. The normal path thaws root
before restoring services and hashing the snapshot.

libext2fs probes a mounted root by creating `/.ismount-test-file`. This blocks
on a frozen root even with `e2image -f`. The child process therefore uses
`EXT2FS_PRETEND_RW_MOUNT=1` **only after a successful freeze**, avoiding the
probe without allowing the source to change. Never use this switch on an
unfrozen writable source. This behavior is visible in the upstream
[mount-detection implementation](https://github.com/tytso/e2fsprogs/blob/master/lib/ext2fs/ismounted.c).

The initial real-machine attempt exposed this root-specific probe, which a
non-root-filesystem integration fixture did not exercise. Its timeout watchdog
thawed the source. A broken-pipe cleanup error was corrected; the HMI/EFI state
was restored and checked, and the invalid temporary image mount discarded.
The subsequent capture completed successfully with the corrected probe handling.

## Transfer and larger candidate

`fetch_snapshot.py` waits for a completed, thawed source receipt, then downloads
each component and checks its exact size and SHA-256. Root QCOW2 is compressed
during SSH transfer and verified after decompression. Authentication uses the
existing machine-specific SSH key, not saved plaintext passwords.

`prepare_snapshot.py` waits for transfer acceptance, converts the QCOW2 clone,
performs ordinary noninteractive recovery on disposable partition copies, then
grows the root filesystem using `resize2fs`. Run `e2fsck -p -f` before resizing:
preen without `-f` can skip a nominally clean frozen clone, leaving `resize2fs`
to refuse expansion. A failed preparation never authorizes a physical write.
`assemble_snapshot.py` keeps the EFI
partition/start, disk GUID, partition GUIDs and filesystem UUIDs; moves swap to
the end; and rebuilds both GPT copies for the exact larger capacity. The
independent checker must pass before a write plan is produced.

For replacement of the same machine, retain its identity, network bindings and
service policy. A separate machine instead requires explicit identity/network
adaptation. Neither case authorizes enabling motion during cloning.

Physical deployment still uses the guarded macOS writer in
[the recovery guide](disk-image-recovery.md). Never reuse another physical
disk's plan or a previously completed job directory. Check the complete target
by full read-back before ejection. Boot acceptance remains a separate step.

## Follow-up and cache cleanup

Keep background worker PIDs and status paths in the private job record. The
transfer/preparation workers advance independently; lightweight scheduled
follow-up checks their status and stays quiet during normal progress.

After verified transfer, unmount only the specific private source tmpfs image
directory and remove the known temporary scripts. Preserve source receipts and
confirm HMI recovery. After deployment, retain the original compressed source
bundle and a verified final compressed whole-disk image, then remove redundant
raw images, root check copies and unpacked/build intermediates. Record cleanup
separately from retained backups. Pause scheduled follow-up at a terminal outcome.
