---
name: jlink-commander
description: "This skill should be used when a task needs to drive an ARM Cortex-M target through J-Link Commander (NOT J-Link GDB) — flashing firmware, live-reading global variables / struct fields from RAM while the core keeps running (NoHalt), single-variable A/B tuning by writing RAM in the same Commander session, or parsing DWARF from an .axf/.elf to resolve absolute addresses after code changes. Trigger on mentions of J-Link, JLink, Commander, 烧录, 在线采样, 读内存, NoHalt, 改 RAM 变量, 不停机观测, 无感/观测器调试, or any Cortex-M live-debug task that must avoid halting the firmware's ISR."
agent_created: true
---

# J-Link Commander 调试技巧

## Purpose
把在 S32K144 FOC 电机控制（以及其它 Cortex-M 工程）调试中沉淀下来的 **J-Link Commander
实战技巧** 封装成 Skill + 纯标准库 Python MCP，让任意 WorkBuddy 任务都能：
- 烧录固件（`loadfile`）；
- **不停机**读取固件运行时的全局变量 / 结构体字段（NoHalt 读 RAM，绝不 halt 内核）；
- 在同一 Commander 会话内写 RAM 做在线单变量 A/B（规避 `connect` 复位冲掉独立写入）；
- 用 DWARF（.axf/.elf）自动解析全局/成员绝对地址，改结构后无需手写死地址。

底层永远是 **J-Link Commander 脚本 + `JLink.exe`**，**绝不使用 J-Link GDB**——GDB 会
halt 内核、冻住电机控制 ISR，导致 PDB 序列错误等伪影（见 references/jlink_reference.md §2 B22）。

## When to use
- 需要烧录 / 复位 Cortex-M 目标。
- 需要在固件**运行**时读取变量（观测器角度、电流、状态机、故障标志等），且不能打断 ISR。
- 需要在线改一个 `volatile` 配置量做单变量对照，而不重烧固件。
- 改完源码/结构后，需要重新解析全局变量地址（DWARF）。

## How to use
MCP 服务以 **`Jlink-Commander`** 注册名（注意大写 C，与系统里另一个 `jlink` node MCP 区分）
写入 `~/.workbuddy/Jlink-Commander.json`（并同步列入中央 `~/.workbuddy/mcp.json`），stdio 传输。
用户在连接器管理页 **Trust** 启用后，工具以 `mcp__Jlink-Commander__jlink_*` 形式暴露，直接按完整名调用。

典型流程：
1. `jlink_selftest` — 自检 JLink.exe 是否存在、版本探针（不连真机）、pyelftools 是否就绪。
2. `jlink_hw_status` — 台架动作前电源预检：读 `VTref`/`ITarget`（probe 在 USB 即可，无需目标供电）。
3. `jlink_dwarf_resolve` — 解析 `.axf` 拿到全局基址与成员偏移（R6 地址重算，必须在改结构后做）。
4. `jlink_nohalt_sample` — 核心：给定 `axf`+`regions_spec`（或显式 `addr_map`），稳态
   NoHalt 采样 N 帧，输出 CSV/JSON；可带 `writes` 做同一会话内的在线单变量 A/B。
5. `jlink_write_ram` — 同会话写 RAM 字并读回校验（B20 铁律）。`width` 取 1/2/4/8 映射到 w1/w2/w4/w8。
6. `jlink_read_memory` — 单次读内存，默认 NoHalt；`width` 取 8/16/32 映射到 mem/mem16/mem32。
7. `jlink_flash` — 烧录；烧录后**务必 `jlink_verify_flash` 读回校验（R6）**。
8. `jlink_save_memory` — `savebin` 把内存区间存成 .bin（离线分析 / Flash 备份）。
9. `jlink_erase_flash` — `erase` 整片或区间擦除（烧录前清保护扇区时用）。
10. `jlink_read_registers` — `regs` 读 CPU 寄存器；**会隐式 halt 内核**，仅故障/断点后或非实时场景用（见 B22）。
11. `jlink_exec` — 执行任意 Commander 命令列表（RSetType / exec / SetBP 等高级用法走这里）。

命令速查与每条命令的实测语法见 `references/jlink_reference.md`（§6 收录宽度读写、savebin/
verifybin/erase、ShowHWStatus、regs/断点、RSetType/exec 等常用命令）。

完整命令速查、铁律（B20/B21/B22…）、标准工作流与可移植方法见
`references/jlink_reference.md`。

## Safety envelope（必须遵守）
- **禁用 J-Link GDB**：所有读/写走 Commander，不允许 `halt` 内核的稳态采样（`jlink_nohalt_sample`
  默认 `halt=false`，加 `NoHalt` 读内存）。
- **同会话写读**（B20）：写 RAM 必须在 `connect` 之后的同一脚本里完成读回，否则下一次
  `connect` 会复位冲掉写入。
- **地址靠 DWARF**（R6）：改结构后旧地址漂移，必须用 `jlink_dwarf_resolve` 重算，禁止写死。
- **电源归 it6721-control 管**：本 skill 不碰程控电源；台架动作前/后由 it6721-control
  确保 `output=false` 并读回 `link_lost=false`。任何烧录/写 RAM 前先确认前置条件
  （镜像存在且 sha 一致、接线正确、电源状态已知）。
- **DWARF 依赖 pyelftools**：未装时 `jlink_dwarf_resolve` 报错，但显式 `addr_map` 模式不受影响。
- **`regs`/`SetBP`/`SetWP` 会 halt 内核**：`jlink_read_registers` 与 `jlink_exec` 里的
  断点/观察点命中即冻结 Cortex-M 内核。仅在固件已停（故障/断点后）或可接受短暂冻结时
  使用；**电机 ISR 活跃时禁用**（B22：halt → PDB SEQERR 伪影）。
- **烧录后必做 R6 读回校验**：`jlink_flash` 之后立即 `jlink_verify_flash`（.hex 自动逐记录
  比对），`all_match=true` 才判有效；任一条不符立即停，不轻信 "loadfile 成功"。

## Notes
- MCP 服务为纯标准库 Python（仅 DWARF 解析可选依赖 pyelftools），与 it6721-control 同范式。
- **自包含 / 零个人环境依赖**：核心模块 `scripts/jlink_commander/`（core/dwarf/selftest）开箱即用；
  Python 由启动器 `scripts/jlink_commander_mcp_launcher.{bat,sh}` 自动探测（优先 WorkBuddy 自带、
  其次系统 PATH），不再写死用户名 / Python 版本路径；J-Link 可执行文件由 `core.resolve_exe`
  **自动探测最新 `JLink_V*` 安装**（环境变量 `JLINK_EXE` 仍可覆盖）。因此分享给他人时只需把
  连接器配置的 `command` 指向其本机启动器路径即可。
- 安装与版本迭代见仓库根 `README.md`：clone → 复制 skill 到
  `~/.workbuddy/skills/jlink-commander` → 复制 `examples/jlink-commander.mcp.json` 到
  `~/.workbuddy/Jlink-Commander.json` → 在连接器管理页 Trust。
