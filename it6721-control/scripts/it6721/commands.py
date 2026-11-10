# -*- coding: utf-8 -*-
"""命令终端语法解析与执行。

大小写不敏感；= 号、单位后缀 V/A 可省略；; 分隔多命令。
多命令为「原子组」：先按当前设定值推演最终 V/I，过三重校验后才逐条下发；
任一项越限则整组拒绝，一条都不发到设备（对应设计稿的功率拦截示例）。
"""

import re

from . import device as dv

HELP_TEXT = """命令语法  /  SYNTAX  ------------------------------------------
  on / off                 输出开 / 关          → 21H 帧
  V 12   V=12V   v=24v     设电压              → 23H 帧（mV, u32）
  A 0.5  A=1.2   I 0.5     设电流              → 24H 帧（mA, u16）
  V?  A?  MEAS  READ       回读电压 / 电流 / 功率 → 26H 帧
  IDN / INFO / *IDN?       读型号、版本、序列号   → 31H 帧
  REMOTE / LOCAL           20H 进/退 PC 控制模式
  OVP 62                   设电压上限           → 22H 帧
  OCP 8.2                  仅记录本地阈值（协议无此命令字，请面板设置）
  RCL 1..4                 本机不支持远程调用预设（请面板按 A/B/C/D）
  LOCK ON / LOCK OFF       全局操作锁（前后端同时拦截写操作）
  CLS                      本机无对应命令字，仅本地提示
  BAUD 4800                记录串口速率（重连生效；手册固定 4800）
  HEX AA 00 26 ...         原始 26 字节帧直通（自动补零与校验）
  HELP   ?                 显示本帮助
  ; 分隔多命令              V 24 ; A 0.5 ; on
协议：26 字节二进制帧 / 4800 8N1，不是 SCPI。
安全：V/A 改动先过 0–60 V、0–8 A、P ≤ 180 W 三重校验；
      写设定前自动先发 20H 进 PC 控制模式；未知命令绝不下发。"""

_NUM = r"(\d+(?:\.\d+)?)"
_RE_OUT_ON = re.compile(r"^(?:on|outp\s*on|output\s*on)$", re.I)
_RE_OUT_OFF = re.compile(r"^(?:off|outp\s*off|output\s*off)$", re.I)
_RE_MEAS = re.compile(r"^(?:meas|measure|read|[vai]\?|[vaiu]\s*\?|p\?)$", re.I)
_RE_V = re.compile(r"^(?:v|u|volt|voltage)\s*[=:]?\s*" + _NUM + r"\s*v?$", re.I)
_RE_I = re.compile(r"^(?:a|i|amp|curr|current)\s*[=:]?\s*" + _NUM + r"\s*a?$", re.I)
_RE_OVP = re.compile(r"^ovp\s*[=:]?\s*" + _NUM + r"\s*v?$", re.I)
_RE_OCP = re.compile(r"^ocp\s*[=:]?\s*" + _NUM + r"\s*a?$", re.I)
_RE_RCL = re.compile(r"^(?:rcl|preset)\s*[=:]?\s*([1-4])$", re.I)
_RE_LOCK = re.compile(r"^lock(?:\s+(on|off))?$", re.I)
_RE_BAUD = re.compile(r"^baud\s*[=:]?\s*(\d+)$", re.I)
_RE_HELP = re.compile(r"^(?:help|h|\?)$", re.I)
_RE_CLS = re.compile(r"^(?:cls|clear)$", re.I)
_RE_HEX = re.compile(r"^(?:hex|frame)\s+", re.I)
_RE_INFO = re.compile(r"^(?:idn|info)$", re.I)
_RE_MODE = re.compile(r"^(remote|local)$", re.I)
# 直通里若直接改 V/I，仍然抽出数值做功率校验
_RE_SCPI_V = re.compile(r"^volt(?:age)?\s+(\d+(?:\.\d+)?)", re.I)
_RE_SCPI_I = re.compile(r"^curr(?:ent)?\s+(\d+(?:\.\d+)?)", re.I)


def parse_one(raw):
    s = raw.strip()
    if not s:
        return None
    if _RE_HELP.match(s):
        return {"kind": "help", "raw": s}
    if _RE_OUT_ON.match(s):
        return {"kind": "out_on", "raw": s}
    if _RE_OUT_OFF.match(s):
        return {"kind": "out_off", "raw": s}
    if _RE_MEAS.match(s):
        return {"kind": "measure", "raw": s}
    m = _RE_V.match(s)
    if m:
        return {"kind": "set_v", "value": float(m.group(1)), "raw": s}
    m = _RE_I.match(s)
    if m:
        return {"kind": "set_i", "value": float(m.group(1)), "raw": s}
    m = _RE_OVP.match(s)
    if m:
        return {"kind": "ovp", "value": float(m.group(1)), "raw": s}
    m = _RE_OCP.match(s)
    if m:
        return {"kind": "ocp", "value": float(m.group(1)), "raw": s}
    m = _RE_RCL.match(s)
    if m:
        return {"kind": "rcl", "value": int(m.group(1)), "raw": s}
    m = _RE_LOCK.match(s)
    if m:
        return {"kind": "lock", "value": (m.group(1) or "on").lower(), "raw": s}
    m = _RE_BAUD.match(s)
    if m:
        return {"kind": "baud", "value": int(m.group(1)), "raw": s}
    if _RE_CLS.match(s):
        return {"kind": "cls", "raw": s}
    if _RE_INFO.match(s):
        return {"kind": "scpi", "raw": "*IDN?"}
    m = _RE_MODE.match(s)
    if m:
        return {"kind": "scpi",
                "raw": "SYST:REM" if m.group(1).upper() == "REMOTE" else "SYST:LOC"}
    if _RE_HEX.match(s) or ":" in s or s.startswith("*"):
        return {"kind": "scpi", "raw": s}
    return {"kind": "unknown", "raw": s}


def parse(line):
    return [a for a in (parse_one(p) for p in line.split(";")) if a]


def execute(dev, line):
    """执行一行命令，返回 [(level, text), ...] 供终端回显。"""
    line = (line or "").strip()
    if not line:
        return []
    dev._log("CMD", "> " + line)
    out = [("info", "> " + line)]
    actions = parse(line)

    unknown = [a["raw"] for a in actions if a["kind"] == "unknown"]
    if unknown:
        msg = "未知命令：%s（只回显，不下发设备；输入 HELP 查看语法）" % ", ".join(unknown)
        dev._log("ERR", msg, "error")
        return out + [("error", "ERR   " + msg)]

    # ---- 原子校验：推演整组执行后的最终 V / I ----
    v, i = dev.set_v, dev.set_i
    for a in actions:
        if a["kind"] == "set_v":
            v = a["value"]
        elif a["kind"] == "set_i":
            i = a["value"]
        elif a["kind"] == "scpi":
            mv = _RE_SCPI_V.match(a["raw"])
            mi = _RE_SCPI_I.match(a["raw"])
            if mv:
                v = float(mv.group(1))
            if mi:
                i = float(mi.group(1))
    try:
        p = dv.validate(v, i)
    except dv.ValidationError as exc:
        dev._log("ERR", str(exc), "error")
        return out + [("error", "ERR   " + str(exc))]

    # ---- 校验通过才真正下发：设定统一先行（安全顺序），再执行其余动作 ----
    setters = [a for a in actions if a["kind"] in ("set_v", "set_i")]
    if setters:
        for sent in dev.apply_step(v, i):
            out.append(("ok", "TX    %-28s P=%.1f W  [PASS]" % (sent, p)))

    for a in (x for x in actions if x["kind"] not in ("set_v", "set_i")):
        kind = a["kind"]
        try:
            if kind == "help":
                for ln in HELP_TEXT.splitlines():
                    out.append(("info", ln))
            elif kind == "out_on":
                out.append(("ok", "TX    %-28s 输出开启" % dev.output_on()))
            elif kind == "out_off":
                out.append(("ok", "TX    %-28s 输出关闭" % dev.output_off()))
            elif kind == "measure":
                dev.read_measure()
                out.append(("ok", "RX    V=%.3f V   I=%.4f A   P=%.3f W   %s   OUT=%s"
                            % (dev.v, dev.i, dev.p, dev.mode,
                               "ON" if dev.output else "OFF")))
            elif kind == "ovp":
                out.append(("ok", "TX    %s" % dev.set_ovp(a["value"])))
            elif kind == "ocp":
                out.append(("ok", "TX    %s" % dev.set_ocp(a["value"])))
            elif kind == "rcl":
                out.append(("ok", "TX    %s" % dev.recall(a["value"])))
            elif kind == "lock":
                fn = getattr(dev, "lock_fn", None)
                on = a["value"] == "on"
                if fn is None:
                    out.append(("warn", "LOCK  当前会话无全局锁控制器（CLI 模式），未生效"))
                else:
                    fn(on)
                    out.append(("ok", "LOCK  全局操作锁已%s" % ("启用" if on else "解除")))
            elif kind == "cls":
                dev._log("SYS", "CLS：本机无状态清除命令字，已清空上位机侧日志显示", "warn")
                out.append(("warn", "CLS   本机无对应命令字，未下发"))
            elif kind == "baud":
                out.append(("warn", "BAUD  %d 已记录；手册固定 4800，请用「重新连接」使其生效"
                            % a["value"]))
            elif kind == "scpi":
                is_raw = a["raw"].upper().startswith(("HEX", "FRAME"))
                reply = dev.query(a["raw"]) if is_raw else dev.transact(a["raw"])
                if is_raw or "?" in a["raw"]:
                    out.append(("ok", "RX    %s" % (reply or "(无应答)")))
                else:
                    out.append(("ok", "TX    %s   [26B 帧]" % a["raw"]))
        except Exception as exc:
            msg = "%s：%s" % (type(exc).__name__, exc)
            dev._log("ERR", msg, "error")
            out.append(("error", "ERR   " + msg))
    return out
