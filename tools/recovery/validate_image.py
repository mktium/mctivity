#!/usr/bin/env python3
"""Independent Linux checks. Mount image directory read-only; only /tmp is written."""
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import uuid
import zlib


def check(value, message):
    if not value:
        raise ValueError(message)


def run(args):
    print("RUN:", " ".join(map(str, args)), flush=True)
    subprocess.run(list(map(str, args)), check=True)


def region_hash(f, offset, size):
    h = hashlib.sha256()
    f.seek(offset)
    while size:
        b = f.read(min(size, 8 * 1024 * 1024))
        check(b, "Unexpected EOF")
        h.update(b)
        size -= len(b)
    return h.hexdigest()


def validate_gpt(f, size):
    arrays = []
    for lba, backup in ((1, size // 512 - 1), (size // 512 - 1, 1)):
        f.seek(lba * 512)
        header = bytearray(f.read(512))
        check(header[:8] == b"EFI PART", "Missing GPT signature")
        length, expected = struct.unpack_from("<II", header, 12)
        check(92 <= length <= 512, "Invalid GPT length")
        struct.pack_into("<I", header, 16, 0)
        check(zlib.crc32(header[:length]) & 0xFFFFFFFF == expected, "GPT CRC mismatch")
        check(struct.unpack_from("<QQ", header, 24) == (lba, backup), "GPT locations mismatch")
        check(struct.unpack_from("<QQ", header, 40) == (34, size // 512 - 34), "GPT usable boundaries mismatch")
        table, count, entrysize, crc = struct.unpack_from("<QIII", header, 72)
        check((count, entrysize) == (128, 128), "Unexpected GPT array format")
        check(table == (2 if lba == 1 else size // 512 - 33), "Wrong GPT array location")
        f.seek(table * 512)
        entries = f.read(count * entrysize)
        check(zlib.crc32(entries) & 0xFFFFFFFF == crc, "GPT array CRC mismatch")
        arrays.append(entries)
    check(arrays[0] == arrays[1], "Primary and backup GPT arrays differ")
    parts = []
    for i in range(128):
        e = arrays[0][i * 128:(i + 1) * 128]
        if any(e[:16]):
            first, last = struct.unpack_from("<QQ", e, 32)
            check(34 <= first <= last <= size // 512 - 34, "Partition boundary error")
            parts.append((first * 512, (last - first + 1) * 512))
    check(len(parts) == 3, "Expected exactly three partitions")
    for left, right in zip(parts, parts[1:]):
        check(left[0] + left[1] <= right[0], "Overlapping partitions")
    return parts


def check_efi(path):
    with path.open("rb") as f:
        def read(offset, size):
            f.seek(offset)
            return f.read(size)
        b = read(0, 512)
        bps = struct.unpack_from("<H", b, 11)[0]
        spc, nf = b[13], b[16]
        reserved = struct.unpack_from("<H", b, 14)[0]
        fatsz = struct.unpack_from("<I", b, 36)[0]
        check(bps == 512 and fatsz > 0, "Expected FAT32 EFI system partition")
        fat = read(reserved * bps, fatsz * bps)
        data = (reserved + nf * fatsz) * bps
        def cluster(c):
            out, seen = bytearray(), set()
            while 2 <= c < 0x0FFFFFF8:
                check(c not in seen, "FAT cluster loop")
                seen.add(c)
                out.extend(read(data + (c - 2) * spc * bps, spc * bps))
                c = struct.unpack_from("<I", fat, c * 4)[0] & 0x0FFFFFFF
            return bytes(out)
        def find(buf, name):
            for offset in range(0, len(buf), 32):
                e = buf[offset:offset + 32]
                if not e or not e[0]:
                    break
                if e[0] == 0xE5 or e[11] == 0x0F or e[11] & 8:
                    continue
                if e[:11] == name:
                    c = struct.unpack_from("<H", e, 26)[0] + (struct.unpack_from("<H", e, 20)[0] << 16)
                    return cluster(c)
            raise ValueError("Missing EFI path component: " + repr(name))
        root = cluster(struct.unpack_from("<I", b, 44)[0])
        boot = find(find(find(root, b"EFI        "), b"BOOT       "), b"BOOTX64 EFI")
        check(boot[:2] == b"MZ", "Invalid EFI executable")
        off = struct.unpack_from("<I", boot, 60)[0]
        check(boot[off:off + 4] == b"PE\0\0" and struct.unpack_from("<H", boot, off + 4)[0] == 0x8664,
              "Fallback EFI executable is not x86-64")


def main():
    folder = Path(sys.argv[1])
    other_checks_only = sys.argv[2:] == ["--other-checks-only"]
    check(not sys.argv[2:] or other_checks_only, "Unsupported arguments")
    report = json.loads((folder / "manifest.json").read_text())
    image = folder / "adapted.img"
    check(image.stat().st_size == report["target_bytes"], "Image length mismatch")
    with image.open("rb") as f:
        parts = validate_gpt(f, report["target_bytes"])
        check(region_hash(f, *parts[0]) == report["efi_sha256"], "EFI payload changed")
        print("EFI payload matches manifest: OK", flush=True)
        check(region_hash(f, *parts[1]) == report["root_sha256"], "Root payload changed")
        check(region_hash((folder / "root.img").open("rb"), 0, parts[1][1]) == report["root_sha256"],
              "Root check image differs")
        print("Root payload and root check image match manifest: OK", flush=True)
        # Sparse ESP copy in private container /tmp; host files remain read-only.
        with Path("/tmp/efi.img").open("wb") as esp:
            esp.truncate(parts[0][1])
            f.seek(parts[0][0])
            copied = 0
            while copied < parts[0][1]:
                b = f.read(min(1024 * 1024, parts[0][1] - copied))
                check(b, "Incomplete EFI partition")
                if b != bytes(len(b)):
                    esp.seek(copied)
                    esp.write(b)
                copied += len(b)
        f.seek(parts[2][0])
        actual_swap = f.read(4096)
    run(["sgdisk", "--verify", image])
    if not other_checks_only:
        run(["e2fsck", "-f", "-n", folder / "root.img"])
    run(["fsck.fat", "-n", "/tmp/efi.img"])
    check_efi(Path("/tmp/efi.img"))
    with Path("/tmp/swap-reference.img").open("wb") as f:
        f.truncate(parts[2][1])
    Path("/tmp/swap-reference.img").chmod(0o600)
    label = actual_swap[1052:1068].rstrip(b"\0").decode("utf-8")
    args = ["mkswap", "--pagesize", "4096", "--uuid", report["swap_uuid"]]
    if label:
        args.extend(["--label", label])
    run(args + ["/tmp/swap-reference.img"])
    with Path("/tmp/swap-reference.img").open("rb") as f:
        check(f.read(4096) == actual_swap, "Swap header differs from util-linux mkswap reference")
    check(str(uuid.UUID(bytes=actual_swap[1036:1052])) == report["swap_uuid"], "Swap UUID changed")
    if other_checks_only:
        print("OTHER CHECKS PASSED; ROOT FILESYSTEM CHECK EXCLUDED. NOT A DEPLOYMENT APPROVAL.", flush=True)
    else:
        print("VALIDATION PASSED: GPT, manifest-matching EFI/root, e2fsck -fn, fsck.fat -n, x64 fallback EFI, mkswap reference.", flush=True)


if __name__ == "__main__":
    main()
