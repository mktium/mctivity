from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import prepare_clean_image as c


class CleanDerivativeTests(unittest.TestCase):
    def test_explicit_acknowledgement_required_before_reading_input(self):
        args = ["prepare_clean_image.py", "--source-folder", "/nonexistent/source",
                "--recovered-root", "/nonexistent/root", "--recovered-efi", "/nonexistent/efi",
                "--output-folder", "/nonexistent/output"]
        with patch("sys.argv", args), self.assertRaisesRegex(ValueError, "acknowledgement"):
            c.main()

    def test_patches_only_selected_region_and_retains_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            parent, image, recovered = d / "parent", d / "image", d / "recovered"
            original = b"P" * 4096 + b"A" * 8192 + b"S" * 4096
            parent.write_bytes(original)
            c.clone(parent, image)
            recovered.write_bytes(b"B" * 4096 + b"A" * 4096)
            self.assertEqual(c.patch_region(image, recovered, 4096, 8192), 1)
            self.assertEqual(image.read_bytes(), b"P" * 4096 + recovered.read_bytes() + b"S" * 4096)
            self.assertEqual(parent.read_bytes(), original)
            with self.assertRaisesRegex(ValueError, "length"):
                c.patch_region(image, recovered, 4096, 4096)
            with self.assertRaisesRegex(ValueError, "already exists"):
                c.clone(parent, image)


if __name__ == "__main__":
    unittest.main()
