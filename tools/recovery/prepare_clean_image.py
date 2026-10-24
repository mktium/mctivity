#!/usr/bin/env python3
"""Make a NEW derivative from independently recovered validation copies.

Requires explicit acknowledgement. Never edits the source image or its manifest.
Recovered root/ESP must be produced with recorded, approved filesystem tools;
run validate_image.py on the resulting candidate before any disk deployment.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys

from adapt_image import MIB, require, sha256


def clone(source, destination):
    require(not destination.exists(), "Destination already exists: " + str(destination))
    if sys.platform == "darwin":
        subprocess.run(["/bin/cp", "-c", str(source), str(destination)], check=True)
    else:
        subprocess.run(["cp", "--reflink=auto", "--sparse=always", str(source), str(destination)], check=True)


def patch_region(image, source_region, offset, size):
    require(source_region.stat().st_size == size, "Recovered partition length mismatch")
    changed = 0
    with image.open("r+b") as output, source_region.open("rb") as source:
        pos = 0
        while pos < size:
            data = source.read(min(8 * MIB, size - pos))
            require(data, "Recovered partition ended early")
            output.seek(offset + pos)
            previous = output.read(len(data))
            if data != previous:
                for local in range(0, len(data), 4096):
                    block = data[local:local + 4096]
                    if block != previous[local:local + 4096]:
                        output.seek(offset + pos + local)
                        output.write(block)
                        changed += 1
            pos += len(data)
    return changed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-folder", type=Path, required=True)
    parser.add_argument("--recovered-root", type=Path, required=True)
    parser.add_argument("--recovered-efi", type=Path, required=True)
    parser.add_argument("--output-folder", type=Path, required=True)
    parser.add_argument("--acknowledge-filesystem-metadata-changes", action="store_true")
    args = parser.parse_args()
    require(args.acknowledge_filesystem_metadata_changes, "Explicit acknowledgement is required")
    old = json.loads((args.source_folder / "manifest.json").read_text())
    original = args.source_folder / "adapted.img"
    require(sha256(original) == old["adapted_raw_sha256"], "Source derivative SHA-256 mismatch")
    require(not args.output_folder.exists(), "Output folder already exists")
    args.output_folder.mkdir(parents=True)
    result = args.output_folder / "adapted.img"
    clone(original, result)
    changes = {}
    for index, name, replacement in ((0, "efi", args.recovered_efi), (1, "root", args.recovered_root)):
        part = old["source_partitions"][index]
        offset, size = part["start_lba"] * 512, (part["end_lba"] - part["start_lba"] + 1) * 512
        changes[name] = patch_region(result, replacement, offset, size)
    clone(args.recovered_root, args.output_folder / "root.img")
    report = dict(old)
    report.update({"parent_adapted_raw_sha256": old["adapted_raw_sha256"],
                   "original_root_sha256": old["root_sha256"],
                   "original_efi_sha256": old["efi_sha256"],
                   "root_sha256": sha256(args.recovered_root), "efi_sha256": sha256(args.recovered_efi),
                   "adapted_raw_sha256": sha256(result), "root_filesystem_modified": changes["root"] > 0,
                   "efi_filesystem_modified": changes["efi"] > 0, "filesystem_metadata_recovery_acknowledged": True,
                   "changed_4096_byte_blocks": changes, "root_filesystem_check": "pending",
                   "target_disk_written": False})
    (args.output_folder / "manifest.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    main()
