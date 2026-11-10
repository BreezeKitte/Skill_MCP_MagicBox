# -*- coding: utf-8 -*-
"""ITECH IT6720 / IT6721 专有 26 字节串口协议（纯标准库，无第三方依赖）。

依据
----
* 《IT6720 系列编程与语法指南》V1.0（ITECH，2023-11-03）第 1~3 章
* 《IT6720 Series User's Manual》V3.0 第 1.3 节（4800 / 8N1 / TTL 电平）
* 同族 26 字节协议公开帧例（remote ON = AA 00 20 01 00…00 CB，
  应答 AA 00 12 80 00…00 3C）用于反推校验算法

重要：IT6721 **不是 SCPI 设备**。它属于 IT6720 经济型数控系列，说的是
固定 26 字节二进制帧；带 SYST SET → COMM 菜单、说 SCPI 的是 IT6700H
高压系列，两者手册不能混用。

帧结构（收发同构，固定 26 字节）::

    [0]    0xAA                 同步头
    [1]    0x00~0x1E / 0xFF    电源地址（0xFF 为广播）
    [2]    命令字               见 CMD_*
    [3..24] 数据 22 字节，未用补 0
    [25]   校验码 = sum(frame[0:25]) & 0xFF

数值单位：电压 u32 小端、1 mV/LSB（16.000 V = 0x00003E80）；
电流 u16 小端、1 mA/LSB（1.000 A = 0x03E8）。
"""

import re
import struct

FRAME_LEN = 26
SYNC = 0xAA
BROADCAST = 0xFF
ADDR_MAX = 0x1E

CMD_STATUS = 0x12       # 设备返回的操作结果（校验/执行状态）
CMD_MODE = 0x20         # 0=面板操作  1=PC 远程操作（写设定前必须先发）
CMD_OUTPUT = 0x21       # 0=输出 OFF  1=输出 ON
CMD_MAX_V = 0x22        # 电压上限（u32, mV）
CMD_SET_V = 0x23        # 设定输出电压（u32, mV）
CMD_SET_I = 0x24        # 设定输出电流（u16, mA）
CMD_SET_ADDR = 0x25     # 修改设备地址
CMD_READ = 0x26         # 读实际/设定值及状态
CMD_INFO = 0x31         # 读型号、软件版本、序列号
CMD_RESET_CAL = 0x32    # 恢复出厂校准数据（本程序不会发送）

STATUS_OK = 0x80
STATUS_BAD_CHECKSUM = 0x90
STATUS_BAD_PARAM = 0xA0
STATUS_CANT_EXEC = 0xB0
STATUS_INVALID = 0xC0
STATUS_TEXT = {
    STATUS_OK: "OK",
    STATUS_BAD_CHECKSUM: "校验和错误(90H)",
    STATUS_BAD_PARAM: "参数错误/溢出(A0H)",
    STATUS_CANT_EXEC: "命令不能执行(B0H)",
    STATUS_INVALID: "无效命令(C0H)",
}

_NUM = re.compile(r"[-+]?\d*\.?\d+")
_V_RE = re.compile(r"^VOLT(?:AGE)?\s+([-+]?\d*\.?\d+)", re.I)
_I_RE = re.compile(r"^CURR(?:ENT)?\s+([-+]?\d*\.?\d+)", re.I)
_MV_RE = re.compile(r"^VOLT:PROT\s+([-+]?\d*\.?\d+)", re.I)


def checksum(frame25, mode="sum8"):
    """由前 25 字节算第 26 字节校验码。

    sum8（默认）：sum & 0xFF。已用同族协议两个公开帧例验算：
      AA 00 20 01 → 0xAA+0x00+0x20+0x01 = 0xCB ✓
      AA 00 12 80 → 0xAA+0x00+0x12+0x80 = 0x13C → 0x3C ✓
    IT6720 手册只写「第二十六字节 校验码」，未给公式，故保留其它候选
    供真机诊断时比对；若真机对 sum8 应答 90H，可切换模式复核。
    """
    data = bytes(frame25)[:25]
    if mode == "xor":
        r = 0
        for b in data:
            r ^= b
        return r & 0xFF
    if mode == "neg":
        return (~sum(data)) & 0xFF
    return sum(data) & 0xFF


def build(addr, cmd, data=b"", check_mode="sum8"):
    """构造 26 字节帧。data 为 [3..24] 的内容，不足补 0，多余截断。"""
    body = bytearray([SYNC, int(addr) & 0xFF, int(cmd) & 0xFF])
    body += bytes(data)[:22]
    body += b"\x00" * (25 - len(body))
    body.append(checksum(body, check_mode))
    return bytes(body)


def parse(frame, verify=True, check_mode="sum8"):
    """解析 26 字节应答帧。失败返回 None，不抛异常。"""
    b = bytes(frame)
    if len(b) != FRAME_LEN or b[0] != SYNC:
        return None
    if verify and checksum(b[:25], check_mode) != b[25]:
        return None
    return {
        "addr": b[1],
        "cmd": b[2],
        "data": b[3:25],
        "check": b[25],
        "raw": b,
    }


def frame_hex(frame):
    return " ".join("%02X" % c for c in bytes(frame))


def enc_voltage(v):
    """V → u32 小端，单位 mV。"""
    return struct.pack("<I", max(0, int(round(float(v) * 1000))))


def enc_current(i):
    """A → u16 小端，单位 mA（最大 8000 mA，正好落在 u16 内）。"""
    return struct.pack("<H", max(0, min(65535, int(round(float(i) * 1000)))))


def dec_u32(b):
    return struct.unpack("<I", bytes(b[:4]) + b"\x00" * (4 - len(b[:4])))[0]


def dec_u16(b):
    return struct.unpack("<H", bytes(b[:2]) + b"\x00" * (2 - len(b[:2])))[0]


def decode_state(data):
    """解析 26H 读回帧的 22 字节数据区（手册第三章第 7 节）。

    手册按「帧的第 N 字节」编号，data[0] 就是帧的第 4 字节：
      data[0:2]  实际电流 u16    data[2:6]  实际电压 u32
      data[6]    状态字节        data[7:9]  设定电流 u16
      data[9:13] 最大电压 u32    data[13:17] 设定电压 u32
      data[17:22] 系统保留
    """
    d = bytes(data)[:22] + b"\x00" * max(0, 22 - len(data))
    st = d[6]
    mode_bits = (st >> 2) & 0x03
    return {
        "actual_i": dec_u16(d[0:2]) / 1000.0,
        "actual_v": dec_u32(d[2:6]) / 1000.0,
        "output": bool(st & 0x01),
        "otp": bool(st & 0x02),
        "mode": "CC" if mode_bits == 2 else ("CV" if mode_bits == 1 else "--"),
        "fan": (st >> 4) & 0x07,
        "remote": bool(st & 0x80),
        "set_i": dec_u16(d[7:9]) / 1000.0,
        "max_v": dec_u32(d[9:13]) / 1000.0,
        "set_v": dec_u32(d[13:17]) / 1000.0,
        "status_byte": st,
    }


def encode_state(set_v=0.0, set_i=0.0, actual_v=0.0, actual_i=0.0,
                 output=False, remote=True, mode="CV"):
    """模拟设备用：构造 26H 应答的数据区（与 decode_state 镜像）。"""
    st = (0x01 if output else 0) | (0x80 if remote else 0)
    st |= (0x08 if mode == "CC" else 0x04)
    d = bytearray(22)
    d[0:2] = enc_current(actual_i)
    d[2:6] = enc_voltage(actual_v)
    d[6] = st
    d[7:9] = enc_current(set_i)
    d[9:13] = enc_voltage(62.0)
    d[13:17] = enc_voltage(set_v)
    return bytes(d)


def decode_info(data):
    """解析 31H 型号/版本/序列号帧的数据区（手册第三章第 8 节）。

    data[0:5] 型号 ASCII / data[5] 版本低(BCD) / data[6] 版本高(BCD)
    data[7:17] 序列号后 10 位 ASCII / data[17:22] 保留
    """
    d = bytes(data)[:22] + b"\x00" * max(0, 22 - len(data))
    model = "".join(chr(c) for c in d[0:5] if 32 <= c < 127)
    ver = "%02X.%02X" % (d[6], d[5])
    serial = "".join(chr(c) for c in d[7:17] if 32 <= c < 127)
    # 型号字段只有 5 字节，面板可能回 "IT672" 之类的截断串；上位机服务的
    # 机型是确定的 IT6721，握手串按完整型号输出，原始字段保留在 model 里。
    return {"model": model, "version": ver, "serial": serial,
            "text": ("ITECH,IT6721,%s,V%s" % (serial or "000000", ver))}


def encode_info(model="IT672", version=(1, 0), serial="0000000000"):
    """模拟设备用：构造 31H 应答的数据区。"""
    d = bytearray(22)
    m = model.encode("ascii", "replace")[:5]
    d[0:len(m)] = m
    d[5] = int(version[1]) & 0xFF
    d[6] = int(version[0]) & 0xFF
    s = serial.encode("ascii", "replace")[:10]
    d[7:7 + len(s)] = s
    return bytes(d)


def status_text(frame):
    """把 12H 状态帧的第 4 字节翻成中文。"""
    r = parse(frame)
    if not r or r["cmd"] != CMD_STATUS:
        return ""
    code = r["data"][0] if r["data"] else 0
    return STATUS_TEXT.get(code, "未知状态码 %02XH" % code)


def mode_frame(addr, remote=True):
    return build(addr, CMD_MODE, b"\x01" if remote else b"\x00")


def output_frame(addr, on):
    return build(addr, CMD_OUTPUT, b"\x01" if on else b"\x00")


def command_from_text(cmd, addr=0, check_mode="sum8"):
    """上层 SCPI 风格命令 → (26 字节帧, tag)。

    保留原有的用户简写/命令终端语法，只把「下发到串口的内容」换成二进制帧。
    返回 None 表示 IT6721 没有对应命令字（只做本地记录，绝不发伪造帧）。
    """
    s = (cmd or "").strip()
    up = s.upper()
    if up.startswith("*IDN?") or up in ("IDN?", "INFO"):
        return build(addr, CMD_INFO, check_mode=check_mode), "info"
    if up.startswith("MEAS:VOLT") or up.startswith("MEAS:CURR") or up in ("MEAS", "READ"):
        return build(addr, CMD_READ, check_mode=check_mode), "read"
    if up.startswith("VOLT:PROT"):
        m = _MV_RE.match(up)
        if m:
            return build(addr, CMD_MAX_V, enc_voltage(float(m.group(1))),
                         check_mode), "max_v"
        return None
    m = _V_RE.match(up)
    if m:
        return build(addr, CMD_SET_V, enc_voltage(float(m.group(1))),
                     check_mode), "set_v"
    m = _I_RE.match(up)
    if m:
        return build(addr, CMD_SET_I, enc_current(float(m.group(1))),
                     check_mode), "set_i"
    if up in ("OUTP ON", "OUTPUT ON", "OUTP 1", "OUTP:STAT ON"):
        return output_frame(addr, True), "output_on"
    if up in ("OUTP OFF", "OUTPUT OFF", "OUTP 0", "OUTP:STAT OFF"):
        return output_frame(addr, False), "output_off"
    if up.startswith("SYST:REM") or up == "REMOTE":
        return mode_frame(addr, True), "mode_remote"
    if up.startswith("SYST:LOC") or up == "LOCAL":
        return mode_frame(addr, False), "mode_local"
    # 原始帧直通：HEX AA 00 26 00 ...（调试用，需自行给足字节或由程序补齐）
    if up.startswith("HEX ") or up.startswith("FRAME "):
        raw = s.split(None, 1)[1] if len(s.split(None, 1)) > 1 else ""
        try:
            vals = [int(x, 16) for x in re.split(r"[\s,]+", raw.strip()) if x]
        except ValueError:
            return None
        if not vals:
            return None
        if vals[0] != SYNC:
            vals.insert(0, SYNC)
        body = bytearray(vals[:25])
        body += b"\x00" * (25 - len(body))
        body.append(checksum(body, check_mode))
        return bytes(body), "raw"
    return None


# 便于外部查询的协议摘要
SUMMARY = {
    "name": "IT6720/IT6721 proprietary 26-byte frame",
    "baud": 4800,
    "format": "8N1",
    "address_range": "0~30 (0x00~0x1E), 0xFF broadcast",
    "level": "TTL @ rear DB9 (RS232 requires IT-E121 level shifter)",
    "scpi": False,
}
