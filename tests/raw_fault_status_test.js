"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "../mctivity_hmi/mctivity_hmi.py"), "utf8");
function section(first, next) {
  const start = source.indexOf(first);
  const end = source.indexOf(next, start + first.length);
  assert(start >= 0 && end > start, first);
  return source.slice(start, end);
}
const commands = [];
const state = {commandSeq: 0};
const context = vm.createContext({
  currentLang: "en",
  UI_TEXT: {en: {ok: "Normal", fault: "Fault"}, zh: {ok: "正常", fault: "故障"}},
  current: {fault: false},
  activeDevice: "mctivity",
  currentStatus: () => context.current,
  currentMotion: () => state,
  isAuxEncoderDevice: device => device === "aux_encoder",
  renderMotionToggle: () => {},
  setGearPanelLocked: () => {},
  api: async payload => { commands.push(payload); return {ok: true}; },
  console
});
vm.runInContext(section("function hex4(value)", "function normalizeAuxEncoderStatus(raw)"), context);
vm.runInContext(section("function resetFault(event)", "function stopMotion()"), context);
for (const language of ["en", "zh"]) {
  context.currentLang = language;
  for (const device of ["mctivity", "fv3"]) {
    for (const err of [0, 1, 0x1234, 0xffff]) {
      const value = context.faultDisplay({device, err, fault: true});
      assert.equal(value.code, "0x" + err.toString(16).toUpperCase().padStart(4, "0"));
      assert.equal(value.name, context.UI_TEXT[language].fault);
      assert.equal(value.hasDetail, undefined);
    }
    assert.equal(context.faultDisplay({device, err: 0, fault: false}).name, context.UI_TEXT[language].ok);
  }
}
assert.equal(context.faultDisplay({device: "aux_encoder", operational: true, wc_complete: true, alarm_status: 0}).code, "0x0000");
assert.equal(context.faultDisplay({device: "aux_encoder", operational: false, alarm_status: 7}).code, "0x0007");
assert.equal(context.faultDisplay({device: "aux_encoder", operational: false, warning_status: 3}).code, "0x0003");
assert.equal(commands.length, 0, "Displaying faults must not issue a control command");
context.resetFault();
assert.equal(commands.length, 0, "No reset when no fault is present");
context.current = {fault: true};
context.activeDevice = "aux_encoder";
context.resetFault();
assert.equal(commands.length, 0, "Encoder remains read-only");
for (const device of ["mctivity", "fv3"]) {
  context.activeDevice = device;
  context.resetFault();
}
assert.equal(commands.length, 2);
assert(commands.every(command => command.cmd === "fault_reset"));
assert.equal(state.latch, false);

const mockContext = vm.createContext({
  activeDevice: "mctivity",
  mockFaultEnabled: () => true,
  mockStatus: () => ({ok: true, status: {fault: true, err: 0xffff}}),
  render: () => {},
  fetch: () => { throw Error("Mock page reached the network"); }
});
vm.runInContext(section("async function api(payload)", "function cls(el, good)"), mockContext);
vm.runInContext(section("async function apiForDevice(device, payload)", "function modeOption(value)"), mockContext);
(async () => {
  assert.equal((await mockContext.api({cmd: "status"})).status.err, 0xffff);
  for (const cmd of ["enable", "fault_reset", "move_abs", "homing_start_torque"]) {
    assert.equal((await mockContext.api({cmd})).error, "mock_read_only");
    assert.equal((await mockContext.apiForDevice("fv3", {cmd})).error, "mock_read_only");
  }
  assert.equal((await mockContext.apiForDevice("aux_encoder", {cmd: "status"})).status.err, 0xffff);
  assert(!source.includes("ServoDiagnostic"));
  assert(!source.includes("servo-diagnostic-"));
  console.log("raw fault display, manual reset and read-only mock checks passed");
})().catch(error => { console.error(error); process.exitCode = 1; });
