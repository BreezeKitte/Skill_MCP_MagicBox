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


WCMD = {1: "w1", 2: "w2", 4: "w4", 8: "w8"}
RCMD = {1: "mem8", 2: "mem16", 4: "mem", 8: "mem32"}
# 写字节宽(w1..w8) → 读回解析用的位宽（assemble_region_lines 第 4 参）：
#   1B→mem8(8bit 直序)  2B→mem16(16bit 翻转)  4B→mem(8bit 直序)  8B→mem32(32bit 翻转)
PARSE_WIDTH = {1: 8, 2: 16, 4: 8, 8: 32}


def build_write_ram_script(addr, word, device="S32K144", iface="1", speed=4000,
                           readback_len=4, width=4, no_halt=True):
    """B20：写 RAM 与读回必须在同一会话。connect → g → Sleep → wN → mem 读回 → q。

    addr/word 均为整数；word 按 width 位宽截断（w1/w2/w4/w8）。读回用同宽度命令，
    便于解析。返回脚本文本。
    """
    if width not in WCMD:
        raise ValueError("width 必须是 1/2/4/8，收到 %r" % width)
    mask = (1 << (width * 8)) - 1
    wcmd = WCMD[width]
    rcmd = RCMD[width]
    # readback_len 以字节计；换算成该宽度的条目数（width 此处为字节数 1/2/4/8）
    rcount = max(1, readback_len // width)
    suffix = " NoHalt" if no_halt else ""
    lines = build_connect_lines(device, iface, speed)
    lines += ["g", "Sleep 100",
              f"{wcmd} 0x{addr:08X}, 0x{word & mask:0{width * 2}X}",
              "Sleep 100",
              f"{rcmd} 0x{addr:08X},{rcount}{suffix}",
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


# --------------------------------------------------------------------------
# 宽度感知的内存读写（mem8/mem16/mem32 + w1/w2/w8）与解析
# 命令语法均来自本机 JLink.exe V9.88 内置 `?` 帮助（一手权威）：
#   Mem  [<Zone>:]<Addr>, <NumBytes> (hex)            读字节
#   Mem8 [<Zone>:]<Addr>, <NumBytes> (hex)            读 8-bit
#   Mem16 [<Zone>:]<Addr>, <NumItems> (hex)           读 16-bit 项
#   Mem32 [<Zone>:]<Addr>, <NumItems> (hex)           读 32-bit 项
#   W1/W2/W4/W8 [<Zone>:]<Addr>, <Data> (hex)        写 8/16/32/64-bit
# 注：本工程实测 `mem` 长度按十进制解析（帮助标注 hex，但实测为十进制，以实测为准），
#     故下面 count 一律按十进制传入。
# --------------------------------------------------------------------------
READ_CMD = {8: "mem", 16: "mem16", 32: "mem32"}


def build_read_memory_script(addr, length, width=8, no_halt=True,
                             device="S32K144", iface="1", speed=4000):
    """生成单次读内存脚本。width 取 8/16/32（bit 宽）。count 按条目数算：
    8→字节数；16→length//2；32→length//4。地址一律 0x... 十六进制。"""
    if width not in READ_CMD:
        raise ValueError("width 必须是 8/16/32，收到 %r" % width)
    count = length if width == 8 else length // (width // 8)
    cmd = READ_CMD[width]
    suffix = " NoHalt" if no_halt else ""
    lines = build_connect_lines(device, iface, speed)
    lines += ["g", "Sleep 100",
              f"{cmd} 0x{addr:08X},{count}{suffix}", "g", "q"]
    return "\n".join(lines) + "\n"


def build_savebin_script(addr, length, out_path, device="S32K144",
                         iface="1", speed=4000):
    """savebin：把目标内存区间存成二进制文件（离线分析 / Flash 备份）。
    语法 SaveBin <filename>, <addr>, <NumBytes>。"""
    lines = build_connect_lines(device, iface, speed)
    lines += ["g", f"savebin {out_path}, 0x{addr:08X}, {length}", "q"]
    return "\n".join(lines) + "\n"


def build_verifybin_script(bin_path, addr, device="S32K144",
                           iface="1", speed=4000):
    """verifybin：校验 .bin 是否已烧录到指定地址（R6 读回校验）。
    语法 VerifyBin <filename>, <addr>。"""
    lines = build_connect_lines(device, iface, speed)
    lines += [f"verifybin {bin_path}, 0x{addr:08X}", "q"]
    return "\n".join(lines) + "\n"


def build_erase_script(addr=None, length=None, device="S32K144",
                       iface="1", speed=4000):
    """erase：擦除 Flash。无参数=整片擦除；给定 addr+length=区间擦除。
    语法 Erase [<SAddr>, <EAddr>]。"""
    lines = build_connect_lines(device, iface, speed)
    if addr is not None and length is not None:
        end = addr + length - 1
        lines.append(f"erase 0x{addr:08X}, 0x{end:08X}")
    else:
        lines.append("erase")
    lines.append("q")
    return "\n".join(lines) + "\n"


def build_regs_script(device="S32K144", iface="1", speed=4000):
    """regs：显示 CPU 寄存器（R0-R15、xPSR、MSP、PSP、LR、PC 等）。
    注意：读取核心寄存器需要内核停止（Registers 命令会隐式 halt），故本命令
    只用于固件已停（故障/断点后）或可接受短暂冻结的场景，勿在 ISR 活跃时用于
    稳态观测（见 B22）。"""
    lines = build_connect_lines(device, iface, speed)
    lines += ["h", "regs", "q"]
    return "\n".join(lines) + "\n"


# ------------------------- 宽度感知解析 -------------------------
MEM_LINE_RE = re.compile(r"^([0-9A-Fa-f]{8})\s*=\s*(.*)$")


def parse_mem_lines(text):
    """把 Commander mem/mem8/mem16/mem32 输出解析为 [(addr, [hex_token,...]), ...]。

    每行形如 `20000000 = 12 34 56 78   .4Vx`（字节）或
    `20000000 = 3412 7856`（mem16，每 token 4 hex）或
    `20000000 = 78563412`（mem32，每 token 8 hex）。仅收集连续 hex token。
    """
    out = []
    for line in text.splitlines():
        s = line.strip()
        m = MEM_LINE_RE.match(s)
        if not m:
            continue
        addr = int(m.group(1), 16)
        rest = m.group(2)
        tokens = []
        for tok in rest.split():
            if re.fullmatch(r"[0-9A-Fa-f]{2,}", tok):
                tokens.append(tok)
            else:
                break
        if tokens:
            out.append((addr, tokens))
    return out


def tokens_to_bytes(tokens, width):
    """按宽度把 hex token 转成内存字节（小端字/双字需翻转）。

    width 1/8 → 每 token 2 hex = 1 字节（直序）；
    width 2/16 → 每 token 4 hex = 1 个 LE 半字 → 翻转；
    width 4/32 → 每 token 8 hex = 1 个 LE 字 → 翻转。
    """
    buf = bytearray()
    for t in tokens:
        b = bytes.fromhex(t)
        if width in (2, 16, 4, 32):
            b = b[::-1]
        buf += b
    return bytes(buf)


def assemble_region_lines(lines, region_addr, region_len, width=8):
    """把落在 [region_addr, region_addr+len) 内的行按地址拼接成连续字节。"""
    rel = [(a, toks) for a, toks in lines if region_addr <= a < region_addr + region_len]
    rel.sort(key=lambda x: x[0])
    buf = bytearray()
    for _a, toks in rel:
        buf += tokens_to_bytes(toks, width)
    return bytes(buf[:region_len])


# ------------------------- Intel HEX 解析（verify .hex 用） -------------------------
def parse_intel_hex(path):
    """解析 Intel HEX 为已排序的 [(addr, bytes), ...] 数据记录（处理 04 扩展线性地址）。"""
    records = []
    hi = 0
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line.startswith(":"):
                continue
            body = line[1:]
            if len(body) < 10:
                continue
            count = int(body[0:2], 16)
            addr = int(body[2:6], 16)
            rtype = int(body[6:8], 16)
            data_hex = body[8:8 + count * 2]
            if rtype == 0:
                base = (hi << 16) | addr
                records.append((base, bytes.fromhex(data_hex)))
            elif rtype == 4:
                hi = int(data_hex, 16)
            elif rtype == 1:
                break
    records.sort(key=lambda x: x[0])
    return records


def build_verify_hex_script(records, device="S32K144", iface="1", speed=4000):
    """一次会话读回所有 HEX 记录区间，便于逐条比对（R6 读回校验）。"""
    lines = build_connect_lines(device, iface, speed)
    lines += ["g"]
    for addr, data in records:
        lines.append(f"mem 0x{addr:08X},{len(data)}")
    lines.append("q")
    return "\n".join(lines) + "\n"


def compare_hex(out, records):
    """把读回输出与 HEX 记录逐条比对，返回 [{addr, len, match, first_mismatch_offset}]。"""
    rows = parse_mem_output(out)
    results = []
    for addr, data in records:
        block = assemble_region(rows, addr, len(data))
        ok = block == data
        miss = None
        if not ok:
            for i in range(min(len(data), len(block))):
                if data[i] != block[i]:
                    miss = i
                    break
        results.append({"addr": "0x%08X" % addr, "len": len(data),
                        "match": ok, "first_mismatch_offset": miss})
    return results

