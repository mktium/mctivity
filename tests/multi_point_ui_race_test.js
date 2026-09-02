"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "../mctivity_hmi/mctivity_hmi.py"), "utf8");
function section(first, next) {
  const start = source.indexOf(first), end = source.indexOf(next, start + first.length);
  assert(start >= 0 && end > start);
  return source.slice(start, end);
}
const A = "mctivity", B = "fv3";
const unknown = id => ({run_id:id, state:"error", running:false,
  stop_requested:true, stop_confirmed:false});
const complete = id => ({run_id:id, state:"complete", running:false,
  stop_requested:false, stop_confirmed:false});
const response = runner => ({ok:true, point_table_runner:runner});
function fixture() {
  const calls = [], renders = [], saves = [], errors = [];
  const motion = Object.fromEntries([A, B].map(axis => [axis, {commandSeq:0, stopRequested:false, latch:false}]));
  const profiles = Object.fromEntries([A, B].map(axis => [axis, {editing:false, start:1, step:2, cycleCount:2}]));
  let ctx;
  ctx = vm.createContext({
    activeDevice:A, multiPointSelectionRevision:0, multiPointRequestsByDevice:{},
    multiPointStatusByDevice:{[A]:complete(11), [B]:unknown(22)}, console,
    UI_TEXT:{en:{multiPointIdle:"idle", multiPointWrite:"write", multiPointComplete:"done"}}, currentLang:"en",
    modeIsAssembled:() => true, isMultiPointModeSelected:() => true,
    currentMotion:(device=ctx.activeDevice) => motion[device],
    currentMultiPoint:(device=ctx.activeDevice) => profiles[device],
    syncMultiPointRowsFromInputs:() => profiles[ctx.activeDevice],
    multiPointPayloadRows:() => [{row:1, pos:ctx.activeDevice === A ? 111 : 222}],
    renderMultiPointRunner:runner => renders.push({axis:ctx.activeDevice, runner}),
    renderMultiPointPanel:() => renders.push({axis:ctx.activeDevice}),
    renderMotionToggle:() => {},
    setGearPanelLocked:() => {},
    saveUiState:() => {}, scheduleUiStateSave:device => saves.push(device),
    modeLabel:() => "multi_point", openDiagModal:(...args) => errors.push(args),
    showApiError:() => {},
    apiForDevice:(device, payload) => new Promise((resolve, reject) => calls.push({device, payload, resolve, reject}))
  });
  for (const [first, next] of [
    ["async function multiPointRequest(", "function renderMultiPointRunner("],
    ["async function refreshMultiPointStatus()", "async function refreshAntiSwaySensorStatus()"],
    ["async function stopMultiPointMotion(", "function stopMotion()"],
    ["async function startMultiPointMotion()", "async function startSinglePointMotion()"]
  ]) vm.runInContext(section(first, next), ctx);
  const select = axis => { if (ctx.activeDevice !== axis) ctx.multiPointSelectionRevision++; ctx.activeDevice = axis; };
  return {ctx, calls, renders, saves, errors, motion, profiles, select};
}
async function flush() { for (let i=0; i<8; i++) await Promise.resolve(); }
(async () => {
  {
    const f = fixture(), pending = f.ctx.refreshMultiPointStatus();
    f.select(B);
    f.calls[0].resolve(response(complete(11)));
    await pending;
    assert.equal(f.ctx.multiPointStatusByDevice[B].run_id, 22);
    assert(f.ctx.multiPointStatusByDevice[B].stop_requested);
    assert.equal(f.renders.length, 0);
  }
  {
    const f = fixture();
    const old = f.ctx.refreshMultiPointStatus(), next = f.ctx.refreshMultiPointStatus();
    f.calls[1].resolve(response(unknown(12))); await next;
    f.calls[0].resolve(response(complete(11))); await old;
    assert.equal(f.ctx.multiPointStatusByDevice[A].run_id, 12);
    assert.equal(f.renders.length, 1);
  }
  {
    const f = fixture();
    f.ctx.multiPointStatusByDevice[A] = unknown(11);
    const poll = f.ctx.refreshMultiPointStatus(), stop = f.ctx.stopMultiPointMotion();
    assert.equal(await f.ctx.refreshMultiPointStatus(), null, "Polling must wait for in-flight mutations");
    f.calls[0].resolve(response(complete(10))); await poll;
    assert(f.ctx.multiPointStatusByDevice[A].stop_requested, "Pre-stop poll overwrote stop state");
    f.select(B);
    f.calls[1].resolve(response(unknown(11))); await stop;
    assert.equal(f.ctx.multiPointStatusByDevice[B].run_id, 22);
    assert(!f.motion[A].stopRequested);
    assert.equal(f.renders.length, 0);
  }
  {
    const f = fixture();
    const stop = f.ctx.stopMultiPointMotion();
    const run = f.ctx.multiPointRequest(A, {cmd:"point_table_run"});
    f.motion[A].commandSeq++;
    f.motion[A].stopRequested = true;
    f.calls[1].resolve(response(unknown(12))); await run;
    f.calls[0].resolve(response(complete(11))); await stop;
    assert.equal(f.ctx.multiPointStatusByDevice[A].run_id, 12);
    assert(f.motion[A].stopRequested, "Late stop modified a newer operation");
  }
  for (const switchBack of [false, true]) {
    const f = fixture(), pending = f.ctx.startMultiPointMotion();
    f.select(B);
    if (switchBack) f.select(A);
    f.calls[0].resolve({ok:true}); await pending;
    assert.equal(f.calls.length, 1, "Axis switch must cancel remaining startup steps");
    assert.equal(f.calls[0].device, A);
  }
  {
    const f = fixture(), pending = f.ctx.startMultiPointMotion();
    f.calls[0].resolve({ok:true}); await flush();
    assert.equal(f.calls[1].payload.cmd, "point_table_write");
    assert.equal(f.calls[1].payload.rows[0].pos, 111);
    f.select(B);
    f.calls[1].resolve(response(complete(11))); await pending;
    assert.equal(f.calls.length, 2, "Late write must not start another axis");
    assert.equal(f.ctx.multiPointStatusByDevice[B].run_id, 22);
  }
  {
    const f = fixture(), pending = f.ctx.startMultiPointMotion();
    f.calls[0].resolve({ok:true}); await flush();
    f.calls[1].resolve(response(complete(11))); await flush();
    assert.equal(f.calls[2].payload.cmd, "point_table_run");
    f.select(B);
    f.calls[2].resolve(response({...unknown(12), state:"running", running:true})); await pending;
    assert(f.calls.every(call => call.device === A));
    assert.equal(f.ctx.multiPointStatusByDevice[A].run_id, 12);
    assert.equal(f.ctx.multiPointStatusByDevice[B].run_id, 22);
    assert(f.renders.every(item => item.axis === A));
  }
  {
    const f = fixture();
    f.profiles[A].editing = f.profiles[B].editing = true;
    const pending = f.ctx.toggleMultiPointEdit();
    f.select(B); f.calls[0].resolve(response(complete(11))); await pending;
    assert.equal(f.profiles[A].editing, false);
    assert.equal(f.profiles[B].editing, true);
    assert.deepEqual(f.saves, [A]);
    assert.equal(f.renders.length, 0);
  }
  {
    const f = fixture();
    const pending = f.ctx.startMultiPointMotion();
    const stop = f.ctx.stopMultiPointMotion();
    f.calls[1].resolve(response(unknown(11))); await stop;
    f.calls[0].resolve({ok:true}); await pending;
    assert.deepEqual(f.calls.map(call => call.payload.cmd), ["set_mode", "point_table_stop"]);
  }
  {
    const f = fixture();
    f.ctx.multiPointStatusByDevice[A] = unknown(11);
    const missing = f.ctx.multiPointRequest(A, {cmd:"point_table_stop"});
    f.calls[0].resolve({ok:false, error:"unavailable"}); await missing;
    assert(f.ctx.multiPointStatusByDevice[A].stop_requested);
    const failed = f.ctx.stopMultiPointMotion();
    f.select(B); f.calls[1].reject(new Error("disconnected")); await failed;
    assert.equal(f.errors.length, 0, "A's late failure displayed on B");
    assert.equal(f.ctx.multiPointRequestsByDevice[A].pending, 0);
    assert.equal(f.ctx.multiPointStatusByDevice[B].run_id, 22);
  }
  console.log("multi-point axis binding, response ordering and startup cancellation checks passed");
})().catch(error => { console.error(error); process.exitCode = 1; });
