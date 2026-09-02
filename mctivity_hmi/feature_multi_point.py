#!/usr/bin/env python3

"""
Multi-point positioning feature handler.

The feature owns the point-table API surface and runs the first mctivity
implementation as a software point-table executor. Protocol-specific backends
can later replace the row execution path without changing the HMI contract.
"""

from __future__ import annotations

import copy
import threading
import time
from typing import Any

from feature_contract import motion_not_ready


MAX_POINT_ROWS = 64
MAX_POINT_CYCLES = 1000
POINT_TARGET_TOLERANCE_COUNTS = 2048
POINT_ROW_TIMEOUT_S = 120.0
POINT_STOP_TIMEOUT_S = 120.0
POINT_STATUS_PERIOD_S = 0.08

_LOCK = threading.RLock()
_TABLES: dict[str, dict[str, Any]] = {}
_RUNNERS: dict[str, dict[str, Any]] = {}
_THREADS: dict[str, threading.Thread] = {}
_STOP_THREADS: dict[str, threading.Thread] = {}
_STOP_EVENTS: dict[str, threading.Event] = {}
_COMMAND_LOCKS: dict[str, threading.RLock] = {}
_RUN_SEQUENCES: dict[str, int] = {}

_ACTIVE_RUNNER_STATES = {"starting", "running", "stopping"}


def _now() -> float:
    return time.time()


def _default_table() -> dict[str, Any]:
    return {
        "rows": [],
        "start": 1,
        "step": 0,
        "cycle_count": 1,
        "updated_at": None,
    }


def _default_runner() -> dict[str, Any]:
    return {
        "running": False,
        "state": "idle",
        "current_row": None,
        "current_index": -1,
        "completed_rows": 0,
        "cycle_count": 0,
        "current_cycle": 0,
        "cycle_total": 1,
        "started_at": None,
        "updated_at": None,
        "message": "",
        "error": "",
        "run_id": None,
        "preparing": False,
        "stop_requested": False,
        "stop_confirmed": False,
        "execution_error": "",
        "stop_error": "",
        "stop_attempts": 0,
    }


def _table_for(device: str) -> dict[str, Any]:
    with _LOCK:
        return _TABLES.setdefault(device, _default_table())


def _runner_for(device: str) -> dict[str, Any]:
    with _LOCK:
        return _RUNNERS.setdefault(device, _default_runner())


def _command_lock_for(device: str) -> threading.RLock:
    with _LOCK:
        return _COMMAND_LOCKS.setdefault(device, threading.RLock())


def _runner_active_locked(device: str) -> bool:
    runner = _RUNNERS.get(device)
    thread = _THREADS.get(device)
    runner_active = bool(
        runner
        and (
            runner.get("running")
            or runner.get("preparing")
            or (runner.get("stop_requested") and not runner.get("stop_confirmed"))
            or str(runner.get("state") or "").lower() in _ACTIVE_RUNNER_STATES
        )
    )
    stop_thread = _STOP_THREADS.get(device)
    return runner_active or bool(thread and thread.is_alive()) or bool(stop_thread and stop_thread.is_alive())


def _next_run_id_locked(device: str) -> int:
    run_id = int(_RUN_SEQUENCES.get(device, 0)) + 1
    _RUN_SEQUENCES[device] = run_id
    return run_id


def _is_current_run_locked(device: str, run_id: int) -> bool:
    return int((_RUNNERS.get(device) or {}).get("run_id") or 0) == int(run_id)


def _update_current_runner(device: str, run_id: int, values: dict[str, Any]) -> bool:
    with _LOCK:
        if not _is_current_run_locked(device, run_id) or _runner_for(device)["stop_requested"]:
            return False
        _runner_for(device).update(values)
        return True


def _release_start_reservation(
    device: str,
    run_id: int,
    stop_event: threading.Event,
    state: str,
    message: str,
) -> None:
    with _LOCK:
        if not _is_current_run_locked(device, run_id):
            return
        if stop_event.is_set():
            return
        _runner_for(device).update(
            {
                "running": False,
                "state": state,
                "updated_at": _now(),
                "message": message,
                "error": message if state == "error" else "",
            }
        )
        if _STOP_EVENTS.get(device) is stop_event:
            _STOP_EVENTS.pop(device, None)


def _snapshot(device: str) -> dict[str, Any]:
    with _LOCK:
        table = copy.deepcopy(_table_for(device))
        runner = copy.deepcopy(_runner_for(device))
    return {
        "ok": True,
        "feature": "multi_point",
        "point_table": table,
        "point_table_runner": runner,
    }


def _enabled_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if row.get("enabled", True)]


def _target_gap(status: dict[str, Any], target_pos: int) -> int | None:
    try:
        return abs(int(status.get("pos")) - int(target_pos))
    except (TypeError, ValueError):
        return None


def _wait_for_row(device: str, row: dict[str, Any], transport_fn, stop_event: threading.Event) -> tuple[bool, str]:
    target_pos = int(row["pos"])
    started = _now()
    seen_motion = False
    while not stop_event.is_set():
        if _now() - started > POINT_ROW_TIMEOUT_S:
            return False, f"point table row {row['row']} timed out"
        result = transport_fn(device, {"cmd": "status"})
        if not result.get("ok"):
            return False, str(result.get("error") or "status failed")
        status = result.get("status", {})
        if not isinstance(status, dict) or type(status.get("moving")) not in (bool, int) or status["moving"] not in (0, 1):
            return False, "motion status unavailable"
        if status.get("fault"):
            return False, "axis fault during point table execution"
        moving = bool(status.get("moving"))
        seen_motion = seen_motion or moving
        gap = _target_gap(status, target_pos)
        close_enough = gap is not None and gap <= POINT_TARGET_TOLERANCE_COUNTS
        settled_enough = _now() - started > 0.5
        if close_enough and (seen_motion and not moving or settled_enough and not moving):
            return True, "row complete"
        time.sleep(POINT_STATUS_PERIOD_S)
    return False, "point table stopped"


def _wait_for_axis_stopped(device: str, transport_fn) -> tuple[bool, str]:
    started = time.monotonic()
    while time.monotonic() - started <= POINT_STOP_TIMEOUT_S:
        result = transport_fn(device, {"cmd": "status"})
        if not result.get("ok"):
            return False, str(result.get("error") or "status failed")
        status = result.get("status", {})
        if not isinstance(status, dict) or type(status.get("moving")) not in (bool, int) or status["moving"] not in (0, 1):
            return False, "motion status unavailable"
        if not bool(status.get("moving")):
            return True, "point table stopped"
        time.sleep(POINT_STATUS_PERIOD_S)
    return False, "point table stop timed out"


def _sleep_dwell_ms(dwell_ms: int, stop_event: threading.Event) -> bool:
    end_at = _now() + max(0, dwell_ms) / 1000.0
    while _now() < end_at:
        if stop_event.is_set():
            return False
        time.sleep(min(0.05, max(0.0, end_at - _now())))
    return True


def _clamp_cycle_count(value: Any) -> int:
    try:
        numeric = int(value)
    except (TypeError, ValueError):
        numeric = 1
    return max(1, min(MAX_POINT_CYCLES, numeric))


def _cycle_count_from_payload(payload: dict[str, Any], default: int = 1) -> int:
    if "cycle_count" in payload:
        return _clamp_cycle_count(payload.get("cycle_count"))
    loop_mode = str(payload.get("loop_mode") or "").strip().lower()
    if loop_mode == "cycle":
        return MAX_POINT_CYCLES
    if loop_mode == "single":
        return 1
    return _clamp_cycle_count(default)


def _row_move_payload(row: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "cmd": "move_abs",
        "pos": int(row["pos"]),
        "speed_rpm": int(row["speed_rpm"]),
        "acceleration_rpm_s": int(row["acceleration_rpm_s"]),
    }
    if "move_ms" in row:
        payload["move_ms"] = int(row["move_ms"])
    if "min_pos" in row and "max_pos" in row:
        payload["min_pos"] = int(row["min_pos"])
        payload["max_pos"] = int(row["max_pos"])
    return payload


def _stop_payload_for_row(row: dict[str, Any] | None) -> dict[str, Any]:
    payload = {"cmd": "stop"}
    if row:
        try:
            decel = int(row.get("acceleration_rpm_s") or 0)
        except (TypeError, ValueError):
            decel = 0
        if decel > 0:
            payload["deceleration_rpm_s"] = decel
    return payload


def _active_stop_row(device: str) -> dict[str, Any] | None:
    with _LOCK:
        table = copy.deepcopy(_table_for(device))
        runner = copy.deepcopy(_runner_for(device))
    rows = table.get("rows", [])
    current_row = runner.get("current_row")
    current_index = runner.get("current_index")
    if current_row is not None:
        for row in rows:
            if int(row.get("row", -1)) == int(current_row):
                return row
    try:
        index = int(current_index)
    except (TypeError, ValueError):
        index = -1
    enabled = _enabled_rows(rows)
    if 0 <= index < len(enabled):
        return enabled[index]
    return enabled[0] if enabled else None


def _abort_worker(device, run_id, transport_fn, stop_payload):
    confirmed = False
    stop_error = ""
    try:
        # Row dispatch and the stop command share one per-axis ordering boundary.
        with _command_lock_for(device):
            with _LOCK:
                if not _is_current_run_locked(device, run_id):
                    return
            result = transport_fn(device, stop_payload)
        if not result.get("ok"):
            stop_error = str(result.get("error") or "stop command failed")
        else:
            confirmed, message = _wait_for_axis_stopped(device, transport_fn)
            if not confirmed:
                stop_error = message
    except Exception as exc:
        stop_error = str(exc) or "stop failed"
    with _LOCK:
        if not _is_current_run_locked(device, run_id):
            return
        runner = _runner_for(device)
        execution_error = runner["execution_error"]
        message = "axis stop confirmed" if confirmed else "axis stop unconfirmed: " + stop_error
        runner.update({
            "running": False,
            "state": "error" if execution_error or not confirmed else "stopped",
            "stop_confirmed": confirmed,
            "stop_error": stop_error,
            "updated_at": _now(),
            "error": execution_error or stop_error,
            "message": (execution_error + "; " if execution_error else "") + message,
        })


def _request_abort(device, run_id, transport_fn, execution_error="", stop_payload=None, retry=False):
    with _LOCK:
        if not _is_current_run_locked(device, run_id):
            return
        runner = _runner_for(device)
        if execution_error and not runner["execution_error"]:
            runner["execution_error"] = execution_error
            runner["error"] = execution_error
            if runner["stop_confirmed"]:
                runner["state"] = "error"
                runner["message"] = execution_error + "; axis stop confirmed"
        event = _STOP_EVENTS.get(device)
        if event:
            event.set()
        thread = _STOP_THREADS.get(device)
        if thread and thread.is_alive():
            return
        if runner["stop_requested"] and (not retry or runner["stop_confirmed"]):
            return
        runner.update({
            "running": True,
            "state": "stopping",
            "stop_requested": True,
            "stop_confirmed": False,
            "stop_error": "",
            "stop_attempts": runner["stop_attempts"] + 1,
            "updated_at": _now(),
            "message": "point table stopping; awaiting axis feedback",
        })
        payload = dict(stop_payload or _stop_payload_for_row(_active_stop_row(device)))
        thread = threading.Thread(
            target=_abort_worker, args=(device, run_id, transport_fn, payload),
            name=f"point-stop-{device}-{run_id}", daemon=True,
        )
        _STOP_THREADS[device] = thread
        thread.start()


def _busy_result(device):
    snapshot = _snapshot(device)
    runner = snapshot["point_table_runner"]
    error = "point_table_running"
    if runner["stop_requested"]:
        error = "point_table_stopping" if runner["state"] == "stopping" else "point_table_stop_unconfirmed"
        if runner["stop_confirmed"]:
            error = "point_table_stopping"
    snapshot.update({"ok": False, "error": error,
                     "message": "Point table must finish cancelling and confirm standstill before another command."})
    return snapshot


def guarded_axis_command(device, payload, transport_fn, run_command):
    cmd = str(payload.get("cmd", "")).strip().lower()
    if cmd == "stop":
        return _stop_table(device, transport_fn, stop_payload=payload)
    if cmd == "status" or cmd.startswith("point_table_") or cmd in ("anti_sway_input", "anti_sway_run"):
        return run_command()
    with _command_lock_for(device):
        with _LOCK:
            active = _runner_active_locked(device)
            run_id = (_RUNNERS.get(device) or {}).get("run_id")
        if active:
            if cmd in ("set_mode", "disable", "homing_stop", "gear_stop"):
                _request_abort(device, run_id, transport_fn, retry=cmd == "disable")
            if cmd != "disable":
                return _busy_result(device)
        # Disable remains available even when a controlled stop cannot be confirmed.
        return run_command()


def _run_worker(
    device: str,
    rows: list[dict[str, Any]],
    cycle_count: int,
    transport_fn,
    stop_event: threading.Event,
    run_id: int,
) -> None:
    total_cycles = _clamp_cycle_count(cycle_count)
    with _LOCK:
        if not _is_current_run_locked(device, run_id):
            return
        if stop_event.is_set():
            return
        _runner_for(device).update(
            {
                "running": True,
                "state": "running",
                "current_row": None,
                "current_index": -1,
                "completed_rows": 0,
                "cycle_count": 0,
                "current_cycle": 0,
                "cycle_total": total_cycles,
                "started_at": _now(),
                "updated_at": _now(),
                "message": "point table running",
                "error": "",
            }
        )
    try:
        for cycle_index in range(total_cycles):
            if stop_event.is_set():
                break
            if not _update_current_runner(
                device,
                run_id,
                {
                    "current_cycle": cycle_index + 1,
                    "cycle_total": total_cycles,
                    "updated_at": _now(),
                },
            ):
                return
            for index, row in enumerate(rows):
                if stop_event.is_set():
                    break
                if not _update_current_runner(
                    device,
                    run_id,
                    {
                        "current_row": row["row"],
                        "current_index": index,
                        "updated_at": _now(),
                        "message": f"running point table row {row['row']} cycle {cycle_index + 1}/{total_cycles}",
                    },
                ):
                    return
                with _command_lock_for(device):
                    if stop_event.is_set():
                        break
                    result = transport_fn(device, _row_move_payload(row))
                if not result.get("ok"):
                    raise RuntimeError(str(result.get("error") or f"row {row['row']} move failed"))
                ok, message = _wait_for_row(device, row, transport_fn, stop_event)
                if not ok:
                    if stop_event.is_set() and message == "point table stopped":
                        break
                    raise RuntimeError(message)
                if not _sleep_dwell_ms(int(row.get("dwell_ms", 0)), stop_event):
                    break
                with _LOCK:
                    if not _is_current_run_locked(device, run_id) or stop_event.is_set():
                        return
                    runner = _runner_for(device)
                    runner["completed_rows"] = int(runner.get("completed_rows", 0)) + 1
                    runner["updated_at"] = _now()
            if stop_event.is_set():
                break
            if not _update_current_runner(
                device,
                run_id,
                {"cycle_count": cycle_index + 1, "updated_at": _now()},
            ):
                return
        with _LOCK:
            if stop_event.is_set() or not _is_current_run_locked(device, run_id):
                return
            _runner_for(device).update({
                "running": False, "state": "complete", "current_row": None,
                "current_index": -1, "cycle_total": total_cycles,
                "updated_at": _now(), "message": "point table complete", "error": "",
            })
    except Exception as exc:
        _request_abort(device, run_id, transport_fn, execution_error=str(exc))


def _write_table(device: str, payload: dict[str, Any]) -> dict[str, Any]:
    rows = copy.deepcopy(payload.get("rows") or [])
    cycle_count = _cycle_count_from_payload(payload)
    with _LOCK:
        if _runner_active_locked(device):
            snapshot = _snapshot(device)
            snapshot.update({"ok": False, "error": "point_table_running"})
            return snapshot
        table = _table_for(device)
        table.update(
            {
                "rows": rows,
                "start": int(payload.get("start", rows[0]["row"] if rows else 1)),
                "step": int(payload.get("step", len(rows))),
                "cycle_count": cycle_count,
                "updated_at": _now(),
            }
        )
    return _snapshot(device)


def _start_table(device: str, payload: dict[str, Any], transport_fn, adapter) -> dict[str, Any]:
    with _command_lock_for(device), _LOCK:
        if _runner_active_locked(device):
            snapshot = _busy_result(device)
            if snapshot["error"] == "point_table_running":
                snapshot["error"] = "point_table_already_running"
            return snapshot
        table = copy.deepcopy(_table_for(device))
        rows = _enabled_rows(table.get("rows", []))
        if not rows:
            snapshot = _snapshot(device)
            snapshot.update({"ok": False, "error": "point_table_empty"})
            return snapshot
        cycle_count = _cycle_count_from_payload(payload, int(table.get("cycle_count") or 1))
        run_id = _next_run_id_locked(device)
        stop_event = threading.Event()
        runner = _default_runner()
        runner.update({
            "running": True, "preparing": True, "state": "starting",
            "cycle_total": cycle_count, "started_at": _now(), "updated_at": _now(),
            "message": "point table starting", "run_id": run_id,
        })
        _RUNNERS[device] = runner
        _STOP_EVENTS[device] = stop_event
    try:
        ready, message = adapter.wait_motion_ready(device)
        with _command_lock_for(device):
            if stop_event.is_set():
                snapshot = _snapshot(device)
                snapshot.update({"ok": False, "error": "point_table_start_cancelled"})
                return snapshot
            if not ready:
                _release_start_reservation(device, run_id, stop_event, "idle", message or "motion not ready")
                return motion_not_ready(message)
            if device == "fv3":
                adapter.fv3_set_mode("multi_point")
            mode_result = transport_fn(device, {"cmd": "set_mode", "mode": "multi_point"})
            if not mode_result.get("ok"):
                raise RuntimeError(str(mode_result.get("error") or "set_mode multi_point failed"))
            thread = threading.Thread(
                target=_run_worker,
                args=(device, rows, cycle_count, transport_fn, stop_event, run_id),
                name=f"multi-point-{device}-{run_id}", daemon=True,
            )
            with _LOCK:
                _THREADS[device] = thread
                thread.start()
        return _snapshot(device)
    except Exception as exc:
        _request_abort(device, run_id, transport_fn, execution_error=str(exc))
        snapshot = _snapshot(device)
        snapshot.update({"ok": False, "error": str(exc)})
        return snapshot
    finally:
        with _LOCK:
            if _is_current_run_locked(device, run_id):
                _runner_for(device)["preparing"] = False


def _stop_table(device: str, transport_fn, stop_payload=None) -> dict[str, Any]:
    with _command_lock_for(device):
        with _LOCK:
            active = _runner_active_locked(device)
            run_id = (_RUNNERS.get(device) or {}).get("run_id")
        if not active:
            return transport_fn(device, dict(stop_payload or _stop_payload_for_row(_active_stop_row(device))))
        _request_abort(device, run_id, transport_fn, stop_payload=stop_payload, retry=True)
        with _LOCK:
            thread = _STOP_THREADS.get(device)
    if thread and thread.is_alive():
        thread.join(timeout=0.5)
    snapshot = _snapshot(device)
    runner = snapshot["point_table_runner"]
    if runner["stop_error"]:
        snapshot.update({"ok": False, "error": "point_table_stop_unconfirmed",
                         "transport_error": runner["stop_error"], "message": runner["message"]})
    return snapshot


def _clear_table(device: str, transport_fn) -> dict[str, Any]:
    with _command_lock_for(device), _LOCK:
        if _runner_active_locked(device):
            runner = _runner_for(device)
            # An unknown stop requires an explicit stop retry, never a clear/restart.
            if not runner["stop_requested"]:
                _request_abort(device, runner["run_id"], transport_fn)
            return _busy_result(device)
        _TABLES[device] = _default_table()
        _RUNNERS[device] = _default_runner()
        _STOP_EVENTS.pop(device, None)
        _THREADS.pop(device, None)
        _STOP_THREADS.pop(device, None)
        return _snapshot(device)


def handle_axis_command(ctx):
    cmd = ctx.cmd()
    mode = ctx.mode()
    if cmd == "set_mode" and mode == "multi_point":
        if ctx.device == "fv3":
            ctx.adapter.fv3_set_mode("multi_point")
        return ctx.run_transport()
    if cmd == "point_table_write":
        return _write_table(ctx.device, ctx.payload)
    if cmd == "point_table_run":
        return _start_table(ctx.device, ctx.payload, ctx.transport_fn, ctx.adapter)
    if cmd == "point_table_stop":
        return _stop_table(ctx.device, ctx.transport_fn)
    if cmd == "point_table_clear":
        return _clear_table(ctx.device, ctx.transport_fn)
    if cmd == "point_table_status":
        return _snapshot(ctx.device)
    return ctx.run_transport()
