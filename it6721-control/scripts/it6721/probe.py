# -*- coding: utf-8 -*-
"""IT6721 真机只读诊断 v4（26 字节二进制帧版）。

协议依据：《IT6720 系列编程与语法指南》V1.0 / 《IT6720 Series User's Manual》
§1.3。IT6721 属于 IT6720 经济型系列：4800 8N1 固定、26 字节二进制帧、
后面板 DB9 为 TTL 电平，不是 SCPI 设备，也没有 SYST SET / COMM 菜单。

本工具只发 31H（读型号）/ 26H（读状态）两种只读帧，绝不发 20H（远程模式）、
21H（输出）、23H/24H（设定），因此不会改变电源任何状态。

v4 改进
-------
1. 按实测 RX 特征分类下结论，不预设某一种硬件嫌疑。
2. 捕获并打印全部 RX 原始字节（含短帧、杂散字节、非 AA 应答），
   同时统计累计字节数，消除「只收 0xAA 且满 26 字节」的判定盲点。
3. 手册 §3.10：收到读命令时即使校验和错误也会回状态帧，因此「一个字节
   都没有」可排除校验算法猜错，直接指向物理层。
4. 本机手册的 DB9(TTL) 图只标引脚编号、不给信号名，TXD/RXD/GND 须实测
   确定；本工具不假设固定线序。
"""

import ctypes
import json
import os
import sys
import time

from . import protocol as pr
from . import serial_port as sp

BAUDS = [4800, 9600, 19200, 38400, 57600, 115200]
READ_FRAMES = [("31H 读型号", pr.CMD_INFO), ("26H 读状态", pr.CMD_READ)]
BASELINE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    ".probe_baseline.json")

IS_WIN = sys.platform == "win32"


def _hex(data):
    return " ".join("%02X" % c for c in bytes(data))


def _drain_raw(p, seconds=0.25, limit=64):
    """收任意字节（用于空闲噪声 / 回环特征）。"""
    deadline = time.time() + seconds
    buf = bytearray()
    while time.time() < deadline and len(buf) < limit:
        chunk = p.read(limit - len(buf))
        if chunk:
            buf += chunk
        else:
            time.sleep(0.004)
    return bytes(buf)


def _drain_frame(p, seconds=0.25, limit=128):
    """收 RX 全部原始字节，再尝试对齐 0xAA 取一帧 26 字节。

    返回 (frame, all_rx)。all_rx 是诊断证据：哪怕不成帧也必须看得见，
    否则「设备回了半帧 / 回了错误帧」会被误判成「设备完全没说话」。
    """
    deadline = time.time() + seconds
    buf = bytearray()
    while time.time() < deadline and len(buf) < limit:
        chunk = p.read(min(64, limit - len(buf)))
        if chunk:
            buf += chunk
        else:
            time.sleep(0.004)
    all_rx = bytes(buf)
    idx = all_rx.find(bytes([pr.SYNC]))
    frame = (all_rx[idx:idx + pr.FRAME_LEN]
             if idx >= 0 and len(all_rx) - idx >= pr.FRAME_LEN else b"")
    return frame, all_rx


def port_info():
    print("\n[1] 串口枚举")
    ports = sp.list_ports()
    print("    %s" % (", ".join(ports) or "(未发现)"))
    print("    程序自动选择 → %s" % sp.default_port())
    if IS_WIN:
        try:
            for dev, com in sp._win_port_devices():
                print("    %-28s → %s" % (dev, com))
        except Exception:
            pass
    return ports


def access_matrix(name):
    """逐种访问模式试打开，识别 err=5 端口独占。"""
    print("\n[2] 端口访问矩阵")
    ok = False
    err5 = 0
    if IS_WIN:
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.CreateFileW.restype = ctypes.c_void_p
        k.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32,
                                  ctypes.c_uint32, ctypes.c_void_p,
                                  ctypes.c_uint32, ctypes.c_uint32,
                                  ctypes.c_void_p]
        k.CloseHandle.argtypes = [ctypes.c_void_p]
        for share in (0, 3):
            for access in (0xC0000000, 0x80000000, 0x40000000, 0):
                h = k.CreateFileW("\\\\.\\" + name, access, share,
                                  None, 3, 0, None)
                err = ctypes.get_last_error()
                valid = h and h != ctypes.c_void_p(-1).value
                if valid:
                    k.CloseHandle(h)
                    ok = True
                if err == 5:
                    err5 += 1
                print("    share=%d access=%-10s → %s%s"
                      % (share, hex(access), "OK" if valid else "拒绝",
                         " err=%d" % err if err else ""))
    if err5:
        print("    ⚠ %d 种组合返回 err=5 (ERROR_ACCESS_DENIED)："
              "端口被其它进程独占或驱动句柄僵死" % err5)
        print("      处理：关闭串口调试助手 / Keil / S32DS / JScope 等，"
              "或拔插一次 USB 转串口")
    return ok, err5


def idle_noise(name, baud=4800, stop_bits=1, handshake=True, seconds=1.5):
    print("\n[3] RX 空闲噪声（%d 8N1，%.1f 秒）" % (baud, seconds))
    try:
        p = sp.open_serial(name, baud, 0.12, stop_bits=stop_bits,
                           handshake=handshake)
    except Exception as exc:
        print("    打开失败：%s" % exc)
        return b""
    try:
        p.flush_input()
        data = _drain_raw(p, seconds, limit=256)
    finally:
        p.close()
    print("    收到 %d 字节 %s" % (len(data), _hex(data[:32]) if data else ""))
    if data:
        print("    ⚠ 空闲线上有数据：可能是浮空、自环或设备在上报，需结合下一步判断")
    return data


def loopback_check(name, baud=4800, stop_bits=1, handshake=True):
    """发特征字节，判断是不是自己 TX 的反相回环。"""
    print("\n[4] 回环 / 电平特征（原始字节，不是命令帧）")
    probes = [("41×5", b"AAAAA"), ("00×4", b"\x00\x00\x00\x00"),
              ("FF×4", b"\xFF\xFF\xFF\xFF")]
    out = {}
    try:
        p = sp.open_serial(name, baud, 0.12, stop_bits=stop_bits,
                           handshake=handshake)
    except Exception as exc:
        print("    打开失败：%s" % exc)
        return out
    try:
        for label, payload in probes:
            p.flush_input()
            p.write(payload)
            rx = _drain_raw(p, 0.22, limit=64)
            out[label] = _hex(rx)
            verdict = "静默"
            if rx:
                inv = bytes((~c) & 0xFF for c in payload)
                if rx[:len(inv)] == inv:
                    verdict = "按位反相回显 → 典型 TTL/RS232 电平反接"
                elif rx == payload:
                    verdict = "原样回显 → TX/RX 短接自环"
                else:
                    verdict = "杂散字节"
            print("    发 %-6s → %-28s %s" % (label, _hex(rx) or "-", verdict))
    finally:
        p.close()
    return out


def selfcheck_hint():
    """给出可自行验证 USB-TTL 模块是否正常的硬指标。"""
    print("\n[4b] 模块自检（关键二分实验，先做这个）")
    print("    用杜邦线/跳线帽把 USB-TTL 模块自己的 TX 与 RX 短接，")
    print("    重跑本诊断：若 [4] 三组特征串都出现「原样回显」，")
    print("    → 模块、驱动、程序全部正常，问题在电源侧接线。")
    print("    若短接后仍全部「静默」→ 模块/驱动/选错 COM 口，先解决这个。")


def long_listen(name, seconds, baud=4800, stop_bits=1, handshake=True):
    """长监听：用于捕捉电源上电 / 复位瞬间主动发出的数据。"""
    if seconds <= 0:
        return b""
    print("\n[4c] 长监听 RX %.1f 秒（此时可给电源断电再上电）" % seconds)
    try:
        p = sp.open_serial(name, baud, 0.12, stop_bits=stop_bits,
                           handshake=handshake)
    except Exception as exc:
        print("    打开失败：%s" % exc)
        return b""
    try:
        p.flush_input()
        data = _drain_raw(p, seconds, limit=512)
    finally:
        p.close()
    print("    共 %d 字节：%s" % (len(data), _hex(data[:64]) if data else "(静默)"))
    return data


def _try_frame(name, baud, addr, cmd, stop_bits, handshake, check_mode):
    """发一帧只读命令。返回 (成帧数据, 解析结果, 校验通过, 全部原始RX)。"""
    try:
        p = sp.open_serial(name, baud, 0.12, stop_bits=stop_bits,
                           handshake=handshake)
    except Exception:
        return b"", None, False, b""
    try:
        p.flush_input()
        p.write(pr.build(addr, cmd, check_mode=check_mode))
        raw, all_rx = _drain_frame(p, 0.24)
        if not raw:
            return b"", None, False, all_rx
        strict = pr.parse(raw, check_mode=check_mode)
        loose = strict or pr.parse(raw, verify=False)
        return raw, loose, bool(strict), all_rx
    finally:
        p.close()


def frame_scan(name, quick=False, stop_bits=1, handshake=True,
               address=0, check_mode="sum8"):
    """按 26 字节帧协议扫描。命中必须连发 5 次复核。"""
    print("\n[5] 协议帧扫描（只读 31H / 26H，绝不发 20H/21H/23H/24H）")
    bauds = [4800] if quick else BAUDS
    addrs = list(dict.fromkeys([address, 0, 1, 0xFF]))
    modes = [check_mode] if quick else [check_mode, "xor", "neg"]
    nstop = [stop_bits] if quick else [1, 2]
    hits = []
    attempts = 0
    rx_total = 0
    samples = []
    for baud in bauds:
        for ns in nstop:
            for mode in modes:
                for addr in addrs:
                    for label, cmd in READ_FRAMES:
                        attempts += 1
                        raw, r, ok, all_rx = _try_frame(
                            name, baud, addr, cmd, ns, handshake, mode)
                        rx_total += len(all_rx)
                        if all_rx and len(samples) < 8:
                            samples.append((baud, ns, mode, addr, all_rx))
                        if r is None:
                            continue
                        print("    %6d 8N%d addr=%-3d %s %-5s → %s… %s"
                              % (baud, ns, addr, label, mode,
                                 _hex(raw[:5]),
                                 "校验OK" if ok else "校验不符"))
                        if ok and r["cmd"] in (pr.CMD_INFO, pr.CMD_STATUS,
                                               pr.CMD_READ):
                            hits.append((baud, ns, mode, addr, cmd, raw))
    print("\n    共 %d 次尝试，候选命中 %d 组" % (attempts, len(hits)))
    print("    ★ 全部尝试期间 RX 累计收到 %d 字节" % rx_total)
    for baud, ns, mode, addr, data in samples:
        print("      原始RX样本 %d 8N%d %s addr=%d → %s"
              % (baud, ns, mode, addr, _hex(data[:32])))
    confirmed = []
    for baud, ns, mode, addr, cmd, raw in hits[:10]:
        good = 0
        for _ in range(5):
            raw2, r2, ok2, _rx = _try_frame(name, baud, addr, cmd, ns,
                                            handshake, mode)
            if ok2 and r2 and r2["cmd"] in (pr.CMD_INFO, pr.CMD_STATUS,
                                            pr.CMD_READ):
                good += 1
        print("    复核 %d 8N%d addr=%d %s → %d/5 有效"
              % (baud, ns, addr, mode, good))
        if good >= 4:
            confirmed.append((baud, ns, mode, addr, cmd, raw))
    return confirmed, attempts, len(hits), rx_total


def phase_note(phase, idle, echo, name="<PORT>"):
    """拔线对比：把 RX 特征存成基线，插回后自动比较。"""
    record = {"phase": phase, "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
              "idle": _hex(idle), "echo": echo}
    if phase == "unplugged":
        with open(BASELINE, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)
        print("\n[7] 已保存拔线基线 → %s" % BASELINE)
        print("    现在把 DB9 插回电源，再跑："
              "python app.py --probe --port %s --probe-phase plugged" % name)
        return
    if phase == "plugged" and os.path.exists(BASELINE):
        with open(BASELINE, "r", encoding="utf-8") as f:
            base = json.load(f)
        print("\n[7] 拔线对比（基线 %s）" % base.get("ts", "?"))
        print("    空闲  拔线=%s / 插回=%s"
              % (base.get("idle", "-"), record["idle"]))
        for k, v in record["echo"].items():
            print("    %-6s 拔线=%s / 插回=%s"
                  % (k, base.get("echo", {}).get(k, "-"), v))
        same = (base.get("idle") == record["idle"]
                and base.get("echo") == record["echo"])
        print("    结论：%s" % ("两次特征完全一致 → 电源端没有参与通信"
                              if same else "特征有差异 → 电源端确实影响了 RX"))


def run(port=None, quick=False, phase=None, stop_bits=1, handshake=True,
        address=0, check_mode="sum8", listen=0.0):
    name = port or sp.default_port()
    print("\n" + "=" * 72)
    print("  IT6721 真机只读诊断 v4  ·  26 字节帧协议 / 4800 8N1")
    print("=" * 72)
    print("  IT6721 属 IT6720 经济型系列：不是 SCPI 设备，无 COMM 菜单。")
    print("  手册 §1.3：4800 8N1、地址 0~30（出厂 0）、后面板 DB9 为 TTL 电平。")
    print("  本诊断只发 31H / 26H 读帧，不改设定、不开输出。")
    print("=" * 72)

    ports = port_info()
    if name not in ports and name.upper() != "SIM":
        print("    ⚠ 指定的 %s 不在枚举结果里；请确认 USB-TTL 模块已插好" % name)
    print("    本次诊断目标端口：%s" % name)
    ok, err5 = access_matrix(name)
    if not ok and err5:
        print("\n  端口当前无法打开，请先解除占用后重跑。")
        return 2

    idle = idle_noise(name, 4800, stop_bits, handshake)
    echo = loopback_check(name, 4800, stop_bits, handshake)
    selfcheck_hint()
    heard = long_listen(name, float(listen or 0), 4800, stop_bits, handshake)
    confirmed, attempts, candidates, rx_total = frame_scan(
        name, quick, stop_bits, handshake, address, check_mode)
    rx_all = len(idle) + len(heard) + rx_total
    echo_bytes = sum(len(v.split()) for v in echo.values() if v and v != "-")

    print("\n[6] 结论")
    if confirmed:
        baud, ns, mode, addr, cmd, raw = confirmed[0]
        print("    ✅ 通信打通：%d 8N%d · 地址 %d · 校验 %s" % (baud, ns, addr, mode))
        print("       应答帧 %s" % _hex(raw))
        if cmd == pr.CMD_INFO:
            info = pr.decode_info(pr.parse(raw)["data"])
            print("       型号 %s / 版本 %s / 序列号 %s"
                  % (info["model"], info["version"], info["serial"]))
        print("       启动：python app.py --port %s --baud %d --address %d "
              "--no-auto --check-mode %s" % (name, baud, addr, mode))
        print("       硬指标已达成：连续 5 次有效帧 ≥4，才判通。")
    elif rx_all == 0 and echo_bytes == 0:
        print("    ❌ 物理层 RX 完全静默：%d 次帧尝试 + 空闲/回环/长监听，"
              "累计收到 0 字节。" % attempts)
        print("       手册 §3.10 写明：设备收到读命令时，校验和错误也会回")
        print("       12H / 90H 帧。因此「一个字节都没有」可排除「校验算法猜错」")
        print("       和「地址不对」，直接指向 RX 连线没有接通。")
        print("")
        print("       按优先级执行（每步都有可判定的硬指标）：")
        print("       ① 短接 USB-TTL 模块自己的 TX↔RX，重跑本诊断。")
        print("          判定：[4] 出现「原样回显」才说明模块/驱动/COM 口正常。")
        print("       ② 交换模块与电源 DB9 之间的 TX、RX 两根线，再跑一次。")
        print("          两种线序只有一种能通，这是最快的二分实验。")
        print("          注意按 DB9 **插孔面**数引脚，不是转接板的焊接面（会镜像）。")
        print("       ③ 确认 GND 已连接；万用表通断档量模块 GND ↔ DB9-5。")
        print("          再量电源 DB9 各针对 GND 的直流电压，找出 TXD（空闲为")
        print("          3.3V 或 5V 高电平）与 GND（0V）。")
        print("       ④ 电平：模块跳线先 5V 试，不通再切 3.3V。手册未写明 IT6721")
        print("          的 TTL 电压等级，这是目前唯一无法从文档确认的硬件参数。")
        print("       ⑤ 仍静默则怀疑接口损坏：此前普通 USB-RS232（±12V）曾直接")
        print("          灌入 TTL 口，手册明令禁止，存在损伤可能。")
        print("")
        print("       ⚠ 手册第 4 页的 DB9(TTL) 图只标了引脚编号 1~9，")
        print("         **没有给信号名定义**。之前给出的 2=TXD/3=RXD/5=GND 是")
        print("         从 IT6700H 另一机型外推的，请以万用表实测为准。")
        print("       面板没有 COMM / BAUD 菜单，不必再找；Shift + V/A 长按 3 秒")
        print("       进的是 OVP/OCP/BEEP/ADD/GRP 设置，可用来核对地址（出厂 0）。")
    else:
        print("    ❌ 收到 %d 字节，但 %d 次尝试中 %d 组候选、0 组通过 5 连发复核。"
              % (rx_all, attempts, candidates))
        print("       RX 上有信号却无法成帧，优先查：① 波特率/停止位；② TTL 电平")
        print("       幅度（3.3V vs 5V）；③ 是否 TX/RX 自环（对比 [4] 特征）；")
        print("       ④ 帧结构/校验码。可用 --check-mode xor / neg 复核。")
        print("       把上面打印的「原始RX样本」原文发我，可直接判定是哪种情况。")

    print("\n    复测命令：python app.py --probe --port %s --probe-quick" % name)
    print("    上电瞬间长监听：python app.py --probe --port %s --probe-listen 10"
          % name)

    if phase:
        phase_note(phase, idle, echo, name)
    return 0 if confirmed else 1
