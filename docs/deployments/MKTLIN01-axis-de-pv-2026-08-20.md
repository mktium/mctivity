# MKTLIN01 Axis D/E dual Uservo PV - 2026-08-20

## Hardware discovery baseline

Two identical Uservo / DS1-E4806N-4I slaves were discovered in fixed ring
positions 0 and 1. Both report vendor `0x00666999`, product `0x00004806`,
revision `1`, serial `1`, hardware `0.7`, firmware `5.11`, encoder resolution
`10000/1`, and gear ratio `1/1`. Because alias and serial are identical, the
deployment maps the upstream drive to D and the downstream drive to E by
absolute ring position (`alias=0`, position 0/1).

Before implementation, motiond was stopped and
`MCTIVITY_COMMISSIONING_INHIBIT=1` was restored. Both drives returned to PREOP.
Position 0 then showed the known restart/stop transition fault `0x8100`;
position 1 remained error-free. No reset, mode change, SDO download, enable, or
motion command was issued.

## Profile and parameter source

`profiles/axis-de-uservo-pv.json` instantiates the existing
`axis-device-uservo-pv` module twice. D and E inherit the same confirmed motor,
encoder, and native-PV values from the one module manifest; only logical axis,
transport key, and physical position are instance data. Runtime values are
resolved separately for D and E, even though the confirmed hardware values are
currently equal.

The target deployment remains inhibited. Target compilation, commit, release
path, archive and binary hashes, service/unit verification, two-axis read-only
status, and rollback path must be appended after deployment. Motion testing is
not part of deployment and requires a new operator confirmation.
