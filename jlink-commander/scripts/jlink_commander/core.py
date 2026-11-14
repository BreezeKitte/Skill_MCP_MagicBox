# Copyright (c) 2026 BreezeKitte
# SPDX-License-Identifier: MIT

"""J-Link Commander 脚本生成与执行、mem 输出解析。

只依赖标准库 + 子进程调用本机 JLink.exe。DWARF 解析在 dwarf.py（可选依赖
pyelftools）。所有面向真机的动作都走 J-Link Commander 脚本，**绝不调用 GDB**——
GDB 会 halt 内核，把电机控制 ISR 冻住，导致 PDB 序列错误等伪影（B22）。
"""
import os
import re
import glob
import shutil
import tempfile
import subprocess

# 兜底默认值（旧版固定路径）；实际运行时优先自动探测最新安装，避免写死版本号。
DEFAULT_JLINK_EXE = "C:/Program Files/SEGGER/JLink_V988/JLink.exe"


def _candidate_exe_paths():
    """可能的 JLink 可执行文件位置（不依赖固定版本号，按版本降序返回）。

    Windows 默认装到 C:/Program Files/SEGGER/JLink_Vxxxx/JLink.exe；
    Linux/macOS 常见 /opt/SEGGER/JLink/JLinkExe。返回列表已按版本号从新到旧排序。
    """
    found = []
    bases = [
        os.environ.get("ProgramFiles", "C:/Program Files"),
        os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)"),
        "C:/Program Files/SEGGER",
        "C:/Program Files (x86)/SEGGER",
        "/opt/SEGGER/JLink",
        "/Applications/SEGGER/JLink",
    ]
    for base in bases:
        if not base or not os.path.isdir(base):
            continue
        # Windows 风格：JLink_V* 目录（按版本降序）
        for d in sorted(glob.glob(os.path.join(base, "JLink_V*")), reverse=True):
            for name in ("JLink.exe", "JLinkExe"):
                p = os.path.join(d, name)
                if os.path.isfile(p):
                    found.append(p)
        # 目录内直接放可执行（Linux/macOS 常见）
        for name in ("JLinkExe", "JLink.exe", "JLink"):
            p = os.path.join(base, name)
            if os.path.isfile(p):
                found.append(p)
    return found


def resolve_exe(exe=None):
    """返回 JLink 可执行文件绝对路径，按优先级：

    入参 > 环境变量 JLINK_EXE > 自动探测最新安装（不写死版本号）> PATH > 兜底默认。
    """
    exe = exe or os.environ.get("JLINK_EXE")
    if exe:
        return exe
    for p in _candidate_exe_paths():
        return p  # 已按版本降序，取最新
    on_path = shutil.which("JLink.exe") or shutil.which("JLinkExe") or shutil.which("JLink")
    if on_path:
        return on_path
    return DEFAULT_JLINK_EXE


def run_script(jlink_exe, script_text, timeout=600, cwd=None):
    """把脚本写入临时 .jlink 文件并交给 JLink.exe 执行，返回合并后的 stdout+stderr。

    固定参数：-CommandFile <f> -ExitOnError 0（ExitOnError 0 = 出错不立即退出，
    便于把错误信息一并回传解析）。不在代码里做 halt/reset 之外的危险操作。
    """
    jlink_exe = resolve_exe(jlink_exe)
    fd, path = tempfile.mkstemp(prefix="jlink_", suffix=".jlink", dir=cwd or None)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(script_text)
        proc = subprocess.run(
            [jlink_exe, "-CommandFile", path, "-ExitOnError", "0"],
            capture_output=True, text=True, timeout=timeout,
            cwd=cwd, encoding="utf-8", errors="replace",
        )
        out = (proc.stdout or "") + "\n" + (proc.stderr or "")
        return out
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def build_connect_lines(device="S32K144", iface="1", speed=4000):
    """连接头：si 1=SWD，si 0=JTAG；speed 单位 kHz。"""
    return [
        f"device {device}",
        f"si {iface}",
        f"speed {speed}",
        "connect",
    ]


MEM_RE = re.compile(r"^([0-9A-Fa-f]{8})\s*=\s*(.*)$")


def parse_mem_output(text):
    """解析 Commander `mem` 输出为有序 [(addr_int, bytes), ...]。

    V988 格式：`<8位十六进制地址> = <两个一组的十六进制字节>  <ascii>`
    一段 length=L 的 mem 会跨若干 16 字节行，每行带自己递增的地址；
    调用方按地址把同一 region 的行拼回连续字节。
    """
    rows = []
    for line in text.splitlines():
        s = line.strip()
        m = MEM_RE.match(s)
        if not m:
            continue
        addr = int(m.group(1), 16)
        buf = bytearray()
        for tok in m.group(2).split():
            if len(tok) == 2 and re.fullmatch(r"[0-9A-Fa-f]{2}", tok):
                buf.append(int(tok, 16))
            else:
                break  # 遇到 ASCII 块就停
        if buf:
            rows.append((addr, bytes(buf)))
    return rows


def assemble_region(rows, region_addr, region_len):
    """把落在 [region_addr, region_addr+len) 内的所有行按地址拼接成连续字节。"""
    rel = [(a, b) for a, b in rows if region_addr <= a < region_addr + region_len]
    rel.sort(key=lambda x: x[0])
    buf = bytearray()
    for _a, b in rel:
        buf += b
    return bytes(buf[:region_len])


def build_flash_script(hex_path, device="S32K144", iface="1", speed=4000,
                       reset_after=True):
    lines = build_connect_lines(device, iface, speed)
    lines += ["r", f"loadfile {hex_path}", "r"]
    if reset_after:
        lines.append("g")
    lines.append("q")
    return "\n".join(lines) + "\n"


def build_write_ram_script(addr, word_hex, device="S32K144", iface="1", speed=4000,
                           readback_len=4):
    """B20：写 RAM 与读回必须在同一会话。connect → g → Sleep → w4 → mem 读回 → q。

    addr/word 均为整数。word 是 32-bit 字（小端 4 字节）。返回脚本文本。
    """
    lines = build_connect_lines(device, iface, speed)
    lines += ["g", "Sleep 100",
              f"w4 0x{addr:08X}, 0x{word_hex & 0xFFFFFFFF:08X}",
              "Sleep 100",
              f"mem 0x{addr:08X},{readback_len}",
              "g", "q"]
    return "\n".join(lines) + "\n"


def build_nohalt_sample_script(regions, device="S32K144", iface="1", speed=4000,
                               samples=40, period_ms=200, init_ms=5000,
                               flash_hex=None, reset=False, writes=None,
                               no_halt=True):
    """稳态验证核心：NoHalt 采样，绝不 halt 内核（B22）。

    regions: [(name, addr_int, length_int), ...]，首个 region 的地址用于切分采样帧。
    flash_hex: 采样前先烧录（r + loadfile + r）。
    writes: 在线单变量 A/B，形如 [(addr_int, word_int), ...]；按 B20 插在 connect+g
            之后、采样之前（同一会话，写一次即全程生效）。
    长度按已验证的 capture_global.py 写法用十进制整数（与 V988 实测一致）。
    """
    writes = writes or []
    lines = build_connect_lines(device, iface, speed)
    if flash_hex:
        lines += ["r", f"loadfile {flash_hex}", "r"]
    elif reset:
        lines += ["r"]
    lines += ["g"]
    # 启动早期 halt 会打断 ADC1 ISR 清 FTM3 EXTTRIG，导致 PDB SEQERR 永久 FAULT；
    # 官方的 5000ms 等待是有意保留的观测方法修正，不是改固件。
    if init_ms > 0:
        lines.append(f"Sleep {init_ms}")
    for addr, word in writes:
        lines.append(f"w4 0x{addr:08X}, 0x{word & 0xFFFFFFFF:08X}")
    if writes:
        lines.append("Sleep 100")
    for _ in range(samples):
        lines.append(f"Sleep {period_ms}")
        # NoHalt：内核持续运行，经 AHB-AP 读 live RAM。
        suffix = " NoHalt" if no_halt else ""
        for _name, a, ln in regions:
            lines.append(f"mem 0x{a:08X},{ln}{suffix}")
    lines.append("q")
    return "\n".join(lines) + "\n"
