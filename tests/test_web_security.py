from contextlib import ExitStack
import http.client
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "mctivity_hmi"))
import mctivity_hmi as app
from web_security import is_loopback_host, validate_web_access


class WebConfigurationTests(unittest.TestCase):
    def test_loopback_remains_optional_token(self):
        for host in ("127.0.0.1", "127.0.0.2", "localhost", "::1"):
            self.assertTrue(is_loopback_host(host))
            validate_web_access(host, "")

    def test_external_bind_requires_generated_token(self):
        for host in ("0.0.0.0", "::", "hmi.example.invalid"):
            self.assertFalse(is_loopback_host(host))
            for token in ("", "short", "a" * 64, "<replace-with-a-long-random-token>"):
                with self.assertRaises(ValueError):
                    validate_web_access(host, token)
            validate_web_access(host, secrets.token_hex(32))

    def test_missing_external_token_exits_before_serving(self):
        env = os.environ.copy()
        env.update(MCTIVITY_WEB_HOST="0.0.0.0", MCTIVITY_API_TOKEN="", MCTIVITY_WEB_PORT="0")
        result = subprocess.run([sys.executable, "mctivity_hmi.py"], cwd=ROOT / "mctivity_hmi",
                                env=env, capture_output=True, text=True, timeout=5)
        self.assertNotEqual(0, result.returncode)
        self.assertIn("Non-loopback HMI access requires", result.stderr)
        self.assertNotIn("listening", result.stdout)


class WebAuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.token = secrets.token_hex(32)
        self.stack.enter_context(patch.object(app, "API_TOKEN", self.token))
        self.stack.enter_context(patch.object(app, "WEB_HOST", "127.0.0.1"))
        self.stack.enter_context(patch.dict(os.environ, {"MCTIVITY_ALLOWED_HOSTS": ""}))
        self.status = self.stack.enter_context(patch.object(app, "motiond_command", return_value={"ok": True, "simulated": True}))
        self.dispatch = self.stack.enter_context(patch.object(app, "feature_dispatch_axis_command", return_value={"ok": True, "simulated": True}))
        self.load = self.stack.enter_context(patch.object(app, "load_ui_state", return_value={}))
        self.save = self.stack.enter_context(patch.object(app, "save_ui_state"))
        self.server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def request(self, path, method="GET", headers=None, payload=None):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=2)
        try:
            connection.request(method, path, json.dumps(payload) if payload is not None else None, headers or {})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_unauthenticated_requests_never_reach_backend(self):
        for headers in ({}, {"X-MCTIVITY-Token": "wrong"}, {"X-MCTIVITY-Token": "invalid-\u00ff"}):
            for path in ("/api/status", "/api/ui_state"):
                self.assertEqual(401, self.request(path, headers=headers)[0])
            for path in ("/api/command", "/api/ui_state"):
                self.assertEqual(401, self.request(path, "POST", {"Content-Type": "application/json", **headers}, {"cmd": "status"})[0])
        self.status.assert_not_called()
        self.dispatch.assert_not_called()
        self.save.assert_not_called()
        self.load.assert_not_called()

    def test_correct_tokens_allow_simulated_status_and_command(self):
        for headers in ({"X-MCTIVITY-Token": self.token}, {"Authorization": f"Bearer {self.token}"}):
            status, body = self.request("/api/status", headers=headers)
            self.assertEqual(200, status)
            self.assertTrue(body["simulated"])
            status, body = self.request("/api/command", "POST", {"Content-Type": "application/json", **headers}, {"cmd": "status"})
            self.assertEqual(200, status)
            self.assertTrue(body["simulated"])
            self.assertEqual(200, self.request("/api/ui_state", headers=headers)[0])

    def test_foreign_host_and_origin_and_wrong_content_type_stay_blocked(self):
        headers = {"X-MCTIVITY-Token": self.token, "Content-Type": "application/json"}
        self.assertEqual(403, self.request("/api/status", headers={**headers, "Host": "foreign.example.invalid"})[0])
        self.assertEqual(403, self.request("/api/command", "POST", {**headers, "Origin": "http://foreign.example.invalid"}, {"cmd": "status"})[0])
        self.assertEqual(415, self.request("/api/command", "POST", {**headers, "Content-Type": "text/plain"}, {"cmd": "status"})[0])
        self.dispatch.assert_not_called()

    def test_loopback_without_token_and_fail_closed_external_configuration(self):
        with patch.object(app, "API_TOKEN", ""):
            self.assertEqual(200, self.request("/api/status")[0])
            with patch.object(app, "WEB_HOST", "0.0.0.0"):
                self.assertEqual(401, self.request("/api/status")[0])

    def test_public_metadata_stays_available(self):
        for path in ("/api/capabilities", "/api/health/modular"):
            self.assertEqual(200, self.request(path)[0])

    def test_public_page_has_no_detail_assets_or_entry_point(self):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=2)
        try:
            connection.request("GET", "/")
            response = connection.getresponse()
            self.assertEqual(200, response.status)
            page = response.read().decode()
        finally:
            connection.close()
        self.assertNotIn("ServoDiagnostic", page)
        self.assertNotIn("servo-diagnostic-", page)
        self.assertNotIn("faultDetailButton", page)
        self.assertIn('id="faultCodeText"', page)
        self.assertIn('onclick="resetFault(event)"', page)
        self.assertIn('id="diagModal"', page)
        assets = ("servo-diagnostic-core.js", "servo-diagnostic-widget.js",
                  "servo-diagnostic-widget.css", "servo-diagnostic-hcfa-y7-data.js",
                  "servo-diagnostic-hcfa-y7-profile.js")
        for asset in assets:
            connection = http.client.HTTPConnection(*self.server.server_address, timeout=2)
            try:
                connection.request("GET", "/assets/servo-diagnostic-hcfa-y7/" + asset)
                response = connection.getresponse()
                self.assertEqual(404, response.status)
                response.read()
            finally:
                connection.close()
        self.status.assert_not_called()
        self.dispatch.assert_not_called()

    def test_manual_reset_routing_remains_and_encoder_is_read_only(self):
        from tests.test_profile_assembly import load_manifest
        capabilities = frozenset(load_manifest("full")["capabilities"])
        self.stack.enter_context(patch.object(app, "_CAPABILITY_SET", capabilities))
        headers = {"X-MCTIVITY-Token": self.token, "Content-Type": "application/json"}
        for device in ("mctivity", "fv3"):
            self.dispatch.reset_mock()
            status, body = self.request("/api/command", "POST", headers,
                                        {"cmd": "fault_reset", "device": device})
            self.assertEqual(200, status)
            self.assertTrue(body["simulated"])
            self.assertEqual(device, self.dispatch.call_args.args[0])
            self.assertEqual("fault_reset", self.dispatch.call_args.args[1]["cmd"])
        self.dispatch.reset_mock()
        status, body = self.request("/api/command", "POST", headers,
                                    {"cmd": "fault_reset", "device": "aux_encoder"})
        self.assertEqual(400, status)
        self.assertEqual("read_only_device", body["error"])
        self.dispatch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
