import struct
import tempfile
import unittest
from pathlib import Path
from assemble_snapshot import geometry
from adapt_image import MIB, parse_gpt
from test_adapt_image import fixture
from validate_image import validate_gpt


class GrowthTests(unittest.TestCase):
    def test_larger_disk_moves_swap_and_preserves_identities(self):
        source = fixture()
        first, table, backup, report = geometry(source[:MIB], 32 * MIB)
        result = bytearray(32 * MIB)
        result[:MIB] = first
        result[-33 * 512:-512] = table
        result[-512:] = backup
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'disk'
            p.write_bytes(result)
            with p.open('rb') as f:
                parts = validate_gpt(f, len(result))
        old = parse_gpt(source[:MIB])[2]
        new = parse_gpt(first)[2]
        self.assertEqual([p['guid'] for p in old], [p['guid'] for p in new])
        self.assertEqual(old[0], new[0])
        self.assertEqual(old[1]['start_lba'], new[1]['start_lba'])
        self.assertGreater(parts[1][1], 4 * MIB)
        self.assertEqual(parts[2][1], 9 * MIB)
        self.assertEqual(parts[1][0] + parts[1][1], parts[2][0])
        self.assertEqual(report['root_bytes'], parts[1][1])

    def test_refuses_shrink_and_bad_alignment(self):
        for size in [12 * MIB, 16 * MIB, 32 * MIB + 1]:
            with self.subTest(size=size), self.assertRaises(ValueError):
                geometry(fixture()[:MIB], size)


if __name__ == '__main__':
    unittest.main()
