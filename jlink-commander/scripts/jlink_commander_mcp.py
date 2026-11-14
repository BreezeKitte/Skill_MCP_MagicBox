#!/usr/bin/env python3
# Copyright (c) 2026 BreezeKitte
# SPDX-License-Identifier: MIT

"""jlink-commander MCP —— 把 J-Link Commander 调试技巧暴露成一组 stdio 工具。

工具分两类：
  · 不碰真机（安全）：jlink_selftest / jlink_build_script / jlink_dwarf_resolve
  · 访问真机（按需调用）：jlink_exec / jlink_flash / jlink_nohalt_sample /
                        jlink_write_ram / jlink_read_memory / jlink_save_memory /
                        jlink_verify_flash / jlink_erase_flash / jlink_hw_status /
                        jlink_read_registers
  · 内存读写支持宽度：读 mem8/mem16/mem32（width 8/16/32）；写 w1/w2/w4/w8（width 1/2/4/8）。

铁律（违反会读出假数据甚至误判固件故障，见 references/jlink_reference.md）：
  B20  connect 复位芯片 → 写 RAM 与采样必须在同一 Commander 会话（见 jlink_write_ram）。
  B22  电机控制 ISR 活跃时 halt 会触发 PDB SEQERR 伪影 → 稳态采样默认 NoHalt，绝不 halt。
  全程只用 Commander，禁用 J-Link GDB。
"""
import os
import sys
import json
import struct
import tempfile
import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from jlink_commander import (
    resolve_exe, run_script, build_connect_lines,
    build_nohalt_sample_script, build_write_ram_script, build_flash_script,
    parse_mem_output, assemble_region, resolve_globals, member_offset,
    resolve_elf, selftest,
    build_read_memory_script, build_savebin_script, build_verifybin_script,
    build_erase_script, build_regs_script,
    parse_mem_lines, assemble_region_lines, parse_intel_hex,
    build_verify_hex_script, compare_hex, PARSE_WIDTH,
)

# --------------------------------------------------------------------------
# 工具实现
# --------------------------------------------------------------------------

def t_selftest(args):
    return selftest(args.get("jlink_exe"))


def t_build_script(args):
    """根据参数生成 Commander 脚本文本（不执行），便于人工审阅/审批后再跑。"""
    kind = args.get("kind", "nohalt_sample")
    device = args.get("device", "S32K144")
    iface = str(args.get("iface", "1"))
    speed = int(args.get("speed", 4000))
    if kind == "connect":
        script = "\n".join(build_connect_lines(device, iface, speed)) + "\nq\n"
    elif kind == "flash":
        script = build_flash_script(args["hex_path"], device, iface, speed,
                                    args.get("reset_after", True))
    elif kind == "write_ram":
        script = build_write_ram_script(int(args["addr"], 0), int(args["value"], 0),
                                        device, iface, speed)
    elif kind == "nohalt_sample":
        regions = _regions_from_args(args)
        writes = _writes_from_args(args)
        script = build_nohalt_sample_script(
            regions, device, iface, speed, args.get("samples", 40),
            args.get("period_ms", 200), args.get("init_ms", 5000),
            args.get("flash_hex"), args.get("reset", False), writes,
            no_halt=not args.get("halt", False))
    else:
        raise ValueError("unknown kind: %s" % kind)
    return {"script": script}


def t_exec(args):
    """执行一段 Commander 命令列表（直接碰真机）。commands 为字符串列表。"""
    cmds = args.get("commands") or []
    if isinstance(cmds, str):
        cmds = cmds.splitlines()
    script = "\n".join(cmds) + "\nq\n"
    out = run_script(args.get("jlink_exe"), script, timeout=int(args.get("timeout", 600)))
    return {"output": out}


def t_flash(args):
    script = build_flash_script(args["hex_path"], args.get("device", "S32K144"),
                                str(args.get("iface", "1")), int(args.get("speed", 4000)),
                                args.get("reset_after", True))
    out = run_script(args.get("jlink_exe"), script, timeout=int(args.get("timeout", 600)))
    return {"output": out}


def t_dwarf_resolve(args):
    axf = args["axf"]
    globals_dict = args.get("globals") or {}
    member_paths = args.get("member_paths") or {}
    if not globals_dict:
        # 只解析显式给定的全局名
        names = set(args.get("names") or [])
        if names:
            globals_dict = {n: None for n in names}
    if not globals_dict and not member_paths:
        raise ValueError("需提供 globals（全局名->类型）或 names 或 member_paths")
    r = resolve_elf(axf, globals_dict, member_paths)
    return r


def _regions_from_args(args):
    """从 axf+DWARF 自动建地区，或从显式 addr_map 建地区。返回 [(name,addr,len)]。"""
    axf = args.get("axf")
    if axf and (args.get("regions_spec") or args.get("globals")):
        r = resolve_elf(axf, args.get("globals") or {},
                        args.get("member_paths") or {})
        out = []
        for spec in (args.get("regions_spec") or []):
            g = spec["global"]
            base = r["globals"].get(g, {}).get("addr")
            if base is None:
                raise KeyError("global %s 未在 .axf 找到" % g)
            if "member" in spec:
                m = r["members"].get("%s.%s" % (g, spec["member"]))
                if m is None or "abs" not in m:
                    raise KeyError("member %s.%s 未解析" % (g, spec["member"]))
                addr = m["abs"]
            else:
                addr = base
            out.append((spec.get("name", g), addr, int(spec["length"])))
        return out
    # 显式地址表模式（不依赖 DWARF）
    amap = args.get("addr_map") or args.get("regions")
    if amap:
        return [(a.get("name", "r%d" % i), int(a["addr"], 0), int(a["length"]))
                for i, a in enumerate(amap)]
    raise ValueError("需提供 axf+regions_spec 或 addr_map")


def _writes_from_args(args):
    out = []
    for w in (args.get("writes") or []):
        addr = int(w["addr"], 0)
        v = w["value"]
        if isinstance(v, str) and v.lower().startswith("0x"):
            word = int(v, 16) & 0xFFFFFFFF
        elif isinstance(v, float) or (isinstance(v, str) and "." in v):
            word = struct.unpack("<I", struct.pack("<f", float(v)))[0]
        else:
            word = int(v) & 0xFFFFFFFF
        out.append((addr, word))
    return out


def _group_samples(rows, anchor_addr):
    cycles, cur = [], None
    for a, b in rows:
        if a == anchor_addr:
            if cur is not None:
                cycles.append(cur)
            cur = []
        if cur is not None:
            cur.append((a, b))
    if cur is not None:
        cycles.append(cur)
    return cycles


def _decode_fields(block_bytes, decode):
    out = {}
    for d in (decode or []):
        off = int(d["offset"])
        region = d.get("region")
        base = block_bytes.get(region, b"") if region else b""
        if len(base) < off + 4:
            continue
        t = d["type"]
        raw = base[off:off + 4]
        if t == "f32":
            val = struct.unpack_from("<f", raw)[0]
        elif t == "i32":
            val = struct.unpack_from("<i", raw)[0]
        elif t == "u32":
            val = struct.unpack_from("<I", raw)[0]
        elif t == "i16":
            val = struct.unpack_from("<h", base, off)[0]
        elif t == "u16":
            val = struct.unpack_from("<H", base, off)[0]
        elif t == "i8":
            val = struct.unpack_from("<b", base, off)[0]
        elif t == "u8":
            val = struct.unpack_from("<B", base, off)[0]
        else:
            continue
        if "scale" in d:
            val = val * d["scale"]
        out[d["name"]] = val
    return out


def t_nohalt_sample(args):
    """稳态 NoHalt 采样（核心技巧，默认不 halt 内核）。返回 CSV/JSON 路径与摘要。"""
    regions = _regions_from_args(args)
    writes = _writes_from_args(args)
    script = build_nohalt_sample_script(
        regions, args.get("device", "S32K144"), str(args.get("iface", "1")),
        int(args.get("speed", 4000)), args.get("samples", 40),
        args.get("period_ms", 200), args.get("init_ms", 5000),
        args.get("flash_hex"), args.get("reset", False), writes,
        no_halt=not args.get("halt", False))
    if args.get("dry"):
        return {"script": script}
    out = run_script(args.get("jlink_exe"), script, timeout=int(args.get("timeout", 600)))
    rows = parse_mem_output(out)
    anchor = regions[0][1]
    cycles = _group_samples(rows, anchor)

    # 默认输出到系统临时目录下的固定子目录，避免依赖运行 CWD（MCP 的 CWD 不可预期）
    outdir = args.get("outdir") or os.path.join(
        tempfile.gettempdir(), "jlink_commander_captures")
    os.makedirs(outdir, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = os.path.join(outdir, "jlink_sample_%s.csv" % ts)
    json_path = os.path.join(outdir, "jlink_sample_%s.json" % ts)

    region_names = [n for n, _a, _l in regions]
    decode = args.get("decode")
    headers = region_names + ([d["name"] for d in decode] if decode else [])
    decoded = []
    with open(csv_path, "w") as f:
        f.write(",".join(headers) + "\n")
        for rows_i in cycles:
            block = {}
            for name, a, l in regions:
                block[name] = assemble_region(rows_i, a, l)
            rec = {n: block[n].hex() for n in region_names}
            if decode:
                rec.update(_decode_fields(block, decode))
            decoded.append(rec)
            f.write(",".join(str(rec.get(h, "")) for h in headers) + "\n")
    with open(json_path, "w") as f:
        json.dump({"regions": regions, "samples": decoded}, f, indent=2)

    return {
        "csv": csv_path,
        "json": json_path,
        "samples": len(decoded),
        "mem_blocks_parsed": len(rows),
        "script": script,
    }


def t_write_ram(args):
    """B20：同会话写 RAM + 读回。width 取 1/2/4/8（默认 4=w4）。返回写入值与读回。"""
    addr = int(args["addr"], 0)
    width = int(args.get("width", 4))
    if width not in (1, 2, 4, 8):
        raise ValueError("width 必须是 1/2/4/8")
    v = args["value"]
    if isinstance(v, str) and v.lower().startswith("0x"):
        word = int(v, 16) & ((1 << (width * 8)) - 1)
    elif isinstance(v, float) or (isinstance(v, str) and "." in v):
        word = struct.unpack("<I", struct.pack("<f", float(v)))[0]
    else:
        word = int(v) & ((1 << (width * 8)) - 1)
    script = build_write_ram_script(addr, word, args.get("device", "S32K144"),
                                     str(args.get("iface", "1")),
                                     int(args.get("speed", 4000)),
                                     readback_len=int(args.get("readback_len", width)),
                                     width=width, no_halt=not args.get("halt", False))
    out = run_script(args.get("jlink_exe"), script, timeout=int(args.get("timeout", 120)))
    rows = parse_mem_lines(out)
    block = assemble_region_lines(rows, addr, width, PARSE_WIDTH[width])
    if len(block) >= width:
        readback = int.from_bytes(block[:width], "little")
    else:
        readback = None
    return {"addr": "0x%08X" % addr, "width": width, "written": "0x%0*X" % (width * 2, word),
            "readback": ("0x%0*X" % (width * 2, readback)) if readback is not None else None,
            "match": readback == word, "output": out}


def t_read_memory(args):
    """单次读内存。width 取 8/16/32（默认 8=字节）。no_halt 默认 True（核运行时不冻结）。"""
    addr = int(args["addr"], 0)
    length = int(args.get("length", 4))
    width = int(args.get("width", 8))
    if width not in (8, 16, 32):
        raise ValueError("width 必须是 8/16/32")
    script = build_read_memory_script(addr, length, width,
                                      no_halt=not args.get("halt", False),
                                      device=args.get("device", "S32K144"),
                                      iface=str(args.get("iface", "1")),
                                      speed=int(args.get("speed", 4000)))
    if args.get("dry"):
        return {"script": script}
    out = run_script(args.get("jlink_exe"), script, timeout=int(args.get("timeout", 120)))
    rows = parse_mem_lines(out)
    block = assemble_region_lines(rows, addr, length, width)
    return {"addr": "0x%08X" % addr, "length": length, "width": width,
            "bytes_hex": block.hex(), "output": out}


def t_save_memory(args):
    """savebin：把目标内存区间存成 .bin 文件（离线分析 / Flash 备份）。"""
    addr = int(args["addr"], 0)
    length = int(args["length"])
    out_path = args["out_path"]
    script = build_savebin_script(addr, length, out_path,
                                  args.get("device", "S32K144"),
                                  str(args.get("iface", "1")),
                                  int(args.get("speed", 4000)))
    out = run_script(args.get("jlink_exe"), script, timeout=int(args.get("timeout", 120)))
    return {"addr": "0x%08X" % addr, "length": length, "out_path": out_path,
            "file_exists": os.path.isfile(out_path), "output": out}


def t_verify_flash(args):
    """R6 读回校验。.bin → verifybin；.hex → 解析后逐记录回读比对。"""
    fpath = args["file"]
    device = args.get("device", "S32K144")
    iface = str(args.get("iface", "1"))
    speed = int(args.get("speed", 4000))
    if fpath.lower().endswith(".bin"):
        addr = int(args.get("addr", "0"), 0)
        script = build_verifybin_script(fpath, addr, device, iface, speed)
        out = run_script(args.get("jlink_exe"), script, timeout=int(args.get("timeout", 120)))
        ok = "Verify OK" in out or "O.K." in out
        return {"file": fpath, "addr": "0x%08X" % addr, "verify_ok": ok, "output": out}
    elif fpath.lower().endswith((".hex", ".ihx")):
        records = parse_intel_hex(fpath)
        script = build_verify_hex_script(records, device, iface, speed)
        out = run_script(args.get("jlink_exe"), script, timeout=int(args.get("timeout", 600)))
        results = compare_hex(out, records)
        all_ok = all(r["match"] for r in results)
        return {"file": fpath, "records": len(results), "all_match": all_ok,
                "results": results, "output": out}
    else:
        raise ValueError("仅支持 .bin 或 .hex 校验")


def t_erase_flash(args):
    """erase：擦除 Flash。不传 addr/length=整片擦除；否则区间擦除。"""
    addr = int(args["addr"], 0) if args.get("addr") is not None else None
    length = int(args["length"]) if args.get("length") is not None else None
    if (addr is None) != (length is None):
        raise ValueError("addr 与 length 必须同时提供或同时省略")
    script = build_erase_script(addr, length, args.get("device", "S32K144"),
                                str(args.get("iface", "1")),
                                int(args.get("speed", 4000)))
    out = run_script(args.get("jlink_exe"), script, timeout=int(args.get("timeout", 300)))
    return {"addr": ("0x%08X" % addr) if addr is not None else None,
            "length": length, "output": out}


def t_hw_status(args):
    """ShowHWStatus：读 J-Link 硬件状态，重点是 VTref（目标供电电压）与 ITarget。
    无需目标板供电即可运行（probe 在 USB 上即可），适合台架动作前的电源预检。"""
    lines = build_connect_lines(args.get("device", "S32K144"),
                                str(args.get("iface", "1")),
                                int(args.get("speed", 4000)))
    lines += ["ShowHWStatus", "q"]
    out = run_script(args.get("jlink_exe"), "\n".join(lines) + "\n",
                     timeout=int(args.get("timeout", 60)))
    return {"output": out}


def t_read_registers(args):
    """regs：显示 CPU 寄存器。注意 regs 会隐式 halt 内核，仅用于固件已停（故障/断点后）
    或可接受短暂冻结的场景，勿在 ISR 活跃时用于稳态观测（见 B22）。"""
    script = build_regs_script(args.get("device", "S32K144"),
                               str(args.get("iface", "1")),
                               int(args.get("speed", 4000)))
    out = run_script(args.get("jlink_exe"), script, timeout=int(args.get("timeout", 60)))
    return {"output": out}


# --------------------------------------------------------------------------
# 工具清单（MCP tools/list）
# --------------------------------------------------------------------------
TOOLS = [
    {"name": "jlink_selftest",
     "description": "环境自检：检查 JLink.exe 是否存在、版本探针（不连真机）、pyelftools 是否可用。安全，无硬件访问。",
     "inputSchema": {"type": "object", "properties": {
         "jlink_exe": {"type": "string", "description": "可选覆盖 JLink 可执行文件路径（默认 JLINK_EXE，否则自动探测最新 SEGGER 安装，最后回退默认路径）"}}}},
    {"name": "jlink_build_script",
     "description": "根据参数生成 Commander 脚本文本（不执行），便于人工审阅后再跑。kind: connect/flash/write_ram/nohalt_sample。",
     "inputSchema": {"type": "object", "properties": {
         "kind": {"type": "string", "enum": ["connect", "flash", "write_ram", "nohalt_sample"]},
         "device": {"type": "string"}, "iface": {"type": "string", "description": "1=SWD 0=JTAG"},
         "speed": {"type": "integer", "description": "kHz"},
         "hex_path": {"type": "string"}, "addr": {"type": "string"}, "value": {"type": "string"},
         "samples": {"type": "integer"}, "period_ms": {"type": "integer"}, "init_ms": {"type": "integer"},
         "axf": {"type": "string"}, "addr_map": {"type": "array"},
         "writes": {"type": "array"}, "flash_hex": {"type": "string"}, "halt": {"type": "boolean"}}}},
    {"name": "jlink_exec",
     "description": "执行一段 Commander 命令列表（直接碰真机）。commands 为字符串或字符串列表。慎用。",
     "inputSchema": {"type": "object", "properties": {
         "commands": {"type": "array", "items": {"type": "string"},
                      "description": "Commander 命令，如 ['device S32K144','si 1','speed 4000','connect','r','q']"},
         "jlink_exe": {"type": "string"}, "timeout": {"type": "integer"}}}},
    {"name": "jlink_flash",
     "description": "烧录固件：connect → r → loadfile → r → (g)。会复位并覆盖 Flash。碰真机。",
     "inputSchema": {"type": "object", "properties": {
         "hex_path": {"type": "string", "description": ".hex 烧录文件路径"},
         "device": {"type": "string"}, "iface": {"type": "string"}, "speed": {"type": "integer"},
         "reset_after": {"type": "boolean"}, "jlink_exe": {"type": "string"}, "timeout": {"type": "integer"}}}},
    {"name": "jlink_dwarf_resolve",
     "description": "解析 ELF/AXF：从 DWARF 取全局变量基址与结构体成员绝对地址（R6 地址重算依据）。只读文件，不碰硬件。",
     "inputSchema": {"type": "object", "properties": {
         "axf": {"type": "string", "description": ".axf/.elf 路径"},
         "globals": {"type": "object", "description": "{全局名: 静态类型名或 null}"},
         "member_paths": {"type": "object", "description": "{全局名: [[label,[path...]],...]}"},
         "names": {"type": "array", "items": {"type": "string"}, "description": "仅解析这些全局的基址"}}}},
    {"name": "jlink_nohalt_sample",
     "description": "稳态 NoHalt 采样（核心技巧）：connect→g→Sleep(init_ms)→[w4 在线单变量]→循环(Sleep+mem NoHalt)→q。默认不 halt 内核（B22）。支持 axf+regions_spec 自动建地区，或显式 addr_map。可选 decode 字段表把原始字节解成数值。碰真机。",
     "inputSchema": {"type": "object", "properties": {
         "device": {"type": "string"}, "iface": {"type": "string"}, "speed": {"type": "integer"},
         "samples": {"type": "integer", "description": "采样帧数"},
         "period_ms": {"type": "integer", "description": "帧间隔 ms"},
         "init_ms": {"type": "integer", "description": "启动后首帧前等待 ms（默认 5000，避免打断 ISR）"},
         "flash_hex": {"type": "string", "description": "采样前先烧录"},
         "axf": {"type": "string", "description": "用于 DWARF 自动解析地区"},
         "globals": {"type": "object"}, "member_paths": {"type": "object"},
         "regions_spec": {"type": "array", "description": "[{global, member?, length, name?}]"},
         "addr_map": {"type": "array", "description": "[{name, addr, length}] 显式地址表"},
         "writes": {"type": "array", "description": "在线单变量 A/B [{addr, value(0x..或float)}]"},
         "decode": {"type": "array", "description": "[{region, type, offset, name, scale?}] 数值解码"},
         "outdir": {"type": "string"}, "halt": {"type": "boolean", "description": "true=改用 halt 模式（危险，电机 ISR 活跃时勿用）"},
         "dry": {"type": "boolean", "description": "只返回脚本不执行"}, "timeout": {"type": "integer"}}}},
    {"name": "jlink_write_ram",
     "description": "B20：同一会话内写 RAM 字并读回校验（connect→g→wN→mem→q）。width 取 1/2/4/8（默认 4=w4），解决 connect 复位冲掉独立写入的问题。碰真机。",
     "inputSchema": {"type": "object", "properties": {
         "addr": {"type": "string", "description": "32 位地址，0x..."},
         "value": {"type": "string", "description": "0x 十六进制或浮点（按 IEEE754 打包成字）"},
         "width": {"type": "integer", "enum": [1, 2, 4, 8], "description": "写位宽：1/2/4/8 对应 w1/w2/w4/w8"},
         "readback_len": {"type": "integer", "description": "读回字节数（默认=width）"},
         "halt": {"type": "boolean", "description": "true=halt 后写读（危险）"},
         "device": {"type": "string"}, "iface": {"type": "string"}, "speed": {"type": "integer"},
         "jlink_exe": {"type": "string"}, "timeout": {"type": "integer"}}}},
    {"name": "jlink_read_memory",
     "description": "单次读内存，默认 NoHalt（核运行时不冻结）。width 取 8/16/32（默认 8=字节；16=mem16 半字；32=mem32 字）。碰真机。",
     "inputSchema": {"type": "object", "properties": {
         "addr": {"type": "string"}, "length": {"type": "integer", "description": "字节数"},
         "width": {"type": "integer", "enum": [8, 16, 32], "description": "读位宽：8/16/32"},
         "halt": {"type": "boolean", "description": "true=halt 后读（危险）"},
         "dry": {"type": "boolean", "description": "只返回脚本不执行"},
         "device": {"type": "string"}, "iface": {"type": "string"}, "speed": {"type": "integer"},
         "jlink_exe": {"type": "string"}, "timeout": {"type": "integer"}}}},
    {"name": "jlink_save_memory",
     "description": "savebin：把目标内存区间存成 .bin 文件（离线分析 / Flash 备份）。语法 SaveBin <file>,<addr>,<NumBytes>。碰真机。",
     "inputSchema": {"type": "object", "properties": {
         "addr": {"type": "string"}, "length": {"type": "integer", "description": "字节数"},
         "out_path": {"type": "string", "description": "输出 .bin 路径"},
         "device": {"type": "string"}, "iface": {"type": "string"}, "speed": {"type": "integer"},
         "jlink_exe": {"type": "string"}, "timeout": {"type": "integer"}}}},
    {"name": "jlink_verify_flash",
     "description": "R6 读回校验：.bin 用 verifybin 校验；.hex 解析后逐记录回读比对（all_match）。烧录后必做。碰真机。",
     "inputSchema": {"type": "object", "properties": {
         "file": {"type": "string", "description": ".bin 或 .hex 文件路径"},
         "addr": {"type": "string", "description": ".bin 校验起始地址（默认 0）"},
         "device": {"type": "string"}, "iface": {"type": "string"}, "speed": {"type": "integer"},
         "jlink_exe": {"type": "string"}, "timeout": {"type": "integer"}}}},
    {"name": "jlink_erase_flash",
     "description": "erase：擦除 Flash。addr 与 length 同时省略=整片擦除；同时提供=区间擦除（Erase <SAddr>,<EAddr>）。碰真机。",
     "inputSchema": {"type": "object", "properties": {
         "addr": {"type": "string", "description": "起始地址（与 length 配对）"},
         "length": {"type": "integer", "description": "字节数（与 addr 配对）"},
         "device": {"type": "string"}, "iface": {"type": "string"}, "speed": {"type": "integer"},
         "jlink_exe": {"type": "string"}, "timeout": {"type": "integer"}}}},
    {"name": "jlink_hw_status",
     "description": "ShowHWStatus：读 J-Link 硬件状态，重点是 VTref（目标供电电压）与 ITarget。无需目标板供电（probe 在 USB 即可），适合台架动作前电源预检。碰真机（仅 probe）。",
     "inputSchema": {"type": "object", "properties": {
         "device": {"type": "string"}, "iface": {"type": "string"},
         "jlink_exe": {"type": "string"}, "timeout": {"type": "integer"}}}},
    {"name": "jlink_read_registers",
     "description": "regs：显示 CPU 寄存器（R0-R15/xPSR/MSP/PSP/LR/PC）。注意会隐式 halt 内核，仅用于固件已停（故障/断点后）或可接受短暂冻结时，勿在 ISR 活跃稳态观测用（见 B22）。碰真机。",
     "inputSchema": {"type": "object", "properties": {
         "device": {"type": "string"}, "iface": {"type": "string"}, "speed": {"type": "integer"},
         "jlink_exe": {"type": "string"}, "timeout": {"type": "integer"}}}},
]

DISPATCH = {
    "jlink_selftest": t_selftest,
    "jlink_build_script": t_build_script,
    "jlink_exec": t_exec,
    "jlink_flash": t_flash,
    "jlink_dwarf_resolve": t_dwarf_resolve,
    "jlink_nohalt_sample": t_nohalt_sample,
    "jlink_write_ram": t_write_ram,
    "jlink_read_memory": t_read_memory,
    "jlink_save_memory": t_save_memory,
    "jlink_verify_flash": t_verify_flash,
    "jlink_erase_flash": t_erase_flash,
    "jlink_hw_status": t_hw_status,
    "jlink_read_registers": t_read_registers,
}


# --------------------------------------------------------------------------
# 协议层：stdio + JSON-RPC 2.0（newline-delimited），与 it6721-control 同范式
# --------------------------------------------------------------------------
def _make_result(rid, result):
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def _make_error(rid, code, message):
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def _handle_tool_call(rid, params):
    name = params.get("name", "")
    args = params.get("arguments") or {}
    if name not in DISPATCH:
        return _make_error(rid, -32602, "Unknown tool: %s" % name)
    try:
        data = DISPATCH[name](args)
    except Exception as exc:  # noqa: BLE001
        return _make_result(rid, {"content": [{"type": "text", "text": json.dumps(
            {"error": str(exc), "type": type(exc).__name__}, ensure_ascii=False)}],
            "isError": True})
    return _make_result(rid, {"content": [{"type": "text", "text": json.dumps(
        data, ensure_ascii=False, default=str)}]})


def handle_message(msg):
    if not isinstance(msg, dict):
        return None
    method = msg.get("method")
    rid = msg.get("id")
    params = msg.get("params") or {}
    if method == "initialize":
        return _make_result(rid, {
            "protocolVersion": params.get("protocolVersion") or "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "Jlink-Commander", "version": "1.2.0"}})
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


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stdin.reconfigure(encoding="utf-8")
    except Exception:
        pass
    out = sys.stdout
    for raw in sys.stdin:
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


if __name__ == "__main__":
    main()
