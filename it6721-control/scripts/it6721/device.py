# -*- coding: utf-8 -*-
# Copyright (c) 2026 BreezeKitte
# SPDX-License-Identifier: MIT
"""IT6721 设备层：26 字节二进制帧收发、功率校验、轮询与断线兜底。

协议依据：IT6720 经济型数控系列专有 26 字节帧 + 4800 8N1。
帧结构、命令字与数值缩放见 protocol.py，安全约束见 validate()。
"""

import re
import threading
import time
from collections import deque
from datetime import datetime

from . import serial_port as sp
from . import protocol as pr

# ---- 设备硬规格（IT6720/IT6721 用户手册 + 编程与语法指南）----
V_MAX = 60.00          # V
I_MAX = 8.000          # A
P_MAX = 180.0          # W
V_RESOLUTION = 0.01    # 10 mV
I_RESOLUTION = 0.001   # 1 mA
BAUD_CANDIDATES = [4800, 9600, 19200, 38400, 57600, 115200]
DEFAULT_BAUD = 4800            # 手册 1.2 通讯设置：4800 固定
DEVICE_FACTORY_BAUD = 4800     # IT6720 Series User's Manual 3.0 / 1.3
LINE_END = b""                 # 二进制帧无终止符，保留字段仅为兼容旧界面
DEFAULT_PARITY = "N"           # 手册：Parity None（固定，面板无此项）
DEFAULT_ADDRESS = 0            # 手册：地址 0~30，出厂 0
POLL_INTERVAL = 0.100  # s
REPLY_TIMEOUT = 0.300  # s
MAX_TIMEOUTS = 3

_NUM = re.compile(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?")


def is_printable_reply(text):
    """只接受可打印 ASCII 应答。

    真机物理层不通时（TX/RX 接反、RS232/TTL 电平不符、线序错误），
    RX 上会出现自己发出信号的反相回环乱码。若不拦截，_parse_number
    会从乱码里抠出数字当成电压/电流显示，界面上就是一串假读数。
    """
    return bool(text) and all(32 <= ord(c) < 127 for c in text)


class ValidationError(Exception):
    """设定值不合法或超出功率上限。"""


def validate(v, i, strict=True):
    """三重校验：电压量程 → 电流量程 → 功率上限。返回实际功率 W。"""
    v = float(v)
    i = float(i)
    if not (0.0 <= v <= V_MAX):
        raise ValidationError("电压 %.3f V 超出量程 0.00–60.00 V" % v)
    if not (0.0 <= i <= I_MAX):
        raise ValidationError("电流 %.4f A 超出量程 0.000–8.000 A" % i)
    p = v * i
    if p > P_MAX + 1e-9:
        raise ValidationError(
            "P = %.2f V × %.3f A = %.1f W > 180 W，已拒绝下发（未发送到设备）" % (v, i, p)
        )
    if strict and v > 0 and i > P_MAX / v + 1e-9:
        raise ValidationError("功率越限")
    return p


def max_current_at(v):
    """给定电压下的最大允许电流（180 W 约束）。"""
    return 0.0 if v <= 0 else min(I_MAX, P_MAX / v)


def _parse_number(text):
    m = _NUM.search(text or "")
    return float(m.group(0)) if m else None


def _flush(p):
    for name in ("flush_input", "reset_input_buffer", "flushInput"):
        fn = getattr(p, name, None)
        if fn:
            try:
                fn()
                return
            except Exception:
                pass


def _stamp():
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


class IT6721(object):
    """线程安全的设备会话。所有串口操作都经过 self.lock 串行化。"""

    def __init__(self, poll_interval=POLL_INTERVAL):
        self.lock = threading.RLock()
        self.port = None
        self.port_name = ""
        self.baud = DEFAULT_BAUD
        self.sim = False
        self.auto_locked = False
        self.connected = False
        self.link_lost = False

        # 设定值：与 v1.1 冻结的设计稿基线一致
        self.set_v = 1.79
        self.set_i = 2.000
        self.ovp = 62.00
        self.ocp = 8.20

        # 实时回读
        self.v = 0.0
        self.i = 0.0
        self.p = 0.0
        self.mode = "--"
        self.output = False

        self.tx_count = 0
        self.rx_count = 0
        self.err_count = 0
        self.rx_garbage = 0            # 非 ASCII 乱码应答计数（物理层诊断用）
        self.parity = DEFAULT_PARITY
        self.line_end = LINE_END
        self.stop_bits = 1                 # 手册：固定 1 停止位
        self.dtr_rts = True                # 是否拉高 DTR / RTS（不叫 handshake，避免与 handshake() 方法冲突）
        self.address = DEFAULT_ADDRESS     # 设备地址 0~30，面板 ADD 菜单可改
        self.check_mode = "sum8"           # 校验算法；真机若回 90H 可切 xor / neg 复核
        self.remote_ok = False             # 是否已成功发过 20H 进入 PC 控制模式
        self.last_frame = ""               # 最近一次收发的帧（十六进制，诊断用）
        self.last_status = ""              # 最近一次 12H 状态帧的结果文字
        self.consecutive_timeouts = 0
        self.last_error = ""
        self.idn = ""
        self.settle_until = 0.0     # 设定变更后的稳定窗口，记录器据此丢弃过渡点

        self.log = deque(maxlen=600)
        self.history = deque(maxlen=300)     # 趋势曲线
        self.on_sample = None                # callable(state_dict)
        self.on_event = None                 # callable(level, text)

        self.poll_interval = poll_interval
        self._poll_thread = None
        self._stop = threading.Event()
        self._paused = threading.Event()

    # ---------------------------------------------------------------- 日志
    def _log(self, direction, text, level="info"):
        item = {"t": _stamp(), "dir": direction, "text": text, "level": level}
        self.log.append(item)
        if self.on_event:
            try:
                self.on_event(level, text)
            except Exception:
                pass
        return item

    # ------------------------------------------------------------ 连接管理
    def connect(self, port=None, baud=DEFAULT_BAUD, sim=False,
                use_pyserial=False, parity=None, line_end=None,
                stop_bits=None, handshake=None, address=None,
                check_mode=None):
        """以指定波特率打开端口。不做握手，握手由 auto_connect / handshake 完成。"""
        with self.lock:
            self.disconnect(quiet=True)
            port = port or sp.default_port()
            self.sim = bool(sim) or str(port).upper() == "SIM"
            self.parity = str(parity or self.parity).upper()[:1]
            if line_end is not None:
                self.line_end = line_end
            if stop_bits is not None:
                self.stop_bits = 2 if int(stop_bits) == 2 else 1
            if handshake is not None:
                self.dtr_rts = bool(handshake)
            if address is not None:
                self.address = max(0, min(pr.ADDR_MAX, int(address)))
            if check_mode:
                self.check_mode = check_mode
            self.port = sp.open_serial(
                port, int(baud), REPLY_TIMEOUT,
                sim=self.sim, use_pyserial=use_pyserial, parity=self.parity,
                stop_bits=self.stop_bits, handshake=self.dtr_rts,
            )
            self.port_name = "SIM" if self.sim else port
            self.baud = int(baud)
            self.connected = True
            self.link_lost = False
            self.auto_locked = False
            self.remote_ok = False
            self.consecutive_timeouts = 0
            self._log("SYS", "已打开 %s  ·  %d · 8%s%d  ·  地址 %d  ·  26B 帧%s"
                      % (self.port_name, self.baud, self.parity,
                         self.stop_bits, self.address,
                         "  ·  模拟设备" if self.sim else ""))
            self._start_poll()
            return True

    def auto_connect(self, port=None, sim=False, use_pyserial=False,
                     candidates=None, parity=None, stop_bits=None,
                     handshake=None, addresses=None):
        """逐档试探：4800 优先，每档再试地址 0 / 1 / 广播，用 31H 只读帧握手。

        IT6721 速率固定 4800 8N1、说 26 字节二进制帧（不是 SCPI）。
        命中条件：收到 26 字节、0xAA 开头、校验和正确的应答帧。
        """
        port = port or sp.default_port()
        cands = candidates or BAUD_CANDIDATES
        if addresses is None:
            addresses = list(dict.fromkeys(
                [self.address, 0, 1, pr.BROADCAST]))
        if sim or str(port).upper() == "SIM":
            self.connect("SIM", DEFAULT_BAUD, sim=True, parity=parity,
                         stop_bits=stop_bits, handshake=handshake)
            self.handshake()
            self.auto_locked = True
            return {"ok": True, "baud": DEFAULT_BAUD, "idn": self.idn,
                    "tried": [DEFAULT_BAUD], "elapsed": 0.0, "sim": True}

        tried = []
        t0 = time.time()
        last_err = ""
        garbage_seen = 0
        check_bad = 0
        for baud in cands:
            tried.append(baud)
            try:
                self.connect(port, baud, use_pyserial=use_pyserial,
                             parity=parity, stop_bits=stop_bits,
                             handshake=handshake)
                self.pause_poll(True)
                for addr in addresses:
                    self.address = addr
                    r = self._frame(
                        pr.build(addr, pr.CMD_INFO, check_mode=self.check_mode),
                        timeout=0.22, quiet=True)
                    if r and r.get("check_bad"):
                        check_bad += 1
                    if (r and not r.get("check_bad")
                            and r["cmd"] in (pr.CMD_INFO, pr.CMD_STATUS,
                                             pr.CMD_READ)):
                        self.auto_locked = True
                        self.baud = baud
                        self.idn = (pr.decode_info(r["data"])["text"]
                                    if r["cmd"] == pr.CMD_INFO
                                    else "IT6721 (26B 帧应答)")
                        self.pause_poll(False)
                        self._log("SYS", "握手命中 %d / 地址 %d  ·  31H → %s"
                                  % (baud, addr, self.idn), "ok")
                        return {"ok": True, "baud": baud, "address": addr,
                                "idn": self.idn, "tried": tried,
                                "elapsed": round(time.time() - t0, 2),
                                "sim": False}
                garbage_seen = max(garbage_seen, self.rx_garbage)
            except Exception as exc:
                last_err = str(exc)
            self._log("SYS", "%d 未命中，关闭并切下一档" % baud)
            self.disconnect(quiet=True)

        self.pause_poll(False)
        self.last_error = last_err or "全部档位未收到合法 26 字节应答帧"
        diagnosis = (
            "IT6721 使用 26 字节二进制帧（4800 8N1），不是 SCPI，*IDN? 永远不会有应答。"
            "请依次确认：① 后面板 DB9 是 TTL 电平，必须用 ITECH IT-E121(RS232) / "
            "IT-E122(USB) 电平转换模块，禁止标准 RS232 线直连；② 面板 Shift + V/A "
            "长按 3 秒 → 按 Lock 翻到 ADD 查看通讯地址（出厂 0）；③ 模块与线缆插紧、电源开机。"
        )
        if check_bad:
            diagnosis += (" 检测到 %d 帧「26 字节但校验和不符」，"
                          "可试 --check-mode xor / neg 复核算法。" % check_bad)
        if garbage_seen:
            diagnosis += " 另有 %d 次乱码回环，倾向电平/线序错误。" % garbage_seen
        self._log("ERR", "自适应失败：%s" % self.last_error, "error")
        self._log("ERR", diagnosis, "error")
        return {"ok": False, "baud": None, "idn": "", "tried": tried,
                "elapsed": round(time.time() - t0, 2), "error": self.last_error,
                "diagnosis": diagnosis, "garbage": garbage_seen,
                "check_bad": check_bad, "sim": False}

    def handshake(self):
        """只读握手：发 31H 读型号帧，不碰输出与设定。"""
        r = self._frame(
            pr.build(self.address, pr.CMD_INFO, check_mode=self.check_mode),
            timeout=0.50)
        if not r or r.get("check_bad"):
            self._log("ERR", "握手无应答（设备未开机 / 电平不对 / 地址不符 / 端口被占用）",
                      "error")
            return False, ""
        self.idn = (pr.decode_info(r["data"])["text"]
                    if r["cmd"] == pr.CMD_INFO else "IT6721 (26B 帧应答)")
        ok = r["cmd"] in (pr.CMD_INFO, pr.CMD_STATUS, pr.CMD_READ)
        self._log("SYS", "握手 %s：%s" % ("成功" if ok else "机型不符", self.idn),
                  "ok" if ok else "warn")
        return ok, self.idn

    def disconnect(self, quiet=False):
        with self.lock:
            self._stop_poll()
            if self.port is not None and self.remote_ok:
                # 交还前面板控制权（尽力而为，不等应答）
                try:
                    self.port.write(pr.mode_frame(self.address, False))
                    self.tx_count += 1
                    if not quiet:
                        self._log("SYS", "已发 20H 交还前面板控制权")
                except Exception:
                    pass
            if self.port is not None:
                try:
                    self.port.close()
                except Exception:
                    pass
            if not quiet:
                self._log("SYS", "已断开 %s" % (self.port_name or "串口"))
            self.port = None
            self.connected = False
            self.auto_locked = False
            self.remote_ok = False
            self.mode = "--"

    # ------------------------------------------------------ 26 字节帧收发
    def _frame(self, frame, timeout=REPLY_TIMEOUT, quiet=False):
        """发一帧、收一帧。返回解析字典；校验不符时带 check_bad=True。"""
        with self.lock:
            if not self.connected or self.port is None:
                raise sp.SerialError("串口未连接，帧未下发")
            self.port.write(bytes(frame))
            self.tx_count += 1
            self.last_frame = pr.frame_hex(frame)
            if not quiet:
                self._log("TX", self.last_frame)
            deadline = time.time() + timeout
            buf = bytearray()
            while time.time() < deadline and len(buf) < pr.FRAME_LEN:
                chunk = self.port.read(pr.FRAME_LEN - len(buf))
                if chunk:
                    buf += chunk
                    while buf and buf[0] != pr.SYNC:
                        del buf[0]
                else:
                    time.sleep(0.004)
            if len(buf) < pr.FRAME_LEN:
                self.consecutive_timeouts += 1
                self.err_count += 1
                if not quiet:
                    self._log("ERR", "无应答 / 帧不完整（%d/26 字节，超时 %d ms）"
                              % (len(buf), int(timeout * 1000)), "error")
                if self.consecutive_timeouts >= MAX_TIMEOUTS:
                    self._emergency_off()
                return None
            raw = bytes(buf[:pr.FRAME_LEN])
            self.last_frame = pr.frame_hex(raw)
            r = pr.parse(raw, check_mode=self.check_mode)
            if r is None:
                r = pr.parse(raw, verify=False, check_mode=self.check_mode)
                if r is None:
                    self.rx_garbage += 1
                    return None
                r["check_bad"] = True
                self.rx_garbage += 1
                if not quiet:
                    self._log("ERR", "帧校验和不符（算法 %s）: %s …"
                              % (self.check_mode,
                                 pr.frame_hex(raw[:6])), "error")
                return r
            self.rx_count += 1
            self.consecutive_timeouts = 0
            self.rx_garbage = 0
            if not quiet:
                self._log("RX", pr.frame_hex(raw), "ok")
            if r["cmd"] == pr.CMD_STATUS:
                self.last_status = pr.STATUS_TEXT.get(
                    r["data"][0] if r["data"] else 0, "")
                if self.last_status and self.last_status != "OK" and not quiet:
                    self._log("ERR", "设备返回 %s" % self.last_status, "error")
            return r

    def _ensure_remote(self):
        """写设定前必须先发 20H 进 PC 控制模式；失败则中止写入。"""
        if self.remote_ok:
            return True
        r = self._frame(pr.mode_frame(self.address, True), quiet=True)
        bad = bool(r and r["cmd"] == pr.CMD_STATUS and r["data"]
                   and r["data"][0] != pr.STATUS_OK)
        ok = bool(r) and not r.get("check_bad") and not bad
        self.remote_ok = ok
        if ok:
            self._log("SYS", "已发 20H 进入 PC 远程控制模式（断开时自动交还面板）", "ok")
        else:
            self._log("ERR", "20H 远程模式未确认，写设定已中止（避免误判设备已执行）",
                      "error")
        return ok

    def query(self, cmd, timeout=REPLY_TIMEOUT, quiet=False):
        """查询（只读帧）。返回可读文本，无应答返回 ''。"""
        item = pr.command_from_text(cmd, self.address, self.check_mode)
        if item is None:
            if not quiet:
                self._log("WARN", "%s：IT6721 无此命令字，未下发" % cmd, "warn")
            return ""
        frame, tag = item
        r = self._frame(frame, timeout, quiet)
        if not r or r.get("check_bad"):
            return ""
        if tag == "info":
            self.idn = (pr.decode_info(r["data"])["text"]
                        if r["cmd"] == pr.CMD_INFO else "IT6721")
            return self.idn
        if tag == "read":
            self._apply_state(pr.decode_state(r["data"]))
            return "V=%.3f I=%.4f" % (self.v, self.i)
        if tag == "raw":
            return pr.frame_hex(r["raw"])
        return pr.status_text(r["raw"]) or (
            "OK" if r["cmd"] == pr.CMD_STATUS else "")

    def send(self, cmd, quiet=False):
        """下发一条设定帧。IT6721 对每条命令都回 26 字节状态帧。"""
        item = pr.command_from_text(cmd, self.address, self.check_mode)
        if item is None:
            self._log("WARN", "%s：本机无对应命令字（未下发，仅本地记录）" % cmd,
                      "warn")
            return True
        frame, tag = item
        if tag not in ("mode_remote", "mode_local") and not self._ensure_remote():
            raise sp.SerialError("未进入 PC 远程控制模式，%s 未下发" % cmd)
        r = self._frame(frame, quiet=quiet)
        if r and r["cmd"] == pr.CMD_STATUS and r["data"] \
                and r["data"][0] != pr.STATUS_OK:
            self._log("ERR", "%s 被设备拒绝：%s"
                      % (cmd, pr.status_text(r["raw"])), "error")
        return True

    def transact(self, cmd, expect_reply=None):
        if expect_reply is None:
            expect_reply = ("?" in cmd
                            or cmd.strip().upper() in ("MEAS", "READ", "INFO"))
        return self.query(cmd) if expect_reply else self.send(cmd)

    def _apply_state(self, st):
        self.v = st["actual_v"]
        self.i = st["actual_i"]
        self.p = round(self.v * self.i, 3)
        self.mode = st["mode"]
        self.output = st["output"]
        if st["set_v"] > 0:
            self.set_v = st["set_v"]
        if st["set_i"] > 0:
            self.set_i = st["set_i"]
        self.history.append((time.time(), self.v, self.i, self.p))
        return True

    # -------------------------------------------------------------- 动作层
    def _send_voltage(self, v):
        self.send("VOLT %.2f" % v)
        self.set_v = v
        self.settle_until = time.time() + 0.30
        return "VOLT %.2f" % v

    def _send_current(self, i):
        self.send("CURR %.3f" % i)
        self.set_i = i
        self.settle_until = time.time() + 0.30
        return "CURR %.3f" % i

    def set_voltage(self, v):
        v = round(float(v) / V_RESOLUTION) * V_RESOLUTION
        validate(v, self.set_i)
        return self._send_voltage(v)

    def set_current(self, i):
        i = round(float(i) / I_RESOLUTION) * I_RESOLUTION
        validate(self.set_v, i)
        return self._send_current(i)

    def apply(self, v=None, i=None):
        """原子设定：先整体校验最终态，再按安全顺序下发。任一项越限则一条都不发。"""
        nv = self.set_v if v is None else round(float(v) / V_RESOLUTION) * V_RESOLUTION
        ni = self.set_i if i is None else round(float(i) / I_RESOLUTION) * I_RESOLUTION
        validate(nv, ni)
        return self.apply_step(nv, ni)

    def apply_step(self, v, i):
        """按安全顺序下发一组设定，避免中间态功率越限。

        从「低压大电流」切到「高压小电流」时，若先升电压会瞬时越限
        （例如当前 8 A → 目标 60 V/3 A，60×8=480 W），此时必须先降电流。
        """
        v = round(float(v) / V_RESOLUTION) * V_RESOLUTION
        i = round(float(i) / I_RESOLUTION) * I_RESOLUTION
        validate(v, i)
        out = []
        must_drop_current_first = (v * self.set_i > P_MAX + 1e-9)
        if must_drop_current_first and abs(i - self.set_i) > 1e-9:
            out.append(self._send_current(i))
        if abs(v - self.set_v) > 1e-9:
            out.append(self._send_voltage(v))
        if not must_drop_current_first and abs(i - self.set_i) > 1e-9:
            out.append(self._send_current(i))
        return out

    def output_on(self):
        self.send("OUTP ON")
        self.output = True
        self.settle_until = time.time() + 0.30
        return "OUTP ON"

    def output_off(self):
        self.send("OUTP OFF")
        self.output = False
        self.consecutive_timeouts = 0
        return "OUTP OFF"

    def set_output(self, on):
        return self.output_on() if on else self.output_off()

    def recall(self, slot):
        slot = int(slot)
        if slot not in (1, 2, 3, 4):
            raise ValidationError("预设槽位只能是 1–4（对应面板 A/B/C/D）")
        # IT6721 的 26 字节协议没有「调用面板预设」命令字（A/B/C/D 只能面板操作）。
        self._log("WARN", "RCL %d：本机无远程调用预设的命令字；请在面板按 A/B/C/D"
                  % slot, "warn")
        return "RCL %d [本机不支持，未下发]" % slot

    def set_ovp(self, v):
        v = float(v)
        if v <= self.set_v:
            raise ValidationError("OVP 保护点必须高于当前电压设定值 %.2f V" % self.set_v)
        # 22H 设定的是「最大输出电压」，等价于电压上限保护
        self.send("VOLT:PROT %.2f" % v)
        self.ovp = v
        return "VOLT:PROT %.2f → 22H 电压上限" % v

    def set_ocp(self, i):
        i = float(i)
        if i <= self.set_i:
            raise ValidationError("OCP 保护点必须高于当前电流设定值 %.3f A" % self.set_i)
        # 协议中没有 OCP 设定命令字（电流保护只在面板 OCP 菜单里设置）。
        # 这里只做上位机侧的软件阈值记录，绝不假装已写入设备。
        self.ocp = i
        self._log("WARN", "OCP %.3f A 仅记录在上位机；请到面板 Shift+V/A 菜单设 OCP"
                  % i, "warn")
        return "OCP %.3f A [仅本地阈值，未下发]" % i

    def read_measure(self):
        """一次 26H 帧读回实际 V/I、状态、设定值（协议里没有单独 MEAS 指令）。"""
        r = self._frame(
            pr.build(self.address, pr.CMD_READ, check_mode=self.check_mode),
            quiet=True)
        if not r or r.get("check_bad"):
            return False
        if r["cmd"] not in (pr.CMD_READ, pr.CMD_STATUS):
            return False
        return self._apply_state(pr.decode_state(r["data"]))

    def _emergency_off(self):
        self.link_lost = True
        try:
            if self.port is not None:
                self.port.write(pr.output_frame(self.address, False))
                self.tx_count += 1
        except Exception:
            pass
        self._log("ERR", "连续 %d 次超时 → 判定断线，已下发 21H 输出 OFF 帧"
                  % MAX_TIMEOUTS, "error")
        self._stop_poll()

    # ------------------------------------------------------------ 轮询线程
    def _start_poll(self):
        # 自适应扫描会反复 connect/disconnect，先确保上一轮轮询线程已退出，
        # 避免多个线程同时抢串口。
        old = self._poll_thread
        if old and old.is_alive():
            self._stop.set()
            old.join(timeout=0.5)
        self._stop.clear()
        self._poll_thread = threading.Thread(
            target=self._poll_loop, name="it6721-poll", daemon=True
        )
        self._poll_thread.start()

    def _stop_poll(self):
        self._stop.set()

    def pause_poll(self, paused=True):
        if paused:
            self._paused.set()
        else:
            self._paused.clear()

    def _poll_loop(self):
        while not self._stop.is_set():
            t0 = time.time()
            if not self._paused.is_set() and self.connected and not self.link_lost:
                try:
                    if self.read_measure():
                        state = self.snapshot()
                        if self.on_sample:
                            try:
                                self.on_sample(state)
                            except Exception:
                                pass
                except Exception as exc:
                    self.err_count += 1
                    self._log("ERR", "轮询异常：%s" % exc, "error")
            wait = self.poll_interval - (time.time() - t0)
            self._stop.wait(max(0.005, wait))

    # ---------------------------------------------------------------- 状态
    def snapshot(self):
        with self.lock:
            return {
                "connected": bool(self.connected),
                "sim": bool(self.sim),
                "port": self.port_name,
                "baud": self.baud,
                "parity": self.parity,
                "stop_bits": self.stop_bits,
                "handshake": self.dtr_rts,
                "line_end": repr(self.line_end),
                "address": self.address,
                "check_mode": self.check_mode,
                "protocol": "26B-frame",
                "remote": bool(self.remote_ok),
                "last_frame": self.last_frame,
                "last_status": self.last_status,
                "auto": bool(self.auto_locked),
                "link_lost": bool(self.link_lost),
                "v": round(self.v, 3),
                "i": round(self.i, 4),
                "p": self.p,
                "set_v": round(self.set_v, 2),
                "set_i": round(self.set_i, 3),
                "ovp": self.ovp,
                "ocp": self.ocp,
                "output": bool(self.output),
                "mode": self.mode,
                "tx": self.tx_count,
                "rx": self.rx_count,
                "garbage": self.rx_garbage,
                "errors": self.err_count,
                "timeouts": self.consecutive_timeouts,
                "settle": max(0.0, self.settle_until - time.time()),
                "idn": self.idn,
                "max_i": round(max_current_at(self.set_v), 3),
                "limits": {"v": V_MAX, "i": I_MAX, "p": P_MAX},
                "last_error": self.last_error,
                "version": "2.2.2",
            }

    def log_tail(self, n=120):
        return list(self.log)[-n:]

    def trend_tail(self, n=180):
        pts = list(self.history)[-n:]
        return [[round(t - pts[0][0], 2) if pts else 0, v, i, p]
                for t, v, i, p in pts]
