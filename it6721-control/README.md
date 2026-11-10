# it6721-control

把 **ITECH IT6721 经济型数控直流电源**（0–60 V / 0–8 A / 180 W）封装成一个 **WorkBuddy Skill + 纯标准库 MCP 服务**，让任意 WorkBuddy 任务都能直接驱动机台或内置模拟器。开箱即用、零 pip 依赖、可独立安装。

本项目是 [`Skill_MCP_MagicBox`](https://github.com/BreezeKitte/Skill_MCP_MagicBox) 仓库下的一个**独立子项目**。本目录（`it6721-control/`）可单独复制安装，不依赖仓库内其他项目。

---

## 这是什么

| 角色 | 说明 |
|---|---|
| **Skill** | 本目录，WorkBuddy 的技能描述与文档，决定"何时 / 如何"调用下面的 MCP 工具。 |
| **MCP 服务** | `scripts/it6721_mcp.py`，stdio 上的 JSON-RPC 2.0 服务，暴露 **20 个工具**。 |
| **内嵌引擎** | `scripts/it6721/`，IT6721_Console 的 `it6721` 控制包（**v2.2.2**），随项目分发，**无需任何环境变量即可运行**。 |

**名词解释**
- *MCP（Model Context Protocol）*：一套让 AI 助手调用外部工具/数据的标准协议。这里用来把电源控制能力暴露给 WorkBuddy。
- *stdio*：通过标准输入/输出传 JSON 消息的本地进程通信方式，不开网络端口，最轻量、最安全。
- *Skill*：WorkBuddy 的"技能包"，含触发条件、使用说明与参考资料；放在 `~/.workbuddy/skills/` 后，相关任务会自动加载。

---

## 特性

- **20 个 MCP 工具**：枚举串口、连接/断开、读写电压电流、开关输出（含急停）、自动扫描（递增/递减/往复/跳跃）、多步序列、CSV 记录、链路诊断。
- **三重安全边界**（继承自 `it6721` 引擎，不可绕过）：量程 0–60 V / 0–8 A / ≤180 W；写帧前必须先过 20H 远程模式应答；全局锁开启时仅放行急停类操作。
- **内置模拟器**：`sim: true` 即可在无硬件情况下演示与自测，不碰串口。
- **自包含**：`it6721` 引擎已内嵌，开箱即用。
- **纯标准库**：MCP 服务与引擎均只用 Python 标准库；引擎串口层 Windows 用 `ctypes`、Linux/macOS 用 `termios`，若系统已装 `pyserial` 会优先使用，**未装则自动回退到标准库实现**——因此**无需 `pip install` 任何东西**。

---

## 架构

```
WorkBuddy 任务
   │  (自然语言触发 it6721-control skill)
   ▼
连接器管理页 → it6721-control MCP 服务 (stdio)
   │
   ▼
it6721_mcp.py  (纯标准库 JSON-RPC 2.0)
   │  复用
   ▼
内嵌 it6721 引擎 (protocol / device / autotune / sequence / recorder / serial_port)
   │
   ▼
串口 / USB-TTL  ←→  IT6721 电源
```

---

## 目录结构

```
it6721-control/                  # ← 整个目录复制到 ~/.workbuddy/skills/
├── SKILL.md
├── README.md                    # 本文件
├── CHANGELOG.md                 # 本项目独立版本记录
├── examples/
│   └── it6721-control.mcp.json  # 连接器配置模板（复制到 ~/.workbuddy/）
├── references/
│   └── it6721_reference.md      # 20 工具完整参数表 + 安全边界
└── scripts/
    ├── it6721_mcp.py            # MCP 服务（纯标准库）
    └── it6721/                  # 内嵌引擎（随 IT6721_Console v2.2.2 打包）
        ├── __init__.py
        ├── protocol.py          # 26 字节二进制帧编解码
        ├── device.py            # 设备会话 / 功率校验 / 轮询
        ├── serial_port.py       # 零依赖跨平台串口层
        ├── autotune.py          # 自动微调（递增/递减/往复/跳跃）
        ├── sequence.py          # 序列扫描
        ├── recorder.py          # CSV 记录
        └── ...
```

---

## 环境要求

| 项 | 要求 |
|---|---|
| Python | **3.10+**（仅标准库，无需 pip install） |
| 操作系统 | Windows（`ctypes`）/ Linux·macOS（`termios`）均可 |
| 真机硬件（仅真机需要） | IT6721 电源 + USB-TTL 模块（CH340 / CP2102 / FT232）或原装 IT-E121/IT-E122；**严禁标准 RS232(±12V) 直连 TTL 电平的后面板 DB9** |

---

## 安装（3 步，仅安装本项目）

```bash
# 1) 克隆仓库（只需一次；其他项目共用同一份克隆）
git clone https://github.com/BreezeKitte/Skill_MCP_MagicBox.git
cd Skill_MCP_MagicBox

# 2) 把本项目复制到 WorkBuddy 用户级技能目录
cp -r it6721-control "$HOME/.workbuddy/skills/it6721-control"

# 3) 复制连接器配置模板，并改成你本机的脚本路径
cp it6721-control/examples/it6721-control.mcp.json "$HOME/.workbuddy/it6721-control.json"
#   编辑该文件，把 args 里的路径换成你实际的 it6721_mcp.py 绝对路径
```

然后：

1. **完全重启 WorkBuddy**（连接器列表在启动时读取，重启才能扫到新文件）。
2. 打开 **连接器管理页（右上角）→ 自定义连接器**，找到 **`it6721-control`** → 点 **Trust** 启用。
   > 这一步的信任戳由 WorkBuddy 按命令算 hash 写入 `mcp-approvals.json`，必须你本人点，无法代点。

启用后，任务里提到"IT6721 / 程控电源 / 电源控制"就会自动按 `mcp__it6721-control__it6721_*` 形式调用工具。

**关于 `command`**：示例配置用 `python`（假设 `python` 已在 PATH）。若你的 `python` 不在 PATH，把 `command` 改成 Python 3.10+ 解释器的**绝对路径**（例如 WorkBuddy 托管 Python 或系统 Python）。

---

## 配置项（环境变量，全部可选）

服务**默认**使用内嵌引擎、记录落盘到 `~/it6721_recordings`，无需任何环境变量。仅在以下场景覆盖：

| 变量 | 作用 | 优先级 |
|---|---|---|
| `IT6721_PACKAGE_DIR` | 直接指向你自有的 `it6721/` 包目录（最显式） | 最高 |
| `IT6721_CONSOLE_DIR` | 指向含 `it6721/` 的 IT6721_Console 仓库根（兼容旧用法） | 其次 |
| `IT6721_DATA_DIR` | 覆盖 CSV 记录落盘目录（默认 `~/it6721_recordings`） | 独立 |

> 三者均未设置时，服务自动回退到内嵌的 `scripts/it6721/`。

---

## 安全边界（务必遵守）

- **量程**：0–60 V、0–8 A、≤180 W；任何越限设定在到达设备前即被拒绝。
- **链路门**：写帧（20H/21H/23H/24H）仅在 20H 远程模式应答确认后才下发；此前只允许读帧（31H/26H）。真实硬件连接前，必须先拿到 ≥4/5 次合法 26 字节应答帧才允许下发写帧。
- **全局锁**：`it6721_set_lock on:true` 后，所有写工具被拒，仅急停类（输出 OFF、自动/序列停止、记录停止、解锁）放行。真机改动前先上锁，仅在做具体改动那一步 `on:false` 解锁。
- **硬件现实**：后面板 DB9 是 **TTL 电平**，必须走 USB-TTL 模块或原装转接；**严禁 RS232 直连**。COM 口号随机器/模块变化，**绝不写死**——由 `it6721_connect` 自动探测或你显式传入。

---

## 工具速览

20 个工具分为：端口枚举、连接管理、参数设定、输出控制、自动微调、序列扫描、CSV 记录、安全锁、状态诊断。完整参数与返回值见 [`references/it6721_reference.md`](references/it6721_reference.md)。

---

## 版本

本项目遵循语义化版本（SemVer），使用**仓库内命名空间标签** `it6721-control/vX.Y.Z`，与仓库内其他项目互不干扰。

| 维度 | 当前值 | 含义 |
|---|---|---|
| **本项目版本** | `it6721-control/v1.0.0` | 本 Skill + MCP 封装的版本 |
| **内嵌引擎版本** | `it6721 v2.2.2` | 打包进 `scripts/it6721/` 的 IT6721_Console 控制包版本 |

完整变更记录见 [`CHANGELOG.md`](CHANGELOG.md)。

**冒烟自检**（不碰硬件）：
```bash
python it6721-control/scripts/it6721_mcp.py   # 应无报错地等待 stdin；Ctrl-C 退出
# 或发送一次 initialize 看 serverInfo.name == "it6721-control"、tools 数量 == 20
```

---

## 许可证

[MIT](../../LICENSE) © 2026 BreezeKitte

本项目所有源码文件（含内嵌 `it6721/` 引擎）均在文件头带有：

```text
# Copyright (c) 2026 BreezeKitte
# SPDX-License-Identifier: MIT
```

因此源码被单独复制、分发给他人时，许可声明会随文件一起走，符合 MIT 的"在副本中保留版权与许可声明"要求。
