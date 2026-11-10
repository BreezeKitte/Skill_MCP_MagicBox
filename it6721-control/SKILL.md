---
name: it6721-control
description: "This skill should be used when a task needs to control, monitor, or automate the ITECH IT6721 programmable DC power supply (0-60 V / 0-8 A / 180 W) through the it6721 MCP server. Use it for setting or reading voltage/current, switching the output on/off, running an auto-sweep (递增/递减/往复/跳跃) or multi-step sequence scan, recording measurements to CSV, or diagnosing a non-responsive serial link. Trigger on mentions of IT6721, 程控电源, 直流稳压电源, 电源控制, 负载供电, or any power-supply automation involving this device."
agent_created: true
---

# IT6721 程控电源控制

## Purpose
把 ITECH IT6721 经济型数控直流电源（0–60 V / 0–8 A / 180 W）以一组 MCP 工具的形式暴露出来，
让任意 WorkBuddy 任务都能直接驱动机台或内置模拟器。底层引擎是 IT6721_Console 仓库里的
`it6721` Python 包；本 skill 只说明如何通过 MCP 服务调用它。

## When to use
- 需要设定或读取 IT6721 的电压 / 电流 / 功率。
- 需要开关电源输出（含急停）。
- 需要跑自动扫描（递增 / 递减 / 往复 / 跳跃）或多步序列。
- 需要把测量值记录成 CSV。
- 需要排查串口不通（TTL 电平、线序、通讯地址）。

## How to use
MCP 服务以 **`it6721-control`** 为注册名写入 `~/.workbuddy/it6721-control.json`（连接器管理页实际
读取的每服务配置文件；中央 `~/.workbuddy/mcp.json` 同步列出），stdio 传输。用户在连接器
管理页 **Trust** 启用后，WorkBuddy 会把工具以 `mcp__it6721-control__it6721_*` 形式暴露
（声明名 `it6721_*` 前叠加服务名前缀），直接按完整名调用即可。

典型流程：
1. `it6721_list_ports` — 枚举本机串口，确认端口（或模拟器可用）。
2. `it6721_connect` — 连接。`sim: true` 进入内置模拟器（无需硬件）；否则传端口（如 `COM6`）。
   自适应握手（4800 8N1，仅发 31H 只读帧）先跑；建立合法链路后才允许下发写帧。
3. 真机改动前先 `it6721_set_lock` 带 `on: true`（全局安全门）。
4. `it6721_apply` / `it6721_set_voltage` / `it6721_set_current` / `it6721_output` 驱动电源。
5. `it6721_status` 回读 V/I/P、输出状态、自动微调/序列/记录状态与锁状态。
6. `it6721_set_lock` 带 `on: false` 解锁；结束 `it6721_disconnect`。

完整工具清单、参数与安全边界见 `references/it6721_reference.md`。

## Safety envelope（必须遵守）
- **量程**：0–60 V、0–8 A、≤180 W。任何会越限的设定在到达设备前即被拒绝。
- **链路门**：写帧（20H/21H/23H/24H）仅在 20H 远程模式应答确认后才下发；此前只允许读帧（31H/26H）。
- **全局锁**：锁定时所有写工具被拒，仅急停类（输出 OFF、自动/序列停止、记录停止、解锁）放行。
  真机改动前应先上锁，仅在明确要做那一步改动时解锁。
- **硬件现实**：后面板 DB9 是 **TTL 电平**，必须走 IT-E121/IT-E122 模块或第三方 USB-TTL
  （CH340/CP2102/FT232）+ 交叉线；**严禁标准 RS232（±12 V）直连**。COM 口号随机器/模块变化，
  **绝不写死**——由 `it6721_connect` 自动探测或接受用户给出的端口。

## Notes
- MCP 服务为纯标准库实现（不依赖 pip 包），与 IT6721_Console「零依赖」工程约定一致。
- **自包含**：`it6721` 引擎已内嵌于 `scripts/it6721/`，开箱即用，无需任何环境变量。
- 可选覆盖：用 `IT6721_PACKAGE_DIR` 直接指向自有 `it6721/` 包目录，或用 `IT6721_CONSOLE_DIR`
  指向含 `it6721/` 的 IT6721_Console 仓库根（兼容旧用法）。记录 CSV 落盘目录可用
  `IT6721_DATA_DIR` 覆盖（默认 `~/it6721_recordings`）。
- 安装与版本迭代流程见仓库根 `README.md`：clone → 复制 skill 到
  `~/.workbuddy/skills/it6721-control` → 复制 `examples/it6721-control.mcp.json` 到
  `~/.workbuddy/it6721-control.json` → 在连接器管理页 Trust。
