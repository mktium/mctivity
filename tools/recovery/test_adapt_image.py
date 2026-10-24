import gzip
import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest
import uuid
import zlib

import adapt_image as a


def fixture():
    disk = bytearray(16 * a.MIB)
    sectors = len(disk) // 512
    disk[450] = 0xEE
    struct.pack_into("<II", disk, 454, 1, sectors - 1)
    disk[510:512] = b"\x55\xaa"
    entries = bytearray(16384)
    for i, (kind, start, end) in enumerate(((a.EFI, 2048, 4095),
                                           (a.LINUX, 4096, 12287),
                                           (a.SWAP, 12288, 30719))):
        e = i * 128
        entries[e:e + 16] = kind.bytes_le
        entries[e + 16:e + 32] = uuid.uuid4().bytes_le
        struct.pack_into("<QQ", entries, e + 32, start, end)
    h = bytearray(512)
    h[:8] = b"EFI PART"
    struct.pack_into("<III", h, 8, 0x10000, 92, 0)
    struct.pack_into("<QQQQ", h, 24, 1, sectors - 1, 34, sectors - 34)
    h[56:72] = uuid.uuid4().bytes_le
    struct.pack_into("<QIII", h, 72, 2, 128, 128, zlib.crc32(entries))
    disk[512:1024] = a.crc_header(h)
    disk[1024:1024 + len(entries)] = entries
    struct.pack_into("<QQ", h, 24, sectors - 1, 1)
    struct.pack_into("<Q", h, 72, sectors - 33)
    disk[-512:] = a.crc_header(h)
    disk[-33 * 512:-512] = entries
    disk[a.MIB:a.MIB + 9] = b"EFI BYTES"
    disk[2 * a.MIB:2 * a.MIB + 10] = b"ROOT BYTES"
    swap = bytearray(4096)
    struct.pack_into("<III", swap, 1024, 1, 9 * a.MIB // 4096 - 1, 0)
    swap[1036:1052] = uuid.uuid4().bytes
    swap[-10:] = b"SWAPSPACE2"
    disk[6 * a.MIB:6 * a.MIB + 4096] = swap
    disk[7 * a.MIB:7 * a.MIB + 10] = b"STALE SWAP"
    return disk


class AdapterTests(unittest.TestCase):
    def test_adapt_and_preserve_payload(self):
        disk = fixture()
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            src = d / "source.img.gz"
            src.write_bytes(gzip.compress(disk, compresslevel=1))
            expected = a.sha256(src)
            out, root, manifest = d / "adapted.img", d / "root.img", d / "manifest.json"
            a.adapt(src, expected, 12 * a.MIB, out, root, manifest)
            data = out.read_bytes()
            self.assertEqual(len(data), 12 * a.MIB)
            self.assertEqual(data[a.MIB:6 * a.MIB], disk[a.MIB:6 * a.MIB])
            self.assertEqual(root.read_bytes(), disk[2 * a.MIB:6 * a.MIB])
            self.assertEqual(data[7 * a.MIB:7 * a.MIB + 10], bytes(10))
            _, _, parts, size = a.parse_gpt(data[:a.MIB])
            self.assertEqual(size, len(data))
            self.assertEqual(parts[2]["end_lba"], 11 * 2048 - 1)
            self.assertEqual(a.crc_header(data[-512:]), data[-512:])
            self.assertEqual(data[-33 * 512:-512], data[1024:17408])
            self.assertEqual(struct.unpack_from("<I", data, 458)[0], len(data) // 512 - 1)
            result = json.loads(manifest.read_text())
            self.assertEqual(result["adapted_raw_sha256"], hashlib.sha256(data).hexdigest())
            self.assertEqual(a.sha256(src), expected)
            with self.assertRaisesRegex(ValueError, "Output already exists"):
                a.adapt(src, expected, 12 * a.MIB, out, root, manifest)

    def test_rejects_small_unaligned_or_larger_target(self):
        h = fixture()[:a.MIB]
        for size in (6 * a.MIB, 12 * a.MIB + 1, 16 * a.MIB):
            with self.subTest(size=size), self.assertRaises(ValueError):
                a.layout(h, size)

    def test_rejects_gpt_corruption(self):
        for offset in (528, 1056):
            disk = fixture()
            disk[offset] ^= 1
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                a.layout(disk[:a.MIB], 12 * a.MIB)

    def test_rejects_wrong_partition_type_and_hybrid_mbr(self):
        disk = fixture()
        disk[450] = 0x07
        with self.assertRaisesRegex(ValueError, "protective"):
            a.layout(disk[:a.MIB], 12 * a.MIB)
        disk = fixture()
        disk[466] = 0x07
        with self.assertRaisesRegex(ValueError, "Hybrid"):
            a.layout(disk[:a.MIB], 12 * a.MIB)

    def test_rejects_unsupported_swap(self):
        header = fixture()[6 * a.MIB:6 * a.MIB + 4096]
        for offset in (1024, 1032, 4095):
            changed = bytearray(header)
            changed[offset] ^= 1
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                a.swap_header(changed, 5 * a.MIB)

    def test_rejects_wrong_input_digest_before_creating_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            src = d / "source.gz"
            src.write_bytes(gzip.compress(fixture(), compresslevel=1))
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                a.adapt(src, "0" * 64, 12 * a.MIB, d / "out", d / "root", d / "manifest")
            self.assertFalse((d / "out.partial").exists())


if __name__ == "__main__":
    unittest.main()
