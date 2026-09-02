#!/usr/bin/env python3

from dataclasses import dataclass


@dataclass
class ProtocolAdapter:
    wait_motion_ready_fn: callable = None
    apply_fv3_profile_fn: callable = None
    fv3_set_mode_fn: callable = None
    fv3_force_csp_fn: callable = None

    def wait_motion_ready(self, device):
        fn = self.wait_motion_ready_fn
        if callable(fn):
            return fn(device)
        return True, None

    def apply_fv3_profile(self, payload):
        fn = self.apply_fv3_profile_fn
        if callable(fn):
            fn(payload)

    def fv3_set_mode(self, mode):
        fn = self.fv3_set_mode_fn
        if callable(fn):
            fn(mode)

    def fv3_force_csp(self):
        fn = self.fv3_force_csp_fn
        if callable(fn):
            fn()


@dataclass
class FeatureContext:
    device: str
    payload: dict
    transport_fn: callable
    adapter: ProtocolAdapter

    def cmd(self):
        return str(self.payload.get("cmd", "")).strip().lower()

    def mode(self):
        return str(self.payload.get("mode", "")).strip().lower()

    def run_transport(self):
        return self.transport_fn(self.device, self.payload)

    @property
    def hooks(self):
        # Backward-compat shim for legacy handler code paths.
        return {
            "wait_motion_ready": self.adapter.wait_motion_ready,
            "apply_fv3_profile": self.adapter.apply_fv3_profile,
            "fv3_set_mode": self.adapter.fv3_set_mode,
            "fv3_force_csp": self.adapter.fv3_force_csp,
        }


def motion_not_ready(message=None):
    return {"ok": False, "error": "motion_not_ready", "message": message or "servo is not ready for motion"}
