import copy
import json
from pathlib import Path
import tempfile
import unittest

import macos_restore as m


def identity():
    return {"DeviceIdentifier": "disk9", "DeviceNode": "/dev/disk9", "MediaName": "TEST DISK",
            "DeviceTreePath": "TEST PORT", "TotalSize": 1024, "DeviceBlockSize": 512,
            "Internal": False, "WholeDisk": True, "BusProtocol": "USB"}


class WriterGuardTests(unittest.TestCase):
    def test_rejects_identity_changes_and_internal_disks(self):
        expected = identity()
        m.check_identity(expected, expected)
        for key, value in (("Internal", True), ("WholeDisk", False), ("DeviceNode", "/dev/disk0"),
                           ("DeviceTreePath", "ANOTHER PORT"), ("TotalSize", 2048),
                           ("DeviceBlockSize", 4096), ("BusProtocol", "PCI-Express")):
            changed = copy.deepcopy(expected)
            changed[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                m.check_identity(changed, expected)

    def test_partition_identity_ignores_mountpoint_but_retains_guid(self):
        before = {"AllDisksAndPartitions": [{"Content": "GPT", "Size": 1024,
                  "Partitions": [{"DeviceIdentifier": "disk9s1", "Content": "EFI",
                                  "Size": 512, "DiskUUID": "A", "MountPoint": "/Volumes/X"}]}]}
        unmounted = copy.deepcopy(before)
        del unmounted["AllDisksAndPartitions"][0]["Partitions"][0]["MountPoint"]
        self.assertEqual(m.partition_identity(before), m.partition_identity(unmounted))
        unmounted["AllDisksAndPartitions"][0]["Partitions"][0]["DiskUUID"] = "B"
        self.assertNotEqual(m.partition_identity(before), m.partition_identity(unmounted))

    def test_plan_requires_exact_device_length_and_all_validation_checks(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            image, validation = folder / "disk.img", folder / "validation.json"
            image.write_bytes(bytes(1024))
            checks = {name: True for name in ("gpt", "root_matches_manifest", "root_e2fsck_readonly",
                      "efi_matches_manifest", "efi_fat_readonly", "x64_fallback_efi", "swap_mkswap_reference")}
            report = {"result": "passed", "image_sha256": m.digest(image), "checks": checks}
            validation.write_text(json.dumps(report))
            plan = {"device": "/dev/disk9", "expected_info": identity(), "image_bytes": 1024,
                    "image": str(image), "validation": str(validation), "output_dir": str(folder / "job"),
                    "image_sha256": m.digest(image)}
            m.validate_plan(plan, "/dev/disk9")
            for device in ("/dev/disk0", "/dev/disk9s1", ""):
                with self.subTest(device=device), self.assertRaises(ValueError):
                    m.validate_plan(plan, device)
            for name in checks:
                bad = copy.deepcopy(report)
                bad["checks"][name] = False
                validation.write_text(json.dumps(bad))
                with self.subTest(check=name), self.assertRaises(ValueError):
                    m.validate_plan(plan, "/dev/disk9")
            report["filesystem_metadata_recovered"] = True
            validation.write_text(json.dumps(report))
            with self.assertRaisesRegex(ValueError, "explicit approval"):
                m.validate_plan(plan, "/dev/disk9")
            plan["filesystem_metadata_recovery_approved"] = True
            m.validate_plan(plan, "/dev/disk9")
            with self.assertRaisesRegex(ValueError, "explicit user waiver"):
                m.validate_plan(plan, "/dev/disk9", skip_backup=True)
            plan["backup_waived_by_user"] = True
            with self.assertRaisesRegex(ValueError, "explicit user waiver"):
                m.validate_plan(plan, "/dev/disk9")
            m.validate_plan(plan, "/dev/disk9", skip_backup=True)


if __name__ == "__main__":
    unittest.main()
