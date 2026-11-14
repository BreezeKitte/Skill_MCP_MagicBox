# -*- coding: utf-8 -*-
# Copyright (c) 2026 BreezeKitte
# SPDX-License-Identifier: MIT
"""IT6721 程控电源 MCP 服务（stdio，纯标准库实现）。

通过 Model Context Protocol 把 IT6721 上位机能力暴露给任意支持 MCP 的客户端
（WorkBuddy 及其他 Agent）。零第三方依赖：仅用 Python 标准库实现 stdio 上的
JSON-RPC 2.0（newline-delimited），不引入 mcp PyPI 包，契合 IT6721_Console
「零依赖」工程约定。

控制面直接复用 it6721 包的设备层与引擎：
  - IT6721      设备会话（26 字节帧收发 / 功率校验 / 轮询）
  - AutoTuner   自动微调（递增 / 递减 / 往复 / 跳跃）
  - SequenceRunner  序列扫描
  - Recorder    CSV 记录
并保留原上位机的「全局锁」安全门：锁定时拦截所有写接口，仅放行
急停（输出 OFF / 自动停止 / 序列停止 / 解锁）。

安全约束（继承自 it6721 包，不可绕过）：
  - 电压 0–60 V、电流 0–8 A、功率 ≤180 W 三重校验；
  - 写设定前必须先发 20H 进入 PC 远程模式并获得应答，否则中止写入；
  - 真实硬件连接前，必须先拿到 ≥4/5 次合法 26 字节应答帧才允许下发写帧。

it6721 包默认随本仓库内嵌于 `scripts/it6721/`，开箱即用、无需任何环境变量；
亦可通过 IT6721_PACKAGE_DIR（直接指向 it6721/ 包目录）或 IT6721_CONSOLE_DIR
（指向含 it6721/ 的 IT6721_Console 仓库根）覆盖（详见仓库 README）。
"""

import atexit
import json
import os
import sys
import threading

# --------------------------------------------------------------------------
# 路径装配：定位 it6721 包与数据落盘目录
#
#   it6721 包发现优先级（从高到低）：
#     1) IT6721_PACKAGE_DIR —— 直接指向 it6721/ 包目录（最显式，便于指向自有副本）
#     2) IT6721_CONSOLE_DIR —— 兼容旧用法，指向含 it6721/ 的 IT6721_Console 仓库根
#     3) 内嵌副本           —— scripts/it6721/（随本仓库分发，开箱即用，无需环境变量）
# --------------------------------------------------------------------------
_PKG_DIR = os.environ.get("IT6721_PACKAGE_DIR")
if not _PKG_DIR:
    _console = os.environ.get("IT6721_CONSOLE_DIR")
    _PKG_DIR = (os.path.join(_console, "it6721") if _console
                else os.path.join(os.path.dirname(os.path.abspath(__file__)), "it6721"))
if os.path.isdir(_PKG_DIR) and _PKG_DIR not in sys.path:
    sys.path.insert(0, _PKG_DIR)

import it6721  # noqa: E402
from it6721 import device as dv  # noqa: E402
from it6721 import serial_port as sp  # noqa: E402
from it6721 import autotune as at  # noqa: E402
from it6721 import sequence as sq  # noqa: E402
from it6721 import recorder as rc  # noqa: E402

# --------------------------------------------------------------------------
# 数据目录：记录 CSV 的落盘位置
#   优先级：IT6721_DATA_DIR > IT6721_CONSOLE_DIR/data（旧兼容）> ~/it6721_recordings
# --------------------------------------------------------------------------
_DATA_DIR = os.environ.get("IT6721_DATA_DIR")
if not _DATA_DIR:
    _console = os.environ.get("IT6721_CONSOLE_DIR")
    _DATA_DIR = (os.path.join(_console, "data") if _console
                 else os.path.expanduser(os.path.join("~", "it6721_recordings")))
os.makedirs(_DATA_DIR, exist_ok=True)

# --------------------------------------------------------------------------
# 单例：整个 MCP 进程共享一个设备会话 / 微调引擎 / 记录器
# --------------------------------------------------------------------------
_LOCK = {"on": False}
_DEV = dv.IT6721()
_REC = rc.Recorder(_DATA_DIR)
_DEV.on_sample = _REC.on_sample                       # 轮询结果驱动 CSV 记录
_TUNE = at.AutoTuner(_DEV, can_write=lambda: not _LOCK["on"])
_SEQ = {"obj": None, "steps": list(rc.DEFAULT_STEPS),
        "loops": 1, "speed": 1.0}


def _log_event(text, level="info"):
    """把引擎事件写进设备内部日志（不输出到 stdout，避免污染 MCP 协议流）。"""
    _DEV._log("MCP", text, level)


# ==========================================================================
# 工具实现：每个函数接收 arguments(dict)，返回可 JSON 序列化的结果 dict
# ==========================================================================
def t_list_ports(args):
    return {"ports": sp.list_ports(), "default": sp.default_port(),
            "candidates": dv.BAUD_CANDIDATES}


def t_connect(args):
    port = args.get("port") or sp.default_port()
    sim = bool(args.get("sim")) or str(port).upper() == "SIM"
    baud = int(args.get("baud", dv.DEFAULT_BAUD))
    address = int(args.get("address", dv.DEFAULT_ADDRESS))
    parity = str(args.get("parity", "N")).upper()[:1]
    stop_bits = int(args.get("stop_bits", 1))
    handshake = not bool(args.get("no_handshake", False))
    check_mode = str(args.get("check_mode", "sum8")).lower()
    auto = bool(args.get("auto", True)) and not sim
    if auto:
        r = _DEV.auto_connect(port, baud=baud, address=address, parity=parity,
                              stop_bits=stop_bits, handshake=handshake,
                              check_mode=check_mode)
        return r
    _DEV.connect(port, baud, sim=sim, parity=parity, stop_bits=stop_bits,
                 handshake=handshake, address=address, check_mode=check_mode)
    ok, reply = _DEV.handshake()
    return {"ok": ok, "idn": reply, "sim": sim, "port": _DEV.port_name,
            "remote": _DEV.remote_ok}


def t_disconnect(args):
    _DEV.disconnect()
    return {"ok": True, "connected": False}


def _seq_state():
    st = _SEQ["obj"].state() if _SEQ["obj"] else {"status": "idle"}
    st.update({"loops": _SEQ["loops"], "speed": _SEQ["speed"],
               "steps": len(_SEQ["steps"])})
    return st


def t_status(args):
    return dict(_DEV.snapshot(), tune=_TUNE.state(), seq=_seq_state(),
                rec={"recording": _REC.recording, "rows": _REC.rows,
                     "path": _REC.path, "preview": _REC.preview(4)},
                locked=bool(_LOCK["on"]))


def t_set_voltage(args):
    _DEV.set_voltage(float(args["v"]))
    return _DEV.snapshot()


def t_set_current(args):
    _DEV.set_current(float(args["i"]))
    return _DEV.snapshot()


def t_apply(args):
    _DEV.apply(v=args.get("v"), i=args.get("i"))
    return _DEV.snapshot()


def t_output(args):
    _DEV.set_output(bool(args.get("on")))
    return _DEV.snapshot()


def t_autotune_configure(args):
    _TUNE.configure(
        target=args.get("target"),
        value=args.get("value"),
        vmin=args.get("min"),
        vmax=args.get("max"),
        step=args.get("step"),
        interval=args.get("interval"),
        jump_points=args.get("jump_points"),
    )
    return _TUNE.state()


def t_autotune_set(args):
    _TUNE.set_value(float(args["value"]))
    return _TUNE.state()


def t_autotune_nudge(args):
    _TUNE.nudge(float(args.get("delta", 0)))
    return _TUNE.state()


def t_autotune_start(args):
    _TUNE.start(str(args.get("mode", "up")))
    return _TUNE.state()


def t_autotune_stop(args):
    _TUNE.stop("手动停止")
    return _TUNE.state()


def t_sequence_set_steps(args):
    raw = args.get("steps") or []
    steps = [rc.SequenceStep(float(s["v"]), float(s["i"]), float(s.get("hold", 1.0)))
             for s in raw]
    for s in steps:
        dv.validate(s.v, s.i)            # 任一越限立即抛 ValidationError
    _SEQ["steps"] = steps
    return {"ok": True, "count": len(steps),
            "steps": [s.as_dict() for s in steps]}


def t_sequence_run(args):
    if _SEQ["obj"] and _SEQ["obj"].is_alive():
        _SEQ["obj"].stop()
    _SEQ["loops"] = max(1, int(args.get("loops", _SEQ["loops"])))
    _SEQ["speed"] = max(0.1, float(args.get("speed", _SEQ["speed"])))
    runner = sq.SequenceRunner(_DEV, steps=_SEQ["steps"], loops=_SEQ["loops"],
                               speed=_SEQ["speed"], on_event=_log_event)
    _SEQ["obj"] = runner
    runner.start()
    return {"ok": True, "status": "running"}


def t_sequence_stop(args):
    if _SEQ["obj"]:
        _SEQ["obj"].stop()
    return _seq_state()


def t_sequence_pause(args):
    if _SEQ["obj"]:
        _SEQ["obj"].pause(bool(args.get("on", True)))
    return _seq_state()


def t_record_start(args):
    path = _REC.start(_DATA_DIR)
    return {"ok": True, "path": path}


def t_record_stop(args):
    path = _REC.stop()
    return {"ok": True, "path": path, "rows": _REC.rows}


def t_set_lock(args):
    _LOCK["on"] = bool(args.get("on", True))
    if _LOCK["on"]:
        _TUNE.stop("全局锁定")
    return {"ok": True, "locked": _LOCK["on"]}


# 写类工具名（锁定时会被拦截）；急停/只读/解锁工具在 guard 里单独放行
_WRITE_TOOLS = {
    "it6721_set_voltage", "it6721_set_current", "it6721_apply",
    "it6721_output", "it6721_autotune_configure", "it6721_autotune_set",
    "it6721_autotune_nudge", "it6721_autotune_start",
    "it6721_sequence_set_steps", "it6721_sequence_run",
    "it6721_sequence_pause", "it6721_record_start",
}


def _guard_allows(name, args):
    """全局锁开启时，仅放行安全/急停动作与解锁本身。"""
    if not _LOCK["on"]:
        return True
    if name in ("it6721_set_lock", "it6721_disconnect", "it6721_status",
                "it6721_list_ports", "it6721_connect"):
        return True
    if name == "it6721_output" and not bool(args.get("on", True)):
        return True
    if name in ("it6721_autotune_stop", "it6721_sequence_stop",
                "it6721_record_stop"):
        return True
    return False


DISPATCH = {
    "it6721_list_ports": t_list_ports,
    "it6721_connect": t_connect,
    "it6721_disconnect": t_disconnect,
    "it6721_status": t_status,
    "it6721_set_voltage": t_set_voltage,
    "it6721_set_current": t_set_current,
    "it6721_apply": t_apply,
    "it6721_output": t_output,
    "it6721_autotune_configure": t_autotune_configure,
    "it6721_autotune_set": t_autotune_set,
    "it6721_autotune_nudge": t_autotune_nudge,
    "it6721_autotune_start": t_autotune_start,
    "it6721_autotune_stop": t_autotune_stop,
    "it6721_sequence_set_steps": t_sequence_set_steps,
    "it6721_sequence_run": t_sequence_run,
    "it6721_sequence_stop": t_sequence_stop,
    "it6721_sequence_pause": t_sequence_pause,
    "it6721_record_start": t_record_start,
    "it6721_record_stop": t_record_stop,
    "it6721_set_lock": t_set_lock,
}


# ==========================================================================
# 工具清单（MCP tools/list 返回，含 JSON Schema 输入定义）
# ==========================================================================
TOOLS = [
    {"name": "it6721_list_ports",
     "description": "枚举本机可用串口（不打开、不占用），返回端口列表、默认猜测端口与候选波特率。只读、安全。",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "it6721_connect",
     "description": "连接 IT6721。可传 port（如 COM6 / /dev/ttyUSB0 / SIM）；sim=true 进入内置模拟设备。auto 默认 true：逐档自适应握手（4800 8N1，仅发 31H 只读帧），命中后才可下发写帧。",
     "inputSchema": {"type": "object", "properties": {
         "port": {"type": "string", "description": "串口名或 SIM；缺省自动选 USB 串口"},
         "sim": {"type": "boolean", "description": "使用内置模拟设备，不碰真实硬件"},
         "baud": {"type": "integer", "description": "波特率，手册固定 4800"},
         "auto": {"type": "boolean", "description": "true=自适应握手；false=按 baud 直连"},
         "address": {"type": "integer", "description": "设备地址 0–30，出厂 0"},
         "parity": {"type": "string", "enum": ["N", "O", "E"]},
         "stop_bits": {"type": "integer", "enum": [1, 2]},
         "no_handshake": {"type": "boolean", "description": "关闭 DTR/RTS"},
         "check_mode": {"type": "string", "enum": ["sum8", "xor", "neg"],
                        "description": "帧校验算法"}}}},
    {"name": "it6721_disconnect",
     "description": "断开并释放串口（尽力交还前面板控制权）。安全。",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "it6721_status",
     "description": "读回完整状态：实时 V/I/P、设定值、输出开关、远程模式、链接状态、自动微调状态、序列状态、CSV 记录状态、全局锁状态。只读。",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "it6721_set_voltage",
     "description": "设定电压（V）。经 0–60V / 功率校验；写前需进入 PC 远程模式。全局锁开启时拒绝。",
     "inputSchema": {"type": "object", "properties": {
         "v": {"type": "number", "minimum": 0, "maximum": 60,
               "description": "目标电压 0–60 V"}}}},
    {"name": "it6721_set_current",
     "description": "设定电流（A）。经 0–8A / 功率校验。全局锁开启时拒绝。",
     "inputSchema": {"type": "object", "properties": {
         "i": {"type": "number", "minimum": 0, "maximum": 8,
               "description": "目标电流 0–8 A"}}}},
    {"name": "it6721_apply",
     "description": "原子设定电压与电流（可只给其一）。先整体校验最终态再按安全顺序下发，避免中间态功率越限。全局锁开启时拒绝。",
     "inputSchema": {"type": "object", "properties": {
         "v": {"type": "number", "minimum": 0, "maximum": 60},
         "i": {"type": "number", "minimum": 0, "maximum": 8}}}},
    {"name": "it6721_output",
     "description": "开关电源输出（OUTP ON/OFF）。急停（on=false）在全局锁下仍放行；开输出会被全局锁拦截。",
     "inputSchema": {"type": "object", "properties": {
         "on": {"type": "boolean", "description": "true=输出开，false=输出关（急停）"}}}},
    {"name": "it6721_autotune_configure",
     "description": "配置自动微调：对象 V/I、起点/上下限/步进/间隔、跳跃点序列。全局锁开启时拒绝。",
     "inputSchema": {"type": "object", "properties": {
         "target": {"type": "string", "enum": ["V", "I"]},
         "value": {"type": "number"}, "min": {"type": "number"},
         "max": {"type": "number"}, "step": {"type": "number"},
         "interval": {"type": "number", "description": "每步间隔秒，最小 0.1"},
         "jump_points": {"type": "array", "items": {"type": "number"},
                         "description": "跳跃模式目标值序列，如 [2,5,9,16,8]"}}}},
    {"name": "it6721_autotune_set",
     "description": "手动设定自动微调对象到指定值（单步）。全局锁开启时拒绝。",
     "inputSchema": {"type": "object", "properties": {
         "value": {"type": "number"}}}},
    {"name": "it6721_autotune_nudge",
     "description": "相对当前值微调 ±delta（单步）。全局锁开启时拒绝。",
     "inputSchema": {"type": "object", "properties": {
         "delta": {"type": "number"}}}},
    {"name": "it6721_autotune_start",
     "description": "启动自动扫描：off/up(递增)/down(递减)/pingpong(往复)/jump(跳跃)。全局锁开启时拒绝。",
     "inputSchema": {"type": "object", "properties": {
         "mode": {"type": "string", "enum": ["off", "up", "down", "pingpong", "jump"]}}}},
    {"name": "it6721_autotune_stop",
     "description": "停止自动扫描（急停类，全局锁下仍放行）。",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "it6721_sequence_set_steps",
     "description": "设置序列步骤，每项 {v, i, hold(秒)}。全部经功率校验，任一越限即报错。全局锁开启时拒绝。",
     "inputSchema": {"type": "object", "properties": {
         "steps": {"type": "array", "items": {
             "type": "object",
             "properties": {"v": {"type": "number"}, "i": {"type": "number"},
                            "hold": {"type": "number"}}}}}}},
    {"name": "it6721_sequence_run",
     "description": "按当前步骤运行序列（可指定 loops / speed）。全局锁开启时拒绝。",
     "inputSchema": {"type": "object", "properties": {
         "loops": {"type": "integer", "minimum": 1},
         "speed": {"type": "number", "minimum": 0.1}}}},
    {"name": "it6721_sequence_stop",
     "description": "停止序列（急停类，全局锁下仍放行）。",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "it6721_sequence_pause",
     "description": "暂停/恢复序列。全局锁开启时拒绝（仅停止放行）。",
     "inputSchema": {"type": "object", "properties": {
         "on": {"type": "boolean", "description": "true=暂停，false=恢复"}}}},
    {"name": "it6721_record_start",
     "description": "开始把轮询结果写 CSV。全局锁开启时拒绝。",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "it6721_record_stop",
     "description": "停止记录并返回路径与行数（全局锁下仍放行）。",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "it6721_set_lock",
     "description": "设置全局操作锁。on=true 时拦截所有写接口（急停除外）；on=false 解锁。始终放行。建议：真机操作前先 it6721_set_lock on:true 防误改。",
     "inputSchema": {"type": "object", "properties": {
         "on": {"type": "boolean", "description": "true=锁定，false=解锁"}}}},
]


# ==========================================================================
# 协议层：stdio + JSON-RPC 2.0（newline-delimited）
# ==========================================================================
def _make_result(rid, result):
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def _make_error(rid, code, message):
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def _handle_tool_call(rid, params):
    name = params.get("name", "")
    args = params.get("arguments") or {}
    if name not in DISPATCH:
        return _make_error(rid, -32602, "Unknown tool: %s" % name)
    if not _guard_allows(name, args):
        return _make_result(rid, {"content": [{"type": "text", "text": json.dumps(
            {"error": "全局操作锁已启用，请先 it6721_set_lock on:false 解锁（急停动作仍可用）",
             "locked": True}, ensure_ascii=False)}], "isError": True})
    try:
        data = DISPATCH[name](args)
    except Exception as exc:   # noqa: BLE001 — 把任意异常转成 isError，不让进程崩
        return _make_result(rid, {"content": [{"type": "text", "text": json.dumps(
            {"error": str(exc), "type": type(exc).__name__}, ensure_ascii=False)}],
            "isError": True})
    return _make_result(rid, {"content": [{"type": "text", "text": json.dumps(
        data, ensure_ascii=False, default=str)}]})


def handle_message(msg):
    """返回响应 dict；若是通知（无 id）返回 None。"""
    if not isinstance(msg, dict):
        return None
    method = msg.get("method")
    rid = msg.get("id")
    params = msg.get("params") or {}

    if method == "initialize":
        return _make_result(rid, {
            "protocolVersion": params.get("protocolVersion") or "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "it6721-control", "version": "1.1.0"}})
    if method == "notifications/initialized":
        return None
    if method == "ping":
        return _make_result(rid, {})
    if method == "tools/list":
        return _make_result(rid, {"tools": TOOLS})
    if method == "tools/call":
        return _handle_tool_call(rid, params)
    if method in ("resources/list", "prompts/list"):
        return _make_result(rid, {"resources": []} if method == "resources/list"
                            else {"prompts": []})
    if rid is not None:
        return _make_error(rid, -32601, "Method not found: %s" % method)
    return None


def _cleanup():
    try:
        _DEV.disconnect(quiet=True)
    except Exception:
        pass


def main():
    # 强制 UTF-8，避免 Windows 默认 GBK 把含中文的 JSON 写坏
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stdin.reconfigure(encoding="utf-8")
    except Exception:
        pass
    atexit.register(_cleanup)
    out = sys.stdout
    for raw in sys.stdin:                 # 父进程关闭 stdin → EOF → 退出
        line = raw.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            continue
        resp = handle_message(msg)
        if resp is not None:
            out.write(json.dumps(resp, ensure_ascii=False) + "\n")
            out.flush()
    _cleanup()


if __name__ == "__main__":
    main()
