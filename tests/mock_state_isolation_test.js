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
const writes = [], timers = new Map();
let sequence = 0, reloads = 0;
const context = vm.createContext({
  activeDevice: "mctivity", mockFaultCode: "0xFFFF", mockSessionReadOnly: true,
  uiStateSaveTimer: 0, URLSearchParams,
  window: {location: {search: "?mock_fault=0xFFFF", reload: () => { reloads++; }}},
  setTimeout: callback => {
    const id = ++sequence;
    timers.set(id, () => { timers.delete(id); return callback(); });
    return id;
  },
  clearTimeout: id => timers.delete(id),
  saveUiState: () => {}, currentProfile: () => ({transmission: {forwardLimit: 123}}),
  apiHeaders: value => value, showApiError: () => {}, console,
  refreshMockFaultPanel: () => {}, api: () => Promise.resolve({ok: true}),
  fetch: async (url, options) => {
    writes.push({url, options});
    return {json: async () => ({ok: true})};
  }
});
vm.runInContext(section("function mockFaultEnabled()", "function currentMockFaultOption()"), context);
vm.runInContext(section("async function persistUiState(", "function saveUiState("), context);
vm.runInContext(section("function syncMockFaultFromUrl()", "function mockFaultValue()"), context);
vm.runInContext(section("async function hydrateUiStateFromServer()", "function fmt("), context);
(async () => {
  for (const device of ["mctivity", "fv3", "aux_encoder"]) {
    await context.persistUiState(device, {updateAntiSwayPeriod: true, updateAntiSwaySettings: true});
    context.scheduleUiStateSave(device);
  }
  await context.hydrateUiStateFromServer();
  assert.equal(writes.length, 0);
  assert.equal(timers.size, 0);
  // Even a callback queued before a mode transition must recheck the session.
  context.uiStateSaveTimer = context.setTimeout(() => context.persistUiState(), 180);
  const queued = [...timers.values()][0];
  context.window.location.search = "";
  context.syncMockFaultFromUrl();
  assert.equal(reloads, 1);
  assert.equal(timers.size, 0);
  await queued();
  await context.persistUiState();
  assert.equal(writes.length, 0);
  assert(context.mockFaultEnabled(), "Old document must remain read-only while reloading");
  // A fresh, normal document has no preview state and can save.
  context.mockFaultCode = null;
  context.mockSessionReadOnly = false;
  context.scheduleUiStateSave();
  await [...timers.values()][0]();
  timers.clear();
  assert.equal(writes.length, 1);
  assert.equal(writes[0].options.method, "POST");
  context.scheduleUiStateSave();
  const normalQueued = [...timers.values()][0];
  context.window.location.search = "?mock_fault=1";
  await normalQueued();
  assert.equal(writes.length, 1, "URL transition must block stale writes before popstate");
  context.syncMockFaultFromUrl();
  assert.equal(reloads, 2);
  assert.equal(timers.size, 0);
  console.log("mock configuration isolation and transition checks passed");
})().catch(error => { console.error(error); process.exitCode = 1; });
