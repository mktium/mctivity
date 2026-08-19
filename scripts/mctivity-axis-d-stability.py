#!/usr/bin/env python3
import argparse
import json
import os
import socket
import time


def read_status():
    with socket.create_connection(("127.0.0.1", 10001), timeout=1.0) as sock:
        sock.sendall(b'{"cmd":"status","device":"mctivity"}\n')
        response = b""
        while not response.endswith(b"\n"):
            chunk = sock.recv(4096)
            if not chunk:
                break
            response += chunk
    payload = json.loads(response.decode("utf-8"))
    if payload.get("ok") is not True:
        raise RuntimeError(payload)
    return payload["status"]


def assert_safe(status):
    expected_topology = os.environ.get("MCTIVITY_EXPECT_PROFILE", "axis-d-uservo").strip() or "axis-d-uservo"
    expected = {
        "topology": expected_topology,
        "counts_per_rev": 10000,
        "commissioning_inhibit": True,
        "phase_search_confirmation_required": True,
        "phase_search_confirmed": False,
        "enabled": False,
        "servo_request": False,
        "moving": False,
        "cw": 0,
        "operational": 1,
        "wc_complete": True,
        "fault": False,
        "rt_memory_locked": True,
        "rt_scheduler_policy": 1,
        "timing_guard_armed": True,
        "communication_timing_fault": False,
    }
    for key, value in expected.items():
        if status.get(key) != value:
            raise RuntimeError(f"unsafe or unstable {key}: expected {value!r}, got {status.get(key)!r}")
    if expected_topology == "axis-d-uservo-pv" and status.get("control_mode") != "velocity":
        raise RuntimeError(f"unsafe or unstable control_mode: expected 'velocity', got {status.get('control_mode')!r}")
    if int(status.get("rt_scheduler_priority", 0)) <= 0:
        raise RuntimeError(f"invalid RT priority: {status.get('rt_scheduler_priority')!r}")


def main():
    parser = argparse.ArgumentParser(description="Read-only Axis D no-motion stability gate")
    parser.add_argument("--duration", type=float, default=1800.0)
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--max-position-span", type=int, default=0)
    args = parser.parse_args()
    if args.duration <= 0 or args.interval <= 0 or args.max_position_span < 0:
        parser.error("duration/interval must be positive and max-position-span must be nonnegative")

    baseline = read_status()
    assert_safe(baseline)
    counters = (
        "rt_deadline_miss_count",
        "rt_skipped_periods",
        "wc_change_count",
        "wc_incomplete_cycles",
    )
    baseline_counters = {key: int(baseline.get(key, -1)) for key in counters}
    if baseline_counters["rt_deadline_miss_count"] != 0 or baseline_counters["rt_skipped_periods"] != 0:
        raise RuntimeError(f"startup deadline counters are not clean: {baseline_counters}")
    min_position = max_position = int(baseline["pos_raw"])
    samples = 1
    deadline = time.monotonic() + args.duration

    while time.monotonic() < deadline:
        time.sleep(min(args.interval, max(0.0, deadline - time.monotonic())))
        status = read_status()
        assert_safe(status)
        samples += 1
        position = int(status["pos_raw"])
        min_position = min(min_position, position)
        max_position = max(max_position, position)
        if max_position - min_position > args.max_position_span:
            raise RuntimeError(
                f"position changed during no-motion gate: span {max_position - min_position} counts "
                f"> allowed {args.max_position_span}"
            )
        for key, initial in baseline_counters.items():
            current = int(status.get(key, -1))
            if current != initial:
                raise RuntimeError(f"{key} changed during stability window: {initial} -> {current}")

    result = {
        "ok": True,
        "duration_seconds": args.duration,
        "samples": samples,
        "baseline_counters": baseline_counters,
        "position_min": min_position,
        "position_max": max_position,
        "position_span_counts": max_position - min_position,
        "rt_max_wake_lateness_ns": int(status["rt_max_wake_lateness_ns"]),
        "rt_max_cycle_runtime_ns": int(status["rt_max_cycle_runtime_ns"]),
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
