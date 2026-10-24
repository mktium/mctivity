#!/usr/bin/env python3
"""Explicit macOS disk restore: verified rollback backup, write, full read-back.

No write occurs without --execute, --confirm-device and root privileges.
The JSON plan must pin the external device identity and original partition GUIDs.
Use a NEW plan/output directory for each physical disk.
"""
import argparse
import datetime
import gzip
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import stat
import subprocess
import sys

CHUNK = 8 * 1024 * 1024
IDENTITY_KEYS = ("DeviceIdentifier", "DeviceNode", "MediaName", "DeviceTreePath",
                 "TotalSize", "DeviceBlockSize", "Internal", "WholeDisk", "BusProtocol")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(CHUNK), b""):
            h.update(b)
    return h.hexdigest()


def plist(*args):
    return plistlib.loads(subprocess.check_output(["/usr/sbin/diskutil", *args]))


def partition_identity(value):
    disk = value["AllDisksAndPartitions"][0]
    return {"Content": disk["Content"], "Size": disk["Size"],
            "Partitions": [{k: p.get(k) for k in ("DeviceIdentifier", "Content", "Size", "DiskUUID")}
                           for p in disk.get("Partitions", [])]}


def check_identity(actual, expected):
    require(not actual.get("Internal") and actual.get("WholeDisk") and actual.get("BusProtocol") == "USB",
            "Only an external whole USB disk is permitted")
    require(actual.get("DeviceBlockSize") == 512, "Expected 512-byte logical sectors")
    for key in IDENTITY_KEYS:
        require(actual.get(key) == expected.get(key), "Device identity changed: " + key)


def validate_plan(plan, confirmation):
    device = plan["device"]
    require(re.fullmatch(r"/dev/disk[0-9]+", device) is not None, "Select a whole macOS disk")
    require(confirmation == device, "Explicit device confirmation mismatch")
    require(plan["expected_info"]["DeviceNode"] == device, "Plan device mismatch")
    require(plan["image_bytes"] == plan["expected_info"]["TotalSize"], "Image must match exact target capacity")
    for key in ("image", "validation", "output_dir"):
        require(Path(plan[key]).is_absolute(), "Plan paths must be absolute: " + key)
    for key in ("image", "validation"):
        require(stat.S_ISREG(Path(plan[key]).lstat().st_mode), "Expected a regular file: " + key)
    require(Path(plan["image"]).stat().st_size == plan["image_bytes"], "Image length mismatch")
    validation = json.loads(Path(plan["validation"]).read_text())
    require(validation["result"] == "passed" and validation["image_sha256"] == plan["image_sha256"],
            "Matching independent validation is required")
    for name in ("gpt", "root_matches_manifest", "root_e2fsck_readonly", "efi_matches_manifest", "efi_fat_readonly",
                 "x64_fallback_efi", "swap_mkswap_reference"):
        require(validation["checks"].get(name) is True, "Missing validation: " + name)
    if validation.get("filesystem_metadata_recovered"):
        require(plan.get("filesystem_metadata_recovery_approved") is True,
                "Filesystem metadata recovery requires explicit approval in this plan")


class Job:
    def __init__(self, plan):
        self.plan = plan
        self.folder = Path(plan["output_dir"])
        self.folder.mkdir(parents=True, exist_ok=True)
        self.state = {"device": plan["device"], "phase": "preflight", "target_written": False,
                      "backup_verified": False, "readback_verified": False}

    def event(self, phase, **values):
        self.state.update(values)
        self.state["phase"] = phase
        self.state["updated_at"] = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).isoformat()
        line = json.dumps(self.state, ensure_ascii=False)
        print(line, flush=True)
        with (self.folder / "restore-events.jsonl").open("a") as f:
            f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())
        temporary = self.folder / "restore-status.json.partial"
        temporary.write_text(json.dumps(self.state, ensure_ascii=False, indent=2) + "\n")
        temporary.replace(self.folder / "restore-status.json")

    def identity(self, with_partitions=True):
        actual = plist("info", "-plist", self.plan["device"])
        check_identity(actual, self.plan["expected_info"])
        if with_partitions:
            require(partition_identity(plist("list", "-plist", self.plan["device"])) == self.plan["expected_partitions"],
                    "Original partition identities changed")

    def run(self):
        p = self.plan
        self.identity()
        require(shutil.disk_usage(self.folder).free > p["image_bytes"] + 1024**3,
                "Insufficient backup space (reserve full uncompressed disk size plus 1 GiB)")
        require(digest(p["image"]) == p["image_sha256"], "Candidate image SHA-256 mismatch")
        image = open(p["image"], "rb", buffering=0)
        initial = os.fstat(image.fileno())
        require(not (self.folder / "target-before.img.gz").exists(), "Rollback backup already exists; use a new job directory")
        lock = os.open(str(self.folder / "restore.lock"), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.write(lock, (str(os.getpid()) + "\n").encode())
        os.close(lock)
        self.event("unmounting", pid=os.getpid())
        subprocess.run(["/usr/sbin/diskutil", "unmountDisk", p["device"]], check=True)
        self.identity()
        raw = "/dev/r" + p["device"][5:]
        require(stat.S_ISCHR(os.stat(raw).st_mode), "Expected a raw character disk device")
        backup_tmp = self.folder / "target-before.img.gz.partial"
        backup = self.folder / "target-before.img.gz"
        original_hash, first_hash = hashlib.sha256(), None
        self.event("backing_up", bytes_done=0, bytes_total=p["image_bytes"])
        with open(raw, "rb", buffering=0) as src, backup_tmp.open("xb") as out:
            raw_identity = os.fstat(src.fileno()).st_rdev
            with gzip.GzipFile(filename="", mode="wb", fileobj=out, compresslevel=1, mtime=0) as compressed:
                count = 0
                while count < p["image_bytes"]:
                    b = src.read(min(CHUNK, p["image_bytes"] - count))
                    require(b, "Short backup read")
                    if count == 0:
                        first_hash = hashlib.sha256(b[:1024 * 1024]).hexdigest()
                    compressed.write(b)
                    original_hash.update(b)
                    count += len(b)
                    if count // 1024**3 != (count - len(b)) // 1024**3:
                        self.event("backing_up", bytes_done=count)
            out.flush()
            os.fsync(out.fileno())
        self.event("verifying_backup", bytes_done=0)
        checked_hash, checked_size = hashlib.sha256(), 0
        with gzip.open(backup_tmp, "rb") as src:
            for b in iter(lambda: src.read(CHUNK), b""):
                checked_hash.update(b)
                checked_size += len(b)
        require(checked_size == p["image_bytes"] and checked_hash.digest() == original_hash.digest(),
                "Rollback backup failed verification; target will not be written")
        backup_tmp.replace(backup)
        self.event("backup_verified", backup_verified=True, backup_raw_sha256=original_hash.hexdigest(),
                   backup_gzip_sha256=digest(backup), backup_path=str(backup))
        # Last identity and source checks before the first target write.
        self.identity()
        current = os.fstat(image.fileno())
        require((current.st_ino, current.st_size, current.st_mtime_ns) ==
                (initial.st_ino, initial.st_size, initial.st_mtime_ns), "Candidate image changed")
        with open(raw, "r+b", buffering=0) as target:
            require(os.fstat(target.fileno()).st_rdev == raw_identity, "Raw device changed")
            require(hashlib.sha256(target.read(1024 * 1024)).hexdigest() == first_hash, "Disk content changed after backup")
            target.seek(0)
            image.seek(0)
            written_hash, count = hashlib.sha256(), 0
            self.event("writing", target_written=True, bytes_done=0)
            while count < p["image_bytes"]:
                b = image.read(min(CHUNK, p["image_bytes"] - count))
                require(b and len(b) % 512 == 0, "Invalid image read or sector alignment")
                view = memoryview(b)
                while view:
                    done = target.write(view)
                    require(done and done % 512 == 0, "Short or unaligned disk write")
                    view = view[done:]
                written_hash.update(b)
                count += len(b)
                if count // 1024**3 != (count - len(b)) // 1024**3:
                    self.event("writing", bytes_done=count)
            require(written_hash.hexdigest() == p["image_sha256"], "Image changed while writing")
            os.fsync(target.fileno())
        image.close()
        subprocess.run(["/bin/sync"], check=True)
        self.identity(with_partitions=False)
        self.event("reading_back", bytes_done=0)
        read_hash, count = hashlib.sha256(), 0
        with open(raw, "rb", buffering=0) as target, open(p["image"], "rb", buffering=0) as expected:
            require(os.fstat(target.fileno()).st_rdev == raw_identity, "Raw device changed during verification")
            while count < p["image_bytes"]:
                b = target.read(min(CHUNK, p["image_bytes"] - count))
                require(b and b == expected.read(len(b)), "Disk byte comparison failed at offset " + str(count))
                read_hash.update(b)
                count += len(b)
                if count // 1024**3 != (count - len(b)) // 1024**3:
                    self.event("reading_back", bytes_done=count)
        require(read_hash.hexdigest() == p["image_sha256"], "Read-back SHA-256 mismatch")
        self.event("verified", readback_verified=True, readback_sha256=read_hash.hexdigest(), bytes_done=count)
        gpt = subprocess.check_output(["/usr/sbin/gpt", "-r", "show", p["device"]], stderr=subprocess.STDOUT)
        (self.folder / "target-gpt-after.txt").write_bytes(gpt)
        self.event("ejecting")
        subprocess.run(["/usr/sbin/diskutil", "eject", p["device"]], check=True)
        self.event("completed", safely_ejected=True, physical_boot_check="pending")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--confirm-device", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    require(sys.platform == "darwin", "This disk writer is macOS-only")
    plan = json.loads(args.plan.read_text())
    validate_plan(plan, args.confirm_device)
    check_identity(plist("info", "-plist", plan["device"]), plan["expected_info"])
    print("Plan and current external-device identity: OK", flush=True)
    if not args.execute:
        print("Check-only mode. No disks opened, unmounted or written.", flush=True)
        return
    require(os.geteuid() == 0, "Execute requires administrator authentication (sudo)")
    job = Job(plan)
    try:
        job.run()
    except BaseException as exc:
        job.event("failed", error=repr(exc))
        raise


if __name__ == "__main__":
    main()
