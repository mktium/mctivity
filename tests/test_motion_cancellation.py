import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "mctivity_hmi"))
import feature_multi_point as mp
from feature_contract import ProtocolAdapter
from feature_dispatch import dispatch_axis_command

ROW = {"row": 1, "enabled": True, "pos": 10000, "speed_rpm": 100,
       "acceleration_rpm_s": 200, "dwell_ms": 0}
DEVICES = ("cancel_test_a", "cancel_test_b")


class MemoryTransport:
    def __init__(self):
        self.calls = []
        self.moving = False
        self.move_seen = threading.Event()
        self.stop_seen = threading.Event()
        self.stop_error = ""
        self.stop_exception = False
        self.status_error = ""
        self.keep_moving = False
        self.missing_motion_status = False
        self.before_move_return = None
        self.lock = threading.Lock()

    def __call__(self, device, payload):
        cmd = payload["cmd"]
        with self.lock:
            self.calls.append((cmd, dict(payload)))
        if cmd == "move_abs":
            self.moving = True
            self.move_seen.set()
            if self.before_move_return:
                self.before_move_return()
        if cmd == "stop":
            self.stop_seen.set()
            if self.stop_exception:
                raise OSError("stop connection lost")
            if self.stop_error:
                return {"ok": False, "error": self.stop_error}
            if not self.keep_moving:
                self.moving = False
        if cmd == "disable":
            self.moving = False
        if cmd == "status":
            if self.status_error:
                return {"ok": False, "error": self.status_error}
            if self.missing_motion_status:
                return {"ok": True, "status": {}}
            return {"ok": True, "status": {"moving": self.moving, "pos": 0}}
        return {"ok": True}

    def commands(self):
        with self.lock:
            return [cmd for cmd, _ in self.calls if cmd != "status"]


class MotionCancellationTests(unittest.TestCase):
    def setUp(self):
        self.transports = []
        self.releases = []
        self.local_threads = []
        self.patches = [
            patch.object(mp, "POINT_STATUS_PERIOD_S", 0.005),
            patch.object(mp, "POINT_STOP_TIMEOUT_S", 0.15),
        ]
        for item in self.patches:
            item.start()
        self.clear_stores()

    def clear_stores(self):
        with mp._LOCK:
            for device in DEVICES:
                for store in (mp._TABLES, mp._RUNNERS, mp._THREADS, mp._STOP_THREADS,
                              mp._STOP_EVENTS, mp._COMMAND_LOCKS, mp._RUN_SEQUENCES):
                    store.pop(device, None)

    def tearDown(self):
        for event in self.releases:
            event.set()
        for transport in self.transports:
            transport.moving = False
            transport.status_error = ""
        with mp._LOCK:
            for device in DEVICES:
                if device in mp._STOP_EVENTS:
                    mp._STOP_EVENTS[device].set()
        threads = self.local_threads + [store[device] for store in (mp._THREADS, mp._STOP_THREADS)
                                        for device in DEVICES if device in store]
        for thread in threads:
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive(), thread.name)
        for item in reversed(self.patches):
            item.stop()
        self.clear_stores()

    def transport(self):
        transport = MemoryTransport()
        self.transports.append(transport)
        return transport

    def release_event(self):
        event = threading.Event()
        self.releases.append(event)
        return event

    def start_async(self, fn):
        thread = threading.Thread(target=fn)
        self.local_threads.append(thread)
        thread.start()
        return thread

    def start(self, transport, device=DEVICES[0], adapter=None):
        mp._write_table(device, {"rows": [dict(ROW), dict(ROW, row=2, pos=20000)]})
        return mp._start_table(device, {}, transport, adapter or ProtocolAdapter())

    def command(self, transport, cmd, device=DEVICES[0], **values):
        return dispatch_axis_command(device, {"cmd": cmd, **values}, transport,
                                     enabled_feature_keys={"multi_point", "position", "homing", "gear_cam"})

    def finish(self, device=DEVICES[0]):
        for store in (mp._THREADS, mp._STOP_THREADS):
            thread = store.get(device)
            if thread:
                thread.join(timeout=2)
                self.assertFalse(thread.is_alive())
        return mp._snapshot(device)["point_table_runner"]

    def test_generic_stop_cancels_dwell_without_next_row(self):
        transport = self.transport()
        entered, release = threading.Event(), self.release_event()

        def dwell(*args):
            entered.set()
            release.wait(2)
            return True

        with patch.object(mp, "_wait_for_row", return_value=(True, "complete")), \
                patch.object(mp, "_sleep_dwell_ms", side_effect=dwell):
            self.start(transport)
            self.assertTrue(entered.wait(1))
            response = self.command(transport, "stop", deceleration_rpm_s=321)
            self.assertTrue(response["ok"])
            self.assertTrue(mp._STOP_EVENTS[DEVICES[0]].is_set())
            release.set()
            runner = self.finish()
        self.assertEqual(["set_mode", "move_abs", "stop"], transport.commands())
        self.assertEqual(321, next(p["deceleration_rpm_s"] for c, p in transport.calls if c == "stop"))
        self.assertTrue(runner["stop_confirmed"])
        self.assertEqual("stopped", runner["state"])

    def test_stop_during_start_preparation_blocks_restart_and_mode_setup(self):
        transport = self.transport()
        entered, release = threading.Event(), self.release_event()
        result = {}

        def ready(_):
            entered.set()
            release.wait(2)
            return True, None

        self.start_async(lambda: result.update(self.start(transport, adapter=ProtocolAdapter(wait_motion_ready_fn=ready))))
        self.assertTrue(entered.wait(1))
        self.command(transport, "stop")
        self.assertFalse(mp._clear_table(DEVICES[0], transport)["ok"])
        self.assertFalse(mp._start_table(DEVICES[0], {}, transport, ProtocolAdapter())["ok"])
        release.set()
        self.local_threads[0].join(2)
        self.finish()
        self.assertEqual(["stop"], transport.commands())
        self.assertEqual("point_table_start_cancelled", result["error"])

    def test_stop_during_row_wait(self):
        transport = self.transport()
        self.start(transport)
        self.assertTrue(transport.move_seen.wait(1))
        self.command(transport, "stop")
        runner = self.finish()
        self.assertEqual(["set_mode", "move_abs", "stop"], transport.commands())
        self.assertTrue(runner["stop_confirmed"])

    def test_stop_at_next_row_boundary(self):
        transport = self.transport()
        entered, release = threading.Event(), self.release_event()
        update = mp._update_current_runner

        def pause(device, run_id, values):
            if values.get("current_index") == 1:
                entered.set()
                release.wait(2)
            return update(device, run_id, values)

        with patch.object(mp, "_wait_for_row", return_value=(True, "complete")), \
                patch.object(mp, "_update_current_runner", side_effect=pause):
            self.start(transport)
            self.assertTrue(entered.wait(1))
            self.command(transport, "stop")
            release.set()
            self.finish()
        self.assertEqual(["set_mode", "move_abs", "stop"], transport.commands())

    def test_inflight_move_finishes_before_stop_and_other_axis_is_independent(self):
        transport = self.transport()
        other = self.transport()
        release = self.release_event()
        transport.before_move_return = lambda: release.wait(2)
        self.start(transport)
        self.assertTrue(transport.move_seen.wait(1))
        stop_thread = self.start_async(lambda: self.command(transport, "stop"))
        self.start(other, DEVICES[1])
        self.assertTrue(other.move_seen.wait(1))
        release.set()
        stop_thread.join(2)
        self.finish()
        self.assertFalse(mp._STOP_EVENTS[DEVICES[1]].is_set())
        self.assertEqual(["set_mode", "move_abs", "stop"], transport.commands())
        self.command(other, "stop", DEVICES[1])
        self.finish(DEVICES[1])

    def test_mode_switch_requests_cancel_but_does_not_switch_while_active(self):
        transport = self.transport()
        self.start(transport)
        self.assertTrue(transport.move_seen.wait(1))
        result = self.command(transport, "set_mode", mode="position")
        self.assertFalse(result["ok"])
        self.finish()
        self.assertEqual(["set_mode", "move_abs", "stop"], transport.commands())
        self.assertTrue(self.command(transport, "set_mode", mode="position")["ok"])

    def test_disable_cancels_without_waiting_for_stop_confirmation(self):
        transport = self.transport()
        self.start(transport)
        self.assertTrue(transport.move_seen.wait(1))
        self.assertTrue(self.command(transport, "disable")["ok"])
        runner = self.finish()
        self.assertTrue(runner["stop_confirmed"])
        self.assertEqual(1, transport.commands().count("move_abs"))
        self.assertIn("disable", transport.commands())
        self.assertIn("stop", transport.commands())

    def test_other_stop_commands_cancel_multi_point(self):
        for cmd in ("point_table_stop", "homing_stop", "gear_stop"):
            with self.subTest(cmd=cmd):
                transport = self.transport()
                self.start(transport)
                self.assertTrue(transport.move_seen.wait(1))
                self.command(transport, cmd)
                self.assertTrue(self.finish()["stop_confirmed"])
                self.assertEqual(["set_mode", "move_abs", "stop"], transport.commands())

    def test_execution_failures_stop_and_retain_original_error(self):
        for reason in ("point table row 1 timed out", "status failed"):
            with self.subTest(reason=reason):
                transport = self.transport()
                with patch.object(mp, "_wait_for_row", return_value=(False, reason)):
                    self.start(transport)
                    runner = self.finish()
                self.assertEqual(["set_mode", "move_abs", "stop"], transport.commands())
                self.assertEqual("error", runner["state"])
                self.assertEqual(reason, runner["execution_error"])
                self.assertTrue(runner["stop_confirmed"])
                self.assertTrue(mp._clear_table(DEVICES[0], transport)["ok"])

    def test_stop_failure_blocks_clear_restart_and_motion_until_explicit_retry(self):
        for failure in ("send", "exception", "status", "moving", "missing"):
            with self.subTest(failure=failure):
                transport = self.transport()
                transport.stop_error = "stop transport failed" if failure == "send" else ""
                transport.stop_exception = failure == "exception"
                transport.status_error = "feedback unavailable" if failure == "status" else ""
                transport.keep_moving = failure == "moving"
                transport.missing_motion_status = failure == "missing"
                with patch.object(mp, "_wait_for_row", return_value=(False, "original row failure")):
                    self.start(transport)
                    runner = self.finish()
                self.assertFalse(runner["stop_confirmed"])
                self.assertEqual("error", runner["state"])
                self.assertEqual("original row failure", runner["execution_error"])
                self.assertTrue(runner["stop_error"])
                self.assertIn("unconfirmed", runner["message"])
                self.assertFalse(mp._clear_table(DEVICES[0], transport)["ok"])
                self.assertFalse(mp._write_table(DEVICES[0], {"rows": []})["ok"])
                self.assertFalse(mp._start_table(DEVICES[0], {}, transport, ProtocolAdapter())["ok"])
                for cmd in ("move_abs", "enable", "fault_reset", "set_zero"):
                    self.assertFalse(self.command(transport, cmd)["ok"])
                self.assertEqual(1, transport.commands().count("stop"))
                transport.stop_error = transport.status_error = ""
                transport.stop_exception = False
                transport.keep_moving = transport.missing_motion_status = False
                self.assertTrue(self.command(transport, "stop")["ok"])
                runner = self.finish()
                self.assertTrue(runner["stop_confirmed"])
                self.assertEqual("original row failure", runner["execution_error"])
                self.assertEqual(2, runner["stop_attempts"])
                self.assertTrue(mp._clear_table(DEVICES[0], transport)["ok"])

    def test_stop_pending_is_not_reported_as_confirmed(self):
        transport = self.transport()
        transport.keep_moving = True
        with patch.object(mp, "POINT_STOP_TIMEOUT_S", 2.0):
            self.start(transport)
            self.assertTrue(transport.move_seen.wait(1))
            response = self.command(transport, "stop")
            self.assertTrue(response["ok"])
            self.assertEqual("stopping", response["point_table_runner"]["state"])
            self.assertFalse(response["point_table_runner"]["stop_confirmed"])
            transport.moving = False
            self.assertTrue(self.finish()["stop_confirmed"])

    def test_concurrent_execution_error_and_user_stop_keep_both_results(self):
        transport = self.transport()
        entered, release = threading.Event(), self.release_event()

        def row_failure(*args):
            entered.set()
            release.wait(2)
            return False, "feedback failed during user stop"

        with patch.object(mp, "_wait_for_row", side_effect=row_failure):
            self.start(transport)
            self.assertTrue(entered.wait(1))
            self.command(transport, "stop")
            self.assertFalse(mp._clear_table(DEVICES[0], transport)["ok"])
            release.set()
            runner = self.finish()
        self.assertTrue(runner["stop_confirmed"])
        self.assertEqual("error", runner["state"])
        self.assertEqual("feedback failed during user stop", runner["execution_error"])
        self.assertEqual(1, transport.commands().count("stop"))
        self.assertTrue(mp._clear_table(DEVICES[0], transport)["ok"])
        self.start(transport)
        self.assertEqual(2, mp._snapshot(DEVICES[0])["point_table_runner"]["run_id"])
        self.command(transport, "stop")
        self.finish()

    def test_actual_row_timeout_and_unreadable_feedback(self):
        transport = self.transport()
        with patch.object(mp, "POINT_ROW_TIMEOUT_S", 0.02):
            self.start(transport)
            runner = self.finish()
        self.assertIn("timed out", runner["execution_error"])
        self.assertTrue(runner["stop_confirmed"])
        mp._clear_table(DEVICES[0], transport)
        transport = self.transport()
        transport.status_error = "status failed"
        self.start(transport)
        runner = self.finish()
        self.assertEqual("status failed", runner["execution_error"])
        self.assertFalse(runner["stop_confirmed"])

    def test_setup_exception_aborts_and_releases_preparation_only(self):
        transport = self.transport()
        transport.stop_error = "stop rejected"

        def failed_ready(_):
            raise OSError("readiness feedback lost")

        result = self.start(transport, adapter=ProtocolAdapter(wait_motion_ready_fn=failed_ready))
        self.assertFalse(result["ok"])
        runner = self.finish()
        self.assertFalse(runner["preparing"])
        self.assertFalse(runner["stop_confirmed"])
        self.assertEqual("readiness feedback lost", runner["execution_error"])
        self.assertEqual(["stop"], transport.commands())
        self.assertFalse(mp._start_table(DEVICES[0], {}, transport, ProtocolAdapter())["ok"])


if __name__ == "__main__":
    unittest.main()
