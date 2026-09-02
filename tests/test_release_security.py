import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("release_check", ROOT / "scripts/release_content_check.py")
scan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scan)


class ReleaseContentTests(unittest.TestCase):
    def test_generic_findings_are_redacted(self):
        samples = [
            "/" + "Users/example-person/project/file.py",
            "-----BEGIN " + "PRIVATE KEY-----",
            "ghp" + "_" + "a" * 36,
            "http://" + "example-user:example-password@example.invalid",
            ".".join(("192", "168", "45", "67")),
            "http://" + ".".join(("192", "168", "45", "67")) + "/status",
            ".".join(("192", "168", "45", "0")) + "/24",
            "API_" + "TOKEN = 'example-secret-value'",
        ]
        for sample in samples:
            with self.subTest(sample_type=samples.index(sample)):
                findings = scan.check_bytes("config.yaml", sample.encode(), ())
                self.assertTrue(findings)
                self.assertNotIn(sample, "\n".join(findings))

    def test_placeholders_loopback_and_scanner_source_are_allowed(self):
        for sample in ("127.0.0.1", 'API_TOKEN = ""', 'API_TOKEN = os.getenv("TOKEN")',
                       'API_TOKEN = "<generated-at-install>"', 'API_TOKEN = ${SITE_TOKEN}'):
            self.assertEqual([], scan.text_findings(sample))
        self.assertEqual([], scan.text_findings((ROOT / "scripts/release_content_check.py").read_text()))

    def test_private_terms_and_sensitive_file_types(self):
        value = "private-" + "example-identity"
        self.assertEqual([(2, "private-deny-term")], scan.text_findings("title\n" + value, [value]))
        for name in (".env", ".env.production", "server.key", "server.pem", "identity.p12", "id_ed25519"):
            self.assertTrue(scan.forbidden_path(name), name)
        self.assertFalse(scan.forbidden_path("web_security.py"))
        self.assertTrue(scan.forbidden_path("mctivity_hmi/assets/servo-diagnostic-vendor/data.js"))
        self.assertTrue(scan.forbidden_path("modules/feature/hmi/servo/diagnostic/module.json"))

    def test_history_catches_removed_content_without_echoing_it(self):
        with tempfile.TemporaryDirectory() as directory:
            def git(*args):
                return subprocess.check_output(["git", *args], cwd=directory, stderr=subprocess.DEVNULL).decode().strip()
            git("init")
            git("config", "user.name", "Release Test")
            git("config", "user.email", "release-test@example.invalid")
            path = Path(directory) / "config.yaml"
            path.write_text("safe: true\n")
            git("add", ".")
            git("commit", "-m", "Baseline")
            base = git("rev-parse", "HEAD")
            secret = "ghp" + "_" + "b" * 36
            path.write_text("value: " + secret + "\n")
            excluded = Path(directory) / "servo-diagnostic-vendor" / "data.js"
            excluded.parent.mkdir()
            excluded.write_text("const sample = {};\n")
            git("add", ".")
            git("commit", "-m", "Temporary fixture")
            path.write_text("safe: true\n")
            excluded.unlink()
            git("commit", "-am", "Remove fixture")
            result = subprocess.run([sys.executable, str(ROOT / "scripts/release_content_check.py"),
                                     "--history", base], cwd=directory, capture_output=True, text=True)
            self.assertEqual(1, result.returncode)
            self.assertIn("access-token", result.stdout)
            self.assertIn("prohibited-release-file", result.stdout)
            self.assertNotIn(secret, result.stdout + result.stderr)

    def test_source_archive_without_git_is_scanned(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            path.write_text("safe: true\n")
            command = [sys.executable, str(ROOT / "scripts/release_content_check.py")]
            result = subprocess.run(command, cwd=directory, capture_output=True, text=True)
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            secret = "ghp" + "_" + "c" * 36
            path.write_text("value: " + secret + "\n")
            result = subprocess.run(command, cwd=directory, capture_output=True, text=True)
            self.assertEqual(1, result.returncode)
            self.assertIn("access-token", result.stdout)
            self.assertNotIn(secret, result.stdout + result.stderr)
            result = subprocess.run(command + ["--history", "main"], cwd=directory,
                                    capture_output=True, text=True)
            self.assertEqual(2, result.returncode)
            self.assertIn("--history requires", result.stderr)


class MotionToolGuardTests(unittest.TestCase):
    def test_guard_does_not_link_or_access_hardware(self):
        compiler = shutil.which("cc")
        if not compiler:
            self.skipTest("C compiler unavailable")
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "guard_test.c"
            binary = Path(directory) / "guard_test"
            source.write_text('#include "motion_test_guard.h"\n'
                              'int main(int argc, char **argv) {\n'
                              'if (!acknowledge_motion_test(&argc, &argv)) return 2;\n'
                              'printf("%s:%d:%s\\n", argv[0], argc, argc > 1 ? argv[1] : "empty");\n'
                              'return argv[argc] != NULL;\n}\n')
            subprocess.run([compiler, "-std=c11", "-Wall", "-Wextra", "-Werror", "-I",
                            str(ROOT / "mctivity_pdo_monitor"), str(source), "-o", str(binary)], check=True)
            for arguments in ([], ["10"], ["--help"]):
                result = subprocess.run([str(binary), *arguments], capture_output=True, text=True)
                self.assertEqual(2, result.returncode)
                self.assertIn("No hardware has been accessed", result.stderr)
            result = subprocess.run([str(binary), "--confirm-motion", "10"], capture_output=True, text=True)
            self.assertEqual(0, result.returncode)
            self.assertEqual(f"{binary}:2:10\n", result.stdout)

    def test_all_motion_tools_guard_before_master_access(self):
        for name in ("mctivity_enable_hold", "mctivity_csp_step", "mctivity_csp_roundtrip"):
            source = (ROOT / "mctivity_pdo_monitor" / f"{name}.c").read_text()
            main = source[source.index("int main("):]
            self.assertLess(main.index("acknowledge_motion_test"), main.index("ecrt_request_master"))
        makefile = (ROOT / "mctivity_pdo_monitor/Makefile").read_text()
        default = next(line for line in makefile.splitlines() if line.startswith("all:"))
        self.assertNotIn("csp", default)
        self.assertNotIn("enable_hold", default)


if __name__ == "__main__":
    unittest.main()
