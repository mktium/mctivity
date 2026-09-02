"use strict";

// Run against the real page, but delay only the simulated device responses.
module.exports = async function checkMultiPointRaces(page) {
  await page.evaluate(async () => {
    const A = "mctivity", B = "fv3";
    const originalApi = apiForDevice;
    const calls = [];
    const check = (condition, message) => { if (!condition) throw Error(message); };
    const unknown = id => ({run_id:id, state:"error", running:false, preparing:false,
      stop_requested:true, stop_confirmed:false, message:"axis stop unconfirmed"});
    const complete = id => ({run_id:id, state:"complete", running:false,
      stop_requested:false, stop_confirmed:false});
    const reply = (call, runner) => call.resolve({ok:true, point_table_runner:runner});
    const flush = async () => { for (let i=0; i<12; i++) await Promise.resolve(); };
    apiForDevice = (device, payload) => new Promise((resolve, reject) => calls.push({device, payload, resolve, reject}));
    function reset() {
      calls.length = 0;
      for (const device of [A, B]) {
        delete multiPointRequestsByDevice[device];
        Object.assign(currentMotion(device), {commandSeq:0, stopRequested:false, latch:false, seenMoving:false});
        currentProfile(device).mode = "multi_point";
        currentMultiPoint(device).editing = false;
      }
      multiPointStatusByDevice[A] = complete(11);
      multiPointStatusByDevice[B] = unknown(22);
      switchAxis(A);
      modeSelect.value = "multi_point";
      syncModePanels("multi_point");
    }
    function expectUnknown(device, id) {
      const runner = multiPointStatusByDevice[device];
      check(runner.run_id === id && isMultiPointRunnerRunning(device), "Wrong axis/task cache");
      if (device === activeDevice) {
        renderMultiPointRunner(runner);
        check(document.getElementById("motionIndicatorText").textContent === UI_TEXT[currentLang].multiPointStopUnconfirmed,
          "Unknown stop label was lost");
      }
    }
    async function clickStopRetry(device, id) {
      const before = calls.length;
      document.getElementById("motionIndicator").click();
      await flush();
      check(calls.length === before + 1, "Stop retry did not issue exactly one request");
      const call = calls[before];
      check(call.device === device && call.payload.cmd === "point_table_stop", "Control chose start instead of the correct stop retry");
      reply(call, unknown(id));
      await flush();
    }
    try {
      reset();
      const statusA = refreshMultiPointStatus();
      switchAxis(B);
      reply(calls[0], complete(11)); await statusA;
      expectUnknown(B, 22);
      await clickStopRetry(B, 22);

      reset();
      multiPointStatusByDevice[A] = unknown(11);
      const stopA = stopMotion();
      switchAxis(B);
      reply(calls[0], {...complete(11), state:"stopped", stop_requested:true, stop_confirmed:true});
      await stopA;
      expectUnknown(B, 22);
      await clickStopRetry(B, 22);

      reset();
      const older = refreshMultiPointStatus(), newer = refreshMultiPointStatus();
      reply(calls[1], unknown(12)); await newer;
      reply(calls[0], complete(11)); await older;
      expectUnknown(A, 12);
      await clickStopRetry(A, 12);

      reset();
      multiPointStatusByDevice[A] = unknown(11);
      const poll = refreshMultiPointStatus(), stop = stopMotion();
      check(await refreshMultiPointStatus() === null, "Poll ran during a pending stop");
      reply(calls[0], complete(11)); await poll;
      expectUnknown(A, 11);
      reply(calls[1], unknown(11)); await stop;

      reset();
      const cancelledStart = startSinglePointMotion();
      check(calls[0].payload.cmd === "set_mode", "Missing start preparation");
      calls[0].resolve({ok:true}); await flush();
      check(calls[1].payload.cmd === "point_table_write", "Missing table write");
      switchAxis(B); switchAxis(A);
      reply(calls[1], complete(11)); await cancelledStart;
      check(calls.length === 2 && calls.every(call => call.device === A), "Cancelled setup sent a later run or changed axis");

      reset();
      const runA = startSinglePointMotion();
      calls[0].resolve({ok:true}); await flush();
      reply(calls[1], complete(11)); await flush();
      check(calls[2].payload.cmd === "point_table_run", "Missing start request");
      switchAxis(B);
      reply(calls[2], {...complete(12), state:"running", running:true}); await runA;
      check(multiPointStatusByDevice[A].run_id === 12, "A run response was lost");
      expectUnknown(B, 22);
      await clickStopRetry(B, 22);

      reset();
      currentMultiPoint(A).editing = true;
      currentMultiPoint(B).editing = true;
      const editA = toggleMultiPointEdit();
      switchAxis(B);
      reply(calls[0], complete(11)); await editA;
      check(!currentMultiPoint(A).editing && currentMultiPoint(B).editing, "Late write modified the other axis editor");
      expectUnknown(B, 22);
    } finally {
      apiForDevice = originalApi;
    }
  });
};
