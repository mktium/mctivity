#!/usr/bin/env python3
"""Offline adapter for a 512-byte GPT image whose third/final partition is swap.

Never opens a device or changes the input. Outputs a sparse raw image and a
separate root-partition image for independent, read-only e2fsck validation.
"""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import struct
import uuid
import zlib

SECTOR = 512
MIB = 1024 * 1024
PAGE = 4096
EFI = uuid.UUID("c12a7328-f81f-11d2-ba4b-00a0c93ec93b")
LINUX = uuid.UUID("0fc63daf-8483-4772-8e79-3d69d8477de4")
SWAP = uuid.UUID("0657fd6d-a4ab-43c4-84e5-0933c84b4f4f")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(8 * MIB), b""):
            h.update(b)
    return h.hexdigest()


def crc_header(header):
    header = bytearray(header)
    size = struct.unpack_from("<I", header, 12)[0]
    require(92 <= size <= SECTOR, "Unsupported GPT header size")
    struct.pack_into("<I", header, 16, 0)
    struct.pack_into("<I", header, 16, zlib.crc32(header[:size]) & 0xFFFFFFFF)
    return bytes(header)


def parse_gpt(head):
    head = bytes(head)
    require(head[510:512] == b"\x55\xaa", "Missing protective MBR")
    require(head[450] == 0xEE, "Expected protective MBR partition")
    require(not any(head[462:510]), "Hybrid MBR layouts are not supported")
    header = bytes(head[SECTOR:2 * SECTOR])
    require(header[:8] == b"EFI PART", "Not a GPT disk image")
    require(crc_header(header) == header, "Invalid primary GPT CRC")
    current, backup, first, last = struct.unpack_from("<QQQQ", header, 24)
    table_lba, count, entry_size, crc = struct.unpack_from("<QIII", header, 72)
    require(current == 1 and table_lba == 2, "Unsupported GPT locations")
    require(count == 128 and entry_size == 128 and first == 34,
            "Only standard 128-entry, 512-byte-sector GPT is supported")
    entries = head[table_lba * SECTOR:table_lba * SECTOR + count * entry_size]
    require(zlib.crc32(entries) & 0xFFFFFFFF == crc, "Invalid partition-array CRC")
    partitions = []
    for i in range(count):
        e = entries[i * entry_size:(i + 1) * entry_size]
        if not any(e[:16]):
            continue
        start, end = struct.unpack_from("<QQ", e, 32)
        require(first <= start <= end <= last, "Partition outside usable disk")
        partitions.append({"index": i + 1, "type": str(uuid.UUID(bytes_le=e[:16])),
                           "guid": str(uuid.UUID(bytes_le=e[16:32])),
                           "start_lba": start, "end_lba": end})
    require([p["index"] for p in partitions] == [1, 2, 3], "Expected exactly partitions 1, 2, 3")
    require([p["type"] for p in partitions] == [str(EFI), str(LINUX), str(SWAP)],
            "Expected EFI, Linux filesystem, final Linux swap")
    require(partitions[0]["end_lba"] < partitions[1]["start_lba"] and
            partitions[1]["end_lba"] < partitions[2]["start_lba"], "Overlapping partitions")
    require(partitions[0]["start_lba"] >= 2048, "EFI partition must start at or after 1 MiB")
    return header, entries, partitions, (backup + 1) * SECTOR


def layout(head, target_bytes):
    header, entries, partitions, source_bytes = parse_gpt(head)
    require(target_bytes % SECTOR == 0, "Target size must be a multiple of 512 bytes")
    require(target_bytes < source_bytes, "This adapter only shrinks the final swap partition")
    sectors = target_bytes // SECTOR
    last_usable = sectors - 34
    end_exclusive = ((sectors - 33) // 2048) * 2048
    swap_start = partitions[2]["start_lba"]
    require(end_exclusive > swap_start and (end_exclusive - swap_start) * SECTOR >= 10 * PAGE,
            "Target too small: EFI and root must fit unchanged, with room for swap and GPT")
    require(end_exclusive - 1 < partitions[2]["end_lba"], "Swap would not shrink")
    entries = bytearray(entries)
    struct.pack_into("<Q", entries, 2 * 128 + 40, end_exclusive - 1)
    entries = bytes(entries)
    primary = bytearray(header)
    struct.pack_into("<Q", primary, 32, sectors - 1)
    struct.pack_into("<Q", primary, 48, last_usable)
    struct.pack_into("<I", primary, 88, zlib.crc32(entries) & 0xFFFFFFFF)
    primary = crc_header(primary)
    secondary = bytearray(primary)
    struct.pack_into("<QQ", secondary, 24, sectors - 1, 1)
    struct.pack_into("<Q", secondary, 72, sectors - 33)
    secondary = crc_header(secondary)
    mbr = bytearray(head[:SECTOR])
    struct.pack_into("<I", mbr, 458, min(sectors - 1, 0xFFFFFFFF))
    first_mib = bytearray(head)
    first_mib[:SECTOR] = mbr
    first_mib[SECTOR:2 * SECTOR] = primary
    first_mib[2 * SECTOR:2 * SECTOR + len(entries)] = entries
    result = {"source_bytes": source_bytes, "target_bytes": target_bytes,
              "source_partitions": partitions,
              "swap_start_bytes": swap_start * SECTOR,
              "swap_bytes": (end_exclusive - swap_start) * SECTOR,
              "swap_end_lba": end_exclusive - 1,
              "backup_table_offset": (sectors - 33) * SECTOR,
              "backup_header_offset": (sectors - 1) * SECTOR}
    return bytes(first_mib), entries, secondary, result


def swap_header(source, size):
    require(len(source) == PAGE and source[-10:] == b"SWAPSPACE2", "Expected 4096-byte Linux swap v1 header")
    version, _, bad = struct.unpack_from("<III", source, 1024)
    require(version == 1 and bad == 0, "Unsupported swap version or bad-page map")
    require(size % PAGE == 0, "Swap size must be page-aligned")
    # Reinitialize swap: retain UUID and label, discard stale pages/reserved bytes.
    header = bytearray(PAGE)
    struct.pack_into("<III", header, 1024, 1, size // PAGE - 1, 0)
    header[1036:1068] = source[1036:1068]
    header[-10:] = b"SWAPSPACE2"
    return bytes(header)


def sparse_write(f, offset, data):
    for start in range(0, len(data), MIB):
        block = data[start:start + MIB]
        if block != bytes(len(block)):
            f.seek(offset + start)
            f.write(block)


def adapt(source, expected_sha, target_bytes, output, root_copy, manifest):
    source, output, root_copy, manifest = map(Path, (source, output, root_copy, manifest))
    require(sha256(source) == expected_sha, "Source compressed SHA-256 mismatch")
    for p in (output, root_copy, manifest):
        require(not p.exists() and not Path(str(p) + ".partial").exists(), "Output already exists: " + str(p))
        p.parent.mkdir(parents=True, exist_ok=True)
    out_tmp, root_tmp = Path(str(output) + ".partial"), Path(str(root_copy) + ".partial")
    with gzip.open(source, "rb") as reader, out_tmp.open("xb") as out, root_tmp.open("xb") as root:
        head = reader.read(MIB)
        first, entries, secondary, result = layout(head, target_bytes)
        original_header, original_entries, parts, original_bytes = parse_gpt(head)
        root_start = parts[1]["start_lba"] * SECTOR
        root_end = (parts[1]["end_lba"] + 1) * SECTOR
        efi_start = parts[0]["start_lba"] * SECTOR
        efi_end = (parts[0]["end_lba"] + 1) * SECTOR
        out.truncate(target_bytes)
        root.truncate(root_end - root_start)
        sparse_write(out, 0, first)
        root_hash, efi_hash = hashlib.sha256(), hashlib.sha256()
        swap = bytearray()
        tail = b""
        offset = len(head)
        while True:
            chunk = reader.read(8 * MIB)
            if not chunk:
                break  # gzip checks its CRC and complete uncompressed size at EOF.
            stop = offset + len(chunk)
            if offset < result["swap_start_bytes"]:
                sparse_write(out, offset, chunk[:result["swap_start_bytes"] - offset])
            for a, b, hasher, writer in ((root_start, root_end, root_hash, root),
                                        (efi_start, efi_end, efi_hash, None)):
                lo, hi = max(offset, a), min(stop, b)
                if lo < hi:
                    data = chunk[lo - offset:hi - offset]
                    hasher.update(data)
                    if writer is not None:
                        sparse_write(writer, lo - a, data)
            lo, hi = max(offset, result["swap_start_bytes"]), min(stop, result["swap_start_bytes"] + PAGE)
            if lo < hi:
                swap.extend(chunk[lo - offset:hi - offset])
            tail = (tail + chunk)[-33 * SECTOR:]
            offset = stop
            if offset // (1024 * MIB) != (offset - len(chunk)) // (1024 * MIB):
                print("Source processed: %.1f GiB" % (offset / 2**30), flush=True)
        require(offset == original_bytes, "Source image size disagrees with GPT")
        require(tail[:128 * 128] == original_entries, "Backup GPT entries differ")
        backup = tail[-SECTOR:]
        require(crc_header(backup) == backup and backup[:8] == b"EFI PART", "Invalid source backup GPT")
        require(struct.unpack_from("<QQ", backup, 24) == (original_bytes // SECTOR - 1, 1),
                "Invalid source backup GPT locations")
        new_swap = swap_header(bytes(swap), result["swap_bytes"])
        sparse_write(out, result["swap_start_bytes"], new_swap)
        sparse_write(out, result["backup_table_offset"], entries)
        sparse_write(out, result["backup_header_offset"], secondary)
        for f in (out, root):
            f.flush()
            os.fsync(f.fileno())
    require(sha256(root_tmp) == root_hash.hexdigest(), "Root partition copy mismatch")
    result.update({"source_gzip_sha256": expected_sha, "efi_sha256": efi_hash.hexdigest(),
                   "root_sha256": root_hash.hexdigest(), "root_bytes": root_end - root_start,
                   "swap_uuid": str(uuid.UUID(bytes=bytes(swap[1036:1052]))),
                   "adapted_raw_sha256": sha256(out_tmp),
                   "root_filesystem_modified": False, "root_filesystem_check": "pending",
                   "target_disk_written": False})
    out_tmp.replace(output)
    root_tmp.replace(root_copy)
    with manifest.open("x") as f:
        json.dump(result, f, indent=2)
        f.write("\n")
    print(json.dumps(result, indent=2), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--target-bytes", required=True, type=int)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--root-copy", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    args = parser.parse_args()
    adapt(args.source, args.sha256, args.target_bytes, args.output, args.root_copy, args.manifest)


if __name__ == "__main__":
    main()
