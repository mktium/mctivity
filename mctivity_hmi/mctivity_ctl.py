#!/usr/bin/env python3
import argparse
import json
import os
import socket
import sys


def _env_int(name, default):
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


DEFAULT_HOST = os.environ.get("MCTIVITY_HOST", "127.0.0.1")
DEFAULT_PORT = _env_int("MCTIVITY_PORT", 10001)


def send(payload, host=DEFAULT_HOST, port=DEFAULT_PORT):
    line = json.dumps(payload, separators=(",", ":")) + "\n"
    with socket.create_connection((host, port), timeout=2.0) as sock:
        sock.sendall(line.encode("utf-8"))
        data = b""
        while not data.endswith(b"\n"):
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
    if not data:
        raise RuntimeError("motion daemon returned no data")
    return json.loads(data.decode("utf-8"))


def main(argv=None):
    parser = argparse.ArgumentParser(description="HCFA MCTIVITY motion daemon command tool")
    parser.add_argument("--host", default=DEFAULT_HOST, help="motion daemon host")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="motion daemon TCP port")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("enable")
    sub.add_parser("disable")
    sub.add_parser("stop")
    sub.add_parser("zero")

    p_mode = sub.add_parser("mode")
    p_mode.add_argument("mode", choices=["position", "jog", "point", "homing", "velocity", "torque", "gear_cam"])

    p_vel = sub.add_parser("jog-vel")
    p_vel.add_argument("velocity", type=int, help="velocity jog command in counts/s; use 0 to stop")

    p_torque = sub.add_parser("torque")
    p_torque.add_argument("torque", type=int, help="staged torque command percent")

    p_abs = sub.add_parser("move-abs")
    p_abs.add_argument("pos", type=int, help="absolute target position in soft-zero counts")
    p_abs.add_argument("--ms", type=int, default=3000, help="move duration in milliseconds")

    p_rel = sub.add_parser("move-rel")
    p_rel.add_argument("delta", type=int, help="relative move in counts")
    p_rel.add_argument("--ms", type=int, default=3000, help="move duration in milliseconds")

    args = parser.parse_args(argv)
    if args.cmd == "zero":
        payload = {"cmd": "set_zero"}
    elif args.cmd == "mode":
        payload = {"cmd": "set_mode", "mode": args.mode}
    elif args.cmd == "jog-vel":
        payload = {"cmd": "jog_velocity", "velocity": args.velocity}
    elif args.cmd == "torque":
        payload = {"cmd": "torque_cmd", "torque": args.torque}
    elif args.cmd == "move-abs":
        payload = {"cmd": "move_abs", "pos": args.pos, "move_ms": args.ms}
    elif args.cmd == "move-rel":
        payload = {"cmd": "move_rel", "delta": args.delta, "move_ms": args.ms}
    else:
        payload = {"cmd": args.cmd}

    print(json.dumps(send(payload, host=args.host, port=args.port), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"mctivity_ctl error: {exc}", file=sys.stderr)
        sys.exit(1)
