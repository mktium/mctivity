#!/usr/bin/env python3
"""Build a larger GPT disk from a frozen root clone, ESP and source GPT.

Offline only: input partitions are regular files, never host devices. Root must
already have been resized and checked to exactly match planned geometry.
"""
import argparse
import gzip
import json
from pathlib import Path
import struct
import uuid
import zlib
from adapt_image import MIB, SECTOR, crc_header, parse_gpt, require, sha256, sparse_write, swap_header


def geometry(head, target):
    header, entries, parts, source_size = parse_gpt(head)
    require(target > source_size and target % SECTOR == 0, 'Require a larger sector-aligned target')
    sectors = target // SECTOR
    swap_sectors = parts[2]['end_lba'] - parts[2]['start_lba'] + 1
    end = ((sectors - 33) // 2048) * 2048
    swap_start = ((end - swap_sectors) // 2048) * 2048
    require(swap_start > parts[2]['start_lba'], 'Root would not grow')
    table = bytearray(entries)
    struct.pack_into('<Q', table, 128 + 40, swap_start - 1)
    struct.pack_into('<QQ', table, 256 + 32, swap_start, end - 1)
    primary = bytearray(header)
    struct.pack_into('<Q', primary, 32, sectors - 1)
    struct.pack_into('<Q', primary, 48, sectors - 34)
    struct.pack_into('<I', primary, 88, zlib.crc32(table) & 0xFFFFFFFF)
    primary = crc_header(primary)
    backup = bytearray(primary)
    struct.pack_into('<QQ', backup, 24, sectors - 1, 1)
    struct.pack_into('<Q', backup, 72, sectors - 33)
    first = bytearray(head)
    struct.pack_into('<I', first, 458, min(sectors - 1, 0xFFFFFFFF))
    first[512:1024] = primary
    first[1024:1024 + len(table)] = table
    result = {'target_bytes': target, 'source_bytes': source_size,
              'root_start_bytes': parts[1]['start_lba'] * SECTOR,
              'root_bytes': (swap_start - parts[1]['start_lba']) * SECTOR,
              'efi_start_bytes': parts[0]['start_lba'] * SECTOR,
              'efi_bytes': (parts[0]['end_lba'] - parts[0]['start_lba'] + 1) * SECTOR,
              'swap_start_bytes': swap_start * SECTOR, 'swap_bytes': (end - swap_start) * SECTOR}
    return bytes(first), bytes(table), crc_header(backup), result


def assemble(source, root, target, folder):
    first, table, backup, report = geometry((source / 'disk-head.bin').read_bytes(), target)
    require(root.is_file() and root.stat().st_size == report['root_bytes'], 'Root must match planned size')
    image = folder / 'adapted.img'
    require(not image.exists() and not image.with_suffix('.img.partial').exists(), 'Output already exists')
    root_hash = sha256(root)
    temp = image.with_suffix('.img.partial')
    with temp.open('xb') as out:
        out.truncate(target)
        sparse_write(out, 0, first)
        with gzip.open(source / 'efi.img.gz', 'rb') as efi:
            offset = 0
            while True:
                data = efi.read(8 * MIB)
                if not data:
                    break
                require(offset + len(data) <= report['efi_bytes'], 'Oversized ESP')
                sparse_write(out, report['efi_start_bytes'] + offset, data)
                offset += len(data)
            require(offset == report['efi_bytes'], 'Short ESP')
        with root.open('rb') as inp:
            offset = 0
            while True:
                data = inp.read(8 * MIB)
                if not data:
                    break
                sparse_write(out, report['root_start_bytes'] + offset, data)
                offset += len(data)
        header = swap_header((source / 'swap-header.bin').read_bytes(), report['swap_bytes'])
        out.seek(report['swap_start_bytes'])
        out.write(header)
        out.seek(target - 33 * 512)
        out.write(table)
        out.write(backup)
    # ESP hash from the partition stream, not from compressed bytes.
    import hashlib
    h = hashlib.sha256()
    with gzip.open(source / 'efi.img.gz', 'rb') as f:
        for b in iter(lambda: f.read(8 * MIB), b''):
            h.update(b)
    report.update(root_sha256=root_hash, efi_sha256=h.hexdigest(),
                  swap_uuid=str(uuid.UUID(bytes=header[1036:1052])),
                  adapted_raw_sha256=sha256(temp), source_capture='frozen allocated ext4 data and metadata',
                  source_root_qcow2_sha256=json.loads((source / 'checksums.json').read_text())['root.qcow2']['sha256'])
    temp.replace(image)
    (folder / 'manifest.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2), flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--root', type=Path)
    p.add_argument('--target-bytes', type=int, required=True)
    p.add_argument('--output', type=Path)
    args = p.parse_args()
    if args.output is None:
        print(json.dumps(geometry((args.source / 'disk-head.bin').read_bytes(), args.target_bytes)[3], indent=2))
    else:
        require(args.root is not None, 'Root file is required')
        args.output.mkdir(parents=True, exist_ok=True)
        assemble(args.source, args.root, args.target_bytes, args.output)


if __name__ == '__main__':
    main()
