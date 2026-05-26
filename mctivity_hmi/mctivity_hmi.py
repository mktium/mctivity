#!/usr/bin/env python3
import json
import os
import re
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


def _env_int(name, default):
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name, default):
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


MOTIOND_HOST = os.environ.get("MCTIVITY_HOST", "127.0.0.1")
MOTIOND_PORT = _env_int("MCTIVITY_PORT", 10001)
WEB_HOST = os.environ.get("MCTIVITY_WEB_HOST", "0.0.0.0")
WEB_PORT = _env_int("MCTIVITY_WEB_PORT", 2015)
FV3_SLAVE_POSITION = _env_int("MCTIVITY_FV3_SLAVE_POSITION", 1)
FV3_STATUS_TTL_SEC = _env_float("MCTIVITY_FV3_STATUS_TTL_SEC", 0.5)
UI_STATE_PATH = os.environ.get(
    "MCTIVITY_UI_STATE_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "mctivity_hmi_state.json"),
)
_ui_state_lock = threading.RLock()

HTML = r"""
<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover, user-scalable=no, maximum-scale=1" />
<meta name="apple-mobile-web-app-capable" content="yes" />
<meta name="apple-mobile-web-app-title" content="Motion HMI" />
<meta name="apple-mobile-web-app-status-bar-style" content="default" />
<meta name="mobile-web-app-capable" content="yes" />
<title>多轴控制</title>
<style>
:root {
  --abs-thumb-size:36px;
  --theme-blue:#2A83B7; --theme-deep:#1A69A5; --ink:#111; --dark:#404040;
  --mid:#A6A6A6; --light:#D9D9D9; --paper:#fff; --soft:#F7FAFC;
  --ok:#16864A; --warn:#C77600; --bad:#BA1A1A; --line:rgba(42,131,183,.25);
  --shadow:0 10px 26px rgba(26,105,165,.10);
}
* { box-sizing:border-box; -webkit-tap-highlight-color:transparent; }
html, body { width:100%; height:100%; overflow:hidden; overscroll-behavior:none; position:fixed; inset:0; touch-action:none; }
body { margin:0; color:var(--ink); font-family:MiSans,"MiSans VF","PingFang SC","Helvetica Neue",Helvetica,Arial,sans-serif; background:linear-gradient(180deg,#fff 0%,#f5f9fc 100%); -webkit-user-select:none; user-select:none; }
main { width:100%; height:100dvh; max-width:1360px; margin:0 auto; padding:8px 14px 12px; display:grid; grid-template-rows:auto auto minmax(0,1fr); gap:7px; }
.topbar { display:grid; grid-template-columns:1fr auto; gap:14px; align-items:end; border-bottom:3px solid var(--theme-blue); padding-bottom:9px; min-height:64px; }
.topbar-left { display:grid; gap:4px; }
h1 { margin:0; font-size:clamp(24px,3vw,38px); line-height:1; font-weight:900; }
.topbar-right { display:flex; align-items:flex-end; justify-content:flex-end; gap:10px; position:relative; }
.brand-wordmark { color:var(--theme-deep); font-size:24px; line-height:1; font-weight:900; letter-spacing:.08em; white-space:nowrap; text-transform:none; }
.logo { width:44px; height:44px; object-fit:contain; object-position:right center; flex:0 0 auto; }
.subbar { display:flex; align-items:center; gap:10px; min-height:38px; }
.protocol-chip { display:inline-flex; align-items:center; justify-content:center; min-height:36px; padding:0; border:0; border-radius:0; background:transparent; color:var(--theme-deep); font-size:28px; font-weight:900; letter-spacing:.02em; text-transform:none; white-space:nowrap; }
.tabs { display:flex; flex:1 1 auto; gap:10px; margin:0; min-height:36px; }
.tab-btn { min-height:36px; padding:7px 18px; border:1px solid var(--line); border-radius:999px; background:#fff; color:var(--theme-deep); box-shadow:none; }
.tab-btn.active { color:#fff; background:var(--theme-blue); border-color:var(--theme-blue); }
.lang-menu { position:relative; margin-left:auto; }
.lang-btn { min-width:28px; min-height:28px; padding:0; border:0; border-radius:0; background:transparent; color:var(--theme-deep); font-size:20px; line-height:1; font-weight:400; box-shadow:none; display:inline-flex; align-items:center; justify-content:center; }
.lang-btn:hover, .lang-btn[aria-expanded="true"] { transform:none; box-shadow:none; color:var(--theme-blue); background:transparent; }
.lang-dropdown { position:absolute; right:0; top:calc(100% + 8px); min-width:132px; padding:6px; display:none; background:rgba(255,255,255,.98); border:1px solid var(--line); border-radius:12px; box-shadow:var(--shadow); z-index:20; }
.lang-dropdown.open { display:grid; gap:4px; }
.lang-option { min-height:36px; padding:8px 12px; border:0; border-radius:9px; background:transparent; color:var(--theme-deep); font:inherit; font-size:13px; font-weight:800; text-align:left; }
.lang-option.active { background:var(--soft); color:var(--theme-blue); }
.lang-option:hover { background:var(--soft); }
.tab-panel { display:none; min-height:0; overflow:hidden; }
.tab-panel.active { display:block; }
.monitor-grid { height:100%; display:grid; grid-template-columns:minmax(320px,1.02fr) minmax(250px,.72fr) minmax(330px,1fr); gap:10px; align-items:start; min-height:0; overflow:hidden; }
.left-stack, .middle-stack, .right-stack { min-height:0; display:grid; gap:9px; align-content:start; }
.card { background:rgba(255,255,255,.96); border:1px solid var(--line); border-radius:12px; padding:10px; box-shadow:var(--shadow); overflow:hidden; }
h2 { margin:0 0 8px; font-size:17px; line-height:1.1; font-weight:900; }
.status { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:7px; }
.tile { border:1px solid rgba(166,166,166,.30); border-radius:9px; padding:7px 9px; background:#fff; min-height:52px; }
.label { display:block; font-size:10px; letter-spacing:.04em; color:#666; margin-bottom:3px; }
.value { font-size:18px; line-height:1.05; font-weight:900; overflow-wrap:anywhere; }
.good { color:var(--ok); } .bad { color:var(--bad); } .info { color:var(--theme-deep); }
.feedback-grid { display:grid; grid-template-columns:1fr 1fr; gap:10px; }
.feedback-card { border:1px solid rgba(166,166,166,.30); border-radius:12px; background:#fff; padding:10px; min-height:172px; display:grid; align-content:start; gap:8px; }
.feedback-card.encoder { grid-column:1 / -1; grid-template-columns:152px minmax(0,1fr); align-items:center; }
.feedback-title { color:#555; font-size:13px; line-height:1.1; font-weight:900; text-align:center; }
.encoder-title { grid-column:1; grid-row:1; align-self:end; margin-bottom:-2px; }
.encoder-dial { grid-column:1; grid-row:2; }
.feedback-dial { width:min(100%,144px); aspect-ratio:1; border-radius:50%; position:relative; margin:auto; background:radial-gradient(circle at center,#fff 0 58%,transparent 59% 72%,#fff 73% 100%), repeating-conic-gradient(from 0deg,var(--theme-blue) 0deg 1.1deg,transparent 1.1deg 6deg), radial-gradient(circle at center,#fff 0 57%,#eef6fb 58% 71%,#fff 72% 100%); border:2px solid var(--theme-blue); box-shadow:inset 0 0 0 7px #fff, 0 8px 16px rgba(42,131,183,.12); }
.feedback-dial.small { width:min(100%,120px); }
.feedback-dial.dashboard::after { content:""; position:absolute; inset:-4px; border-radius:50%; background:conic-gradient(from 120deg,#fff 0deg 120deg,transparent 120deg 360deg); pointer-events:none; z-index:0; }
.feedback-hand { position:absolute; left:50%; top:50%; width:4px; height:38%; margin-left:-2px; margin-top:-38%; border-radius:10px; background:var(--bad); transform-origin:50% 100%; transform:rotate(0deg); box-shadow:0 4px 10px rgba(186,26,26,.22); transition:transform .16s linear; z-index:1; }
.feedback-hub { position:absolute; left:50%; top:50%; width:20px; height:20px; margin:-10px 0 0 -10px; border-radius:50%; background:var(--ink); border:5px solid #fff; box-shadow:0 4px 8px rgba(0,0,0,.15); z-index:2; }
.feedback-value { position:absolute; left:0; right:0; bottom:8px; z-index:3; display:grid; justify-items:center; gap:1px; font-variant-numeric:tabular-nums; pointer-events:none; }
.feedback-number { color:var(--theme-deep); font-size:20px; line-height:.95; font-weight:900; }
.feedback-unit { color:#667; font-size:10px; font-weight:900; }
.feedback-tick { position:absolute; z-index:3; color:#5d6c76; font-size:8px; line-height:1; font-weight:900; transform:translate(-50%, -50%); font-variant-numeric:tabular-nums; }
.tick-0 { left:24%; top:66%; } .tick-20 { left:22%; top:39%; } .tick-40 { left:40%; top:22%; } .tick-60 { left:60%; top:22%; } .tick-80 { left:78%; top:39%; } .tick-100 { left:76%; top:66%; }
.tick-1000 { left:27%; top:36%; } .tick-2000 { left:73%; top:36%; } .tick-3000 { left:76%; top:66%; }
.tick-k1 { left:25%; top:34%; } .tick-k2 { left:75%; top:34%; } .tick-k3 { left:78%; top:68%; }
.feedback-metrics { display:grid; grid-template-columns:1fr; gap:7px; align-self:stretch; }
.feedback-metric { border:1px solid rgba(166,166,166,.28); border-radius:9px; background:var(--soft); padding:7px 10px; min-height:39px; display:grid; grid-template-columns:minmax(7.5em,1fr) minmax(11ch,13.5ch); align-items:center; gap:8px; }
.feedback-card.encoder .feedback-metrics { grid-column:2; grid-row:1 / span 2; }
.feedback-metric .label { font-size:11px; min-width:0; line-height:1.25; word-break:keep-all; }
.feedback-metric .value { color:var(--theme-deep); font-size:18px; font-variant-numeric:tabular-nums; white-space:nowrap; overflow-wrap:normal; text-align:right; justify-self:end; min-width:13.5ch; }
.feedback-metric.vertical { grid-template-columns:1fr; align-items:start; align-content:center; gap:4px; min-height:62px; }
.feedback-metric.vertical .label { margin:0; }
.feedback-metric.vertical .value { justify-self:stretch; width:100%; min-width:0; text-align:right; }
.controls { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px; }
select, input[type=number] { width:100%; min-height:36px; border:1px solid rgba(166,166,166,.45); border-radius:9px; background:#fff; color:var(--ink); padding:7px 9px; font:inherit; font-size:14px; font-weight:800; }
.mode-row { display:grid; grid-template-columns:minmax(88px,1fr) minmax(130px,1.25fr); align-items:center; gap:12px; min-height:70px; padding:10px 12px; margin-bottom:10px; border:1px solid rgba(166,166,166,.30); border-radius:12px; background:#fff; }
.mode-row .label { margin:0; color:var(--ink); font-size:20px; line-height:1; font-weight:900; letter-spacing:0; }
.mode-row select { min-height:42px; font-size:14px; }
.label-stack { display:grid; gap:4px; }
.label-subtext { color:#667; font-size:12px; line-height:1; font-weight:900; }
.transmission-trigger { width:100%; min-height:56px; border:1px solid rgba(42,131,183,.18); border-radius:12px; background:linear-gradient(180deg,#ffffff 0%,#f4f8fb 100%); padding:9px 12px; display:grid; grid-template-columns:minmax(0,1fr) auto; align-items:center; gap:10px; color:var(--ink); box-shadow:none; }
.transmission-trigger:hover { transform:none; box-shadow:none; }
.transmission-copy { min-width:0; display:grid; gap:2px; text-align:left; }
.transmission-primary { color:var(--theme-deep); font-size:13px; line-height:1.1; font-weight:900; font-variant-numeric:tabular-nums; white-space:nowrap; }
.transmission-secondary { color:#677582; font-size:11px; line-height:1.05; font-weight:900; font-variant-numeric:tabular-nums; white-space:nowrap; }
.transmission-chevron { color:var(--theme-blue); font-size:20px; line-height:1; font-weight:900; }
.control-stack { display:grid; gap:8px; }
.control-row { display:grid; grid-template-columns:1fr 1fr; gap:8px; }
.control-row.three { grid-template-columns:1fr 1fr 1fr; }
.control-note { min-height:24px; color:#666; font-size:11px; line-height:1.25; font-weight:700; }
.mode-panel { display:none; }
.mode-panel.active { display:grid; gap:8px; }
.active-mode-card { min-height:0; }
.config-grid { height:100%; display:grid; grid-template-columns:minmax(0,1.2fr) minmax(300px,.8fr); gap:10px; align-items:start; overflow:hidden; }
.param-grid { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:8px; }
.param-card { border:1px solid rgba(166,166,166,.30); border-radius:10px; padding:10px; background:#fff; display:grid; gap:7px; }
.param-card h3 { margin:0; font-size:14px; line-height:1.1; }
.param-card label { display:grid; gap:4px; color:#666; font-size:11px; font-weight:800; }
button { border:0; border-radius:999px; padding:9px 13px; min-height:40px; font-size:14px; font-weight:900; cursor:pointer; color:#fff; background:var(--dark); box-shadow:0 8px 18px rgba(64,64,64,.12); transition:transform .12s ease, box-shadow .12s ease; touch-action:manipulation; }
button:hover { transform:translateY(-1px); box-shadow:0 12px 24px rgba(64,64,64,.16); }
button.stop { background:var(--warn); } button.blue { background:var(--theme-deep); } button.neutral { background:var(--dark); }
.enable-toggle { grid-column:1 / -1; display:flex; justify-content:space-between; align-items:center; gap:16px; padding:10px 12px; min-height:70px; border-radius:12px; color:var(--ink); background:#fff; border:1px solid rgba(166,166,166,.30); box-shadow:none; text-align:left; }
.enable-toggle:hover { transform:none; box-shadow:none; }
.enable-copy { display:grid; gap:4px; }
.toggle-title { font-size:20px; line-height:1; font-weight:900; }
.toggle-subtitle { color:#777; font-size:13px; line-height:1; font-weight:800; }
.toggle-track { flex:0 0 auto; width:58px; height:34px; border-radius:999px; padding:3px; background:#d5d7dc; box-shadow:inset 0 0 0 1px rgba(17,17,17,.06); transition:background .18s ease; }
.enable-toggle.on .toggle-track { background:var(--theme-blue); }
.enable-toggle.motion-on .toggle-track { background:var(--ok); }
.power-toggle .toggle-track { background:var(--bad); }
.power-toggle.on .toggle-track { background:var(--ok); }
.power-toggle .toggle-subtitle { color:var(--bad); }
.power-toggle.on .toggle-subtitle { color:var(--ok); }
.motion-toggle .toggle-track { background:var(--bad); }
.motion-toggle.motion-on .toggle-track { background:var(--ok); }
.motion-toggle .toggle-subtitle { color:var(--bad); }
.motion-toggle.motion-on .toggle-subtitle { color:var(--ok); animation:motionBlink .7s infinite; }
.toggle-knob { display:block; width:28px; height:28px; border-radius:50%; background:#fff; box-shadow:0 2px 7px rgba(17,17,17,.24); transform:translateX(0); transition:transform .18s cubic-bezier(.2,.8,.2,1); }
.enable-toggle.on .toggle-knob, .enable-toggle.motion-on .toggle-knob { transform:translateX(24px); }
.status-button { flex:0 0 auto; min-width:58px; min-height:36px; padding:0; border-radius:999px; background:var(--ok); color:transparent; text-align:center; font-size:0; line-height:1; box-shadow:0 8px 18px rgba(22,134,74,.18); overflow:hidden; }
.status-button.fault { min-width:68px; padding:0 14px; display:inline-flex; align-items:center; justify-content:center; background:var(--bad); color:#fff; font-size:13px; font-weight:900; box-shadow:0 8px 18px rgba(186,26,26,.20); animation:faultBlink .72s infinite; }
.fault-toggle.fault-on .toggle-subtitle { color:var(--bad); animation:faultBlink .72s infinite; }
.fault-detail { flex:1 1 auto; display:grid; gap:4px; justify-items:end; color:#7a7f86; font-size:12px; line-height:1; font-weight:900; font-variant-numeric:tabular-nums; }
.fault-toggle.fault-on .fault-detail { color:var(--bad); }
.fault-code { color:inherit; }
.fault-name { color:inherit; font-size:11px; }
@keyframes faultBlink { 0%,100% { opacity:1; } 50% { opacity:.42; } }
@keyframes motionBlink { 0%,100% { opacity:1; } 50% { opacity:.35; } }
.axis-card { display:grid; grid-template-columns:180px minmax(0,1fr); gap:10px; align-items:center; }
.dial { width:180px; height:180px; border-radius:50%; position:relative; margin:auto; background:radial-gradient(circle at center,#fff 0 42%,transparent 43%), repeating-conic-gradient(from 0deg,var(--theme-blue) 0deg 1.1deg,transparent 1.1deg 6deg), radial-gradient(circle at center,#fff 0 57%,#eef6fb 58% 71%,#fff 72% 100%); border:2px solid var(--theme-blue); box-shadow:inset 0 0 0 9px #fff, 0 10px 20px rgba(42,131,183,.12); }
.hand { position:absolute; left:50%; top:50%; width:5px; height:68px; margin-left:-2.5px; margin-top:-68px; border-radius:10px; background:var(--bad); transform-origin:50% 100%; transform:rotate(0deg); box-shadow:0 5px 12px rgba(186,26,26,.22); transition:transform .16s linear; }
.hub { position:absolute; left:50%; top:50%; width:24px; height:24px; margin:-12px 0 0 -12px; border-radius:50%; background:var(--ink); border:6px solid #fff; box-shadow:0 4px 10px rgba(0,0,0,.15); }
.north,.east,.south,.west { position:absolute; font-size:11px; font-weight:900; color:var(--theme-deep); background:#fff; border-radius:999px; padding:2px 5px; }
.north { left:50%; top:22px; transform:translateX(-50%); } .east { right:22px; top:50%; transform:translateY(-50%); } .south { left:50%; bottom:22px; transform:translateX(-50%); } .west { left:22px; top:50%; transform:translateY(-50%); }
.axis-readout { display:grid; grid-template-columns:1fr 1fr; gap:10px; }
.big-angle { grid-column:1 / -1; font-size:54px; line-height:.9; color:var(--theme-deep); font-weight:900; }
.sliders { display:grid; gap:8px; }
.slider-card { border:1px solid rgba(166,166,166,.30); border-radius:10px; padding:9px; background:#fff; }
.slider-head { display:flex; justify-content:space-between; gap:10px; align-items:baseline; margin-bottom:4px; }
.slider-title { font-size:13px; font-weight:900; color:var(--dark); }
.slider-number { font-size:14px; font-weight:900; color:var(--theme-deep); text-align:right; }
input[type=range] { width:100%; accent-color:var(--theme-blue); touch-action:pan-x; }
#absPos { appearance:none; -webkit-appearance:none; height:46px; accent-color:var(--warn); background:transparent; cursor:pointer; }
#absPos::-webkit-slider-runnable-track { height:10px; border-radius:999px; border:1px solid rgba(120,130,140,.45); background:linear-gradient(90deg,#b8bec5 0 50%,#fff 50% 100%); box-shadow:inset 0 1px 2px rgba(0,0,0,.12); }
#absPos::-webkit-slider-thumb { -webkit-appearance:none; width:36px; height:36px; margin-top:-14px; border-radius:50%; background:var(--warn); border:4px solid #fff; box-shadow:0 5px 14px rgba(0,0,0,.30); }
#absPos::-moz-range-track { height:9px; border-radius:999px; border:1px solid rgba(120,130,140,.45); background:linear-gradient(90deg,#b8bec5 0 50%,#fff 50% 100%); box-shadow:inset 0 1px 2px rgba(0,0,0,.12); }
#absPos::-moz-range-thumb { width:30px; height:30px; border-radius:50%; background:var(--warn); border:4px solid #fff; box-shadow:0 5px 14px rgba(0,0,0,.30); }
.position-param { --side-width:130px; --position-gap:12px; display:grid; grid-template-columns:minmax(0,1fr) var(--side-width); gap:var(--position-gap); align-items:start; }
.axis-control { min-height:346px; display:grid; grid-template-rows:auto auto minmax(150px,1fr) auto; gap:10px; align-content:stretch; }
.axis-control > button.blue { position:relative; top:12px; }
.position-axis { position:relative; width:calc(100% + var(--side-width) + var(--position-gap)); padding:0 0 10px; }
.current-position-marker { position:absolute; left:calc((var(--abs-thumb-size) / 2) + (var(--marker-pct, 0) * (100% - var(--abs-thumb-size)))); top:18px; width:0; height:0; border-left:10px solid transparent; border-right:10px solid transparent; border-top:14px solid var(--ok); transform:translateX(-50%); filter:drop-shadow(0 2px 4px rgba(22,134,74,.28)); pointer-events:none; opacity:0; transition:left .16s linear, opacity .16s linear; }
.position-axis.linear-mode .current-position-marker { opacity:1; }
.axis-scale { display:none; }
.axis-line { display:none; }
.axis-zero { display:none; }
.axis-labels { display:grid; grid-template-columns:1fr auto 1fr; margin:0 0 2px; color:#666; font-size:12px; font-weight:900; }
.axis-labels span:nth-child(2) { color:var(--bad); padding:0 8px; }
.axis-labels span:last-child { text-align:right; }
.target-readout { min-height:150px; border:1px solid rgba(42,131,183,.22); border-radius:12px; background:var(--soft); display:grid; grid-template-columns:minmax(0,1fr) minmax(86px,.62fr); align-items:center; gap:12px; padding:12px; text-align:center; }
.target-readout.linear-mode { grid-template-columns:1fr; }
.target-readout.linear-mode .target-cell.secondary { display:none; }
.target-cell { display:grid; gap:5px; min-width:0; }
.target-cell:first-child { transform:translateY(.5em); }
.target-cell:last-child { transform:translateY(.5em); }
.target-cell > div { display:inline-grid; grid-template-columns:17.4ch auto; justify-content:center; align-items:baseline; white-space:nowrap; }
.target-label { color:#667; font-size:12px; line-height:1; font-weight:900; }
.target-number { display:block; width:100%; color:var(--theme-deep); font-size:clamp(41px,5.1vw,68px); line-height:.92; font-weight:900; font-variant-numeric:tabular-nums; text-align:right; }
.target-unit { display:inline-block; color:#667; font-size:15px; font-weight:900; margin-left:6px; white-space:nowrap; }
.target-angle { display:inline-block; transform:translateY(-2em); color:var(--theme-deep); font-size:clamp(15px,1.5vw,21px); line-height:.95; font-weight:900; font-variant-numeric:tabular-nums; }
.vertical-sliders { display:grid; grid-template-columns:1fr 1fr; gap:8px; min-height:210px; align-self:start; margin-top:136px; }
.vertical-slider { display:grid; grid-template-rows:auto 1fr auto; gap:6px; justify-items:center; min-width:0; padding:8px 5px; border:1px solid rgba(166,166,166,.30); border-radius:10px; background:#fff; }
.vertical-slider label { color:#666; font-size:11px; line-height:1.1; font-weight:900; text-align:center; }
.vertical-slider input[type=range] { appearance:none; -webkit-appearance:none; width:48px; height:146px; writing-mode:vertical-lr; direction:rtl; accent-color:var(--theme-blue); background:transparent; cursor:pointer; touch-action:pan-y; }
.vertical-slider input[type=range]::-webkit-slider-runnable-track { width:11px; margin:0 auto; border-radius:999px; border:1px solid rgba(42,131,183,.28); background:#e7f0f6; box-shadow:inset 0 1px 2px rgba(0,0,0,.10); }
.vertical-slider input[type=range]::-webkit-slider-thumb { -webkit-appearance:none; width:34px; height:34px; margin-left:-11.5px; border-radius:50%; background:var(--theme-blue); border:4px solid #fff; box-shadow:0 5px 14px rgba(0,0,0,.28); }
.vertical-slider input[type=range]::-moz-range-track { width:11px; border-radius:999px; border:1px solid rgba(42,131,183,.28); background:#e7f0f6; box-shadow:inset 0 1px 2px rgba(0,0,0,.10); }
.vertical-slider input[type=range]::-moz-range-thumb { width:28px; height:28px; border-radius:50%; background:var(--theme-blue); border:4px solid #fff; box-shadow:0 5px 14px rgba(0,0,0,.28); }
.vertical-slider span { color:var(--theme-deep); font-size:12px; font-weight:900; text-align:center; overflow-wrap:anywhere; }
.gear-panel { display:grid; gap:10px; }
.gear-meta { display:grid; grid-template-columns:1fr 1fr; gap:8px; }
.gear-field { border:1px solid rgba(166,166,166,.30); border-radius:10px; background:#fff; padding:8px; display:grid; gap:6px; }
.gear-field .label { margin:0; color:var(--ink); font-size:20px; line-height:1; font-weight:900; letter-spacing:0; }
.gear-field select { min-height:34px; }
.gear-name { color:var(--theme-deep); font-size:20px; line-height:1; font-weight:900; }
.gear-ratio { display:grid; gap:8px; padding-top:2px; }
.gear-ratio .vertical-sliders { margin-top:0; min-height:176px; grid-template-columns:1fr 1fr; }
.gear-ratio .vertical-slider.gear-slider { display:flex; align-items:center; justify-content:center; padding:6px; min-height:186px; }
.gear-wheel { width:min(100%,240px); height:176px; position:relative; overflow:hidden; border-radius:12px; border:1px solid rgba(120,132,146,.45); background:linear-gradient(180deg,#aab2bc 0%,#d7dde4 12%,#f3f5f8 28%,#ffffff 50%,#f3f5f8 72%,#d7dde4 88%,#aab2bc 100%); box-shadow:inset 0 1px 0 rgba(255,255,255,.85); }
.gear-wheel::before { content:""; position:absolute; left:0; right:0; top:50%; height:46px; transform:translateY(-50%); background:linear-gradient(180deg,rgba(255,255,255,.80) 0%,rgba(226,232,238,.86) 50%,rgba(255,255,255,.80) 100%); border-top:1px solid rgba(130,142,156,.45); border-bottom:1px solid rgba(130,142,156,.45); z-index:1; }
.gear-wheel::after { content:""; position:absolute; left:0; right:0; top:0; bottom:0; background:linear-gradient(180deg,rgba(32,40,50,.28) 0%,rgba(255,255,255,0) 22%,rgba(255,255,255,0) 78%,rgba(32,40,50,.28) 100%); pointer-events:none; z-index:1; }
.gear-wheel { touch-action:none; cursor:ns-resize; }
.gear-wheel-values { position:absolute; inset:0; display:grid; grid-template-rows:repeat(5,1fr); align-items:center; justify-items:center; z-index:2; }
.gear-ratio-value { color:#1b2430; font-weight:900; font-variant-numeric:tabular-nums; text-align:center; user-select:none; line-height:1; }
.gear-ratio-value.current { font-size:clamp(38px,4.8vw,64px); font-weight:900; }
.gear-ratio-value.edge { font-size:clamp(24px,3.1vw,34px); opacity:.88; font-weight:900; }
.gear-ratio-value.far { font-size:clamp(18px,2.4vw,26px); opacity:.60; font-weight:900; }
.gear-panel.locked { background:#f8fafc; }
.gear-panel.locked .gear-field select { background:#ecf0f4; color:#7b8692; border-color:rgba(123,134,146,.45); }
.gear-ratio.locked { pointer-events:none; opacity:.72; }
.meta { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:6px; margin:4px 0 7px; color:#666; font-size:11px; }
.meta span { background:var(--soft); border-radius:8px; padding:5px 6px; min-height:24px; }
.point-table { display:grid; gap:10px; }
.point-row { display:grid; grid-template-columns:44px 1fr auto; gap:8px; align-items:center; padding:8px; border:1px solid rgba(166,166,166,.30); border-radius:10px; background:#fff; }
.point-row strong { font-size:17px; }
.point-actions { display:flex; gap:8px; justify-content:flex-end; }
.point-actions button { min-height:34px; padding:7px 12px; font-size:12px; }
.hidden { display:none !important; }
.modal-shell { position:fixed; inset:0; display:none; place-items:center; padding:18px; background:rgba(17,24,39,.36); z-index:50; }
.modal-shell.open { display:grid; }
.modal-card { width:min(520px,100%); border:1px solid var(--line); border-radius:12px; background:#fff; box-shadow:0 18px 46px rgba(17,24,39,.22); padding:14px; display:grid; gap:12px; }
.modal-title { margin:0; color:var(--theme-deep); font-size:18px; line-height:1.1; font-weight:900; }
.modal-grid { display:grid; gap:10px; }
.modal-mode-row { margin:0; min-height:58px; }
.modal-ratio-row { display:grid; grid-template-columns:auto minmax(88px,1fr) minmax(78px,.9fr); gap:8px; align-items:center; }
.modal-ratio-row.motor { grid-template-columns:auto minmax(88px,1fr) auto; }
.modal-inline-label { color:var(--ink); font-size:14px; font-weight:900; white-space:nowrap; }
.modal-inline-unit { color:#4c5966; font-size:14px; line-height:1; font-weight:900; white-space:nowrap; }
.modal-actions { display:grid; grid-template-columns:1fr 1fr; gap:8px; }
@media (max-width:980px) { main { padding:7px 9px 9px; } .monitor-grid { grid-template-columns:minmax(245px,1fr) minmax(190px,.68fr) minmax(245px,.95fr); gap:8px; } .card { padding:8px; } .brand-wordmark { font-size:22px; } .protocol-chip { font-size:24px; } .logo { width:40px; height:40px; } .axis-card { grid-template-columns:128px minmax(0,1fr); gap:8px; } .dial { width:128px; height:128px; } .hand { height:48px; margin-top:-48px; } .big-angle { font-size:38px; } .tile { min-height:44px; padding:5px 7px; } .value { font-size:15px; } .slider-number { font-size:13px; } .meta { font-size:10px; } .param-grid { grid-template-columns:repeat(2,minmax(0,1fr)); } .position-param { --side-width:108px; } .axis-control { min-height:324px; } .vertical-sliders { min-height:188px; } .vertical-slider input[type=range] { height:122px; } .target-number { font-size:41px; } .target-angle { font-size:15px; } }
@media (max-width:1180px) { .feedback-card.encoder { grid-template-columns:1fr; } .encoder-title { grid-column:1; grid-row:auto; justify-self:center; align-self:auto; margin-bottom:0; } .encoder-dial { grid-column:1; grid-row:auto; } .feedback-card.encoder .feedback-metrics { grid-column:1; grid-row:auto; } }
@media (max-width:720px) { .monitor-grid { grid-template-columns:1fr; overflow:hidden; } .right-stack { display:none; } .middle-stack { grid-template-columns:1fr 1fr; } .axis-card { grid-template-columns:140px 1fr; } .status { grid-template-columns:repeat(3,1fr); } .subbar { flex-wrap:wrap; } .tabs { flex:1 0 100%; order:2; } .brand-wordmark { font-size:20px; } .protocol-chip { font-size:20px; } .logo { width:36px; height:36px; } h1 { font-size:22px; } }
</style>
</head>
<body>
<main>
<header class="topbar">
  <div class="topbar-left">
    <h1 id="pageTitle">多轴控制</h1>
  </div>
  <div class="topbar-right">
    <span class="brand-wordmark">mctivity</span>
    <img class="logo" src="https://iiru.cn/wp-content/uploads/2023/09/thinking-blue-transparent.png" alt="mctivity logo" referrerpolicy="no-referrer" />
  </div>
</header>
<div class="subbar">
  <span id="protocolChip" class="protocol-chip">EtherCAT</span>
  <nav class="tabs">
    <button id="tabMonitorBtn" class="tab-btn active" onclick="showTab('monitor')">轴 A</button>
    <button id="tabConfigBtn" class="tab-btn" onclick="showTab('config')">轴 B</button>
  </nav>
  <div class="lang-menu">
    <button id="langToggleBtn" class="lang-btn" type="button" aria-haspopup="menu" aria-expanded="false" aria-label="Language">🌐</button>
    <div id="langDropdown" class="lang-dropdown" role="menu" aria-label="Language">
      <button id="langZhBtn" class="lang-option" type="button" role="menuitem" onclick="setLanguage('zh')">中文</button>
      <button id="langEnBtn" class="lang-option" type="button" role="menuitem" onclick="setLanguage('en')">English</button>
    </div>
  </div>
</div>
<section id="tabMonitor" class="tab-panel active">
  <div class="monitor-grid">
    <div class="left-stack">
      <section class="card">
        <h2>电机反馈</h2>
        <div class="feedback-grid">
          <div class="feedback-card encoder">
            <div class="feedback-title encoder-title">位置</div>
            <div class="feedback-dial encoder-dial">
              <div class="north">0</div><div class="east">90</div><div class="south">180</div><div class="west">270</div>
              <div id="encoderHand" class="feedback-hand"></div><div class="feedback-hub"></div>
            </div>
            <div class="feedback-metrics">
              <div class="feedback-metric"><span class="label">当前圈数</span><span id="encoderTurns" class="value">--</span></div>
              <div class="feedback-metric"><span class="label">当前编码器角度</span><span id="encoderAngle" class="value">--</span></div>
              <div class="feedback-metric vertical"><span class="label">编码器脉冲数</span><span id="encoderPulses" class="value">--</span></div>
              <div class="feedback-metric"><span class="label">单圈计数</span><span id="encoderSingleTurn" class="value">--</span></div>
            </div>
          </div>
          <div class="feedback-card">
            <div class="feedback-title">扭矩</div>
            <div class="feedback-dial small dashboard">
              <span class="feedback-tick tick-0">0</span><span class="feedback-tick tick-20">20</span><span class="feedback-tick tick-40">40</span><span class="feedback-tick tick-60">60</span><span class="feedback-tick tick-80">80</span><span class="feedback-tick tick-100">100</span>
              <div id="torqueHand" class="feedback-hand"></div><div class="feedback-hub"></div>
              <div class="feedback-value"><span id="torqueGaugeValue" class="feedback-number">0.0</span><span class="feedback-unit">%</span></div>
            </div>
          </div>
          <div class="feedback-card">
            <div class="feedback-title">速度</div>
            <div class="feedback-dial small dashboard">
              <span class="feedback-tick tick-k1">1</span><span class="feedback-tick tick-k2">2</span><span class="feedback-tick tick-k3">3</span>
              <div id="speedHand" class="feedback-hand"></div><div class="feedback-hub"></div>
              <div class="feedback-value"><span id="speedGaugeValue" class="feedback-number">0.0</span><span class="feedback-unit">krpm</span></div>
            </div>
          </div>
        </div>
      </section>
    </div>
    <div class="middle-stack">
      <section class="card">
        <h2>控制</h2>
        <div class="mode-row">
          <span class="label">模式</span>
          <select id="modeSelect" onchange="setMode()">
            <option value="position">单点定位</option>
            <option value="jog">点动</option>
            <option value="point">点位表</option>
            <option value="homing">回零/置零</option>
            <option value="velocity">速度控制</option>
            <option value="torque">转矩控制</option>
            <option value="gear_cam">电子齿轮</option>
          </select>
        </div>
        <div class="mode-row">
          <span class="label-stack"><span id="transmissionLabel" class="label">传动</span><span id="transmissionTypeLabel" class="label-subtext">旋转</span></span>
          <button class="transmission-trigger" onclick="openTransmissionDialog()">
            <span class="transmission-copy">
              <span id="transmissionLoadSummary" class="transmission-primary">负载 360.0 deg</span>
              <span id="transmissionMotorSummary" class="transmission-secondary">电机 1 Rev</span>
            </span>
            <span class="transmission-chevron">›</span>
          </button>
        </div>
        <div class="controls">
          <button id="enableToggle" class="enable-toggle power-toggle" onclick="toggleEnable()"><span class="enable-copy"><span class="toggle-title">使能</span><span id="enableToggleText" class="toggle-subtitle">OFF</span></span><span class="toggle-track"><span class="toggle-knob"></span></span></button>
          <button id="motionIndicator" class="enable-toggle motion-toggle" onclick="startSinglePointMotion()"><span class="enable-copy"><span class="toggle-title">启停</span><span id="motionIndicatorText" class="toggle-subtitle">STANDSTILL</span></span><span class="toggle-track"><span class="toggle-knob"></span></span></button>
          <button id="faultIndicator" class="enable-toggle fault-toggle" onclick="resetFault()"><span class="enable-copy"><span class="toggle-title">状态</span><span id="faultIndicatorText" class="toggle-subtitle">READY</span></span><span class="fault-detail"><span id="faultCodeText" class="fault-code">0x0000</span><span id="faultNameText" class="fault-name">正常</span></span><span id="faultIndicatorButton" class="status-button" aria-label="READY"></span></button>
          <button class="stop" onclick="returnZero()">一键回到零位</button>
          <button class="blue" onclick="cmd('set_zero')">当前位置置零</button>
        </div>
      </section>
    </div>
    <div class="right-stack">
      <section class="card active-mode-card">
        <h2 id="modePanelTitle">定位参数</h2>
        <div id="panel-position" class="mode-panel">
          <div class="slider-card position-param">
            <div class="axis-control">
              <div class="slider-head"><span class="slider-title">目标绝对位置</span></div>
              <div class="position-axis">
                <div class="axis-labels"><span id="axisMinRev">-200 rev</span><span></span><span id="axisMaxRev">+200 rev</span></div>
                <div id="currentPositionMarker" class="current-position-marker"></div>
                <input id="absPos" type="range" min="-1677721600" max="1677721600" step="1024" value="0" oninput="updateSliders()">
              </div>
              <div class="target-readout">
                <div class="target-cell"><div><span id="targetRevBig" class="target-number">0</span><span class="target-unit">rev</span></div></div>
                <div class="target-cell secondary"><span id="targetAngleBig" class="target-angle">0.0 deg</span></div>
              </div>
              <button class="blue" onclick="moveAbs()">绝对移动</button>
            </div>
            <div class="vertical-sliders">
              <div class="vertical-slider">
                <label for="absSpeedRpm">速度</label>
                <input id="absSpeedRpm" type="range" min="1" max="3000" step="1" value="120" oninput="updateSliders()">
                <span id="absSpeedText">120 rpm</span>
              </div>
              <div class="vertical-slider">
                <label for="absAccel">加速度</label>
                <input id="absAccel" type="range" min="10" max="3000" step="10" value="300" oninput="updateSliders()">
                <span id="absAccelText">300 rpm/s</span>
              </div>
            </div>
          </div>
        </div>
        <div id="panel-jog" class="mode-panel">
          <div class="slider-card">
            <div class="slider-head"><span class="slider-title">相对位移</span><span id="relText" class="slider-number">--</span></div>
            <input id="relDelta" type="range" min="-8388608" max="8388608" step="1024" value="4194304" oninput="updateSliders()">
            <div class="meta"><span id="relRev">--</span><span id="relDeg">--</span><span id="relRpm">--</span></div>
            <button class="blue" onclick="moveRel()">相对移动</button>
          </div>
          <div class="slider-card">
            <div class="slider-head"><span class="slider-title">运动时间</span><span id="msText" class="slider-number">--</span></div>
            <input id="moveMs" type="range" min="500" max="15000" step="100" value="3000" oninput="updateSliders()">
            <div class="meta"><span>500 ms</span><span>越大越慢</span><span>15000 ms</span></div>
          </div>
        </div>
        <div id="panel-point" class="mode-panel">
          <div class="point-table">
            <div class="point-row"><strong>P1</strong><span id="p1Quick">0 cnt</span><div class="point-actions"><button class="blue" onclick="gotoPoint(1)">移动</button></div></div>
            <div class="point-row"><strong>P2</strong><span id="p2Quick">4194304 cnt</span><div class="point-actions"><button class="blue" onclick="gotoPoint(2)">移动</button></div></div>
            <div class="point-row"><strong>P3</strong><span id="p3Quick">8388608 cnt</span><div class="point-actions"><button class="blue" onclick="gotoPoint(3)">移动</button></div></div>
          </div>
        </div>
        <div id="panel-homing" class="mode-panel">
          <div class="slider-card">
            <div class="slider-head"><span class="slider-title">回零/置零</span><span class="slider-number">软件零点</span></div>
            <div class="control-note">将当前位置记录为软件零点，当前位置显示会归零。</div>
            <button class="blue" onclick="cmd('set_zero')">当前位置置零</button>
          </div>
        </div>
        <div id="panel-velocity" class="mode-panel">
          <div class="slider-card">
            <div class="slider-head"><span class="slider-title">速度点动</span><span id="velText" class="slider-number">--</span></div>
            <input id="velCps" type="range" min="10000" max="1200000" step="10000" value="200000" oninput="updateSliders()">
            <div class="control-row three">
              <button class="blue" onclick="jogVelocity(-Number(velCps.value))">反转</button>
              <button class="stop" onclick="stopMotion()">停止</button>
              <button class="blue" onclick="jogVelocity(Number(velCps.value))">正转</button>
            </div>
          </div>
        </div>
        <div id="panel-torque" class="mode-panel">
          <div class="slider-card">
            <div class="slider-head"><span class="slider-title">转矩指令</span><span id="torqueText" class="slider-number">--</span></div>
            <input id="torqueCmd" type="range" min="-100" max="100" step="1" value="0" oninput="updateSliders()">
            <div class="control-note">当前仅暂存指令，未切换到 CST 转矩 PDO 输出。</div>
            <button class="neutral" onclick="sendTorque()">写入暂存</button>
          </div>
        </div>
        <div id="panel-gear_cam" class="mode-panel">
          <div id="gearPanelCard" class="slider-card gear-panel">
            <div class="gear-meta">
              <div class="gear-field">
                <span class="label">主轴</span>
                <select id="gearMasterSelect" onchange="updateGearMaster()">
                  <option value="mctivity">Axis A</option>
                  <option value="fv3">Axis B</option>
                  <option value="virtual">虚拟主轴</option>
                </select>
              </div>
              <div class="gear-field">
                <span class="label">从轴</span>
                <span id="gearSlaveName" class="gear-name">--</span>
              </div>
            </div>
            <div id="gearRatioBlock" class="gear-ratio">
              <div class="vertical-sliders">
                <div class="vertical-slider gear-slider master">
                  <div class="gear-wheel" onwheel="onGearWheel(event, 'master')" onclick="onGearTap(event, 'master')" onpointerdown="startGearDrag(event, 'master')" onpointermove="moveGearDrag(event)" onpointerup="endGearDrag()" onpointercancel="endGearDrag()">
                    <div class="gear-wheel-values">
                      <span id="gearMasterPrev2" class="gear-ratio-value far">0</span>
                      <span id="gearMasterPrev" class="gear-ratio-value edge">0</span>
                      <span id="gearMasterRatioText" class="gear-ratio-value current">1</span>
                      <span id="gearMasterNext" class="gear-ratio-value edge">2</span>
                      <span id="gearMasterNext2" class="gear-ratio-value far">3</span>
                    </div>
                  </div>
                  <input id="gearMasterRatio" type="hidden" value="1">
                </div>
                <div class="vertical-slider gear-slider slave">
                  <div class="gear-wheel" onwheel="onGearWheel(event, 'slave')" onclick="onGearTap(event, 'slave')" onpointerdown="startGearDrag(event, 'slave')" onpointermove="moveGearDrag(event)" onpointerup="endGearDrag()" onpointercancel="endGearDrag()">
                    <div class="gear-wheel-values">
                      <span id="gearSlavePrev2" class="gear-ratio-value far">0</span>
                      <span id="gearSlavePrev" class="gear-ratio-value edge">0</span>
                      <span id="gearSlaveRatioText" class="gear-ratio-value current">1</span>
                      <span id="gearSlaveNext" class="gear-ratio-value edge">2</span>
                      <span id="gearSlaveNext2" class="gear-ratio-value far">3</span>
                    </div>
                  </div>
                  <input id="gearSlaveRatio" type="hidden" value="1">
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>
    </div>
  </div>
</section>
<section id="tabConfig" class="tab-panel">
  <div class="config-grid">
    <section class="card">
      <h2>单点定位</h2>
      <div class="param-grid">
        <div class="param-card">
          <h3>位置/点动</h3>
          <label>默认相对位移<input id="cfgRel" type="number" value="4194304" step="1024" onchange="applyConfig()"></label>
          <label>默认绝对位置<input id="cfgAbs" type="number" value="0" step="1024" onchange="applyConfig()"></label>
          <label>默认运动时间 ms<input id="cfgMs" type="number" value="3000" min="500" max="15000" step="100" onchange="applyConfig()"></label>
        </div>
        <div class="param-card">
          <h3>速度/转矩</h3>
          <label>默认速度 cnt/s<input id="cfgVel" type="number" value="200000" min="10000" step="10000" onchange="applyConfig()"></label>
          <label>转矩上限 %<input id="cfgTorqueLimit" type="number" value="100" min="1" max="100" step="1" onchange="applyConfig()"></label>
        </div>
        <div class="param-card">
          <h3>电子齿轮</h3>
          <label>齿轮比分子<input id="cfgGearNum" type="number" value="1" min="1" step="1"></label>
          <label>齿轮比分母<input id="cfgGearDen" type="number" value="1" min="1" step="1"></label>
        </div>
        <div class="param-card">
          <h3>电子凸轮</h3>
          <label>凸轮表编号<input id="cfgCam" type="number" value="1" min="1" step="1"></label>
          <label>同步周期 ms<input id="cfgSyncMs" type="number" value="1" min="1" step="1"></label>
        </div>
      </div>
    </section>
    <section class="card">
      <h2>点表配置</h2>
      <div class="point-table">
        <div class="point-row"><strong>P1</strong><span id="p1">0 cnt</span><div class="point-actions"><button class="neutral" onclick="savePoint(1)">记录</button></div></div>
        <div class="point-row"><strong>P2</strong><span id="p2">4194304 cnt</span><div class="point-actions"><button class="neutral" onclick="savePoint(2)">记录</button></div></div>
        <div class="point-row"><strong>P3</strong><span id="p3">8388608 cnt</span><div class="point-actions"><button class="neutral" onclick="savePoint(3)">记录</button></div></div>
      </div>
    </section>
  </div>
</section>
</main>
<div id="transmissionModal" class="modal-shell" onclick="maybeCloseTransmissionDialog(event)">
  <div class="modal-card" onclick="event.stopPropagation()">
    <h3 id="transmissionModalTitle" class="modal-title">传动设定</h3>
    <div class="modal-grid">
      <div class="mode-row modal-mode-row">
        <span id="transmissionTypeFieldLabel" class="label">运动类型</span>
        <select id="transmissionType" onchange="onTransmissionTypeChange()">
          <option value="rotary">旋转</option>
          <option value="linear">直线</option>
        </select>
      </div>
      <div class="mode-row modal-mode-row">
        <span id="transmissionTravelModeLabel" class="label">运动方式</span>
        <select id="transmissionTravelMode" onchange="onTransmissionTravelModeChange()">
          <option value="periodic">周期</option>
          <option value="reciprocating">往返</option>
        </select>
      </div>
      <div class="modal-ratio-row">
        <span id="transmissionAmountLabel" class="modal-inline-label">负载运动</span>
        <input id="transmissionAmount" type="number" min="0.001" step="0.001" value="360" oninput="updateTransmissionDraft()">
        <select id="transmissionUnit" onchange="updateTransmissionDraft()"></select>
      </div>
      <div class="modal-ratio-row motor">
        <span id="transmissionRevsLabel" class="modal-inline-label">电机旋转</span>
        <input id="transmissionRevs" type="number" min="0.001" step="0.001" value="1" oninput="updateTransmissionDraft()">
        <span class="modal-inline-unit">Rev</span>
      </div>
      <div id="transmissionPeriodRow" class="modal-ratio-row">
        <span id="transmissionPeriodLabel" class="modal-inline-label">周期</span>
        <input id="transmissionPeriod" type="number" min="0.001" step="0.001" value="360" oninput="updateTransmissionDraft()">
        <span id="transmissionPeriodUnit" class="modal-inline-unit">deg</span>
      </div>
      <div id="transmissionForwardRow" class="modal-ratio-row hidden">
        <span id="transmissionForwardLabel" class="modal-inline-label">正向极限</span>
        <input id="transmissionForwardLimit" type="number" step="0.001" value="360" oninput="updateTransmissionDraft()">
        <span id="transmissionForwardUnit" class="modal-inline-unit">deg</span>
      </div>
      <div id="transmissionReverseRow" class="modal-ratio-row hidden">
        <span id="transmissionReverseLabel" class="modal-inline-label">反向极限</span>
        <input id="transmissionReverseLimit" type="number" step="0.001" value="-360" oninput="updateTransmissionDraft()">
        <span id="transmissionReverseUnit" class="modal-inline-unit">deg</span>
      </div>
    </div>
    <div class="modal-actions">
      <button id="transmissionCancelBtn" class="neutral" onclick="closeTransmissionDialog()">取消</button>
      <button id="transmissionSaveBtn" class="blue" onclick="saveTransmissionDialog()">保存</button>
    </div>
  </div>
</div>
<script>
const REV = 8388608;
const AXIS_DIR = -1;
const LANG_KEY = 'mctivity_lang';
const MODE_LABELS = {
  zh: {position:'单点定位', jog:'点动', point:'点位表', homing:'回零/置零', velocity:'速度控制', torque:'转矩控制', gear_cam:'电子齿轮'},
  en: {position:'Point Positioning', jog:'Jog', point:'Point Table', homing:'Zeroing', velocity:'Velocity Control', torque:'Torque Control', gear_cam:'Electronic Gearing'}
};
const UI_TEXT = {
  zh: {
    pageTitle:'多轴控制',
    axisA:'轴 A',
    axisB:'轴 B',
    virtualAxis:'虚拟主轴',
    motorFeedback:'电机反馈',
    position:'位置',
    torque:'扭矩',
    speed:'速度',
    control:'控制',
    mode:'模式',
    enable:'使能',
    startStop:'启停',
    status:'状态',
    returnZero:'一键回到零位',
    setZero:'当前位置置零',
    targetAbs:'目标绝对位置',
    accel:'加速度',
    relMove:'相对位移',
    moveTime:'运动时间',
    slowerHint:'数值越大越慢',
    move:'移动',
    zeroPanel:'回零/置零',
    softwareZero:'软件零点',
    zeroNote:'将当前位置记录为软件零点，当前位置显示会归零。',
    velocityJog:'速度点动',
    reverse:'反转',
    stop:'停止',
    forward:'正转',
    torqueCmd:'转矩指令',
    torqueNote:'当前仅暂存指令，未切换到 CST 转矩 PDO 输出。',
    stageWrite:'写入暂存',
    gearMaster:'主轴',
    gearSlave:'从轴',
    pointPositioning:'单点定位',
    pointConfig:'点表配置',
    posJog:'位置/点动',
    speedTorque:'速度/转矩',
    gear:'电子齿轮',
    cam:'电子凸轮',
    defaultRel:'默认相对位移',
    defaultAbs:'默认绝对位置',
    defaultMoveMs:'默认运动时间',
    defaultVel:'默认速度',
    torqueLimit:'转矩上限',
    gearNum:'齿轮比分子',
    gearDen:'齿轮比分母',
    camTable:'凸轮表编号',
    syncPeriod:'同步周期',
    record:'记录',
    currentTurns:'当前圈数',
    currentAngle:'当前编码器角度',
    currentPulses:'编码器脉冲数',
    singleTurn:'单圈计数',
    posParams:'定位参数',
    gearParams:'电子齿轮参数',
    controlSuffix:'控制',
    off:'OFF',
    on:'ON',
    ready:'READY',
    fault:'FAULT',
    reset:'复位',
    standstill:'静止',
    inMotion:'运动中',
    gearing:'齿轮联动',
    moving:'运动',
    idle:'空闲',
    ok:'正常',
    faultServo:'伺服故障',
    faultCode:'错误码',
    gearLockedPrefix:'无法选择电子齿轮模式，因为',
    gearLockedSuffix:'已经将此轴设为主轴',
    gearUnavailable:'电子齿轮（不可用）',
    transmissionLabel:'传动',
    transmissionModalTitle:'传动设定',
    transmissionTypeFieldLabel:'运动类型',
    transmissionTravelModeLabel:'运动方式',
    transmissionAmountLabel:'负载运动',
    transmissionRevsLabel:'电机旋转',
    transmissionPeriodLabel:'周期',
    transmissionForwardLabel:'正向极限',
    transmissionReverseLabel:'反向极限',
    transmissionCancelBtn:'取消',
    transmissionSaveBtn:'保存',
    rotaryType:'旋转',
    linearType:'直线',
    periodicTravel:'周期',
    reciprocatingTravel:'往返',
    loadPrefix:'负载 ',
    motorPrefix:'电机 ',
    revUnit:'rev'
  },
  en: {
    pageTitle:'Multi-axis Control',
    axisA:'Axis A',
    axisB:'Axis B',
    virtualAxis:'Virtual Master',
    motorFeedback:'Motor Feedback',
    position:'Position',
    torque:'Torque',
    speed:'Speed',
    control:'Control',
    mode:'Mode',
    enable:'Enable',
    startStop:'Start/Stop',
    status:'Status',
    returnZero:'Return To Zero',
    setZero:'Set Current As Zero',
    targetAbs:'Absolute Target',
    accel:'Acceleration',
    relMove:'Relative Displacement',
    moveTime:'Move Time',
    slowerHint:'Higher values move slower',
    move:'Move',
    zeroPanel:'Zeroing',
    softwareZero:'Software Zero',
    zeroNote:'Store the current position as the software zero and reset the displayed position.',
    velocityJog:'Velocity Jog',
    reverse:'Reverse',
    stop:'Stop',
    forward:'Forward',
    torqueCmd:'Torque Command',
    torqueNote:'The current command is staged only and CST torque PDO output is not active.',
    stageWrite:'Stage Command',
    gearMaster:'Master Axis',
    gearSlave:'Slave Axis',
    pointPositioning:'Point Positioning',
    pointConfig:'Point Table',
    posJog:'Position / Jog',
    speedTorque:'Speed / Torque',
    gear:'Electronic Gearing',
    cam:'Electronic Cam',
    defaultRel:'Default Relative Move',
    defaultAbs:'Default Absolute Target',
    defaultMoveMs:'Default Move Time',
    defaultVel:'Default Speed',
    torqueLimit:'Torque Limit',
    gearNum:'Gear Numerator',
    gearDen:'Gear Denominator',
    camTable:'Cam Table ID',
    syncPeriod:'Sync Period',
    record:'Record',
    currentTurns:'Turns',
    currentAngle:'Encoder Angle',
    currentPulses:'Encoder Pulses',
    singleTurn:'Single-turn Count',
    posParams:'Position Parameters',
    gearParams:'Gear Parameters',
    controlSuffix:'Control',
    off:'OFF',
    on:'ON',
    ready:'READY',
    fault:'FAULT',
    reset:'Reset',
    standstill:'Standstill',
    inMotion:'In Motion',
    gearing:'Gearing',
    moving:'Moving',
    idle:'Idle',
    ok:'OK',
    faultServo:'Servo Fault',
    faultCode:'Error Code',
    gearLockedPrefix:'Electronic gearing is unavailable because ',
    gearLockedSuffix:' is already using this axis as its master',
    gearUnavailable:'Electronic Gearing (Locked)',
    transmissionLabel:'Transmission',
    transmissionModalTitle:'Transmission Setup',
    transmissionTypeFieldLabel:'Motion Type',
    transmissionTravelModeLabel:'Travel Type',
    transmissionAmountLabel:'Load Travel',
    transmissionRevsLabel:'Motor Rotation',
    transmissionPeriodLabel:'Period',
    transmissionForwardLabel:'Forward Limit',
    transmissionReverseLabel:'Reverse Limit',
    transmissionCancelBtn:'Cancel',
    transmissionSaveBtn:'Save',
    rotaryType:'ROTARY',
    linearType:'LINEAR',
    periodicTravel:'Periodic',
    reciprocatingTravel:'Reciprocating',
    loadPrefix:'Load ',
    motorPrefix:'Motor ',
    revUnit:'rev'
  }
};
const transmissionUnitSets = {
  rotary: [
    {value:'deg', label:'deg'},
    {value:'rad', label:'rad'}
  ],
  linear: [
    {value:'mm', label:'mm'},
    {value:'cm', label:'cm'},
    {value:'m', label:'m'}
  ]
};
let activeDevice = 'mctivity';
let currentLang = localStorage.getItem(LANG_KEY) === 'en' ? 'en' : 'zh';
const statusByDevice = {mctivity:null, fv3:null};
const feedbackByDevice = {mctivity:null, fv3:null};
const motionStateByDevice = {
  mctivity: {latch:false, seenMoving:false, commandAt:0, commandSeq:0, stopRequested:false, gearEngaged:false, gearStoppedLatched:false, movingOffCandidateAt:0, enableVisual:false, enableOffCandidateAt:0},
  fv3: {latch:false, seenMoving:false, commandAt:0, commandSeq:0, stopRequested:false, gearEngaged:false, gearStoppedLatched:false, movingOffCandidateAt:0, enableVisual:false, enableOffCandidateAt:0}
};
const deviceProfiles = {
  mctivity: {mode:'position', absPos:0, absSpeedRpm:120, absAccel:300, relDelta:4194304, moveMs:3000, velCps:200000, torqueCmd:0, gearMaster:'fv3', gearMasterRatio:1, gearSlaveRatio:1, transmission:{type:'rotary', revs:1, amount:360, unit:'deg', travelMode:'periodic', period:360, forwardLimit:360, reverseLimit:-360}, points:{1:0, 2:REV/2, 3:REV}},
  fv3: {mode:'position', absPos:0, absSpeedRpm:120, absAccel:300, relDelta:4194304, moveMs:3000, velCps:200000, torqueCmd:0, gearMaster:'mctivity', gearMasterRatio:1, gearSlaveRatio:1, transmission:{type:'rotary', revs:1, amount:360, unit:'deg', travelMode:'periodic', period:360, forwardLimit:360, reverseLimit:-360}, points:{1:0, 2:REV/2, 3:REV}}
};
let uiStateSaveTimer = 0;
const lastUiStateSnapshot = {mctivity:'', fv3:''};
let transmissionDraft = null;
const modeUiStateByDevice = {
  mctivity: {pending:null, interacting:false},
  fv3: {pending:null, interacting:false}
};
const modePanelStateByDevice = {
  mctivity: null,
  fv3: null
};
function currentModeUi(device = activeDevice) {
  return modeUiStateByDevice[device];
}
function syncModeSelectDisabled(device = activeDevice) {
  if (!modeSelect || device !== activeDevice) return;
  modeSelect.disabled = false;
}
function axisDisplayName(device) {
  const text = UI_TEXT[currentLang];
  if (device === 'fv3') return text.axisB;
  if (device === 'mctivity') return text.axisA;
  return text.virtualAxis;
}
function axisDevices() {
  return Object.keys(deviceProfiles);
}
function preferredGearMaster(device) {
  return device === 'fv3' ? 'mctivity' : 'fv3';
}
function refreshGearPanel(device = activeDevice) {
  const profile = currentProfile(device);
  if (!profile.gearMaster || profile.gearMaster === device) {
    profile.gearMaster = preferredGearMaster(device);
  }
  if (gearMasterSelect) {
    gearMasterSelect.value = profile.gearMaster;
  }
  setText('gearSlaveName', axisDisplayName(device));
}
function clampGearRatioValue(value) {
  return Math.max(1, Math.min(200, Math.round(Number(value) || 1)));
}
function gearWheelText(value) {
  if (value < 1 || value > 200) return '';
  return fmt(value);
}
function isGearEngaged(device = activeDevice) {
  const motion = currentMotion(device);
  const s = currentStatus(device);
  const fromStatus = Boolean(s && s.control_mode === 'gear_cam' && s.gear_running);
  return Boolean((motion && motion.gearEngaged) || fromStatus);
}
function isGearPanelLocked(device = activeDevice) {
  const profile = currentProfile(device);
  const s = currentStatus(device);
  const inGearMode = Boolean((profile && profile.mode === 'gear_cam') || (s && s.control_mode === 'gear_cam'));
  return inGearMode && isGearEngaged(device);
}
function setGearPanelLocked(locked) {
  const panel = document.getElementById('gearPanelCard');
  const ratio = document.getElementById('gearRatioBlock');
  const master = document.getElementById('gearMasterSelect');
  if (panel) panel.classList.toggle('locked', Boolean(locked));
  if (ratio) ratio.classList.toggle('locked', Boolean(locked));
  if (master) master.disabled = Boolean(locked);
}
const gearDragState = {role:null, lastY:0, acc:0, lastT:0, velocity:0, moved:false, suppressTapUntil:0, inertiaRole:null, inertiaAcc:0, inertiaV:0, inertiaTimer:0};
function renderGearWheel(role) {
  const hidden = role === 'master' ? gearMasterRatio : gearSlaveRatio;
  const current = clampGearRatioValue(hidden.value);
  hidden.value = current;
  const prev2 = current - 2;
  const prev = current - 1;
  const next = current + 1;
  const next2 = current + 2;
  if (role === 'master') {
    setText('gearMasterPrev2', gearWheelText(prev2));
    setText('gearMasterPrev', gearWheelText(prev));
    setText('gearMasterRatioText', fmt(current));
    setText('gearMasterNext', gearWheelText(next));
    setText('gearMasterNext2', gearWheelText(next2));
  } else {
    setText('gearSlavePrev2', gearWheelText(prev2));
    setText('gearSlavePrev', gearWheelText(prev));
    setText('gearSlaveRatioText', fmt(current));
    setText('gearSlaveNext', gearWheelText(next));
    setText('gearSlaveNext2', gearWheelText(next2));
  }
}
function stepGearRatio(role, delta) {
  if (isGearPanelLocked()) return;
  const hidden = role === 'master' ? gearMasterRatio : gearSlaveRatio;
  hidden.value = clampGearRatioValue(Number(hidden.value || 1) + Number(delta || 0));
  updateSliders();
}
function onGearWheel(event, role) {
  event.preventDefault();
  if (isGearPanelLocked()) return;
  stepGearRatio(role, event.deltaY < 0 ? 1 : -1);
}
function onGearTap(event, role) {
  if (isGearPanelLocked()) return;
  if (Date.now() < gearDragState.suppressTapUntil) return;
  const rect = event.currentTarget.getBoundingClientRect();
  const midY = rect.top + rect.height / 2;
  stepGearRatio(role, event.clientY < midY ? 1 : -1);
}
function startGearDrag(event, role) {
  if (isGearPanelLocked()) return;
  if (gearDragState.inertiaTimer) {
    cancelAnimationFrame(gearDragState.inertiaTimer);
    gearDragState.inertiaTimer = 0;
  }
  gearDragState.role = role;
  gearDragState.lastY = event.clientY;
  gearDragState.acc = 0;
  gearDragState.lastT = performance.now();
  gearDragState.velocity = 0;
  gearDragState.moved = false;
}
function moveGearDrag(event) {
  const stepPx = 18;
  let dy = 0;
  let dt = 1;
  let now = 0;
  let instantV = 0;
  if (!gearDragState.role) return;
  dy = event.clientY - gearDragState.lastY;
  now = performance.now();
  dt = Math.max(1, now - gearDragState.lastT);
  instantV = dy / dt;
  gearDragState.velocity = gearDragState.velocity * 0.65 + instantV * 0.35;
  gearDragState.lastT = now;
  gearDragState.lastY = event.clientY;
  gearDragState.acc += dy;
  if (Math.abs(dy) > 2) {
    gearDragState.moved = true;
  }
  while (gearDragState.acc >= stepPx) {
    gearDragState.acc -= stepPx;
    stepGearRatio(gearDragState.role, -1);
  }
  while (gearDragState.acc <= -stepPx) {
    gearDragState.acc += stepPx;
    stepGearRatio(gearDragState.role, 1);
  }
}
function runGearInertia() {
  const stepPx = 18;
  if (!gearDragState.inertiaRole) return;
  gearDragState.inertiaV *= 0.92;
  if (Math.abs(gearDragState.inertiaV) < 0.02) {
    gearDragState.inertiaRole = null;
    gearDragState.inertiaTimer = 0;
    return;
  }
  gearDragState.inertiaAcc += gearDragState.inertiaV * 16;
  while (gearDragState.inertiaAcc >= stepPx) {
    gearDragState.inertiaAcc -= stepPx;
    stepGearRatio(gearDragState.inertiaRole, -1);
  }
  while (gearDragState.inertiaAcc <= -stepPx) {
    gearDragState.inertiaAcc += stepPx;
    stepGearRatio(gearDragState.inertiaRole, 1);
  }
  gearDragState.inertiaTimer = requestAnimationFrame(runGearInertia);
}
function endGearDrag() {
  const role = gearDragState.role;
  const v = gearDragState.velocity;
  const moved = gearDragState.moved;
  gearDragState.role = null;
  gearDragState.acc = 0;
  gearDragState.velocity = 0;
  if (moved) {
    gearDragState.suppressTapUntil = Date.now() + 180;
  }
  if (role && Math.abs(v) >= 0.08) {
    gearDragState.inertiaRole = role;
    gearDragState.inertiaAcc = 0;
    gearDragState.inertiaV = v;
    if (gearDragState.inertiaTimer) {
      cancelAnimationFrame(gearDragState.inertiaTimer);
    }
    gearDragState.inertiaTimer = requestAnimationFrame(runGearInertia);
  }
}
async function apiForDevice(device, payload) {
  const requestPayload = Object.assign({}, payload, {device});
  const res = await fetch('/api/command', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(requestPayload)});
  return res.json();
}
function modeOption(value) {
  return Array.from(modeSelect.options || []).find(opt => opt.value === value) || null;
}
function gearMasterHolders(device) {
  return axisDevices().filter(other => other !== device &&
    deviceProfiles[other].mode === 'gear_cam' &&
    deviceProfiles[other].gearMaster === device);
}
function gearLockReason(device) {
  const holders = gearMasterHolders(device);
  if (!holders.length) return '';
  return holders.map(d => axisDisplayName(d)).join(currentLang === 'zh' ? '、' : ', ');
}
function showGearLockedAlert(device) {
  const text = UI_TEXT[currentLang];
  const reason = gearLockReason(device);
  if (!reason) return;
  alert(text.gearLockedPrefix + reason + text.gearLockedSuffix);
}
function isReferencedAsGearMaster(device) {
  return gearMasterHolders(device).length > 0;
}
function applyGearModeAvailability(device = activeDevice) {
  const gearOpt = modeOption('gear_cam');
  const locked = isReferencedAsGearMaster(device);
  if (gearOpt) {
    const modeUi = currentModeUi(device);
    const nextDisabled = Boolean(locked);
    const nextText = locked ? UI_TEXT[currentLang].gearUnavailable : modeLabel('gear_cam');
    if (!(device === activeDevice && modeUi && modeUi.interacting)) {
      if (gearOpt.disabled !== nextDisabled) gearOpt.disabled = nextDisabled;
      if (gearOpt.textContent !== nextText) gearOpt.textContent = nextText;
    }
  }
  return locked;
}
function enforceGearConstraints() {
  const changed = [];
  for (const device of axisDevices()) {
    if (isReferencedAsGearMaster(device) && deviceProfiles[device].mode === 'gear_cam') {
      deviceProfiles[device].mode = 'position';
      motionStateByDevice[device].gearEngaged = false;
      motionStateByDevice[device].gearStoppedLatched = false;
      changed.push(device);
    }
  }
  const activeLocked = applyGearModeAvailability(activeDevice);
  if (activeLocked && modeSelect.value === 'gear_cam') {
    modeSelect.value = 'position';
    syncModePanels('position');
  }
  for (const device of changed) {
    if (device === activeDevice) {
      modeSelect.value = 'position';
      syncModePanels('position');
    }
    apiForDevice(device, {cmd:'set_mode', mode:'position'}).catch(() => {});
  }
}
function currentStatus(device = activeDevice) { return statusByDevice[device]; }
function currentMotion(device = activeDevice) { return motionStateByDevice[device]; }
function currentProfile(device = activeDevice) { return deviceProfiles[device]; }
function normalizedTransmission(profile = currentProfile()) {
  const source = profile && profile.transmission ? profile.transmission : profile;
  const tx = Object.assign({type:'rotary', revs:1, amount:360, unit:'deg', travelMode:'periodic', period:null, forwardLimit:null, reverseLimit:null}, source || {});
  tx.type = tx.type === 'linear' ? 'linear' : 'rotary';
  tx.revs = Math.max(0.001, Number(tx.revs) || 1);
  tx.amount = Math.max(0.001, Number(tx.amount) || (tx.type === 'linear' ? 1 : 360));
  tx.travelMode = tx.travelMode === 'reciprocating' ? 'reciprocating' : 'periodic';
  tx.period = Math.max(0.001, Number(tx.period) || tx.amount);
  tx.forwardLimit = Number.isFinite(Number(tx.forwardLimit)) ? Number(tx.forwardLimit) : tx.amount;
  tx.reverseLimit = Number.isFinite(Number(tx.reverseLimit)) ? Number(tx.reverseLimit) : -tx.amount;
  const options = transmissionUnitSets[tx.type] || transmissionUnitSets.rotary;
  if (!options.some(opt => opt.value === tx.unit)) {
    tx.unit = options[0].value;
  }
  return tx;
}
function transmissionPerRev(profile = currentProfile()) {
  const tx = normalizedTransmission(profile);
  return tx.amount / tx.revs;
}
function transmissionValueFromCounts(counts, profile = currentProfile()) {
  return rev(axisCounts(counts)) * transmissionPerRev(profile);
}
function countsFromTransmissionValue(value, profile = currentProfile()) {
  const tx = normalizedTransmission(profile);
  const motorRev = Number(value) / transmissionPerRev(tx);
  return Math.round((motorRev * REV) / AXIS_DIR);
}
function transmissionBounds(profile = currentProfile()) {
  const tx = normalizedTransmission(profile);
  let minLoad = 0;
  let maxLoad = tx.period;
  if (tx.travelMode === 'reciprocating') {
    minLoad = Math.min(tx.reverseLimit, tx.forwardLimit);
    maxLoad = Math.max(tx.reverseLimit, tx.forwardLimit);
  }
  return {
    minLoad,
    maxLoad,
    minCounts: countsFromTransmissionValue(minLoad, tx),
    maxCounts: countsFromTransmissionValue(maxLoad, tx)
  };
}
function formatTransmissionValue(value, digits = 1) {
  const n = Math.abs(Number(value)) < 0.0005 ? 0 : Number(value);
  return (n > 0 ? '+' : '') + n.toFixed(digits);
}
function formatTransmissionScalar(value, unit, digits = 1) {
  return formatTransmissionValue(value, digits) + ' ' + unit;
}
function formatMotorRevScalar(counts, digits = 3) {
  const motorRev = rev(axisCounts(counts));
  return (motorRev > 0 ? '+' : '') + motorRev.toFixed(digits) + ' rev';
}
function syncTransmissionSummary(device = activeDevice) {
  const tx = normalizedTransmission(currentProfile(device));
  const text = UI_TEXT[currentLang];
  setText('transmissionLabel', text.transmissionLabel);
  setText('transmissionTypeLabel', tx.type === 'linear' ? text.linearType : text.rotaryType);
  setText('transmissionLoadSummary', text.loadPrefix + transmissionPerRev(tx).toFixed(1) + ' ' + tx.unit);
  setText('transmissionMotorSummary', text.motorPrefix + '1 rev');
}
function refillTransmissionUnitOptions(type, preferred) {
  const options = transmissionUnitSets[type] || transmissionUnitSets.rotary;
  transmissionUnit.innerHTML = options.map(opt => '<option value="' + opt.value + '">' + opt.label + '</option>').join('');
  transmissionUnit.value = options.some(opt => opt.value === preferred) ? preferred : options[0].value;
}
function syncTransmissionTravelFields() {
  const isPeriodic = transmissionTravelMode.value !== 'reciprocating';
  transmissionPeriodRow.classList.toggle('hidden', !isPeriodic);
  transmissionForwardRow.classList.toggle('hidden', isPeriodic);
  transmissionReverseRow.classList.toggle('hidden', isPeriodic);
  const unit = transmissionUnit.value || 'deg';
  setText('transmissionPeriodUnit', unit);
  setText('transmissionForwardUnit', unit);
  setText('transmissionReverseUnit', unit);
}
function openTransmissionDialog() {
  const tx = normalizedTransmission(currentProfile());
  transmissionDraft = Object.assign({}, tx);
  transmissionType.value = transmissionDraft.type;
  refillTransmissionUnitOptions(transmissionDraft.type, transmissionDraft.unit);
  transmissionTravelMode.value = transmissionDraft.travelMode;
  transmissionRevs.value = transmissionDraft.revs;
  transmissionAmount.value = transmissionDraft.amount;
  transmissionPeriod.value = transmissionDraft.period;
  transmissionForwardLimit.value = transmissionDraft.forwardLimit;
  transmissionReverseLimit.value = transmissionDraft.reverseLimit;
  updateTransmissionDraft();
  transmissionModal.classList.add('open');
}
function maybeCloseTransmissionDialog(event) {
  if (event.target === transmissionModal) closeTransmissionDialog();
}
function closeTransmissionDialog() {
  transmissionModal.classList.remove('open');
}
function onTransmissionTypeChange() {
  const nextType = transmissionType.value === 'linear' ? 'linear' : 'rotary';
  refillTransmissionUnitOptions(nextType, null);
  if (nextType === 'rotary' && Number(transmissionAmount.value || 0) === 1) {
    transmissionAmount.value = 360;
  }
  if (Number(transmissionPeriod.value || 0) === 1 && nextType === 'rotary') {
    transmissionPeriod.value = 360;
  }
  updateTransmissionDraft();
}
function onTransmissionTravelModeChange() {
  syncTransmissionTravelFields();
  updateTransmissionDraft();
}
function updateTransmissionDraft() {
  const type = transmissionType.value === 'linear' ? 'linear' : 'rotary';
  transmissionDraft = normalizedTransmission({
    type,
    revs: Math.max(0.001, Number(transmissionRevs.value) || 1),
    amount: Math.max(0.001, Number(transmissionAmount.value) || (type === 'linear' ? 1 : 360)),
    unit: transmissionUnit.value,
    travelMode: transmissionTravelMode.value === 'reciprocating' ? 'reciprocating' : 'periodic',
    period: Math.max(0.001, Number(transmissionPeriod.value) || (type === 'linear' ? 1 : 360)),
    forwardLimit: Number(transmissionForwardLimit.value || 0),
    reverseLimit: Number(transmissionReverseLimit.value || 0)
  });
  transmissionRevs.value = transmissionDraft.revs;
  transmissionAmount.value = transmissionDraft.amount;
  transmissionPeriod.value = transmissionDraft.period;
  transmissionForwardLimit.value = transmissionDraft.forwardLimit;
  transmissionReverseLimit.value = transmissionDraft.reverseLimit;
  transmissionTravelMode.value = transmissionDraft.travelMode;
  syncTransmissionTravelFields();
}
function saveTransmissionDialog() {
  updateTransmissionDraft();
  currentProfile().transmission = Object.assign({}, transmissionDraft);
  closeTransmissionDialog();
  updateSliders();
}
async function persistUiState(device = activeDevice) {
  if (device === activeDevice) saveUiState(device, false);
  try {
    await fetch('/api/ui_state', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({device, state: currentProfile(device)})
    });
  } catch (err) {
    console.error(err);
  }
}
function scheduleUiStateSave(device = activeDevice) {
  if (uiStateSaveTimer) clearTimeout(uiStateSaveTimer);
  uiStateSaveTimer = setTimeout(() => {
    uiStateSaveTimer = 0;
    persistUiState(device);
  }, 180);
}
function saveUiState(device = activeDevice) {
  const profile = currentProfile(device);
  profile.transmission = normalizedTransmission(profile);
  profile.absPos = Number(absPos.value);
  profile.absSpeedRpm = Number(absSpeedRpm.value);
  profile.absAccel = Number(absAccel.value);
  profile.relDelta = Number(relDelta.value);
  profile.moveMs = Number(moveMs.value);
  profile.velCps = Number(velCps.value);
  profile.torqueCmd = Number(torqueCmd.value);
  profile.gearMaster = gearMasterSelect.value;
  profile.gearMasterRatio = Number(gearMasterRatio.value);
  profile.gearSlaveRatio = Number(gearSlaveRatio.value);
  profile.mode = modeSelect.value || 'position';
  const snapshot = JSON.stringify(profile);
  const changed = lastUiStateSnapshot[device] !== snapshot;
  lastUiStateSnapshot[device] = snapshot;
  if (changed && (arguments.length < 2 || arguments[1] !== false)) {
    scheduleUiStateSave(device);
  }
}
function loadUiState(device = activeDevice) {
  const profile = currentProfile(device);
  profile.transmission = normalizedTransmission(profile);
  absPos.value = profile.absPos;
  absSpeedRpm.value = profile.absSpeedRpm;
  absAccel.value = profile.absAccel;
  relDelta.value = profile.relDelta;
  moveMs.value = profile.moveMs;
  velCps.value = profile.velCps;
  torqueCmd.value = profile.torqueCmd;
  gearMasterRatio.value = profile.gearMasterRatio || 1;
  gearSlaveRatio.value = profile.gearSlaveRatio || 1;
  if (profile.gearMaster && profile.gearMaster !== device) {
    gearMasterSelect.value = profile.gearMaster;
  } else {
    gearMasterSelect.value = preferredGearMaster(device);
    profile.gearMaster = gearMasterSelect.value;
  }
  modeSelect.value = profile.mode || 'position';
  syncModeSelectDisabled(device);
  refreshGearPanel(device);
}
async function hydrateUiStateFromServer() {
  try {
    const res = await fetch('/api/ui_state');
    const data = await res.json();
    if (!data || !data.ok || !data.state || !data.state.devices) return;
    for (const device of axisDevices()) {
      if (!data.state.devices[device]) continue;
      Object.assign(deviceProfiles[device], data.state.devices[device]);
      deviceProfiles[device].transmission = normalizedTransmission(deviceProfiles[device]);
    }
  } catch (err) {
    console.error(err);
  }
}
function fmt(n) { return Math.round(Number(n)).toLocaleString('en-US'); }
function rev(n) { return Number(n) / REV; }
function revText(n, digits=3) {
  const value = Number(n);
  const prefix = value > 0 ? '+' : '';
  return prefix + value.toFixed(digits) + ' rev';
}
function signedNumber(value, digits=1) {
  const n = Math.abs(Number(value)) < 0.05 ? 0 : Number(value);
  return (n > 0 ? '+' : '') + n.toFixed(digits);
}
function targetParts(counts) {
  const value = Number(counts) / REV;
  const turns = Math.trunc(value);
  const angle = (value - turns) * 360;
  return {turns, angle};
}
function axisCounts(n) { return Number(n) * AXIS_DIR; }
function phaseCounts(n) { return ((axisCounts(n) % REV) + REV) % REV; }
function deg(n) { return phaseCounts(n) / REV * 360; }
function continuousDeg(n) { return axisCounts(n) / REV * 360; }
function rpm(delta, ms) { return Math.abs(rev(delta)) / (Number(ms) / 1000) * 60; }
function absoluteMoveMs(target) {
  const status = currentStatus();
  const current = status ? axisCounts(Number(status.pos)) : 0;
  const speed = Math.max(1, Number(absSpeedRpm.value || 1));
  const distance = Math.abs(Number(target) - current);
  const ms = distance === 0 ? 500 : Math.round(distance / (speed * REV / 60) * 1000);
  return Math.min(60000, Math.max(200, ms));
}
function motionPayload(target) {
  return Object.assign({
    cmd:'move_abs',
    pos:axisCounts(target),
    move_ms:absoluteMoveMs(target),
    speed_rpm:Number(absSpeedRpm.value || 0),
    acceleration_rpm_s:Number(absAccel.value || 0)
  }, currentMotionBoundsPayload());
}
function currentMotionBoundsPayload(profile = currentProfile()) {
  const bounds = transmissionBounds(profile);
  const minNative = axisCounts(bounds.minCounts);
  const maxNative = axisCounts(bounds.maxCounts);
  return {
    min_pos: Math.min(minNative, maxNative),
    max_pos: Math.max(minNative, maxNative)
  };
}
function clamp(value, min, max) { return Math.max(min, Math.min(max, value)); }
function updateFeedbackDial(handId, valueId, value, maxAbs, digits, unit) {
  const hand = document.getElementById(handId);
  const valueEl = document.getElementById(valueId);
  if (!hand || !valueEl) return;
  const bounded = clamp(Math.abs(Number(value) || 0), 0, maxAbs);
  const degrees = 240 + (bounded / maxAbs) * 240;
  hand.style.transform = 'rotate(' + degrees.toFixed(1) + 'deg)';
  valueEl.textContent = signedNumber(value, digits);
  const unitEl = valueEl.nextElementSibling;
  if (unitEl && unit) unitEl.textContent = unit;
}
async function api(payload) {
  if ((payload && payload.cmd) === 'status') {
    const res = await fetch('/api/status?device=' + encodeURIComponent(activeDevice));
    const data = await res.json();
    if (data.ok && data.status) render(data.status);
    return data;
  }
  const requestPayload = Object.assign({}, payload, {device: activeDevice});
  const res = await fetch('/api/command', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(requestPayload)});
  const data = await res.json();
  if (data.ok && data.status) render(data.status);
  return data;
}
function cls(el, good) { el.className = 'value ' + (good ? 'good' : 'bad'); }
function setText(id, text) { const el = document.getElementById(id); if (el) el.textContent = text; }
function modeLabel(mode) {
  return (MODE_LABELS[currentLang] && MODE_LABELS[currentLang][mode]) || mode;
}
function refreshModeOptions() {
  for (const opt of Array.from(modeSelect.options || [])) {
    if (opt.value === 'gear_cam' && opt.disabled) {
      opt.textContent = UI_TEXT[currentLang].gearUnavailable;
    } else {
      opt.textContent = modeLabel(opt.value);
    }
  }
}
function refreshGearMasterOptions() {
  if (!gearMasterSelect || !gearMasterSelect.options) return;
  gearMasterSelect.options[0].textContent = axisDisplayName('mctivity');
  gearMasterSelect.options[1].textContent = axisDisplayName('fv3');
  if (gearMasterSelect.options[2]) gearMasterSelect.options[2].textContent = UI_TEXT[currentLang].virtualAxis;
}
function refreshStaticText() {
  const text = UI_TEXT[currentLang];
  document.documentElement.lang = currentLang === 'zh' ? 'zh-CN' : 'en';
  document.title = text.pageTitle;
  setText('pageTitle', text.pageTitle);
  setText('tabMonitorBtn', text.axisA);
  setText('tabConfigBtn', text.axisB);
  setText('protocolChip', 'EtherCAT');
  const langToggleBtn = document.getElementById('langToggleBtn');
  const langZhBtn = document.getElementById('langZhBtn');
  const langEnBtn = document.getElementById('langEnBtn');
  if (langToggleBtn) {
    langToggleBtn.setAttribute('aria-label', currentLang === 'zh' ? '语言' : 'Language');
    langToggleBtn.title = currentLang === 'zh' ? '语言' : 'Language';
  }
  if (langZhBtn) langZhBtn.classList.toggle('active', currentLang === 'zh');
  if (langEnBtn) langEnBtn.classList.toggle('active', currentLang === 'en');
  const leftCard = document.querySelector('.left-stack .card h2');
  if (leftCard) leftCard.textContent = text.motorFeedback;
  const feedbackTitles = document.querySelectorAll('.feedback-card .feedback-title');
  if (feedbackTitles[0]) feedbackTitles[0].textContent = text.position;
  if (feedbackTitles[1]) feedbackTitles[1].textContent = text.torque;
  if (feedbackTitles[2]) feedbackTitles[2].textContent = text.speed;
  const feedbackLabels = document.querySelectorAll('.feedback-metric .label');
  if (feedbackLabels[0]) feedbackLabels[0].textContent = text.currentTurns;
  if (feedbackLabels[1]) feedbackLabels[1].textContent = text.currentAngle;
  if (feedbackLabels[2]) feedbackLabels[2].textContent = text.currentPulses;
  if (feedbackLabels[3]) feedbackLabels[3].textContent = text.singleTurn;
  const controlTitle = document.querySelector('.middle-stack .card h2');
  if (controlTitle) controlTitle.textContent = text.control;
  const modeLabelNode = document.querySelector('.mode-row .label');
  if (modeLabelNode) modeLabelNode.textContent = text.mode;
  setText('transmissionLabel', text.transmissionLabel);
  setText('transmissionModalTitle', text.transmissionModalTitle);
  setText('transmissionTypeFieldLabel', text.transmissionTypeFieldLabel);
  setText('transmissionTravelModeLabel', text.transmissionTravelModeLabel);
  setText('transmissionAmountLabel', text.transmissionAmountLabel);
  setText('transmissionRevsLabel', text.transmissionRevsLabel);
  setText('transmissionPeriodLabel', text.transmissionPeriodLabel);
  setText('transmissionForwardLabel', text.transmissionForwardLabel);
  setText('transmissionReverseLabel', text.transmissionReverseLabel);
  setText('transmissionCancelBtn', text.transmissionCancelBtn);
  setText('transmissionSaveBtn', text.transmissionSaveBtn);
  const transmissionTypeSelect = document.getElementById('transmissionType');
  if (transmissionTypeSelect && transmissionTypeSelect.options[0]) transmissionTypeSelect.options[0].textContent = text.rotaryType;
  if (transmissionTypeSelect && transmissionTypeSelect.options[1]) transmissionTypeSelect.options[1].textContent = text.linearType;
  const transmissionTravelSelect = document.getElementById('transmissionTravelMode');
  if (transmissionTravelSelect && transmissionTravelSelect.options[0]) transmissionTravelSelect.options[0].textContent = text.periodicTravel;
  if (transmissionTravelSelect && transmissionTravelSelect.options[1]) transmissionTravelSelect.options[1].textContent = text.reciprocatingTravel;
  const toggleTitles = document.querySelectorAll('.toggle-title');
  if (toggleTitles[0]) toggleTitles[0].textContent = text.enable;
  if (toggleTitles[1]) toggleTitles[1].textContent = text.startStop;
  if (toggleTitles[2]) toggleTitles[2].textContent = text.status;
  const controlButtons = document.querySelectorAll('.controls > button');
  if (controlButtons[3]) controlButtons[3].textContent = text.returnZero;
  if (controlButtons[4]) controlButtons[4].textContent = text.setZero;
  const positionTitles = document.querySelectorAll('#panel-position .slider-title');
  if (positionTitles[0]) positionTitles[0].textContent = text.targetAbs;
  const positionLabels = document.querySelectorAll('#panel-position .vertical-slider label');
  if (positionLabels[0]) positionLabels[0].textContent = text.speed;
  if (positionLabels[1]) positionLabels[1].textContent = text.accel;
  const positionButton = document.querySelector('#panel-position button.blue');
  if (positionButton) positionButton.textContent = text.move;
  const targetUnit = document.querySelector('.target-unit');
  if (targetUnit) targetUnit.textContent = normalizedTransmission(currentProfile()).unit;
  const jogTitles = document.querySelectorAll('#panel-jog .slider-title');
  if (jogTitles[0]) jogTitles[0].textContent = text.relMove;
  if (jogTitles[1]) jogTitles[1].textContent = text.moveTime;
  const jogMeta = document.querySelectorAll('#panel-jog .meta span');
  if (jogMeta[1]) jogMeta[1].textContent = text.slowerHint;
  const jogButton = document.querySelector('#panel-jog button.blue');
  if (jogButton) jogButton.textContent = text.move;
  document.querySelectorAll('#panel-point .point-actions button').forEach(btn => btn.textContent = text.move);
  const homingTitle = document.querySelector('#panel-homing .slider-title');
  if (homingTitle) homingTitle.textContent = text.zeroPanel;
  const homingValue = document.querySelector('#panel-homing .slider-number');
  if (homingValue) homingValue.textContent = text.softwareZero;
  const homingNote = document.querySelector('#panel-homing .control-note');
  if (homingNote) homingNote.textContent = text.zeroNote;
  const homingButton = document.querySelector('#panel-homing button.blue');
  if (homingButton) homingButton.textContent = text.setZero;
  const velocityTitle = document.querySelector('#panel-velocity .slider-title');
  if (velocityTitle) velocityTitle.textContent = text.velocityJog;
  const velocityButtons = document.querySelectorAll('#panel-velocity .control-row button');
  if (velocityButtons[0]) velocityButtons[0].textContent = text.reverse;
  if (velocityButtons[1]) velocityButtons[1].textContent = text.stop;
  if (velocityButtons[2]) velocityButtons[2].textContent = text.forward;
  const torqueTitle = document.querySelector('#panel-torque .slider-title');
  if (torqueTitle) torqueTitle.textContent = text.torqueCmd;
  const torqueNote = document.querySelector('#panel-torque .control-note');
  if (torqueNote) torqueNote.textContent = text.torqueNote;
  const torqueButton = document.querySelector('#panel-torque button.neutral');
  if (torqueButton) torqueButton.textContent = text.stageWrite;
  const gearLabels = document.querySelectorAll('.gear-field .label');
  if (gearLabels[0]) gearLabels[0].textContent = text.gearMaster;
  if (gearLabels[1]) gearLabels[1].textContent = text.gearSlave;
  const configTitles = document.querySelectorAll('#tabConfig h2');
  if (configTitles[0]) configTitles[0].textContent = text.pointPositioning;
  if (configTitles[1]) configTitles[1].textContent = text.pointConfig;
  const cardTitles = document.querySelectorAll('#tabConfig .param-card h3');
  if (cardTitles[0]) cardTitles[0].textContent = text.posJog;
  if (cardTitles[1]) cardTitles[1].textContent = text.speedTorque;
  if (cardTitles[2]) cardTitles[2].textContent = text.gear;
  if (cardTitles[3]) cardTitles[3].textContent = text.cam;
  const configLabels = document.querySelectorAll('#tabConfig .param-card label');
  if (configLabels[0]) configLabels[0].childNodes[0].nodeValue = text.defaultRel;
  if (configLabels[1]) configLabels[1].childNodes[0].nodeValue = text.defaultAbs;
  if (configLabels[2]) configLabels[2].childNodes[0].nodeValue = text.defaultMoveMs + ' ms';
  if (configLabels[3]) configLabels[3].childNodes[0].nodeValue = text.defaultVel + ' cnt/s';
  if (configLabels[4]) configLabels[4].childNodes[0].nodeValue = text.torqueLimit + ' %';
  if (configLabels[5]) configLabels[5].childNodes[0].nodeValue = text.gearNum;
  if (configLabels[6]) configLabels[6].childNodes[0].nodeValue = text.gearDen;
  if (configLabels[7]) configLabels[7].childNodes[0].nodeValue = text.camTable;
  if (configLabels[8]) configLabels[8].childNodes[0].nodeValue = text.syncPeriod + ' ms';
  document.querySelectorAll('#tabConfig .point-actions button').forEach(btn => btn.textContent = text.record);
  refreshModeOptions();
  refreshGearMasterOptions();
}
function applyLanguage() {
  refreshStaticText();
  refreshGearPanel(activeDevice);
  syncModeSelectDisabled(activeDevice);
  syncModePanels((currentProfile(activeDevice) && currentProfile(activeDevice).mode) || modeSelect.value || 'position', true);
  updateSliders();
  const status = currentStatus();
  if (status) render(status);
}
function closeLanguageMenu() {
  const dropdown = document.getElementById('langDropdown');
  const button = document.getElementById('langToggleBtn');
  if (dropdown) dropdown.classList.remove('open');
  if (button) button.setAttribute('aria-expanded', 'false');
}
function toggleLanguageMenu(event) {
  if (event) event.stopPropagation();
  const dropdown = document.getElementById('langDropdown');
  const button = document.getElementById('langToggleBtn');
  if (!dropdown || !button) return false;
  const nextOpen = !dropdown.classList.contains('open');
  dropdown.classList.toggle('open', nextOpen);
  button.setAttribute('aria-expanded', nextOpen ? 'true' : 'false');
  return false;
}
function setLanguage(lang) {
  if (lang !== 'zh' && lang !== 'en') return false;
  currentLang = lang;
  localStorage.setItem(LANG_KEY, currentLang);
  closeLanguageMenu();
  applyLanguage();
  return false;
}
function hex4(value) { return '0x' + (Number(value) & 0xffff).toString(16).toUpperCase().padStart(4, '0'); }
function faultName(err, sw) {
  const text = UI_TEXT[currentLang];
  const code = Number(err) & 0xffff;
  if (!code && !(Number(sw) & 0x0008)) return text.ok;
  const names = currentLang === 'zh' ? {
    0x2310:'过流',
    0x3210:'过压',
    0x3220:'欠压',
    0x4210:'驱动过温',
    0x4310:'电机过温',
    0x5280:'参数错误',
    0x6320:'软件限位',
    0x7110:'编码器故障',
    0x7300:'编码器故障',
    0x8400:'运动控制故障',
    0x8611:'跟随误差'
  } : {
    0x2310:'Overcurrent',
    0x3210:'Overvoltage',
    0x3220:'Undervoltage',
    0x4210:'Drive Overtemp',
    0x4310:'Motor Overtemp',
    0x5280:'Parameter Error',
    0x6320:'Software Limit',
    0x7110:'Encoder Fault',
    0x7300:'Encoder Fault',
    0x8400:'Motion Fault',
    0x8611:'Following Error'
  };
  return names[code] || ((Number(sw) & 0x0008) ? text.faultServo : text.faultCode);
}
function showTab(name) {
  saveUiState();
  activeDevice = name === 'config' ? 'fv3' : 'mctivity';
  tabMonitor.classList.add('active');
  tabConfig.classList.remove('active');
  tabMonitorBtn.classList.toggle('active', activeDevice === 'mctivity');
  tabConfigBtn.classList.toggle('active', activeDevice === 'fv3');
  loadUiState();
  enforceGearConstraints();
  feedbackByDevice[activeDevice] = null;
  syncModePanels(modeSelect.value || 'position', true);
  setGearPanelLocked(isGearPanelLocked(activeDevice));
  updateSliders();
  api({cmd:'status'}).catch(() => {});
}
function renderMotionToggle(active, activeText) {
  const motionIndicator = document.getElementById('motionIndicator');
  const motionText = document.getElementById('motionIndicatorText');
  const text = UI_TEXT[currentLang];
  if (!motionIndicator || !motionText) return;
  motionIndicator.classList.toggle('motion-on', Boolean(active));
  motionText.textContent = active ? (activeText || text.inMotion) : text.standstill;
}
function syncModePanels(mode, force=false) {
  const active = mode || 'position';
  if (!force && modePanelStateByDevice[activeDevice] === active) return;
  modePanelStateByDevice[activeDevice] = active;
  const text = UI_TEXT[currentLang];
  for (const key of Object.keys(MODE_LABELS.zh)) {
    const panel = document.getElementById('panel-' + key);
    if (panel) panel.classList.toggle('active', key === active);
  }
  if (active === 'position') {
    setText('modePanelTitle', text.posParams);
  } else if (active === 'gear_cam') {
    setText('modePanelTitle', text.gearParams);
  } else {
    setText('modePanelTitle', modeLabel(active) + ' ' + text.controlSuffix);
  }
}
function updateGearMaster() {
  if (isGearPanelLocked()) return;
  const profile = currentProfile();
  if (gearMasterSelect.value === activeDevice) {
    gearMasterSelect.value = preferredGearMaster(activeDevice);
  }
  profile.gearMaster = gearMasterSelect.value;
  enforceGearConstraints();
  updateSliders();
}
function render(s) {
  const text = UI_TEXT[currentLang];
  const device = s && s.device === 'fv3' ? 'fv3' : 'mctivity';
  statusByDevice[device] = s;
  if (device !== activeDevice) return;
  const motionState = currentMotion(device);
  const now = Date.now();
  let speedFeedback = 0;
  const feedbackSample = feedbackByDevice[device];
  if (feedbackSample && now > feedbackSample.t) {
    speedFeedback = axisCounts(Number(s.pos) - feedbackSample.pos) / REV / ((now - feedbackSample.t) / 1000) * 60;
  }
  feedbackByDevice[device] = {pos:Number(s.pos) || 0, t:now};
  updateFeedbackDial('speedHand', 'speedGaugeValue', speedFeedback / 1000, 3, 1, 'krpm');
  updateFeedbackDial('torqueHand', 'torqueGaugeValue', Number(s.torque_feedback ?? s.torque_actual ?? 0), 100, 1, '%');
  let e = document.getElementById('enabled');
  if (e) { e.textContent = s.enabled ? text.on : text.off; cls(e, s.enabled); }
  const toggle = document.getElementById('enableToggle');
  const toggleText = document.getElementById('enableToggleText');
  const enableRawOn = Boolean(s.servo_request || s.enabled);
  if (enableRawOn) {
    motionState.enableVisual = true;
    motionState.enableOffCandidateAt = 0;
  } else if (s.fault) {
    motionState.enableVisual = false;
    motionState.enableOffCandidateAt = 0;
  } else if (!motionState.enableOffCandidateAt) {
    motionState.enableOffCandidateAt = now;
  } else if (now - motionState.enableOffCandidateAt > 800) {
    motionState.enableVisual = false;
  }
  const enableVisualOn = Boolean(motionState.enableVisual);
  if (toggle && toggleText) {
    toggle.classList.toggle('on', enableVisualOn);
    toggleText.textContent = enableVisualOn ? text.on : text.off;
  }
  if (s.moving) {
    motionState.seenMoving = true;
    motionState.movingOffCandidateAt = 0;
  } else {
    motionState.stopRequested = false;
    if (!motionState.movingOffCandidateAt) motionState.movingOffCandidateAt = now;
  }
  if (motionState.latch && !s.moving && (motionState.seenMoving || now - motionState.commandAt > 1200)) {
    if (motionState.movingOffCandidateAt && now - motionState.movingOffCandidateAt > 600) {
      motionState.latch = false;
    }
  }
  if (s.control_mode === 'gear_cam' && typeof s.gear_running !== 'undefined') {
    motionState.gearEngaged = Boolean(s.gear_running);
  } else if (s.control_mode !== 'gear_cam' && !motionState.latch) {
    motionState.gearEngaged = false;
    motionState.gearStoppedLatched = false;
  }
  const gearEngaged = isGearEngaged(device) && s.control_mode === 'gear_cam';
  const forceGearStandstill = Boolean(motionState.gearStoppedLatched && !gearEngaged);
  if (forceGearStandstill && !s.moving) {
    motionState.gearStoppedLatched = false;
  }
  renderMotionToggle(gearEngaged || (!forceGearStandstill && motionState.latch), gearEngaged ? text.gearing : '');
  setGearPanelLocked(gearEngaged);
  const faultIndicator = document.getElementById('faultIndicator');
  const faultText = document.getElementById('faultIndicatorText');
  const faultButton = document.getElementById('faultIndicatorButton');
  const faultCodeText = document.getElementById('faultCodeText');
  const faultNameText = document.getElementById('faultNameText');
  if (faultIndicator && faultText && faultButton) {
    faultIndicator.classList.toggle('fault-on', Boolean(s.fault));
    faultText.textContent = s.fault ? text.fault : text.ready;
    setText('faultCodeText', hex4(s.err));
    setText('faultNameText', faultName(s.err, s.sw));
    faultButton.setAttribute('aria-label', s.fault ? text.fault : text.ready);
    faultButton.textContent = s.fault ? text.reset : '';
    faultButton.classList.toggle('fault', Boolean(s.fault));
  }
  let m = document.getElementById('moving'); if (m) { m.textContent = s.moving ? text.moving : text.idle; m.className = 'value info'; }
  let f = document.getElementById('fault'); if (f) { f.textContent = s.fault ? text.fault : text.ok; cls(f, !s.fault); }
  setText('pos', fmt(s.pos)); setText('target', fmt(s.target)); setText('follow', fmt(s.following_error));
  setText('op', '0x' + Number(s.al_state).toString(16) + ' / ' + s.operational);
  setText('wc', s.wc + (s.wc_complete ? ' complete' : ''));
  setText('mode', s.mode);
  setText('controlModeView', modeLabel(s.control_mode) || s.control_mode || '--');
  setText('velocityView', fmt(s.jog_velocity_cps || 0));
  setText('torqueView', String(s.torque_cmd || 0) + '%');
  const modeUi = currentModeUi(device);
  if (!modeUi.interacting) {
    if (modeUi.pending && s.control_mode === modeUi.pending) {
      modeUi.pending = null;
      syncModeSelectDisabled(device);
    }
    if (modeSelect && device === activeDevice && !modeUi.pending && s.control_mode && modeSelect.value !== s.control_mode) {
      modeSelect.value = s.control_mode;
    }
    if (!modeUi.pending && s.control_mode) {
      currentProfile(device).mode = s.control_mode;
    }
    syncModePanels(modeUi.pending || s.control_mode || currentProfile(device).mode || 'position');
    enforceGearConstraints();
  }
  const a = deg(s.pos);
  setText('encoderAngle', a.toFixed(1) + ' deg');
  setText('encoderSingleTurn', fmt(phaseCounts(s.pos)) + ' cnt');
  setText('encoderTurns', rev(axisCounts(s.pos)).toFixed(3) + ' rev');
  setText('encoderPulses', fmt(axisCounts(s.pos)) + ' cnt');
  const encoderHand = document.getElementById('encoderHand');
  if (encoderHand) encoderHand.style.transform = 'rotate(' + continuousDeg(s.pos) + 'deg)';
  updateSliders();
}
function updateSliders() {
  saveUiState();
  const profile = currentProfile();
  profile.transmission = normalizedTransmission(profile);
  const tx = profile.transmission;
  const bounds = transmissionBounds(profile);
  const rangeMin = Math.min(bounds.minCounts, bounds.maxCounts);
  const rangeMax = Math.max(bounds.minCounts, bounds.maxCounts);
  if (String(absPos.min) !== String(rangeMin)) absPos.min = String(rangeMin);
  if (String(absPos.max) !== String(rangeMax)) absPos.max = String(rangeMax);
  absPos.value = String(clamp(Number(absPos.value), rangeMin, rangeMax));
  profile.absPos = Number(absPos.value);
  const rel = Number(relDelta.value), abs = Number(absPos.value), ms = Number(moveMs.value);
  const vel = Number(velCps.value), tq = Number(torqueCmd.value);
  const speed = Number(absSpeedRpm.value), accel = Number(absAccel.value);
  const gearSlaveName = axisDisplayName(activeDevice);
  const status = currentStatus();
  const current = status ? axisCounts(Number(status.pos)) : abs;
  const isLinear = tx.type === 'linear';
  const targetValue = transmissionValueFromCounts(abs, profile);
  const relValue = transmissionValueFromCounts(rel, profile);
  syncTransmissionSummary(activeDevice);
  setText('relText', formatTransmissionScalar(relValue, tx.unit, 1));
  setText('relRev', UI_TEXT[currentLang].motorPrefix + formatMotorRevScalar(rel, 3));
  setText('relDeg', UI_TEXT[currentLang].loadPrefix + formatTransmissionScalar(relValue, tx.unit, 1));
  setText('relRpm', rpm(rel, ms).toFixed(1) + ' rpm');
  setText('absText', formatTransmissionScalar(targetValue, tx.unit, 1));
  setText('targetRevBig', formatTransmissionValue(targetValue, 1));
  setText('targetAngleBig', isLinear ? '' : UI_TEXT[currentLang].motorPrefix + formatMotorRevScalar(abs, 3));
  const targetUnit = document.querySelector('.target-unit');
  if (targetUnit) targetUnit.textContent = tx.unit;
  setText('axisMinRev', formatTransmissionScalar(bounds.minLoad, tx.unit, 1));
  setText('axisMaxRev', formatTransmissionScalar(bounds.maxLoad, tx.unit, 1));
  const targetReadout = document.querySelector('.target-readout');
  if (targetReadout) targetReadout.classList.toggle('linear-mode', isLinear);
  const positionAxis = document.querySelector('.position-axis');
  if (positionAxis) positionAxis.classList.toggle('linear-mode', isLinear);
  const currentPositionMarker = document.getElementById('currentPositionMarker');
  if (currentPositionMarker) {
    const currentLoadPos = transmissionValueFromCounts(current, profile);
    const loadSpan = Math.max(0.001, bounds.maxLoad - bounds.minLoad);
    const markerPct = clamp((currentLoadPos - bounds.minLoad) / loadSpan, 0, 1);
    currentPositionMarker.style.setProperty('--marker-pct', String(markerPct));
  }
  setText('absSpeedText', fmt(speed) + ' rpm'); setText('absAccelText', fmt(accel) + ' rpm/s');
  renderGearWheel('master');
  renderGearWheel('slave');
  setText('gearSlaveName', gearSlaveName);
  setText('msText', fmt(ms) + ' ms');
  setText('velText', fmt(vel) + ' cnt/s'); setText('torqueText', tq + '%');
  const points = profile.points;
  for (const k of [1,2,3]) {
    const pointText = formatTransmissionScalar(transmissionValueFromCounts(points[k], profile), tx.unit, 1);
    setText('p' + k, pointText);
    setText('p' + k + 'Quick', pointText);
  }
}
function applyConfig() {
  relDelta.value = cfgRel.value; absPos.value = cfgAbs.value; moveMs.value = cfgMs.value; velCps.value = cfgVel.value;
  absSpeedRpm.value = Math.max(1, Math.min(3000, Math.round(Number(cfgVel.value) * 60 / REV)));
  torqueCmd.min = -Number(cfgTorqueLimit.value); torqueCmd.max = Number(cfgTorqueLimit.value);
  updateSliders();
}
function cmd(name) { return api({cmd:name}); }
function toggleEnable() {
  const status = currentStatus();
  const motionState = currentMotion();
  if (!(status && status.servo_request)) {
    const currentPos = status ? axisCounts(Number(status.pos || 0)) : 0;
    absPos.value = currentPos;
    currentProfile().absPos = currentPos;
    updateSliders();
    motionState.enableVisual = true;
    motionState.enableOffCandidateAt = 0;
  } else {
    motionState.enableVisual = false;
    motionState.enableOffCandidateAt = 0;
  }
  return cmd(status && status.servo_request ? 'disable' : 'enable');
}
function gearPayload(cmdName) {
  return {
    cmd: cmdName,
    master: gearMasterSelect.value,
    master_ratio: Number(gearMasterRatio.value || 1),
    slave_ratio: Number(gearSlaveRatio.value || 1)
  };
}
function isGearModeSelected() {
  const status = currentStatus();
  return (modeSelect && modeSelect.value === 'gear_cam') || (status && status.control_mode === 'gear_cam');
}
function setMode() {
  const requested = modeSelect.value;
  const modeUi = currentModeUi();
  const previous = (currentProfile() && currentProfile().mode) || (currentStatus() && currentStatus().control_mode) || 'position';
  if (requested === 'gear_cam' && applyGearModeAvailability(activeDevice)) {
    showGearLockedAlert(activeDevice);
    modeSelect.value = previous;
    syncModePanels(previous);
    return Promise.resolve(false);
  }
  if (requested !== 'gear_cam') {
    currentMotion().gearEngaged = false;
    currentMotion().gearStoppedLatched = false;
    setGearPanelLocked(false);
  }
  currentProfile().mode = requested;
  modeUi.pending = requested;
  modeUi.interacting = false;
  syncModeSelectDisabled(activeDevice);
  syncModePanels(requested, true);
  return api({cmd:'set_mode', mode:requested}).then(data => {
    if (!(data && data.ok)) {
      throw new Error((data && data.error) || 'set_mode failed');
    }
    if (data.status && data.status.control_mode === requested) {
      modeUi.pending = null;
      syncModeSelectDisabled(activeDevice);
    }
    enforceGearConstraints();
    return data;
  }).catch(err => {
    modeUi.pending = null;
    currentProfile().mode = previous;
    modeSelect.value = previous;
    syncModeSelectDisabled(activeDevice);
    syncModePanels(previous, true);
    enforceGearConstraints();
    console.error(err);
    return false;
  });
}
function resetFault() {
  const motionState = currentMotion();
  if (!(currentStatus() && currentStatus().fault)) return false;
  motionState.commandSeq += 1;
  motionState.stopRequested = false;
  motionState.latch = false;
  motionState.seenMoving = false;
  motionState.gearEngaged = false;
  motionState.gearStoppedLatched = false;
  renderMotionToggle(false);
  setGearPanelLocked(false);
  api({cmd:'fault_reset'}).catch(err => console.error(err));
  return false;
}
function stopMotion() {
  const motionState = currentMotion();
  motionState.commandSeq += 1;
  if (motionState.stopRequested) return false;
  motionState.stopRequested = true;
  motionState.latch = false;
  motionState.seenMoving = false;
  motionState.gearStoppedLatched = false;
  renderMotionToggle(Boolean(currentStatus() && currentStatus().moving) || isGearEngaged(), isGearEngaged() ? UI_TEXT[currentLang].gearing : '');
  if (isGearModeSelected()) {
    return api(gearPayload('gear_stop'))
      .then(data => {
        motionState.stopRequested = false;
        motionState.gearEngaged = false;
        motionState.gearStoppedLatched = true;
        motionState.latch = false;
        motionState.seenMoving = false;
        renderMotionToggle(false);
        setGearPanelLocked(false);
        return data;
      })
      .catch(err => {
        motionState.stopRequested = false;
        renderMotionToggle(Boolean(currentStatus() && currentStatus().moving) || isGearEngaged(), isGearEngaged() ? UI_TEXT[currentLang].gearing : '');
        console.error(err);
        return false;
      });
  }
  return api({cmd:'stop', deceleration_rpm_s:Number(absAccel.value || 0)})
    .then(data => {
      const moving = Boolean(data && data.status && data.status.moving);
      if (!moving) {
        motionState.stopRequested = false;
        motionState.gearEngaged = false;
        renderMotionToggle(false);
        setGearPanelLocked(false);
      }
      return data;
    })
    .catch(err => {
      motionState.stopRequested = false;
      renderMotionToggle(Boolean(currentStatus() && currentStatus().moving) || isGearEngaged(), isGearEngaged() ? UI_TEXT[currentLang].gearing : '');
      console.error(err);
      return false;
    });
}
async function startSinglePointMotion() {
  const motionState = currentMotion();
  if (motionState.stopRequested) return false;
  if (motionState.latch || (currentStatus() && currentStatus().moving)) {
    return stopMotion();
  }
  const commandSeq = ++motionState.commandSeq;
  motionState.stopRequested = false;
  motionState.latch = true;
  motionState.seenMoving = false;
  motionState.gearStoppedLatched = false;
  motionState.commandAt = Date.now();
  renderMotionToggle(true, isGearModeSelected() ? UI_TEXT[currentLang].gearing : '');
  try {
    if (isGearModeSelected()) {
      if (modeSelect && modeSelect.value !== 'gear_cam') {
        modeSelect.value = 'gear_cam';
      }
      syncModePanels('gear_cam');
      motionState.gearEngaged = false;
      setGearPanelLocked(false);
      const modeResult = await api({cmd:'set_mode', mode:'gear_cam'});
      if (!modeResult.ok) throw new Error(modeResult.error || 'set_mode gear_cam failed');
      if (commandSeq !== motionState.commandSeq || motionState.stopRequested) return false;
      const cfgResult = await api(gearPayload('gear_config'));
      if (!cfgResult.ok) throw new Error(cfgResult.error || 'gear_config failed');
      if (commandSeq !== motionState.commandSeq || motionState.stopRequested) return false;
      const startResult = await api(gearPayload('gear_start'));
      if (!startResult.ok) throw new Error(startResult.error || 'gear_start failed');
      motionState.gearEngaged = true;
      renderMotionToggle(true, UI_TEXT[currentLang].gearing);
      setGearPanelLocked(true);
      return startResult;
    }
    motionState.gearEngaged = false;
    setGearPanelLocked(false);
    if (modeSelect && modeSelect.value !== 'position') {
      modeSelect.value = 'position';
      syncModePanels('position');
    }
    const modeResult = await api({cmd:'set_mode', mode:'position'});
    if (!modeResult.ok) throw new Error(modeResult.error || 'set_mode failed');
    if (commandSeq !== motionState.commandSeq || motionState.stopRequested) return false;
    if (commandSeq !== motionState.commandSeq || motionState.stopRequested) return false;
    const moveResult = await api(motionPayload(Number(absPos.value)));
    if (commandSeq !== motionState.commandSeq || motionState.stopRequested) return false;
    if (!moveResult.ok) throw new Error(moveResult.error || 'move_abs failed');
  } catch (err) {
    if (commandSeq !== motionState.commandSeq) return false;
    motionState.latch = false;
    motionState.seenMoving = false;
    motionState.gearEngaged = false;
    setGearPanelLocked(false);
    renderMotionToggle(false);
    console.error(err);
  }
}
function moveAbs() { return api(motionPayload(Number(absPos.value))); }
function returnZero() { absPos.value = 0; updateSliders(); return api(motionPayload(0)); }
function moveRel() {
  return api(Object.assign({
    cmd:'move_rel',
    delta:Number(relDelta.value),
    move_ms:Number(moveMs.value),
    speed_rpm:Number(absSpeedRpm.value || 0),
    acceleration_rpm_s:Number(absAccel.value || 0)
  }, currentMotionBoundsPayload()));
}
function jogVelocity(v) { return api({cmd:'jog_velocity', velocity:v}); }
function sendTorque() { return api({cmd:'torque_cmd', torque:Number(torqueCmd.value)}); }
function savePoint(n) { if (currentStatus()) { currentProfile().points[n] = axisCounts(Number(currentStatus().pos)); updateSliders(); } }
function gotoPoint(n) { absPos.value = currentProfile().points[n]; updateSliders(); return api(motionPayload(Number(absPos.value))); }
function lockViewport() {
  document.addEventListener('gesturestart', e => e.preventDefault(), {passive:false});
  document.addEventListener('touchmove', e => {
    if (!e.target.closest('input[type=range]') && !e.target.closest('.gear-wheel')) e.preventDefault();
  }, {passive:false});
  document.addEventListener('dblclick', e => e.preventDefault(), {passive:false});
}
function tryFullscreen() {
  const el = document.documentElement;
  if (!document.fullscreenElement && el.requestFullscreen) el.requestFullscreen().catch(() => {});
}
syncModePanels('position');
lockViewport();
document.addEventListener('pointerdown', tryFullscreen, {once:true});
const langToggleBtn = document.getElementById('langToggleBtn');
const langDropdown = document.getElementById('langDropdown');
if (langToggleBtn) {
  langToggleBtn.addEventListener('click', toggleLanguageMenu);
}
document.addEventListener('pointerdown', event => {
  if (!langDropdown || !langToggleBtn) return;
  if (langDropdown.contains(event.target) || langToggleBtn.contains(event.target)) return;
  closeLanguageMenu();
});
if (modeSelect) {
  modeSelect.addEventListener('pointerdown', () => { currentModeUi().interacting = true; });
  modeSelect.addEventListener('focus', () => { currentModeUi().interacting = true; });
  modeSelect.addEventListener('blur', () => {
    currentModeUi().interacting = false;
    applyGearModeAvailability(activeDevice);
    syncModePanels((currentModeUi().pending || (currentStatus() && currentStatus().control_mode) || currentProfile().mode || modeSelect.value || 'position'), true);
  });
}
async function bootstrapUi() {
  await hydrateUiStateFromServer();
  loadUiState('mctivity');
  enforceGearConstraints();
  applyLanguage();
  setInterval(() => api({cmd:'status'}).catch(() => {}), 300);
  api({cmd:'status'}).catch(() => {});
}
bootstrapUi();
</script>
</body>
</html>
"""


def motiond_command(payload, port=MOTIOND_PORT):
    line = json.dumps(payload, separators=(",", ":")) + "\n"
    with socket.create_connection((MOTIOND_HOST, port), timeout=1.0) as sock:
        sock.sendall(line.encode("utf-8"))
        data = b""
        while not data.endswith(b"\n"):
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
    if not data:
        return {"ok": False, "error": "motion daemon returned no data"}
    return json.loads(data.decode("utf-8"))


def _default_ui_state():
    return {"devices": {}}


def _normalize_ui_device_state(raw):
    if not isinstance(raw, dict):
        return None
    normalized = {}
    numeric_fields = (
        "absPos",
        "absSpeedRpm",
        "absAccel",
        "relDelta",
        "moveMs",
        "velCps",
        "torqueCmd",
        "gearMasterRatio",
        "gearSlaveRatio",
    )
    for field in numeric_fields:
        if field in raw:
            try:
                normalized[field] = float(raw[field])
            except (TypeError, ValueError):
                pass
    if "mode" in raw and isinstance(raw["mode"], str):
        normalized["mode"] = raw["mode"]
    if "gearMaster" in raw and isinstance(raw["gearMaster"], str):
        normalized["gearMaster"] = raw["gearMaster"]
    points = raw.get("points")
    if isinstance(points, dict):
        normalized_points = {}
        for key, value in points.items():
            try:
                normalized_points[str(key)] = float(value)
            except (TypeError, ValueError):
                continue
        if normalized_points:
            normalized["points"] = normalized_points
    tx = raw.get("transmission")
    if isinstance(tx, dict):
        normalized_tx = {}
        for key in ("type", "unit", "travelMode"):
            if key in tx and isinstance(tx[key], str):
                normalized_tx[key] = tx[key]
        for key in ("revs", "amount", "period", "forwardLimit", "reverseLimit"):
            if key in tx:
                try:
                    normalized_tx[key] = float(tx[key])
                except (TypeError, ValueError):
                    continue
        if normalized_tx:
            normalized["transmission"] = normalized_tx
    return normalized


def load_ui_state():
    with _ui_state_lock:
        if not os.path.exists(UI_STATE_PATH):
            return _default_ui_state()
        try:
            with open(UI_STATE_PATH, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            return _default_ui_state()
        if not isinstance(data, dict):
            return _default_ui_state()
        devices = data.get("devices")
        if not isinstance(devices, dict):
            return _default_ui_state()
        normalized = {"devices": {}}
        for device in ("mctivity", "fv3"):
            if device not in devices:
                continue
            device_state = _normalize_ui_device_state(devices[device])
            if device_state:
                normalized["devices"][device] = device_state
        return normalized


def save_ui_state(device, state):
    normalized_state = _normalize_ui_device_state(state)
    if device not in ("mctivity", "fv3") or normalized_state is None:
        raise ValueError("invalid ui state payload")
    with _ui_state_lock:
        merged = load_ui_state()
        merged["devices"][device] = normalized_state
        directory = os.path.dirname(UI_STATE_PATH)
        if directory:
            os.makedirs(directory, exist_ok=True)
        tmp_path = UI_STATE_PATH + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as fh:
            json.dump(merged, fh, ensure_ascii=False, indent=2)
        os.replace(tmp_path, UI_STATE_PATH)


_fv3_cache = None
_fv3_cache_ts = 0.0
_fv3_cycles = 0
_fv3_lock = threading.RLock()
_fv3_soft_zero_raw = 0
_fv3_last_command = "status"
_fv3_message = "FV3 monitor ready"


def _run_text(cmd):
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=1.5, check=False)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip() or "command failed")
    return result.stdout


def _read_sdo_int(position, data_type, index_hex, subindex=0):
    text = _run_text(
        [
            "ethercat",
            "upload",
            "-p",
            str(position),
            "--type",
            data_type,
            index_hex,
            str(subindex),
        ]
    )
    match = re.search(r"(-?\d+)\s*$", text.strip())
    if not match:
        raise RuntimeError(f"unexpected upload output: {text.strip()}")
    return int(match.group(1))


def _read_fv3_state():
    text = _run_text(["ethercat", "slave", "-p", str(FV3_SLAVE_POSITION)])
    match = re.search(r"State:\s+([A-Z]+)", text)
    if not match:
        match = re.search(r"\b(INIT|PREOP|SAFEOP|OP)\b", text)
    if not match:
        return "UNKNOWN"
    return match.group(1)


def _write_sdo(position, data_type, index_hex, value, subindex=0):
    _run_text(
        [
            "ethercat",
            "download",
            "-p",
            str(position),
            "--type",
            data_type,
            index_hex,
            str(subindex),
            "--",
            str(int(value)),
        ]
    )


def _invalidate_fv3_cache():
    global _fv3_cache_ts
    _fv3_cache_ts = 0.0


def _rpm_to_counts_s(rpm):
    return max(1, int(abs(float(rpm)) * 8388608.0 / 60.0))


def _rpm_s_to_counts_s2(rpm_s):
    return max(1, int(abs(float(rpm_s)) * 8388608.0 / 60.0))


def _mode_name(mode):
    mapping = {
        1: "position",
        3: "velocity",
        4: "torque",
        6: "homing",
        8: "position",
        9: "velocity",
        10: "torque",
    }
    return mapping.get(mode, "position")


def _fv3_mode_code_from_name(mode_name):
    name = str(mode_name or "").strip().lower()
    if name in ("position", "jog", "point"):
        # FV3 single-axis motion uses PP trigger sequence.
        return 1
    if name == "gear_cam":
        # Electronic gearing stays in CSP.
        return 8
    if name == "homing":
        return 6
    if name == "velocity":
        return 9
    if name == "torque":
        return 10
    return 8


def fv3_status():
    return motiond_command({"cmd": "status", "device": "fv3"})


def _apply_fv3_profile_from_payload(payload):
    speed_rpm = int(float(payload.get("speed_rpm", 0) or 0))
    accel_rpm_s = int(float(payload.get("acceleration_rpm_s", 0) or 0))
    if speed_rpm > 0:
        _write_sdo(FV3_SLAVE_POSITION, "uint32", "0x6081", _rpm_to_counts_s(speed_rpm))
    if accel_rpm_s > 0:
        accel = _rpm_s_to_counts_s2(accel_rpm_s)
        _write_sdo(FV3_SLAVE_POSITION, "uint32", "0x6083", accel)
        _write_sdo(FV3_SLAVE_POSITION, "uint32", "0x6084", accel)


def fv3_command(payload):
    payload2 = dict(payload)
    cmd = str(payload2.get("cmd", "status"))
    if cmd in ("move_abs", "move_rel"):
        _apply_fv3_profile_from_payload(payload2)
    if cmd == "set_mode":
        mode_code = _fv3_mode_code_from_name(payload2.get("mode", "position"))
        _write_sdo(FV3_SLAVE_POSITION, "int8", "0x6060", mode_code)
    if cmd in ("gear_config", "gear_start"):
        # Keep FV3 in CSP while running electronic gear tracking.
        _write_sdo(FV3_SLAVE_POSITION, "int8", "0x6060", 8)
    payload2["device"] = "fv3"
    return motiond_command(payload2)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        device = (query.get("device", ["mctivity"])[0] or "mctivity").lower()
        if path == "/" or path == "/index.html":
            body = HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/api/status":
            try:
                if device == "fv3":
                    self.send_json(fv3_status())
                else:
                    self.send_json(motiond_command({"cmd": "status"}))
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, 503)
        elif path == "/api/ui_state":
            try:
                self.send_json({"ok": True, "state": load_ui_state()})
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, 503)
        else:
            self.send_error(404)

    def do_POST(self):
        path = urlparse(self.path).path
        if path not in ("/api/command", "/api/ui_state"):
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(body.decode("utf-8"))
            if path == "/api/ui_state":
                device = str(payload.get("device", "mctivity")).lower()
                state = payload.get("state", {})
                save_ui_state(device, state)
                self.send_json({"ok": True, "state": load_ui_state()})
            else:
                device = str(payload.pop("device", "mctivity")).lower()
                if device == "fv3":
                    status = fv3_command(payload)
                    self.send_json(status, 400 if not status.get("ok") else 200)
                else:
                    self.send_json(motiond_command(payload))
        except Exception as exc:
            self.send_json({"ok": False, "error": str(exc)}, 503)


if __name__ == "__main__":
    server = ThreadingHTTPServer((WEB_HOST, WEB_PORT), Handler)
    print(f"Motion HMI listening on http://{WEB_HOST}:{WEB_PORT}")
    server.serve_forever()
