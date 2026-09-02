import sys
import threading
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "mctivity_hmi"))

import feature_multi_point as multi_point
from feature_contract import ProtocolAdapter


DEVICE = "test_axis"
ROW = {
    "row": 1,
    "enabled": True,
    "pos": 10000,
    "speed_rpm": 100,
    "acceleration_rpm_s": 200,
    "dwell_ms": 0,
}


class BlockingTransport:
    def __init__(self):
        self.moving = False
        self.move_started = threading.Event()
        self.calls = []
        self.lock = threading.Lock()

    def __call__(self, device, payload):
        cmd = payload.get("cmd")
        with self.lock:
            self.calls.append(cmd)
        if cmd == "move_abs":
            self.moving = True
            self.move_started.set()
        if cmd == "status":
            return {"ok": True, "status": {"moving": self.moving, "pos": 0}}
        return {"ok": True}


class MultiPointRunnerTests(unittest.TestCase):
    def setUp(self):
        with multi_point._LOCK:
            thread = multi_point._THREADS.get(DEVICE)
            event = multi_point._STOP_EVENTS.get(DEVICE)
            if event:
                event.set()
        if thread and thread.is_alive():
            thread.join(timeout=2.0)
        with multi_point._LOCK:
            for store in (
                multi_point._TABLES,
                multi_point._RUNNERS,
                multi_point._THREADS,
                multi_point._STOP_EVENTS,
                multi_point._COMMAND_LOCKS,
                multi_point._RUN_SEQUENCES,
            ):
                store.pop(DEVICE, None)
        multi_point._write_table(DEVICE, {"rows": [ROW], "cycle_count": 1})

    def tearDown(self):
        with multi_point._LOCK:
            event = multi_point._STOP_EVENTS.get(DEVICE)
            thread = multi_point._THREADS.get(DEVICE)
            if event:
                event.set()
        if thread and thread.is_alive():
            thread.join(timeout=2.0)

    def test_stop_clear_and_restart_wait_for_existing_worker(self):
        transport = BlockingTransport()
        started = multi_point._start_table(DEVICE, {}, transport, ProtocolAdapter())
        self.assertTrue(started["ok"])
        self.assertTrue(transport.move_started.wait(timeout=1.0))

        stopped = multi_point._stop_table(DEVICE, transport)
        self.assertEqual("stopping", stopped["point_table_runner"]["state"])

        cleared = multi_point._clear_table(DEVICE, transport)
        self.assertFalse(cleared["ok"])
        self.assertEqual("point_table_stopping", cleared["error"])

        restarted = multi_point._start_table(DEVICE, {}, transport, ProtocolAdapter())
        self.assertFalse(restarted["ok"])
        self.assertEqual("point_table_already_running", restarted["error"])

        transport.moving = False
        thread = multi_point._THREADS[DEVICE]
        thread.join(timeout=2.0)
        self.assertFalse(thread.is_alive())

        cleared = multi_point._clear_table(DEVICE, transport)
        self.assertTrue(cleared["ok"])
        self.assertEqual([], cleared["point_table"]["rows"])

    def test_starting_state_reserves_device_and_can_be_cancelled(self):
        transport = BlockingTransport()
        ready_entered = threading.Event()
        release_ready = threading.Event()
        first_result = {}

        def wait_ready(_device):
            ready_entered.set()
            release_ready.wait(timeout=2.0)
            return True, None

        adapter = ProtocolAdapter(wait_motion_ready_fn=wait_ready)

        def start_first():
            first_result.update(multi_point._start_table(DEVICE, {}, transport, adapter))

        starter = threading.Thread(target=start_first)
        starter.start()
        self.assertTrue(ready_entered.wait(timeout=1.0))

        duplicate = multi_point._start_table(DEVICE, {}, transport, ProtocolAdapter())
        self.assertFalse(duplicate["ok"])
        self.assertEqual("point_table_already_running", duplicate["error"])

        multi_point._stop_table(DEVICE, transport)
        release_ready.set()
        starter.join(timeout=2.0)
        self.assertFalse(starter.is_alive())
        self.assertFalse(first_result["ok"])
        self.assertEqual("point_table_start_cancelled", first_result["error"])

    def test_clear_idle_table_does_not_send_axis_stop(self):
        transport = BlockingTransport()

        cleared = multi_point._clear_table(DEVICE, transport)

        self.assertTrue(cleared["ok"])
        self.assertEqual([], cleared["point_table"]["rows"])
        self.assertNotIn("stop", transport.calls)


if __name__ == "__main__":
    unittest.main()
