(function() {
	//#region src/motionProfileCore.ts
	var MOTION_PROFILE_VERSION = "0.95.2";
	var DEFAULT_COUNTS_PER_REV = 8388608;
	var MAX_MOTION_SAMPLE_POINTS = 1200;
	var DEFAULT_MOTION_PARAMS = {
		mode: "position",
		targetPosition: 0,
		targetSpeed: 0,
		accel: 0,
		decel: 0,
		dwell: 0,
		blend: "smooth"
	};
	var VALIDATION_COPY = {
		zh: {
			targetPositionZero: "目标位移不能为 0",
			targetSpeedRequired: "速度上限必须大于 0",
			accelRequired: "加速度上限必须大于 0",
			decelRequired: "减速度上限必须大于 0",
			finalBelow: "终点位置 {final} 低于当前配置下限 {limit}",
			finalAbove: "终点位置 {final} 高于当前配置上限 {limit}",
			vmaxWarning: "速度上限超过轴配置约束",
			accelWarning: "加速度上限超过轴配置约束",
			decelWarning: "减速度上限超过轴配置约束"
		},
		en: {
			targetPositionZero: "targetPosition must not be 0",
			targetSpeedRequired: "targetSpeed must be greater than 0",
			accelRequired: "accel must be greater than 0",
			decelRequired: "decel must be greater than 0",
			finalBelow: "final position {final} is below configured minimum {limit}",
			finalAbove: "final position {final} is above configured maximum {limit}",
			vmaxWarning: "vmax exceeds axis velocity constraint",
			accelWarning: "accel exceeds axis acceleration constraint",
			decelWarning: "decel exceeds axis deceleration constraint"
		}
	};
	function normalizeMotionParams(params = {}) {
		const mode = params.mode === "manual" ? "manual" : "position";
		const blend = params.blend === "linear" || params.blend === "aggressive" ? params.blend : "smooth";
		return {
			mode,
			targetPosition: numberOrZero$1(params.targetPosition),
			targetSpeed: Math.max(0, numberOrZero$1(params.targetSpeed)),
			accel: Math.max(0, numberOrZero$1(params.accel)),
			decel: Math.max(0, numberOrZero$1(params.decel)),
			dwell: Math.max(0, numberOrZero$1(params.dwell)),
			blend
		};
	}
	function solveMotionPlan(input) {
		const p = normalizeMotionParams(input);
		const vmax = Math.max(0, p.targetSpeed);
		const acc = Math.max(1e-6, p.accel);
		const dec = Math.max(1e-6, p.decel);
		const sTarget = Math.abs(p.targetPosition);
		if (vmax <= 0 || sTarget <= 0 || p.accel <= 0 || p.decel <= 0) return {
			vPeak: 0,
			tAcc: 0,
			tCruise: 0,
			tDec: 0,
			totalTime: p.dwell
		};
		if (p.mode === "manual") {
			const tAcc = vmax / acc;
			const tDec = vmax / dec;
			const tCruise = Math.max(0, sTarget - .5 * vmax * tAcc - .5 * vmax * tDec) / vmax;
			return {
				vPeak: vmax,
				tAcc,
				tCruise,
				tDec,
				totalTime: tAcc + tCruise + tDec + p.dwell
			};
		}
		const tAccV = vmax / acc;
		const tDecV = vmax / dec;
		const sNeed = .5 * vmax * tAccV + .5 * vmax * tDecV;
		if (sTarget >= sNeed) {
			const tCruise = vmax > 0 ? (sTarget - sNeed) / vmax : 0;
			return {
				vPeak: vmax,
				tAcc: tAccV,
				tCruise,
				tDec: tDecV,
				totalTime: tAccV + tCruise + tDecV + p.dwell
			};
		}
		const vPeak = Math.sqrt(2 * sTarget / (1 / acc + 1 / dec));
		const tAcc = vPeak / acc;
		const tDec = vPeak / dec;
		return {
			vPeak,
			tAcc,
			tCruise: 0,
			tDec,
			totalTime: tAcc + tDec + p.dwell
		};
	}
	function generateMotionSamples(input, dt = .005) {
		const params = normalizeMotionParams(input);
		const plan = solveMotionPlan(params);
		const baseStep = Math.max(.001, Number(dt) || .005);
		const adaptiveStep = plan.totalTime > 0 ? plan.totalTime / MAX_MOTION_SAMPLE_POINTS : baseStep;
		const step = Math.max(baseStep, adaptiveStep);
		const direction = params.targetPosition < 0 ? -1 : 1;
		const out = [];
		for (let t = 0; t <= plan.totalTime + 1e-9; t += step) {
			let v = 0;
			if (t <= plan.tAcc) v = plan.vPeak * easing(params.blend, plan.tAcc > 0 ? t / plan.tAcc : 1);
			else if (t <= plan.tAcc + plan.tCruise) v = plan.vPeak;
			else if (t <= plan.tAcc + plan.tCruise + plan.tDec) {
				const r = plan.tDec > 0 ? (t - plan.tAcc - plan.tCruise) / plan.tDec : 1;
				v = plan.vPeak * (1 - easing(params.blend, r));
			}
			out.push({
				t,
				x: 0,
				v: v * direction,
				a: 0,
				j: 0
			});
		}
		if (out.length === 0) out.push({
			t: 0,
			x: 0,
			v: 0,
			a: 0,
			j: 0
		});
		for (let i = 1; i < out.length; i += 1) {
			out[i].a = (out[i].v - out[i - 1].v) / step;
			out[i].x = out[i - 1].x + out[i].v * step;
		}
		for (let i = 2; i < out.length; i += 1) out[i].j = (out[i].a - out[i - 1].a) / step;
		return out;
	}
	function buildIncrementalMotionCommand(input, axisContext = {}) {
		const params = normalizeMotionParams(input);
		const plan = solveMotionPlan(params);
		const validation = VALIDATION_COPY[axisContext.language === "en" ? "en" : "zh"];
		const currentPositionCounts = Math.round(numberOrZero$1(axisContext.currentPositionCounts));
		const targetDeltaCounts = countsFromUnits(params.targetPosition, axisContext);
		const vmaxCountsS = Math.abs(countsFromUnits(plan.vPeak, axisContext));
		const accelCountsS2 = Math.abs(countsFromUnits(params.accel, axisContext));
		const decelCountsS2 = Math.abs(countsFromUnits(params.decel, axisContext));
		const finalPositionCounts = currentPositionCounts + targetDeltaCounts;
		const minPos = optionalInteger(axisContext.minPositionCounts);
		const maxPos = optionalInteger(axisContext.maxPositionCounts);
		const positionUnit = unitText$1(axisContext.positionUnit);
		const finalPositionUser = unitsFromCounts(finalPositionCounts, axisContext);
		const minPositionUser = minPos !== void 0 ? unitsFromCounts(minPos, axisContext) : void 0;
		const maxPositionUser = maxPos !== void 0 ? unitsFromCounts(maxPos, axisContext) : void 0;
		const rangeMinUser = minPositionUser !== void 0 && maxPositionUser !== void 0 ? Math.min(minPositionUser, maxPositionUser) : void 0;
		const rangeMaxUser = minPositionUser !== void 0 && maxPositionUser !== void 0 ? Math.max(minPositionUser, maxPositionUser) : void 0;
		const command = {
			cmd: "move_curve_rel",
			mode: "incremental",
			profile_version: MOTION_PROFILE_VERSION,
			target_delta_counts: targetDeltaCounts,
			vmax_counts_s: vmaxCountsS,
			accel_counts_s2: accelCountsS2,
			decel_counts_s2: decelCountsS2,
			dwell_ms: Math.round(params.dwell * 1e3),
			blend: params.blend,
			solve_mode: params.mode
		};
		const errors = [];
		const warnings = [];
		if (minPos !== void 0) command.min_pos = minPos;
		if (maxPos !== void 0) command.max_pos = maxPos;
		if (Math.abs(params.targetPosition) <= 0) errors.push(validation.targetPositionZero);
		if (params.targetSpeed <= 0) errors.push(validation.targetSpeedRequired);
		if (params.accel <= 0) errors.push(validation.accelRequired);
		if (params.decel <= 0) errors.push(validation.decelRequired);
		if (minPos !== void 0 && maxPos !== void 0 && rangeMinUser !== void 0 && rangeMaxUser !== void 0) {
			if (finalPositionUser < rangeMinUser - 1e-9) errors.push(formatMessage(validation.finalBelow, {
				final: formatUserValue(finalPositionUser, positionUnit),
				limit: formatUserValue(rangeMinUser, positionUnit)
			}));
			else if (finalPositionUser > rangeMaxUser + 1e-9) errors.push(formatMessage(validation.finalAbove, {
				final: formatUserValue(finalPositionUser, positionUnit),
				limit: formatUserValue(rangeMaxUser, positionUnit)
			}));
		} else {
			if (minPos !== void 0 && finalPositionCounts < minPos) errors.push(formatMessage(validation.finalBelow, {
				final: String(finalPositionCounts),
				limit: String(minPos)
			}));
			if (maxPos !== void 0 && finalPositionCounts > maxPos) errors.push(formatMessage(validation.finalAbove, {
				final: String(finalPositionCounts),
				limit: String(maxPos)
			}));
		}
		if (axisContext.maxVelocityCountsS !== void 0 && vmaxCountsS > axisContext.maxVelocityCountsS) warnings.push(validation.vmaxWarning);
		if (axisContext.maxAccelCountsS2 !== void 0 && accelCountsS2 > axisContext.maxAccelCountsS2) warnings.push(validation.accelWarning);
		if (axisContext.maxDecelCountsS2 !== void 0 && decelCountsS2 > axisContext.maxDecelCountsS2) warnings.push(validation.decelWarning);
		return {
			valid: errors.length === 0,
			errors,
			warnings,
			command,
			finalPositionCounts,
			totalTimeMs: Math.round(plan.totalTime * 1e3)
		};
	}
	function buildMotionProfileResult(params, axisContext = {}, dt = .005) {
		const normalized = normalizeMotionParams(params);
		return {
			params: normalized,
			plan: solveMotionPlan(normalized),
			samples: generateMotionSamples(normalized, dt),
			commandProfile: buildIncrementalMotionCommand(normalized, axisContext)
		};
	}
	function countsFromUnits(value, axisContext) {
		const countsPerRev = positiveOr(axisContext.countsPerRev, DEFAULT_COUNTS_PER_REV);
		const userUnitsPerRev = positiveOr(axisContext.userUnitsPerRev, 360);
		const transmissionSign = axisContext.direction === "reverse" ? -1 : 1;
		const axisSign = Number(axisContext.axisSign) === -1 ? -1 : 1;
		return Math.round(Number(value) / userUnitsPerRev * countsPerRev * axisSign / transmissionSign);
	}
	function unitsFromCounts(value, axisContext) {
		const countsPerRev = positiveOr(axisContext.countsPerRev, DEFAULT_COUNTS_PER_REV);
		const userUnitsPerRev = positiveOr(axisContext.userUnitsPerRev, 360);
		const transmissionSign = axisContext.direction === "reverse" ? -1 : 1;
		const axisSign = Number(axisContext.axisSign) === -1 ? -1 : 1;
		return Number(value) / countsPerRev * userUnitsPerRev * transmissionSign / axisSign;
	}
	function formatUserValue(value, unit) {
		return `${Number(value).toFixed(2)}${unit ? ` ${unit}` : ""}`;
	}
	function formatMessage(template, values) {
		let output = template;
		for (const [key, value] of Object.entries(values)) output = output.replace(`{${key}}`, value);
		return output;
	}
	function unitText$1(value) {
		return typeof value === "string" ? value.trim() : "";
	}
	function clamp$1(value, min, max) {
		return Math.max(min, Math.min(max, value));
	}
	function easing(mode, raw) {
		const t = clamp$1(raw, 0, 1);
		if (mode === "linear") return t;
		if (mode === "aggressive") return t < .5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;
		return t * t * (3 - 2 * t);
	}
	function numberOrZero$1(value) {
		const num = Number(value);
		return Number.isFinite(num) ? num : 0;
	}
	function positiveOr(value, fallback) {
		const num = Number(value);
		return Number.isFinite(num) && num > 0 ? num : fallback;
	}
	function optionalInteger(value) {
		const num = Number(value);
		return Number.isFinite(num) ? Math.round(num) : void 0;
	}
	//#endregion
	//#region src/embeddedMotionProfile.ts
	var FIELD_NAMES = [
		"mode",
		"targetPosition",
		"targetSpeed",
		"accel",
		"decel",
		"dwell",
		"blend"
	];
	var ALL_CHART_KINDS = [
		"x",
		"v",
		"a",
		"j"
	];
	var CHART_WIDTH = 780;
	var CHART_HEIGHT = 160;
	var CHART_OX = 58;
	var CHART_OY = 12;
	var CHART_AXIS_LABEL_Y_OFFSET = 32;
	var CHART_HOVER_TIME_Y_OFFSET = 22;
	var COPY = {
		zh: {
			mode: "求解方式",
			positionMode: "位置优先",
			manualMode: "手动约束",
			targetPosition: "目标增量位置 S",
			targetSpeed: "速度上限 Vmax",
			accel: "加速度上限 Amax",
			decel: "减速度上限 Dmax",
			dwell: "停顿时间",
			blend: "加速曲线捏合方式",
			linear: "线性",
			smooth: "平滑",
			aggressive: "强化",
			chartX: "位置曲线 x/t",
			chartV: "速度曲线 v/t",
			chartA: "加速度曲线 a/t",
			chartJ: "加加速度曲线 j/t",
			invalid: "参数未满足执行条件"
		},
		en: {
			mode: "Solve mode",
			positionMode: "Position first",
			manualMode: "Manual constraints",
			targetPosition: "Target delta position S",
			targetSpeed: "Speed limit Vmax",
			accel: "Acceleration limit Amax",
			decel: "Deceleration limit Dmax",
			dwell: "Dwell time",
			blend: "Acceleration blend",
			linear: "Linear",
			smooth: "Smooth",
			aggressive: "Aggressive",
			chartX: "Position curve x/t",
			chartV: "Velocity curve v/t",
			chartA: "Acceleration curve a/t",
			chartJ: "Jerk curve j/t",
			invalid: "Parameters are not executable yet"
		}
	};
	var MctivityMotionCurveEditor = {
		version: MOTION_PROFILE_VERSION,
		single: {
			mount,
			buildMotionProfileResult,
			defaultParams: DEFAULT_MOTION_PARAMS
		},
		multi: { status: "reserved" },
		cam: { status: "reserved" },
		core: {
			normalizeMotionParams,
			solveMotionPlan,
			generateMotionSamples,
			buildIncrementalMotionCommand,
			buildMotionProfileResult
		}
	};
	var MctivityMotionProfile = MctivityMotionCurveEditor.single;
	function registerGlobalMotionProfile(target = window) {
		const motionWindow = target;
		motionWindow.MctivityMotionCurveEditor = MctivityMotionCurveEditor;
		motionWindow.MctivityMotionProfile = MctivityMotionProfile;
	}
	function mount(container, options = {}) {
		injectEmbeddedStyles();
		const copy = COPY[options.language === "en" ? "en" : "zh"];
		const hostMode = options.hostMode === "standalone" ? "standalone" : "embedded";
		const appearance = options.appearance ?? (hostMode === "standalone" ? "standalone" : "embedded");
		const chartMode = options.chart ?? (hostMode === "standalone" ? "xvaj" : "xvaj");
		const layoutMode = options.layout ?? (hostMode === "standalone" ? "split" : "stacked");
		let params = normalizeMotionParams({
			...DEFAULT_MOTION_PARAMS,
			...options.initialParams
		});
		let axisContext = { ...options.axisContext ?? {} };
		let hoverTime = null;
		let resizeObserver = null;
		let host = null;
		let charts = {};
		let statusEl = null;
		const chartKinds = chartMode === "position-only" ? ["x"] : ALL_CHART_KINDS;
		const renderMarkup = () => {
			container.innerHTML = `
      <div class="mct-profile-widget${appearance === "standalone" ? " is-standalone" : ""}" data-mct-profile>
        <section class="mct-profile-controls" aria-label="Motion profile parameters">
          ${options.controlsTitle ? `<h2 class="mct-profile-section-title">${options.controlsTitle}</h2>` : ""}
          <label data-field-wrap="mode"><span data-label="mode"></span><select data-field="mode"><option value="position">${copy.positionMode}</option><option value="manual">${copy.manualMode}</option></select></label>
          <label data-field-wrap="targetPosition"><span data-label="targetPosition"></span><input data-field="targetPosition" type="number" step="1" /></label>
          <label data-field-wrap="targetSpeed"><span data-label="targetSpeed"></span><input data-field="targetSpeed" type="number" min="0" step="1" /></label>
          <label data-field-wrap="accel"><span data-label="accel"></span><input data-field="accel" type="number" min="0" step="1" /></label>
          <label data-field-wrap="decel"><span data-label="decel"></span><input data-field="decel" type="number" min="0" step="1" /></label>
          <label data-field-wrap="dwell"><span data-label="dwell"></span><input data-field="dwell" type="number" min="0" step="0.1" /></label>
          <label data-field-wrap="blend"><span data-label="blend"></span><select data-field="blend"><option value="linear">${copy.linear}</option><option value="smooth">${copy.smooth}</option><option value="aggressive">${copy.aggressive}</option></select></label>
        </section>
        <section class="mct-profile-charts">
          ${chartKinds.map((kind) => chartCardMarkup(kind, kind === "x")).join("")}
        </section>
      </div>
    `;
			host = container.querySelector("[data-mct-profile]");
			charts = {};
			for (const kind of chartKinds) {
				const chart = host?.querySelector(`[data-chart="${kind}"]`) ?? void 0;
				if (chart) charts[kind] = chart;
			}
			statusEl = host?.querySelector("[data-status]") ?? null;
		};
		const applyCopy = () => {
			if (!host) return;
			const displayCopy = buildDisplayCopy(copy, axisContext);
			setText(host, "mode", displayCopy.mode);
			setText(host, "targetPosition", displayCopy.targetPosition);
			setText(host, "targetSpeed", displayCopy.targetSpeed);
			setText(host, "accel", displayCopy.accel);
			setText(host, "decel", displayCopy.decel);
			setText(host, "dwell", displayCopy.dwell);
			setText(host, "blend", displayCopy.blend);
			if (charts.x) {
				setChartTitle(host, "x", displayCopy.chartX);
				charts.x.setAttribute("aria-label", displayCopy.chartX);
			}
			if (charts.v) {
				setChartTitle(host, "v", displayCopy.chartV);
				charts.v.setAttribute("aria-label", displayCopy.chartV);
			}
			if (charts.a) {
				setChartTitle(host, "a", displayCopy.chartA);
				charts.a.setAttribute("aria-label", displayCopy.chartA);
			}
			if (charts.j) {
				setChartTitle(host, "j", displayCopy.chartJ);
				charts.j.setAttribute("aria-label", displayCopy.chartJ);
			}
		};
		const fieldElement = (name) => {
			return host?.querySelector(`[data-field="${name}"]`) ?? null;
		};
		const applyLayoutMode = () => {
			if (!host) return;
			let stacked = layoutMode === "stacked";
			if (layoutMode === "auto") {
				const width = Math.max(container.clientWidth || 0, host.clientWidth || 0);
				stacked = width > 0 && width < 980;
			}
			if (layoutMode === "split") stacked = false;
			const chartPanelWidth = host.querySelector(".mct-profile-charts")?.clientWidth ?? 0;
			const wideChart = appearance === "standalone" && !stacked && chartPanelWidth >= 640;
			host.classList.toggle("is-stacked", stacked);
			host.classList.toggle("is-wide-chart", wideChart);
		};
		const syncInputs = () => {
			for (const name of FIELD_NAMES) {
				const el = fieldElement(name);
				if (el) el.value = String(params[name]);
			}
		};
		const syncParams = () => {
			params = {
				mode: fieldElement("mode")?.value === "manual" ? "manual" : "position",
				targetPosition: numberOrZero(fieldElement("targetPosition")?.value),
				targetSpeed: Math.max(0, numberOrZero(fieldElement("targetSpeed")?.value)),
				accel: Math.max(0, numberOrZero(fieldElement("accel")?.value)),
				decel: Math.max(0, numberOrZero(fieldElement("decel")?.value)),
				dwell: Math.max(0, numberOrZero(fieldElement("dwell")?.value)),
				blend: blendOrSmooth(fieldElement("blend")?.value)
			};
		};
		const render = () => {
			const result = buildMotionProfileResult(params, axisContext);
			if (!result.commandProfile.valid) hoverTime = null;
			else if (hoverTime === null) hoverTime = result.samples[0]?.t ?? 0;
			if (charts.x) drawProfileChart(charts.x, result.params, result.samples, (sample) => sample.x, "mct-line-x", hoverTime);
			if (charts.v) drawProfileChart(charts.v, result.params, result.samples, (sample) => sample.v, "mct-line-v", hoverTime);
			if (charts.a) drawProfileChart(charts.a, result.params, result.samples, (sample) => sample.a, "mct-line-a", hoverTime);
			if (charts.j) drawProfileChart(charts.j, result.params, result.samples, (sample) => sample.j, "mct-line-j", hoverTime);
			if (statusEl) {
				statusEl.textContent = result.commandProfile.valid ? "" : result.commandProfile.errors[0] ?? copy.invalid;
				statusEl.classList.toggle("is-invalid", !result.commandProfile.valid);
			}
			options.onChange?.(result);
		};
		const onInput = () => {
			syncParams();
			render();
		};
		const onMouseMove = (event) => {
			const svg = event.currentTarget;
			if (!svg) return;
			const result = buildMotionProfileResult(params, axisContext);
			if (!result.commandProfile.valid) {
				hoverTime = null;
				return;
			}
			const rect = svg.getBoundingClientRect();
			const viewBox = svg.viewBox.baseVal;
			const samples = result.samples;
			const scaleX = (viewBox.width || 860) / rect.width;
			const xInPlot = clamp((event.clientX - rect.left) * scaleX - CHART_OX, 0, CHART_WIDTH);
			const tMax = samples[samples.length - 1]?.t ?? 0;
			hoverTime = xInPlot / CHART_WIDTH * tMax;
			render();
		};
		const onMouseLeave = () => {
			render();
		};
		const mountHost = () => {
			renderMarkup();
			if (!host) return;
			host.addEventListener("input", onInput);
			for (const kind of chartKinds) {
				const chart = charts[kind];
				if (!chart) continue;
				chart.addEventListener("mousemove", onMouseMove);
				chart.addEventListener("mouseleave", onMouseLeave);
			}
			applyLayoutMode();
			if (typeof ResizeObserver !== "undefined") {
				resizeObserver = new ResizeObserver(() => {
					applyLayoutMode();
				});
				resizeObserver.observe(container);
			} else window.addEventListener("resize", applyLayoutMode);
			applyCopy();
			syncInputs();
			render();
		};
		mountHost();
		return {
			getParams: () => ({ ...params }),
			setParams: (next) => {
				params = normalizeMotionParams({
					...DEFAULT_MOTION_PARAMS,
					...params,
					...next
				});
				syncInputs();
				render();
			},
			setAxisContext: (nextAxisContext) => {
				axisContext = {
					...axisContext,
					...nextAxisContext
				};
				applyCopy();
				render();
			},
			getResult: (nextAxisContext = axisContext) => buildMotionProfileResult(params, nextAxisContext),
			getCommandProfile: (nextAxisContext = axisContext) => buildMotionProfileResult(params, nextAxisContext).commandProfile,
			destroy: () => {
				if (resizeObserver) {
					resizeObserver.disconnect();
					resizeObserver = null;
				} else window.removeEventListener("resize", applyLayoutMode);
				container.innerHTML = "";
			}
		};
	}
	function chartCardMarkup(kind, withStatus) {
		return `
    <div class="mct-profile-chart-card">
      <div class="mct-profile-chart-head"><h3 data-chart-title="${kind}"></h3>${withStatus ? "<span data-status></span>" : "<span></span>"}</div>
      <svg data-chart="${kind}" viewBox="0 0 860 240" preserveAspectRatio="xMidYMid meet" role="img"></svg>
    </div>
  `;
	}
	function setText(host, label, value) {
		const node = host.querySelector(`[data-label="${label}"]`);
		if (node) node.textContent = value;
	}
	function setChartTitle(host, kind, value) {
		const node = host.querySelector(`[data-chart-title="${kind}"]`);
		if (node) node.textContent = value;
	}
	function unitText(value) {
		return typeof value === "string" ? value.trim() : "";
	}
	function rateUnit(positionUnit, timeUnit, power) {
		if (!positionUnit) return "";
		if (power === 1) return `${positionUnit}/${timeUnit}`;
		return `${positionUnit}/${timeUnit}${power === 2 ? "²" : "³"}`;
	}
	function labelWithUnit(label, unit) {
		return unit ? `${label} (${unit})` : label;
	}
	function buildDisplayCopy(copy, axisContext = {}) {
		const positionUnit = unitText(axisContext.positionUnit);
		const timeUnit = unitText(axisContext.timeUnit) || "s";
		const speedUnit = unitText(axisContext.speedUnit) || rateUnit(positionUnit, timeUnit, 1);
		const accelUnit = unitText(axisContext.accelUnit) || rateUnit(positionUnit, timeUnit, 2);
		const decelUnit = unitText(axisContext.decelUnit) || accelUnit;
		const jerkUnit = unitText(axisContext.jerkUnit) || rateUnit(positionUnit, timeUnit, 3);
		const dwellUnit = unitText(axisContext.dwellUnit) || timeUnit;
		return {
			mode: copy.mode,
			positionMode: copy.positionMode,
			manualMode: copy.manualMode,
			targetPosition: labelWithUnit(copy.targetPosition, positionUnit),
			targetSpeed: labelWithUnit(copy.targetSpeed, speedUnit),
			accel: labelWithUnit(copy.accel, accelUnit),
			decel: labelWithUnit(copy.decel, decelUnit),
			dwell: labelWithUnit(copy.dwell, dwellUnit),
			blend: copy.blend,
			linear: copy.linear,
			smooth: copy.smooth,
			aggressive: copy.aggressive,
			chartX: labelWithUnit(copy.chartX, positionUnit),
			chartV: labelWithUnit(copy.chartV, speedUnit),
			chartA: labelWithUnit(copy.chartA, accelUnit),
			chartJ: labelWithUnit(copy.chartJ, jerkUnit),
			invalid: copy.invalid
		};
	}
	function blendOrSmooth(value) {
		return value === "linear" || value === "aggressive" ? value : "smooth";
	}
	function numberOrZero(value) {
		const num = Number(value);
		return Number.isFinite(num) ? num : 0;
	}
	function minMax(values) {
		let min = Number.POSITIVE_INFINITY;
		let max = Number.NEGATIVE_INFINITY;
		for (const value of values) {
			if (value < min) min = value;
			if (value > max) max = value;
		}
		if (!Number.isFinite(min) || !Number.isFinite(max)) return {
			min: 0,
			max: 0
		};
		return {
			min,
			max
		};
	}
	function clamp(value, min, max) {
		return Math.max(min, Math.min(max, value));
	}
	function valueAt(samples, t, getter) {
		if (samples.length === 0) return 0;
		if (t <= samples[0].t) return getter(samples[0]);
		if (t >= samples[samples.length - 1].t) return getter(samples[samples.length - 1]);
		let index = 1;
		while (index < samples.length && samples[index].t < t) index += 1;
		const a = samples[index - 1];
		const b = samples[index];
		const ratio = (t - a.t) / (b.t - a.t);
		return getter(a) + ratio * (getter(b) - getter(a));
	}
	function drawProfileChart(svg, params, samples, getter, cls, hoverTime) {
		const topPad = svg.closest(".mct-profile-widget.is-standalone") !== null ? 8 : 0;
		const width = CHART_WIDTH;
		const height = CHART_HEIGHT - topPad;
		const ox = CHART_OX;
		const oy = CHART_OY + topPad;
		const xs = samples.map((sample) => sample.t);
		const ys = samples.map(getter);
		const xrange = minMax(xs);
		const yrange = minMax(ys);
		const spanX = Math.max(xrange.max - xrange.min, 1e-9);
		const spanY = Math.max(yrange.max - yrange.min, 1e-9);
		const plan = solveMotionPlan(params);
		const internalNodeTimes = [
			0,
			plan.tAcc,
			plan.tAcc + plan.tCruise,
			plan.tAcc + plan.tCruise + plan.tDec,
			plan.totalTime
		].slice(1, -1).map((t) => clamp(t, xrange.min, xrange.max)).filter((t, index, list) => {
			const touchesStart = Math.abs(t - xrange.min) < 1e-6;
			const touchesEnd = Math.abs(t - xrange.max) < 1e-6;
			if (touchesStart || touchesEnd) return false;
			return list.findIndex((candidate) => Math.abs(candidate - t) < 1e-6) === index;
		});
		const toX = (t) => ox + (t - xrange.min) / spanX * width;
		const toY = (y) => oy + (height - (y - yrange.min) / spanY * height);
		const path = samples.map((sample, index) => `${index === 0 ? "M" : "L"} ${toX(sample.t).toFixed(2)} ${toY(getter(sample)).toFixed(2)}`).join(" ");
		const nodeEls = internalNodeTimes.map((t) => {
			const x = toX(clamp(t, xrange.min, xrange.max));
			return `<line x1="${x.toFixed(2)}" y1="${oy}" x2="${x.toFixed(2)}" y2="${oy + height}" class="mct-node-line"/><text x="${x.toFixed(2)}" y="${oy + height + CHART_AXIS_LABEL_Y_OFFSET}" class="mct-node-time" text-anchor="middle">${t.toFixed(3)}s</text>`;
		}).join("");
		const zeroY = toY(0);
		const showZero = zeroY >= oy && zeroY <= oy + height;
		let hoverEls = "";
		if (hoverTime !== null) {
			const t = clamp(hoverTime, xrange.min, xrange.max);
			const yVal = valueAt(samples, t, getter);
			const x = toX(t);
			const y = toY(yVal);
			const hoverValueY = Math.max(oy + 14, y - 8);
			const valueNearRight = x > 746;
			const valueTextX = valueNearRight ? x - 8 : x + 8;
			const valueAnchor = valueNearRight ? "end" : "start";
			const timeNearLeft = x < 126;
			const timeNearRight = x > 770;
			const timeTextX = timeNearLeft ? x + 24 : timeNearRight ? x - 26 : x;
			const timeAnchor = timeNearLeft ? "start" : timeNearRight ? "end" : "middle";
			const timeTextY = oy + height + CHART_HOVER_TIME_Y_OFFSET;
			hoverEls = `<line x1="${x.toFixed(2)}" y1="${oy}" x2="${x.toFixed(2)}" y2="${oy + height}" class="mct-hover-line"/><circle cx="${x.toFixed(2)}" cy="${y.toFixed(2)}" r="4.5" class="mct-hover-dot"/><text x="${valueTextX.toFixed(2)}" y="${hoverValueY.toFixed(2)}" class="mct-hover-value" text-anchor="${valueAnchor}">${yVal.toFixed(3)}</text><text x="${timeTextX.toFixed(2)}" y="${timeTextY.toFixed(2)}" class="mct-hover-time" text-anchor="${timeAnchor}">${t.toFixed(3)}s</text>`;
		}
		svg.innerHTML = `<rect x="${ox}" y="${oy}" width="${width}" height="${height}" class="mct-plot-bg"/><line x1="${ox}" y1="${oy + height}" x2="838" y2="${oy + height}" class="mct-axis"/><line x1="${ox}" y1="${oy}" x2="${ox}" y2="${oy + height}" class="mct-axis"/>${showZero ? `<line x1="${ox}" y1="${zeroY.toFixed(2)}" x2="838" y2="${zeroY.toFixed(2)}" class="mct-zero-line"/>` : ""}${nodeEls}<path d="${path}" class="${cls}"/>${hoverEls}<text x="${ox - 10}" y="${oy + 7}" class="mct-tick" text-anchor="end">${yrange.max.toFixed(2)}</text><text x="${ox - 10}" y="${oy + height}" class="mct-tick" text-anchor="end">${yrange.min.toFixed(2)}</text><text x="${ox}" y="${oy + height + CHART_AXIS_LABEL_Y_OFFSET}" class="mct-tick">0 s</text><text x="838" y="${oy + height + CHART_AXIS_LABEL_Y_OFFSET}" class="mct-tick" text-anchor="end">${xrange.max.toFixed(2)} s</text>`;
	}
	function injectEmbeddedStyles() {
		if (document.querySelector("[data-mct-curve-style]")) return;
		const style = document.createElement("style");
		style.setAttribute("data-mct-curve-style", "true");
		style.textContent = [
			".mct-profile-widget{display:grid;grid-template-columns:340px 1fr;gap:14px;color:#233140;font-family:MiSans,\"MiSans VF\",\"PingFang SC\",\"Helvetica Neue\",Helvetica,Arial,sans-serif;}",
			".mct-profile-widget.is-stacked{grid-template-columns:1fr;gap:14px;}",
			".mct-profile-controls,.mct-profile-charts{border:1px solid rgba(41,111,173,.18);border-radius:16px;background:rgba(255,255,255,.92);padding:14px;box-shadow:0 12px 32px rgba(30,48,72,.08);}",
			".mct-profile-controls{display:grid;gap:8px;}",
			".mct-profile-section-title{margin:0 0 6px;font-size:17px;line-height:1.1;font-weight:800;color:#233140;}",
			".mct-profile-controls label{display:grid;grid-template-columns:minmax(126px,1.12fr) minmax(0,1fr);align-items:center;gap:10px;margin-bottom:0;font-size:14px;color:#657180;}",
			".mct-profile-controls label>span{line-height:1.2;}",
			".mct-profile-controls label:last-child{margin-bottom:0;}",
			".mct-profile-controls input,.mct-profile-controls select{min-width:0;border:1px solid rgba(41,111,173,.22);border-radius:10px;padding:8px 10px;background:#fff;color:#233140;font:inherit;}",
			".mct-profile-charts{display:grid;gap:6px;min-width:0;overflow:hidden;}",
			".mct-profile-chart-card{display:grid;gap:0;overflow:hidden;}",
			".mct-profile-chart-head{display:flex;align-items:flex-end;gap:8px;margin-bottom:-4px;min-height:14px;}",
			".mct-profile-chart-head h3{margin:0;font-size:12px;font-weight:500;letter-spacing:.01em;color:#233140;}",
			".mct-profile-chart-head span{margin-left:auto;color:#2f72b7;font-size:12px;}",
			".mct-profile-chart-head span:empty{display:none;}",
			".mct-profile-chart-head span.is-invalid{color:#a95442;}",
			".mct-profile-chart-card svg{width:100%;height:176px;display:block;}",
			".mct-profile-widget.is-stacked .mct-profile-chart-card svg{height:168px;}",
			".mct-profile-widget.is-standalone .mct-profile-controls{display:block;}",
			".mct-profile-widget.is-standalone .mct-profile-controls label{grid-template-columns:1fr;gap:6px;margin-bottom:10px;font-size:14px;}",
			".mct-profile-widget.is-standalone .mct-profile-controls label:last-child{margin-bottom:0;}",
			".mct-profile-widget.is-standalone .mct-profile-chart-head{margin-bottom:10px;min-height:auto;}",
			".mct-profile-widget.is-standalone .mct-profile-chart-head h3{font-size:16px;font-weight:700;}",
			".mct-profile-widget.is-standalone .mct-profile-chart-head span{font-size:12px;}",
			".mct-profile-widget.is-standalone.is-wide-chart .mct-profile-chart-card svg{height:auto;aspect-ratio:860/240;}",
			".mct-profile-widget.is-standalone .mct-node-time,.mct-profile-widget.is-standalone .mct-tick{font-size:11px;font-weight:600;}",
			".mct-profile-widget.is-standalone .mct-hover-value,.mct-profile-widget.is-standalone .mct-hover-time{font-size:11px;font-weight:600;}",
			".mct-plot-bg{fill:#fff;stroke:#dbe8f4;stroke-width:1;}",
			".mct-axis{stroke:#6f9cc7;stroke-width:1.5;}",
			".mct-zero-line{stroke:#bed2e3;stroke-width:1;stroke-dasharray:6 6;}",
			".mct-node-line{stroke:#d7e4f0;stroke-width:1.2;stroke-dasharray:7 7;}",
			".mct-node-time,.mct-tick{fill:#6b7f92;font-size:16px;font-weight:500;}",
			".mct-line-x{fill:none;stroke:#6a4cff;stroke-width:3;stroke-linecap:round;stroke-linejoin:round;}",
			".mct-line-v{fill:none;stroke:#0072ce;stroke-width:3;stroke-linecap:round;stroke-linejoin:round;}",
			".mct-line-a{fill:none;stroke:#ef6c00;stroke-width:3;stroke-linecap:round;stroke-linejoin:round;}",
			".mct-line-j{fill:none;stroke:#1c9a6d;stroke-width:3;stroke-linecap:round;stroke-linejoin:round;}",
			".mct-hover-line{stroke:#263846;stroke-width:1.2;stroke-dasharray:4 4;}",
			".mct-hover-dot{fill:#fff;stroke:#0878c8;stroke-width:2;}",
			".mct-hover-value,.mct-hover-time{fill:#263846;font-size:15px;font-weight:700;}",
			".mct-profile-empty{padding:18px;border:1px dashed rgba(42,131,183,.26);border-radius:16px;color:#6e7f90;font-size:12px;}",
			"@media (max-width:760px){.mct-profile-widget,.mct-profile-widget.is-stacked{grid-template-columns:1fr;}.mct-profile-controls label{grid-template-columns:1fr;gap:4px;}.mct-profile-chart-card svg,.mct-profile-widget.is-stacked .mct-profile-chart-card svg{height:156px;}}"
		].join("");
		document.head.appendChild(style);
	}
	//#endregion
	//#region src/block-entry.ts
	registerGlobalMotionProfile();
	//#endregion
})();
