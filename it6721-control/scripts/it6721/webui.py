# -*- coding: utf-8 -*-
# Copyright (c) 2026 BreezeKitte
# SPDX-License-Identifier: MIT
"""本地浏览器控制台。仅用标准库 http.server，绑定 127.0.0.1，不对外暴露。

界面沿用 Ardot 设计稿的深炭黑 + 单荧光绿信号色，数值使用等宽字体。
"""

import atexit
import json
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from . import autotune as tunemod
from . import commands as cmdmod
from . import recorder as recmod
from . import sequence as seqmod
from . import serial_port as sp
from . import device as dv

# 会改动设备/上位机状态的写接口；全局锁生效时统一拦截
WRITE_PATHS = {
    "/api/connect", "/api/apply", "/api/output", "/api/preset",
    "/api/cmd", "/api/record", "/api/sequence", "/api/sequence/steps",
    "/api/tune",
}

HTML = r"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>IT6721 程控电源控制台</title>
<style>
:root{--bg:#101311;--panel:#191E19;--panel2:#0A0E0A;--line:#2C352C;
--green:#9EFF00;--text:#F2F6F0;--dim:#9AA79B;--mute:#6E7B6F;
--amber:#F59E0B;--red:#EF4444;--blue:#5B9DFF}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);
font-family:"Noto Sans SC","Microsoft YaHei",system-ui,sans-serif;font-size:17px}
.mono{font-family:"JetBrains Mono",Consolas,"Courier New",monospace}
header{height:56px;background:#151A16;border-bottom:1px solid var(--line);
display:flex;align-items:center;justify-content:space-between;padding:0 24px}
.brand{display:flex;align-items:center;gap:12px;font-size:20px;font-weight:600}
.mark{width:24px;height:24px;background:var(--green);border-radius:5px;display:grid;
place-items:center;color:#101311;font-weight:900;font-size:20px}
.spec{color:var(--dim);font-size:15px}
.badge{display:inline-flex;align-items:center;gap:8px;background:#1C2A12;
border:1px solid #3C5A22;color:var(--green);padding:5px 10px;border-radius:8px;font-size:15px}
.dot{width:8px;height:8px;border-radius:50%;background:var(--green);box-shadow:0 0 8px var(--green)}
.dot.off{background:#666;box-shadow:none}
main{padding:20px 24px;display:grid;grid-template-columns:minmax(0,1.7fr) minmax(340px,1fr);
gap:18px;max-width:1640px;margin:0 auto}
section{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:16px}
section.dark{background:var(--panel2)}
h2{font-size:16px;margin:0 0 12px;font-weight:600;letter-spacing:.3px}
h2 small{color:var(--mute);font-weight:400;margin-left:8px;font-size:13px}
.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.spread{justify-content:space-between}
.pill{font-size:13px;padding:4px 9px;border-radius:6px;border:1px solid var(--line);
background:#171C17;color:var(--mute)}
.pill.on{background:#1C2A12;border-color:#3C5A22;color:var(--green)}
.pill.warn{background:#2A2110;border-color:#5C4715;color:var(--amber)}
.readouts{display:flex;align-items:center;gap:18px;margin-top:6px}
.rd3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:14px;flex:1;min-width:0}
.rd{border-right:1px solid var(--line)}.rd:last-child{border:0}
.rd .lbl{font-size:13px;color:var(--mute);margin-bottom:2px}
.big{font-size:69px;font-weight:700;color:var(--green);line-height:1.05;
text-shadow:0 0 12px rgba(158,255,0,.45),0 0 34px rgba(158,255,0,.18)}
.big .u{font-size:27px;font-weight:400;margin-left:6px}
.big.small{font-size:40px;color:var(--text);text-shadow:none}
.meta{font-size:13px;color:var(--mute);margin-top:12px}
canvas{width:100%;height:120px;display:block}
input,select,button{font:inherit;color:var(--text);background:#0F140F;
border:1px solid #3A453A;border-radius:8px;padding:8px 10px;outline:none}
input:focus,select:focus{border-color:var(--green)}
button{cursor:pointer;background:#212821;border-color:#3A453A;transition:.15s;
 white-space:nowrap;flex-shrink:0}
button:hover{border-color:var(--green);color:var(--green)}
button.primary{background:var(--green);color:#101311;border-color:var(--green);font-weight:700}
button.primary:hover{filter:brightness(1.12);color:#101311}
button.danger{background:#2A1616;border-color:#5C2A2A;color:#F08A8A}
button.danger:hover{border-color:var(--red);color:var(--red)}
button.sm{padding:5px 10px;font-size:15px}
.field{display:flex;flex-direction:column;gap:5px;flex:1;min-width:110px}
.field label{font-size:13px;color:var(--dim)}
.hint{background:#2A2110;border:1px solid #5C4715;color:var(--amber);
border-radius:8px;padding:9px 12px;font-size:15px;margin-top:12px}
.cmdbox{display:flex;align-items:center;gap:10px;background:#0A0E0A;
border:1px solid #3A453A;border-radius:9px;padding:8px 10px}
.cmdbox .prompt{color:var(--green);font-size:24px;font-weight:700}
.cmdbox input{flex:1;border:0;background:transparent;padding:4px;font-size:20px}
.cmdbox input:focus{border:0}
.chips{display:flex;gap:7px;flex-wrap:wrap;margin-top:10px}
.chip{font-size:13px;padding:5px 10px;border-radius:6px;background:#212821;
border:1px solid #3A453A;cursor:pointer;user-select:none}
.chip:hover{border-color:var(--green);color:var(--green)}
.chip.g{background:#1C2A12;border-color:#3C5A22;color:var(--green)}
.chip.r{background:#2A1616;border-color:#5C2A2A;color:#F08A8A}
.log{height:190px;overflow:auto;background:#0A0E0A;border:1px solid var(--line);
border-radius:9px;padding:10px;font-size:15px;line-height:1.65}
.log div{white-space:pre-wrap;word-break:break-all}
.t{color:var(--mute);margin-right:8px}.d{color:var(--dim);margin-right:6px}
.lv-ok{color:var(--green)}.lv-error{color:#F08A8A}.lv-warn{color:var(--amber)}
.lv-info{color:var(--dim)}
table{width:100%;border-collapse:collapse;font-size:15px}
th{text-align:left;color:var(--green);font-weight:600;padding:5px 6px;border-bottom:1px solid var(--line)}
td{padding:5px 6px;border-bottom:1px solid #222A22}
footer{color:var(--mute);font-size:13px;text-align:center;padding:14px}
.lockbar{display:none;max-width:1640px;margin:12px auto 0;padding:9px 14px;
 background:#2A1616;border:1px solid #5C2A2A;color:#F08A8A;border-radius:8px;font-size:15px}
.lockbar.on{display:block}
button:disabled,input:disabled,select:disabled{opacity:.42;cursor:not-allowed}
button:disabled:hover{border-color:#3A453A;color:var(--text);filter:none}
/* ---- 锁芯片：挂锁图标 + 开/锁动作 ---- */
.lockchip{display:flex;align-items:center;gap:9px;padding:6px 14px 6px 12px;
 border:1px solid var(--line);border-radius:8px;background:var(--panel2);
 cursor:pointer;user-select:none;transition:border-color .3s,background .3s}
.lockchip.locked{border-color:var(--green);background:rgba(158,255,0,.08)}
.lockchip.unlocked{border-color:var(--amber);background:rgba(255,182,72,.08)}
.lockchip svg{display:block;transition:transform .35s cubic-bezier(.34,1.56,.64,1);overflow:visible;color:var(--green)}
.lockchip.unlocked svg{transform:translateY(-1px) rotate(-14deg);transform-origin:13px 7px;color:var(--amber)}
.lockchip .lock-label{font-family:var(--mono);font-size:16px;font-weight:600;letter-spacing:1px}
.lockchip.locked .lock-label{color:var(--green)}
.lockchip.unlocked .lock-label{color:var(--amber)}
.lockchip.lg{padding:14px 24px;gap:14px;border-width:2px}
.lockchip.lg svg{width:36px;height:34px}
.lockchip.lg .lock-label{font-size:22px;letter-spacing:2px}
.lockchip.lg.unlocked svg{transform-origin:21px 13px}
/* ---- 微调面板：手动 | 自动 两列 ---- */
.tune-grid2{display:grid;grid-template-columns:1fr 1fr;gap:0;margin-top:12px;
 border:1px solid var(--line);border-radius:8px;overflow:hidden;background:var(--panel2)}
.tune-col{padding:14px;display:flex;flex-direction:column;gap:12px;min-width:0}
.tune-col input{min-width:0}
.tune-col.manual{border-right:1px solid var(--line)}
.tune-col .col-h{font-family:var(--mono);font-size:15px;color:var(--green);
 letter-spacing:1px;display:flex;align-items:center;gap:6px}
.tune-col .col-h .en{color:var(--mute)}
.manual-display{display:flex;align-items:stretch;gap:10px}
.mbtn{flex:0 0 56px;height:64px;border-radius:8px;border:1px solid var(--line);
 background:var(--panel);color:var(--green);font-size:37px;font-family:var(--mono);
 cursor:pointer;display:flex;align-items:center;justify-content:center;transition:.15s}
.mbtn:hover{border-color:var(--green);background:rgba(158,255,0,.1)}
.mbtn:active{transform:scale(.96)}
.mnum{flex:1;display:flex;align-items:center;justify-content:center;
 background:var(--bg);border:1px solid var(--line);border-radius:8px;
 color:var(--green);font-family:var(--mono);font-size:38px;font-weight:700;
 text-shadow:0 0 14px rgba(158,255,0,.45);letter-spacing:1px;min-width:0;padding:0 8px}
.mnum small{font-size:17px;color:var(--dim);margin-left:6px;letter-spacing:0}
.mrow{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.mode-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.mode{flex:1;height:44px;border-radius:8px;border:1px solid var(--line);
 background:var(--panel);color:var(--text);font-family:var(--mono);font-size:17px;
 cursor:pointer;display:flex;align-items:center;justify-content:center;gap:6px;transition:.15s}
.mode:hover{border-color:var(--green)}
.mode.primary{border-color:var(--green);color:var(--green);background:rgba(158,255,0,.1)}
.mode.stop{color:var(--dim)}
.keypad{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}
.key{height:46px;border-radius:8px;border:1px solid var(--line);background:var(--bg);
 color:var(--green);font-family:var(--mono);font-size:21px;cursor:pointer;
 display:flex;align-items:center;justify-content:center;transition:.15s}
.key:hover{border-color:var(--green);background:rgba(158,255,0,.12)}
.key.stopkey{color:var(--dim);border-color:var(--line);background:var(--panel2)}
.jump-in{width:100%;min-width:0;height:34px;padding:4px 6px;font-size:17px;text-align:center}
.auto-bottom{display:flex;align-items:center;gap:10px}
.interval{flex:1;height:44px;border-radius:8px;border:1px solid var(--line);
 background:var(--bg);color:var(--amber);font-family:var(--mono);font-size:20px;
 display:flex;align-items:center;justify-content:center}
.interval small{font-size:15px;color:var(--dim);margin-left:6px}
.lockicon{flex:0 0 56px;height:44px;border-radius:8px;border:1px solid var(--green);
 background:rgba(158,255,0,.08);cursor:pointer;display:flex;align-items:center;justify-content:center;transition:.2s}
.lockicon:hover{background:rgba(158,255,0,.18)}
.tunegrid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}
.tuneval{font-size:29px;font-weight:700;color:var(--green);line-height:1.1}
.seqwrap{overflow-x:auto}
.seqin{width:100%;min-width:60px;padding:5px 6px;font-size:15px}
.seqbtns{display:flex;gap:4px;flex-wrap:wrap}
.seqbtns button{padding:3px 7px;font-size:13px}
.seqtotal{font-size:15px;color:var(--dim);margin-top:8px}
.stepbad{background:#2A1616!important;border-color:#5C2A2A!important}
@media(max-width:1000px){main{grid-template-columns:1fr}.big{font-size:53px}
 .tunegrid{grid-template-columns:1fr 1fr}}
</style></head><body>
<header>
 <div class="brand"><span class="mark">&#9889;</span>IT6721 程控电源控制台
   <span class="spec mono" id="spec">ITECH IT6721 · 0–60V / 0–8A / 180W · Auto Range</span></div>
 <div class="row">
   <span class="badge" id="connBadge"><span class="dot off" id="connDot"></span>
   <span id="connText">未连接</span></span><span class="spec mono" id="baudText">4800 · 8N1 · 26B 帧</span></div>
</header>
<div class="lockbar" id="lockBar">全局操作锁已启用 · 设定 / 输出 ON / 命令终端 / 序列 / 微调等写操作均被前后端拦截；断开连接、输出 OFF、序列停止与微调停止仍可用。</div>
<main>
 <div style="display:flex;flex-direction:column;gap:18px">
  <section class="dark">
   <div class="row spread"><h2>实时输出读数 <small>/ MEASURE</small></h2>
    <div class="row" id="pills">
     <span class="pill on" id="pCV">CV 恒压</span><span class="pill" id="pCC">CC 恒流</span>
     <span class="pill" id="pOVP">OVP</span><span class="pill" id="pOCP">OCP</span>
     <span class="pill" id="pOUT">OFF</span></div></div>
   <div class="readouts">
    <div class="rd3">
     <div class="rd"><div class="lbl">电压 VOLTAGE</div><div class="big mono"><span id="v">0.00</span><span class="u">V</span></div></div>
     <div class="rd"><div class="lbl">电流 CURRENT</div><div class="big mono"><span id="i">0.000</span><span class="u">A</span></div></div>
     <div class="rd"><div class="lbl">功率 POWER</div><div class="big small mono"><span id="p">0.00</span><span class="u">W</span></div></div>
    </div>
    <div class="lockchip lg unlocked" id="lockChip" onclick="toggleLock()" title="点击切换 锁定 / 解锁">
     <svg width="36" height="34" viewBox="0 0 22 20" fill="none">
      <path d="M6 9 V6.5 a5 5 0 0 1 10 0 V9" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round"/>
      <rect x="4" y="9" width="14" height="9.5" rx="2" stroke="currentColor" stroke-width="2" fill="rgba(158,255,0,.08)"/>
      <circle cx="11" cy="13.5" r="1.6" fill="currentColor"/>
     </svg>
     <span class="lock-label" id="lockLabel">UNLOCKED</span>
    </div>
   </div>
   <div class="row spread meta"><span id="sample">回读 10 Hz · 轮询 100 ms</span>
    <span id="acc">回读精度 V &lt;0.05%+10mV / I &lt;0.3%+5mA</span></div>
  </section>
  <section id="tunePanel"><div class="row spread">
    <h2>输出微调 / 自动调节 <small>/ MANUAL TUNE · AUTO · 0.01 精度 · 全局锁生效</small></h2>
    <span class="pill" id="tuneState">就绪</span></div>
   <div class="tune-grid2">
    <!-- 左列：手动面板 -->
    <div class="tune-col manual">
     <div class="col-h">手动面板 <span class="en">MANUAL</span></div>
     <div class="mrow">
      <select id="tuneTarget" data-guard="1" style="flex:0 0 92px" onchange="tuneUnitSync()">
       <option value="V">电压 V</option><option value="I">电流 A</option></select>
      <input id="tuneStep" class="mono" type="number" step="0.01" min="0.01" value="0.01" data-guard="1" style="flex:1" title="步进 STEP">
     </div>
     <div class="manual-display">
      <button class="mbtn" data-guard="1" onclick="tuneNudge(-1)">−</button>
      <div class="mnum"><input id="tuneValue" class="mono" type="number" step="0.01" value="1.79" data-guard="1" style="flex:1;min-width:0;background:transparent;border:0;color:var(--green);font-family:var(--mono);font-size:38px;font-weight:700;text-align:center;text-shadow:0 0 14px rgba(158,255,0,.45)"><small id="tuneUnit" style="color:var(--dim);font-size:17px;margin-left:4px">V</small></div>
      <button class="mbtn" data-guard="1" onclick="tuneNudge(1)">+</button>
     </div>
     <div class="mrow">
      <input id="tuneMin" class="mono" type="number" step="0.01" value="0.00" data-guard="1" style="flex:1" title="下限 MIN">
      <input id="tuneMax" class="mono" type="number" step="0.01" value="12.00" data-guard="1" style="flex:1" title="上限 MAX">
      <button class="primary sm" data-guard="1" onclick="tuneSet()">设定</button>
     </div>
     <div class="mrow" style="color:var(--dim);font-size:15px;font-family:var(--mono)">
      <span id="tuneReadout">1.79 V</span><span id="tuneTicks" style="margin-left:auto">0 步</span></div>
    </div>
    <!-- 右列：自动面板 -->
    <div class="tune-col auto">
     <div class="col-h">自动面板 <span class="en">AUTO</span></div>
     <div class="mode-grid">
      <button class="mode stop" data-guard="1" onclick="tuneAuto('off')">× 停止</button>
      <button class="mode" data-guard="1" onclick="tuneAuto('up')">↑ 升压</button>
      <button class="mode" data-guard="1" onclick="tuneAuto('down')">↓ 降压</button>
      <button class="mode primary" data-guard="1" onclick="tuneAuto('pingpong')">⇄ 往复</button>
     </div>
     <div class="keypad" id="jumpPad"><!-- 跳跃区间由 JS 渲染 --></div>
     <div class="auto-bottom">
      <div class="interval" style="flex:1"><input id="tuneInterval" class="mono" type="number" step="0.1" min="0.1" value="1.00" data-guard="1" style="width:60px;background:transparent;border:0;color:var(--amber);font-size:20px;text-align:center"> <small>s 间隔</small></div>
     </div>
     <div class="mrow">
      <button class="mode primary" data-guard="1" style="flex:2" onclick="tuneJump()">跳跃启动 ▶</button>
      <button class="mode" data-guard="1" style="flex:1" onclick="jumpAdd()">+ 区间</button>
     </div>
    </div>
   </div>
   <div class="hint" id="tuneHint">手动面板：大数字 + − / + 微调（步进可调）。自动面板：升压 / 降压 / 往复 / 停止；或编辑跳跃区间后点「跳跃启动」按顺序跳变（如 5→9→16→8，可自由增删）。所有动作经 0–60 V / 0–8 A / ≤180 W 校验，越限或断线立即停止。</div>
  </section>
  <section><h2>输出趋势 <small>/ TREND · 最近 60 秒 · 纵轴自适应</small></h2>
   <canvas id="chart" width="800" height="120"></canvas></section>
  <section><h2>命令终端 <small>/ TERMINAL · 26B 帧 · 直接输入 on / off / V 12 / A 0.5</small></h2>
   <div class="cmdbox"><span class="prompt mono">&gt;</span>
    <input id="cmd" class="mono" placeholder="V 12 ; A 0.5 ; on" autocomplete="off" spellcheck="false">
    <button class="primary sm" onclick="sendCmd()">发送 ⏎</button></div>
   <div class="chips" id="chips"></div>
   <div class="log mono" id="log" style="margin-top:12px;height:170px"></div>
  </section>
 </div>
 <div style="display:flex;flex-direction:column;gap:18px">
  <section><h2>连接设置 <small>/ SERIAL &amp; BAUD</small></h2>
   <div class="row"><div class="field"><label>串口端口</label><select id="port"></select></div>
    <div class="field"><label>波特率（手册固定）</label><select id="baud"></select></div>
    <div class="field"><label>设备地址 0–30</label>
     <input id="addr" class="mono" value="0" type="number" min="0" max="30"></div>
    <div class="field"><label>校验位</label><select id="parity">
     <option value="N">8N1 无校验</option><option value="O">8O1 奇校验</option>
     <option value="E">8E1 偶校验</option></select></div></div>
   <div class="row" style="margin-top:10px">
    <button class="primary sm" data-guard="1" onclick="doConnect(true)">自动扫描并连接</button>
    <button class="sm" data-guard="1" onclick="doConnect(false)">按选定速率连接</button>
    <button class="sm" onclick="api('/api/disconnect',{})">断开</button>
    <button class="sm" data-guard="1" onclick="doSim()">模拟设备</button></div>
   <div class="hint" id="linkInfo">IT6721 用 26 字节二进制帧、4800 8N1（不是 SCPI）。
   后面板 DB9 是 TTL 电平，必须经 IT-E121 / IT-E122 转换模块连 PC，禁止标准 RS232 线直连。
   AUTO 先试 4800，再逐档兜底并换地址；命中条件是收到合法 26 字节应答帧。
   面板 Shift + V/A 长按 3 秒 → Lock 翻到 ADD 可查看设备地址（出厂 0）。</div>
  </section>
  <section><h2>输出设定 <small>/ SET</small></h2>
   <div class="row"><div class="field"><label>电压设定 V (0.00–60.00)</label>
     <input id="setV" class="mono" value="1.79" type="number" step="0.01" min="0" max="60"></div>
    <div class="field"><label>电流设定 A (0.000–8.000)</label>
     <input id="setI" class="mono" value="2.000" type="number" step="0.001" min="0" max="8"></div></div>
   <div class="row" style="margin-top:10px"><button class="primary sm" data-guard="1" onclick="doApply()">应用设定</button>
    <button class="sm" data-guard="1" onclick="doPreset(1)">A</button><button class="sm" data-guard="1" onclick="doPreset(2)">B</button>
    <button class="sm" data-guard="1" onclick="doPreset(3)">C</button><button class="sm" data-guard="1" onclick="doPreset(4)">D</button></div>
   <div class="hint" id="powerHint">P = U × I ≤ 180 W；设为 60 V 时最大电流仅 3.00 A</div>
   <div class="row" style="margin-top:14px">
    <button class="primary" id="outBtn" style="flex:1;height:48px;font-size:21px" onclick="toggleOut()">输出 ON</button>
    <span class="pill mono" id="outState">当前：OFF</span></div>
  </section>
  <section><h2>序列编程 <small>/ SEQUENCE · 可自由增删与编辑</small></h2>
   <div class="seqwrap"><table><thead><tr>
     <th style="width:34px">步</th><th style="width:88px">电压 V</th><th style="width:88px">电流 A</th>
     <th style="width:78px">保持 s</th><th style="width:66px">功率 W</th><th>操作</th>
    </tr></thead><tbody id="seqBody"></tbody></table></div>
   <div class="seqtotal mono" id="seqSummary">—</div>
   <div class="row" style="margin-top:10px">
    <button class="sm" data-guard="1" onclick="seqEdit('add')">＋ 添加步骤</button>
    <button class="sm" data-guard="1" onclick="seqEdit('reset')">恢复默认</button>
    <button class="primary sm" data-guard="1" onclick="seqSave()">保存步骤</button>
    <span class="pill" id="seqDirty">已同步</span></div>
   <div class="row" style="margin-top:10px">
    <div class="field"><label>循环次数</label><input id="loops" type="number" value="3" min="1" max="99" data-guard="1"></div>
    <div class="field"><label>速度倍率</label><input id="speed" type="number" value="10" min="0.1" max="100" step="0.5" data-guard="1"></div></div>
   <div class="row" style="margin-top:10px"><button class="primary sm" data-guard="1" onclick="seqAct('start')">开始序列</button>
    <button class="sm" data-guard="1" onclick="seqAct('pause')">暂停</button><button class="sm" data-guard="1" onclick="seqAct('resume')">继续</button>
    <button class="danger sm" onclick="seqAct('stop')">停止</button>
    <span class="pill" id="seqState">idle</span></div>
   <div class="hint" id="seqHint" style="margin-top:10px">每步 V / I / 保持时间均可编辑；保存后
    运行前会整体校验量程与 ≤180 W。序列结束或异常自动输出 OFF。</div>
  </section>
  <section><h2>数据记录 <small>/ DATA LOG · CSV</small></h2>
   <div class="row"><button class="primary sm" data-guard="1" onclick="recAct('start')">开始记录</button>
    <button class="sm" onclick="recAct('stop')">停止并导出</button>
    <a id="csvLink" href="/api/export.csv" style="display:none"><button class="sm">下载 CSV</button></a>
    <span class="pill" id="recState">未记录</span></div>
   <div class="meta mono" id="csvPreview" style="margin-top:10px;color:var(--dim)">
    time,V,I,P,mode,out</div>
   <div class="hint" style="margin-top:10px">每步下发后丢弃前 300 ms 过渡点；
    CSV 复用回读轮询结果，不额外占用串口。</div>
  </section>
 </div>
</main>
<footer>IT6721 Control Panel <span id="ver">—</span> · 零第三方依赖 ·
 Python 标准库实现 · 仅监听 127.0.0.1 · Enter 发送 / Space 输出开关 / F1 帮助</footer>
<script>
const $=id=>document.getElementById(id);
const CHIPS=['ON','OFF','V 12','V 24','A 0.5','A 1.2','A 0.001','MEAS','RCL 1','HELP','*IDN?'];
let lastLogLen=-1, outState=false;
let locked=false, seqSteps=[], seqSig='', seqDirtyFlag=false;
const DEFAULT_STEPS=[{v:5.00,i:1.000,hold:10},{v:12.00,i:2.000,hold:15},
 {v:24.00,i:3.000,hold:20},{v:1.79,i:1.000,hold:10}];
$('chips').innerHTML=CHIPS.map(c=>{
 const cls=c==='ON'?'chip g':c==='OFF'?'chip r':'chip';
 return '<span class="'+cls+'" onclick="quick(this)">'+c+'</span>'}).join('');
$('port').innerHTML='<option value="">检测端口中…</option>';
$('baud').innerHTML=[4800,9600,19200,38400,57600,115200].map(b=>'<option>'+b+'</option>').join('');
async function api(path,body){
 const opt=body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{};
 const r=await fetch(path,opt);return await r.json();}
function quick(el){$('cmd').value=el.textContent;sendCmd();}
async function sendCmd(){
 const c=$('cmd').value.trim();if(!c)return;$('cmd').value='';
 try{const j=await api('/api/cmd',{cmd:c});await refresh(true);}catch(e){console.error(e)}}
$('cmd').addEventListener('keydown',e=>{if(e.key==='Enter')sendCmd()});
document.addEventListener('keydown',e=>{
 if(e.code==='Space'&&document.activeElement.tagName!=='INPUT'&&!locked){e.preventDefault();toggleOut()}
 if(e.key==='F1'&&!locked){e.preventDefault();$('cmd').value='HELP';sendCmd()}});
async function doConnect(auto){
 const j=await api('/api/connect',{port:$('port').value,baud:+$('baud').value,parity:$('parity').value,address:+$('addr').value,auto:auto});
 $('linkInfo').textContent=j.ok?('已连接 '+(j.port||'')+' · '+(j.baud||'')+' · 地址 '+(j.address||0)+' · '+(j.idn||'')):
   ('扫描失败：'+(j.error||'未知错误')+'。请确认 IT-E121/IT-E122 转换模块、线缆与设备地址(ADD)');
 await refresh(true);}
function doSim(){api('/api/connect',{port:'SIM',sim:true,auto:false}).then(()=>refresh(true))}
// 关闭网页即整体退出：停扫描 / 序列、释放串口并结束 pythonw 进程
function _quitOnLeave(){
  try{navigator.sendBeacon('/api/quit', new Blob(['{}'],{type:'application/json'}))}catch(e){}
}
window.addEventListener('pagehide', _quitOnLeave);
window.addEventListener('beforeunload', _quitOnLeave);
async function doApply(){
 const j=await api('/api/apply',{v:+$('setV').value,i:+$('setI').value});
 if(j.error)$('powerHint').textContent='ERR  '+j.error;
 else $('powerHint').textContent='P = '+$('setV').value+' × '+$('setI').value+' = '+
   (($('setV').value*$('setI').value).toFixed(1))+' W ≤ 180 W  [PASS]';
 await refresh(true);}
async function doPreset(n){await api('/api/preset',{slot:n});await refresh(true)}
async function toggleOut(){await api('/api/output',{on:!outState});await refresh(true)}
async function seqAct(a){
 if(a==='start'&&seqDirtyFlag){$('seqHint').textContent='请先点「保存步骤」再开始序列';return}
 await api('/api/sequence',{action:a,loops:+$('loops').value,speed:+$('speed').value});await refresh(true)}
async function recAct(a){const j=await api('/api/record',{action:a});
 $('csvLink').style.display=a==='stop'?'inline':'none';await refresh(true)}
function draw(trend){
 const c=$('chart'),x=c.getContext('2d'),W=c.width,H=c.height;
 x.clearRect(0,0,W,H);x.strokeStyle='#2C352C';x.lineWidth=1;
 for(let k=1;k<4;k++){x.beginPath();x.moveTo(0,H*k/4);x.lineTo(W,H*k/4);x.stroke()}
 if(!trend||trend.length<2)return;
 const maxV=Math.max(2,...trend.map(p=>p[1]))*1.15,maxI=Math.max(1,...trend.map(p=>p[2]))*1.15;
 function line(idx,max,color){x.beginPath();x.strokeStyle=color;x.lineWidth=2;x.lineJoin='round';
  trend.forEach((p,n)=>{const px=n/(trend.length-1)*W,py=H-8-(p[idx]/max)*(H-18);
   n?x.lineTo(px,py):x.moveTo(px,py)});x.stroke()}
 line(1,maxV,'#9EFF00');line(2,maxI,'#5B9DFF');}
async function refresh(force){
 let s;try{s=await api('/api/state')}catch(e){return}
 $('v').textContent=s.v.toFixed(2);$('i').textContent=s.i.toFixed(3);$('p').textContent=s.p.toFixed(2);
 // 用户正在输入时不要被 250 ms 轮询覆盖
 if(document.activeElement!==$('setV'))$('setV').value=s.set_v.toFixed(2);
 if(document.activeElement!==$('setI'))$('setI').value=s.set_i.toFixed(3);
 $('pCV').className='pill'+(s.mode==='CV'?' on':'');$('pCC').className='pill'+(s.mode==='CC'?' on':'');
 $('pOVP').className='pill'+(s.v>s.ovp?' warn':'');$('pOCP').className='pill'+(s.i>s.ocp?' warn':'');
 $('pOUT').className='pill'+(s.output?' on':'');$('pOUT').textContent=s.output?'ON':'OFF';
 outState=s.output;$('outState').textContent='当前：'+(s.output?'ON':'OFF');
 $('outBtn').textContent=s.output?'输出 OFF':'输出 ON';
 $('outBtn').className=s.output?'danger':'primary';
 $('connDot').className='dot'+(s.connected?'':' off');
 $('connText').textContent=s.connected?(s.port+' 已连接'+(s.sim?' (模拟)':'')):'未连接';
 $('baudText').textContent=s.baud+' · 8'+(s.parity||'N')+'1 · 26B 帧 · A'+(s.address||0)+(s.auto?' · AUTO':'');
 $('spec').textContent=s.idn||'ITECH IT6721 · 0–60V / 0–8A / 180W · Auto Range';
 $('sample').textContent='回读 10 Hz · TX '+s.tx+' / RX '+s.rx+' / ERR '+s.errors+
   ' · 当前电压下电流上限 '+s.max_i.toFixed(3)+' A';
 $('ver').textContent='v'+s.version;
 $('seqState').textContent=s.seq.status+'  '+s.seq.loop+'/'+s.seq.loops+' 步 '+s.seq.step+'/'+s.seq.steps;
 $('recState').textContent=s.rec.recording?('记录中 '+s.rec.rows+' 行'):'未记录';
 $('csvPreview').textContent=(s.rec.preview||[]).join('\n')||'time,V,I,P,mode,out';
 if(s.ports&&$('port').options.length!==s.ports.length+1){
   const cur=$('port').value;
   $('port').innerHTML=s.ports.map(p=>'<option>'+p+'</option>').join('')+'<option>SIM</option>';
   if(s.ports.includes(cur))$('port').value=cur;}
 if(s.log.length!==lastLogLen||force){
  lastLogLen=s.log.length;
  const el=$('log');el.innerHTML=s.log.map(l=>'<div class="lv-'+l.level+'">'+
   '<span class="t">'+l.t+'</span><span class="d">'+l.dir+'</span>'+esc(l.text)+'</div>').join('');
  el.scrollTop=el.scrollHeight;}
 // ---- 全局操作锁 ----
 if(locked!==!!s.locked){locked=!!s.locked;applyLockUI()}
 // ---- 手动微调 ----
 if(s.tune){const t=s.tune;
  $('tuneState').textContent=t.active?('运行中 · '+t.mode):t.status;
  $('tuneReadout').textContent=t.value.toFixed(2)+' '+t.target;
  $('tuneTicks').textContent=t.ticks+' 步';
  $('tuneUnit').textContent=t.target;
  if(!$('tunePanel').dataset.init){
   $('tuneTarget').value=t.target;$('tuneMin').value=t.min.toFixed(2);
   $('tuneMax').value=t.max.toFixed(2);$('tuneStep').value=t.step.toFixed(2);
   $('tuneInterval').value=t.interval.toFixed(2);
   $('tuneValue').value=t.value.toFixed(2);$('tunePanel').dataset.init='1';}
  if(t.active&&document.activeElement!==$('tuneValue'))
   $('tuneValue').value=t.value.toFixed(2);
  if(t.last_error)$('tuneHint').textContent='ERR  '+t.last_error;}
 // ---- 序列编辑器（有未保存改动时不被轮询覆盖）----
 if(!seqDirtyFlag){
  const sig=JSON.stringify(s.steps)+'|'+s.seq.loops+'|'+s.seq.speed;
  if(sig!==seqSig){seqSig=sig;
   seqSteps=s.steps.map(p=>({v:p.v,i:p.i,hold:p.hold}));seqRender();}}
 if(document.activeElement!==$('loops'))$('loops').value=s.seq.loops;
 if(document.activeElement!==$('speed'))$('speed').value=s.seq.speed;
 draw(s.trend);}
function esc(t){return String(t).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}

// ---------------- 全局操作锁 ----------------
async function toggleLock(){const j=await api('/api/lock',{on:!locked});
 if(j&&j.error)console.error(j.error);await refresh(true)}
function applyLockUI(){
 document.body.classList.toggle('locked',locked);
 document.querySelectorAll('[data-guard]').forEach(el=>{el.disabled=locked});
 const chip=$('lockChip');
 chip.classList.toggle('locked',locked);
 chip.classList.toggle('unlocked',!locked);
 $('lockLabel').textContent=locked?'LOCKED':'UNLOCKED';
 $('lockBar').classList.toggle('on',locked);
}

// ---------------- 手动微调 ----------------
function tuneCfg(withValue){
 const o={target:$('tuneTarget').value,vmin:+$('tuneMin').value,
  vmax:+$('tuneMax').value,step:+$('tuneStep').value,
  interval:+$('tuneInterval').value};
 if(withValue)o.value=+$('tuneValue').value;
 return o;}
function tuneShow(j){
 if(j&&j.error){$('tuneHint').textContent='ERR  '+j.error;return}
 if(j&&j.tune){const t=j.tune;
  $('tuneState').textContent=t.active?('运行中 · '+t.mode):t.status;
  $('tuneReadout').textContent=t.value.toFixed(2)+' '+t.target;
  $('tuneTicks').textContent=t.ticks+' 步';}}
async function tuneSet(){
 const j=await api('/api/tune',Object.assign({action:'set'},tuneCfg(true)));
 tuneShow(j);await refresh(true);}
async function tuneNudge(sign){
 const j=await api('/api/tune',Object.assign({action:'nudge',
  delta:+$('tuneStep').value*sign},tuneCfg(false)));
 tuneShow(j);await refresh(true);}
async function tuneAuto(mode){
 const j=await api('/api/tune',Object.assign(
  {action:mode==='off'?'off':'auto',mode:mode},tuneCfg(false)));
 tuneShow(j);await refresh(true);}
// ---- 跳跃键盘 ----
let jumpPts=[2,5,9,16,8];
function jumpRender(){
 const pad=$('jumpPad');
 pad.innerHTML=jumpPts.map((v,i)=>
  '<input class="jump-in mono" type="number" step="0.01" value="'+v+'" data-i="'+i+'" onchange="jumpEdit('+i+',this.value)" data-guard="1">'
 ).join('')+'<button class="key stopkey" onclick="tuneAuto(\'off\')">STOP</button>';}
function jumpEdit(i,val){const v=parseFloat(val);if(!isNaN(v))jumpPts[i]=v;}
function jumpAdd(){jumpPts.push(0);jumpRender();}
async function tuneJump(){
 const pts=jumpPts.filter(v=>!isNaN(parseFloat(v)));
 if(pts.length<2){$('tuneHint').textContent='ERR  跳跃至少需要 2 个有效区间点';return}
 const j=await api('/api/tune',Object.assign(
  {action:'auto',mode:'jump',jump_points:pts},tuneCfg(false)));
 tuneShow(j);await refresh(true);}
function tuneUnitSync(){$('tuneUnit').textContent=$('tuneTarget').value;}

// ---------------- 序列编辑器 ----------------
function seqCalc(){
 const loops=Math.max(1,+$('loops').value||1),speed=Math.max(0.1,+$('speed').value||1);
 let per=0,maxP=0,bad=0;
 seqSteps.forEach(p=>{per+=p.hold;const w=p.v*p.i;if(w>maxP)maxP=w;
  if(p.v<0||p.v>60||p.i<0||p.i>8||w>180||p.hold<=0)bad++;});
 $('seqSummary').textContent=seqSteps.length+' 步 · '+per.toFixed(0)+' s/循环 · ×'+loops+
  ' · 总时长 '+(per*loops/speed).toFixed(0)+' s · 最大 '+maxP.toFixed(1)+' W'+
  (bad?' · '+bad+' 步越限':' · 校验通过');
 return bad;}
function seqRender(){
 $('seqBody').innerHTML=seqSteps.map((p,n)=>{
  const w=p.v*p.i,bad=(p.v<0||p.v>60||p.i<0||p.i>8||w>180||p.hold<=0);
  return '<tr class="mono"><td>'+String(n+1).padStart(2,'0')+'</td>'+
   '<td><input class="seqin mono'+(bad?' stepbad':'')+'" data-guard="1" type="number" step="0.01" min="0" max="60" value="'+p.v.toFixed(2)+'" onchange="seqField('+n+',\'v\',this.value)"></td>'+
   '<td><input class="seqin mono" data-guard="1" type="number" step="0.001" min="0" max="8" value="'+p.i.toFixed(3)+'" onchange="seqField('+n+',\'i\',this.value)"></td>'+
   '<td><input class="seqin mono" data-guard="1" type="number" step="0.1" min="0.1" value="'+p.hold+'" onchange="seqField('+n+',\'hold\',this.value)"></td>'+
   '<td style="color:'+(bad?'#F08A8A':'var(--green)')+'">'+w.toFixed(1)+'</td>'+
   '<td><div class="seqbtns">'+
    '<button class="sm" data-guard="1" onclick="seqEdit(\'up\','+n+')">上移</button>'+
    '<button class="sm" data-guard="1" onclick="seqEdit(\'down\','+n+')">下移</button>'+
    '<button class="sm" data-guard="1" onclick="seqEdit(\'copy\','+n+')">复制</button>'+
    '<button class="danger sm" data-guard="1" onclick="seqEdit(\'del\','+n+')">删除</button>'+
   '</div></td></tr>'}).join('');
 applyLockUI();seqCalc();}
function seqDirty(){
 seqDirtyFlag=true;$('seqDirty').textContent='未保存';$('seqDirty').className='pill warn';}
function seqField(n,k,val){
 const v=parseFloat(val);if(isNaN(v))return;
 seqSteps[n][k]=v;seqDirty();seqRender();}
function seqEdit(op,n){
 if(op==='add')seqSteps.push({v:5.00,i:1.000,hold:10});
 else if(op==='reset')seqSteps=DEFAULT_STEPS.map(p=>Object.assign({},p));
 else if(op==='del'){if(seqSteps.length<=1)return;seqSteps.splice(n,1)}
 else if(op==='copy')seqSteps.splice(n+1,0,Object.assign({},seqSteps[n]));
 else if(op==='up'&&n>0){const t=seqSteps[n];seqSteps[n]=seqSteps[n-1];seqSteps[n-1]=t}
 else if(op==='down'&&n<seqSteps.length-1){const t=seqSteps[n];seqSteps[n]=seqSteps[n+1];seqSteps[n+1]=t}
 seqDirty();seqRender();}
async function seqSave(){
 const j=await api('/api/sequence/steps',{steps:seqSteps,
  loops:+$('loops').value,speed:+$('speed').value});
 if(j.error){$('seqHint').textContent='ERR  '+j.error;return}
 seqDirtyFlag=false;$('seqDirty').textContent='已同步';$('seqDirty').className='pill on';
 $('seqHint').textContent=j.message||'步骤已保存；运行前会整体校验。';
 await refresh(true);}

applyLockUI();
jumpRender();
api('/api/ports').then(j=>{if(j.ports&&j.ports.length){
 $('port').innerHTML=j.ports.map(p=>'<option>'+p+'</option>').join('')+'<option>SIM</option>';
 if(j.ports.includes(j.default))$('port').value=j.default;}});
refresh(true);setInterval(refresh,250);
</script></body></html>"""


class ConsoleApp(object):
    def __init__(self, dev, data_dir="."):
        self.dev = dev
        self.data_dir = data_dir
        self.recorder = recmod.Recorder(data_dir)
        self.sequence = None
        self.locked = False
        self.steps = [recmod.SequenceStep(s.v, s.i, s.hold)
                      for s in recmod.DEFAULT_STEPS]
        self.loops = 3
        self.speed = 10.0
        self.tuner = tunemod.AutoTuner(
            dev, on_event=self.on_tune_event,
            can_write=lambda: not self.locked)
        # 命令终端的 LOCK ON/OFF 直接作用于全局锁
        self.dev.lock_fn = self.set_lock
        # 轮询结果同时喂给记录器，不另发一轮查询
        self.dev.on_sample = self.recorder.on_sample
        self.dev.on_event = lambda level, text: None

    def on_seq_event(self, text, level):
        self.dev._log("SEQ", text, level)

    def on_tune_event(self, text, level):
        self.dev._log("TUNE", text, level)

    def set_lock(self, on):
        """全局操作锁。上锁时立即停止自动扫描，防止后台继续写设备。"""
        on = bool(on)
        if on and not self.locked:
            self.tuner.stop("全局锁定")
        self.locked = on
        self.dev._log("SYS", "全局操作锁已%s" % ("启用" if on else "解除"),
                      "warn" if on else "ok")
        return {"ok": True, "locked": self.locked}

    def _guard(self, path, payload):
        """锁定态拒绝所有写接口；安全动作与解锁本身始终放行。"""
        if not self.locked or path not in WRITE_PATHS:
            return None
        if path == "/api/disconnect":
            return None
        if path == "/api/output" and not payload.get("on"):
            return None                       # 输出 OFF 属于急停
        if path == "/api/sequence" and payload.get("action") in ("stop", "pause"):
            return None
        if path == "/api/tune" and payload.get("action") == "off":
            return None
        if path == "/api/cmd" and re.match(
                r"^\s*(?:lock\s+off|unlock)\s*$", str(payload.get("cmd", "")), re.I):
            return None                       # 允许从终端解锁
        return {"ok": False, "locked": True,
                "error": "全局操作锁已启用，请先解锁（急停动作仍可用）"}

    def handle(self, path, payload):
        d = self.dev
        blocked = self._guard(path, payload)
        if blocked:
            d._log("WARN", blocked["error"] + "  [" + path + "]", "warn")
            return blocked
        if path == "/api/quit":
            return request_quit()
        if path == "/api/lock":
            return self.set_lock(bool(payload.get("on", True)))
        if path == "/api/ports":
            return {"ports": sp.list_ports(), "default": sp.default_port(),
                    "candidates": dv.BAUD_CANDIDATES}
        if path == "/api/state":
            seq_state = (self.sequence.state() if self.sequence
                         else {"status": "idle", "loop": 0, "step": 0,
                               "loops": self.loops, "steps": len(self.steps),
                               "elapsed": 0})
            seq_state["speed"] = self.speed
            steps = [s.as_dict() for s in self.steps]
            return dict(
                d.snapshot(), log=d.log_tail(120), trend=d.trend_tail(180),
                seq=seq_state, steps=steps,
                locked=bool(self.locked), tune=self.tuner.state(),
                rec={"recording": self.recorder.recording,
                     "rows": self.recorder.rows,
                     "path": self.recorder.path,
                     "preview": self.recorder.preview(4)},
            )
        if path == "/api/cmd":
            lines = cmdmod.execute(d, payload.get("cmd", ""))
            return {"lines": [[lv, tx] for lv, tx in lines]}
        if path == "/api/connect":
            auto = bool(payload.get("auto", True))
            port = payload.get("port") or sp.default_port()
            sim = bool(payload.get("sim")) or str(port).upper() == "SIM"
            use_pyserial = bool(payload.get("pyserial", False))
            parity = str(payload.get("parity", "N")).upper()[:1]
            try:
                addr = max(0, min(30, int(payload.get("address", d.address))))
            except (TypeError, ValueError):
                addr = d.address
            d.address = addr
            if payload.get("check_mode"):
                d.check_mode = str(payload["check_mode"])
            addrs = list(dict.fromkeys([addr, 0, 1, 255]))
            if auto or sim:
                r = d.auto_connect(port, sim=sim, use_pyserial=use_pyserial,
                                   parity=parity, addresses=addrs)
            else:
                try:
                    d.connect(port, int(payload.get("baud", dv.DEFAULT_BAUD)),
                              sim=sim, use_pyserial=use_pyserial,
                              parity=parity, address=addr)
                    ok, reply = d.handshake()
                    r = {"ok": True, "port": d.port_name, "baud": d.baud,
                         "idn": reply, "handshake": ok}
                except Exception as exc:
                    r = {"ok": False, "error": str(exc)}
            r["port"] = d.port_name if d.connected else port
            r["address"] = d.address
            return r
        if path == "/api/disconnect":
            d.disconnect()
            return {"ok": True}
        if path == "/api/apply":
            try:
                sent = d.apply(payload.get("v"), payload.get("i"))
                return {"ok": True, "sent": sent}
            except (dv.ValidationError, sp.SerialError, Exception) as exc:
                d._log("ERR", str(exc), "error")
                return {"ok": False, "error": str(exc)}
        if path == "/api/output":
            try:
                d.set_output(bool(payload.get("on")))
                return {"ok": True}
            except Exception as exc:
                return {"ok": False, "error": str(exc)}
        if path == "/api/preset":
            try:
                d.recall(int(payload.get("slot", 1)))
                return {"ok": True}
            except Exception as exc:
                return {"ok": False, "error": str(exc)}
        if path == "/api/record":
            action = payload.get("action")
            if action == "start":
                p = self.recorder.start(self.data_dir)
                d._log("SYS", "开始记录 CSV：" + p, "ok")
                return {"ok": True, "path": p}
            p = self.recorder.stop()
            d._log("SYS", "停止记录，共 %d 行：%s" % (self.recorder.rows, p), "ok")
            return {"ok": True, "path": p, "rows": self.recorder.rows}
        if path == "/api/sequence":
            action = payload.get("action", "status")
            if action == "start":
                if self.sequence and self.sequence.is_alive():
                    return {"ok": False, "error": "序列正在运行"}
                if not self.steps:
                    return {"ok": False, "error": "序列为空，请先添加步骤"}
                self.loops = max(1, min(99, int(payload.get("loops", self.loops))))
                self.speed = max(0.1, min(100.0,
                                          float(payload.get("speed", self.speed))))
                self.sequence = seqmod.SequenceRunner(
                    d, steps=self.steps, loops=self.loops, speed=self.speed,
                    on_event=self.on_seq_event,
                )
                self.sequence.start()
                return {"ok": True, "steps": len(self.steps)}
            if self.sequence:
                if action == "pause":
                    self.sequence.pause(True)
                elif action == "resume":
                    self.sequence.pause(False)
                elif action == "stop":
                    self.sequence.stop()
            return {"ok": True}
        if path == "/api/sequence/steps":
            if payload.get("reset"):
                self.steps = [recmod.SequenceStep(s.v, s.i, s.hold)
                              for s in recmod.DEFAULT_STEPS]
                return {"ok": True, "steps": [s.as_dict() for s in self.steps],
                        "message": "已恢复默认 4 步"}
            raw = payload.get("steps")
            if not isinstance(raw, list) or not raw:
                return {"ok": False, "error": "步骤列表不能为空"}
            if len(raw) > 64:
                return {"ok": False, "error": "步骤最多 64 步"}
            parsed = []
            for idx, item in enumerate(raw, 1):
                try:
                    v = float(item.get("v"))
                    i = float(item.get("i"))
                    hold = float(item.get("hold", 1.0))
                except (TypeError, ValueError):
                    return {"ok": False, "error": "第 %d 步参数不是数字" % idx}
                if hold <= 0 or hold > 86400:
                    return {"ok": False, "error": "第 %d 步保持时间须在 0–86400 s" % idx}
                try:
                    dv.validate(v, i)
                except dv.ValidationError as exc:
                    return {"ok": False, "error": "第 %d 步：%s" % (idx, exc)}
                parsed.append(recmod.SequenceStep(v, i, hold))
            self.steps = parsed
            self.loops = max(1, min(99, int(payload.get("loops", self.loops))))
            self.speed = max(0.1, min(100.0,
                                      float(payload.get("speed", self.speed))))
            total = sum(s.hold for s in self.steps) * self.loops / self.speed
            maxp = max(s.v * s.i for s in self.steps)
            d._log("SEQ", "步骤已更新：%d 步 · 总时长 %.0f s · 最大 %.1f W"
                   % (len(self.steps), total, maxp), "ok")
            return {"ok": True, "steps": [s.as_dict() for s in self.steps],
                    "message": "已保存 %d 步 · 总时长 %.0f s · 最大 %.1f W"
                               % (len(self.steps), total, maxp)}
        if path == "/api/tune":
            action = str(payload.get("action", "status")).lower()
            try:
                if action in ("set", "nudge", "auto", "off"):
                    self.tuner.configure(
                        target=payload.get("target"),
                        vmin=payload.get("vmin"), vmax=payload.get("vmax"),
                        step=payload.get("step"),
                        interval=payload.get("interval"),
                        jump_points=payload.get("jump_points"))
                if action == "set":
                    self.tuner.set_value(payload.get("value"))
                elif action == "nudge":
                    self.tuner.nudge(float(payload.get("delta", 0)))
                elif action == "auto":
                    self.tuner.start(payload.get("mode", "up"))
                elif action == "off":
                    self.tuner.stop()
                return {"ok": True, "tune": self.tuner.state()}
            except (dv.ValidationError, ValueError, TypeError) as exc:
                d._log("ERR", "微调拒绝：%s" % exc, "error")
                return {"ok": False, "error": str(exc),
                        "tune": self.tuner.state()}
        if path == "/api/help":
            return {"text": cmdmod.HELP_TEXT}
        return {"ok": False, "error": "未知接口 " + path}


APP = None
_SRV = None  # 当前 HTTPServer 实例，供退出接口 / 信号处理器使用
_quit_lock = threading.Lock()      # 保证退出只真正执行一次
_quitting = False                 # 退出是否已触发
_activity_lock = threading.Lock()  # 保护最近请求时间戳
_LAST_ACTIVITY = None             # 最近一次客户端请求时间（None=尚未有任何连接）


def _emergency_disconnect():
    """进程正常退出或被终止时的兜底：释放串口与后台轮询线程。"""
    try:
        if APP is not None:
            APP.dev.disconnect(quiet=True)
    except Exception:
        pass


def _sigterm_handler(signum, frame):
    # 收到 SIGTERM（如任务管理器软终止）时，先释放串口再强制退出本进程
    try:
        if APP is not None:
            APP.dev.disconnect(quiet=True)
    except Exception:
        pass
    os._exit(0)


def request_quit():
    """由 /api/quit 调用：从工作线程安全释放串口并终止本 pythonw 进程。

    退出顺序：停止自动微调与序列 → 断开串口（停轮询线程、关句柄、交还面板）
    → 关闭 HTTP 服务 → 最终由进程退出或兜底 os._exit 杀掉本进程。
    先让出 0.3 s 让本次 HTTP 应答写完，再执行关闭，避免与响应竞争。
    本函数幂等：关闭网页的 sendBeacon 与空闲看门狗可能各触发一次，
    但只真正退出一次。
    """
    global _quitting
    with _quit_lock:
        if _quitting:
            return {"ok": True, "quitting": True}
        _quitting = True
    def _run():
        time.sleep(0.3)
        try:
            if APP is not None:
                try:
                    APP.tuner.stop()
                except Exception:
                    pass
                try:
                    if APP.sequence is not None:
                        APP.sequence.stop()
                except Exception:
                    pass
                try:
                    APP.dev.disconnect(quiet=True)
                except Exception:
                    pass
        except Exception:
            pass
        if _SRV is not None:
            try:
                _SRV.shutdown()
            except Exception:
                pass
        # 兜底：若仍有非守护线程阻塞进程退出，1.2 s 后强制结束本 pythonw 进程
        threading.Timer(1.2, lambda: os._exit(0)).start()
    threading.Thread(target=_run, name="it6721-quit", daemon=True).start()
    return {"ok": True, "quitting": True}


def _mark_activity():
    """记录最近一次客户端请求时间；空闲看门狗据此判断网页是否已关闭。"""
    global _LAST_ACTIVITY
    with _activity_lock:
        _LAST_ACTIVITY = time.time()


def _idle_watchdog(timeout):
    """网页关闭 / 崩溃导致轮询停止超过 timeout 秒时，自动释放串口并退出。

    仅在所有客户端都失联（没有 /api 请求）后才触发；首次连接前的空闲
    不计时（_LAST_ACTIVITY 为 None），避免还没打开浏览器就被杀。
    """
    while True:
        time.sleep(2)
        if _quitting:
            return
        with _activity_lock:
            last = _LAST_ACTIVITY
        if last is None:
            continue
        if time.time() - last > timeout:
            request_quit()
            return


class Handler(BaseHTTPRequestHandler):
    server_version = "IT6721Console/2"

    def log_message(self, fmt, *args):
        pass  # 静默，控制台只留设备日志

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        _mark_activity()
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            body = HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/api/export.csv":
            p = APP.recorder.path if APP else ""
            if not p or not os.path.exists(p):
                return self._json({"error": "尚无 CSV 记录"}, 404)
            with open(p, "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition",
                             'attachment; filename="%s"' % os.path.basename(p))
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path.startswith("/api/"):
            try:
                return self._json(APP.handle(path, {}))
            except Exception as exc:
                return self._json({"error": str(exc)}, 500)
        self.send_error(404)

    def do_POST(self):
        _mark_activity()
        path = urlparse(self.path).path
        try:
            n = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            payload = {}
        try:
            return self._json(APP.handle(path, payload))
        except Exception as exc:
            return self._json({"error": "%s: %s" % (type(exc).__name__, exc)}, 500)


def _pids_on_port(port):
    """返回 LISTENING 状态占用本机 TCP 端口的进程 PID 列表。"""
    pids = set()
    try:
        if os.name == "nt":
            out = subprocess.run(["netstat", "-ano", "-p", "TCP"],
                                 capture_output=True, text=True,
                                 timeout=10).stdout
            for line in out.splitlines():
                cols = line.split()
                if len(cols) >= 5 and cols[-1].isdigit() and "LISTENING" in cols:
                    local = cols[1]
                    if (":%d" % port) in local:
                        pids.add(int(cols[-1]))
        else:
            try:
                out = subprocess.run(["lsof", "-ti", "tcp:%d" % port],
                                     capture_output=True, text=True,
                                     timeout=10).stdout
            except FileNotFoundError:
                out = subprocess.run(["fuser", "%d/tcp" % port],
                                     capture_output=True, text=True,
                                     timeout=10).stdout
            for tok in out.split():
                if tok.isdigit():
                    pids.add(int(tok))
    except Exception:
        pass
    return sorted(pids)


def _process_is_ours(pid):
    """仅当 PID 是本工具的 python/pythonw 进程才返回 True，避免误杀其它程序。"""
    if pid == os.getpid():
        return False
    try:
        if os.name == "nt":
            out = subprocess.run(
                ["tasklist", "/FI", "PID eq %d" % pid, "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=10).stdout
            low = out.lower()
            return "python.exe" in low or "pythonw.exe" in low
        with open("/proc/%d/comm" % pid) as f:
            return f.read().strip().lower().startswith("python")
    except Exception:
        return False


def claim_port(host="127.0.0.1", port=8765, max_retry=6):
    """单实例守卫：若端口被本工具的旧实例占用，先终止旧实例再放行绑定。

    用 connect 探测是否有进程正在监听（比 bind 可靠，不受 SO_REUSEADDR 影响），
    再用 netstat / lsof 定位监听者 PID，仅杀本工具的 python 进程。
    返回 True 表示端口已可绑定；返回 False 表示被非本工具进程占用、放弃接管。
    """
    for _ in range(max_retry):
        # 1) connect 探测：能否连上说明端口确实有人 LISTEN
        occupied = False
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1.0)
        try:
            s.connect((host, port))
            occupied = True
        except OSError:
            occupied = False
        finally:
            s.close()
        if not occupied:
            return True  # 端口空闲，可直接绑定
        # 2) 端口被占：找到监听者，仅杀占用该端口的自家 python 进程
        killed = False
        for pid in _pids_on_port(port):
            if _process_is_ours(pid):
                try:
                    if os.name == "nt":
                        subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                                      capture_output=True, timeout=10)
                    else:
                        os.kill(pid, signal.SIGTERM)
                    killed = True
                except Exception:
                    pass
        if not killed:
            return False  # 占用者不是自家进程，不冒险误杀
        time.sleep(0.8)
    return False


def serve(app, host="127.0.0.1", port=8765, open_browser=True):
    global APP, _SRV
    APP = app
    if not claim_port(host, port):
        print("  端口 %d 被其他程序占用，无法启动控制台。" % port)
        return 1
    srv = ThreadingHTTPServer((host, port), Handler)
    _SRV = srv
    # 进程退出 / 被终止时兜底释放串口（与 serve 的 finally 互为冗余保险）
    atexit.register(_emergency_disconnect)
    try:
        signal.signal(signal.SIGTERM, _sigterm_handler)
    except (ValueError, OSError, AttributeError):
        pass
    url = "http://%s:%d/" % (host, port)
    print("  控制台已启动：%s" % url)
    print("  仅监听本机回环地址；关闭浏览器窗口将自动释放串口并退出本程序。")
    if open_browser:
        t = threading.Timer(0.6, lambda: webbrowser.open(url))
        t.daemon = True
        t.start()
    # 空闲看门狗：网页关闭 / 崩溃导致轮询停止超过 15 s 时自动退出，
    # 与关闭网页时的 sendBeacon 互为冗余，确保 pythonw 进程与串口都被释放。
    threading.Thread(target=_idle_watchdog, args=(15,),
                    name="it6721-idle", daemon=True).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
        app.dev.disconnect(quiet=True)
    return 0
