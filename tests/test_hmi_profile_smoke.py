import json
import os
import socket
import subprocess
import sys
import time
import unittest
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def available_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class HmiProfileSmokeTests(unittest.TestCase):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def test_each_profile_serves_capabilities_and_health(self):
        for profile in ("minimal", "standard", "full"):
            with self.subTest(profile=profile):
                port = available_port()
                env = os.environ.copy()
                env.update(
                    {
                        "MCTIVITY_PROFILE": profile,
                        "MCTIVITY_WEB_HOST": "127.0.0.1",
                        "MCTIVITY_WEB_PORT": str(port),
                    }
                )
                process = subprocess.Popen(
                    [sys.executable, "mctivity_hmi.py"],
                    cwd=ROOT / "mctivity_hmi",
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                try:
                    base = f"http://127.0.0.1:{port}"
                    capabilities = self._wait_for_json(process, f"{base}/api/capabilities")
                    health = self._wait_for_json(process, f"{base}/api/health/modular")
                    self.assertEqual(profile, capabilities["profile"])
                    self.assertEqual([], capabilities["warnings"])
                    self.assertEqual("healthy", health["status"])
                finally:
                    process.terminate()
                    try:
                        process.communicate(timeout=3.0)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.communicate(timeout=3.0)

    def _wait_for_json(self, process, url):
        deadline = time.monotonic() + 5.0
        last_error = None
        while time.monotonic() < deadline:
            if process.poll() is not None:
                stdout, stderr = process.communicate()
                self.fail(f"HMI exited before serving {url}\nstdout: {stdout}\nstderr: {stderr}")
            try:
                with self.opener.open(url, timeout=0.5) as response:
                    return json.loads(response.read().decode("utf-8"))
            except Exception as exc:
                last_error = exc
                time.sleep(0.05)
        self.fail(f"HMI did not serve {url}: {last_error}")


if __name__ == "__main__":
    unittest.main()
