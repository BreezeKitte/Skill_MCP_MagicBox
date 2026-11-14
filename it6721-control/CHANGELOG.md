# Changelog · it6721-control

本项目遵循语义化版本（SemVer）。版本标签使用**仓库内命名空间**格式：`it6721-control/vX.Y.Z`，
以便在本仓库（[`Skill_MCP_MagicBox`](https://github.com/BreezeKitte/Skill_MCP_MagicBox)）内与多个项目独立管理、互不干扰。

---

## [it6721-control/v1.1.0] - 2026-11-14

### Changed
- **去除个人环境依赖（可分享给他人的关键）**：新增跨平台启动器 `scripts/it6721_mcp_launcher.{bat,sh}`，自动探测 Python 解释器（优先 WorkBuddy 自带 `~/.workbuddy/binaries/python/versions/*/python.exe`，其次系统 PATH 的 `python`/`py`），并基于启动器自身目录定位 `it6721_mcp.py`。
- 示例连接器配置 `examples/it6721-control.mcp.json` 的 `command` 由写死的 `python` + `C:\Users\<你的用户名>\...` 改为指向启动器，用户只需替换 `<你的用户名>` 即可，**无需手动改 Python 版本或脚本绝对路径**。
- README 安装步骤去掉「手动改 args 路径」与「python 必须在 PATH」提示；`command` 说明改为启动器自动探测。
- `it6721_mcp.py` 的 `serverInfo.version` 由 `1.0.0` 升到 `1.1.0`。

### Notes
- MCP 名称 `it6721-control` 保持不变：当前系统中**没有**与之重名的其他 MCP，无辨识冲突（命名规则仅要求"唯一可辨识"，本名已满足）。
- 其余代码零改动：MCP 服务本就基于 `__file__` 相对定位内嵌 `it6721/` 包与 `~/it6721_recordings` 数据目录，已具备可移植性。

---

## [it6721-control/v1.0.0] - 2026-11-10

### Added
- 初始发布：`it6721-control` Skill + 纯标准库 stdio MCP 服务。
- 内嵌 `it6721` 控制引擎 **v2.2.2**（零第三方依赖，自包含，默认即可运行）。
- **20 个 MCP 工具**：端口枚举、连接/断开、参数读写、输出控制（含急停）、自动微调（递增/递减/往复/跳跃）、多步序列、CSV 记录、全局锁、状态诊断。
- **三重安全边界**（继承自引擎，不可绕过）：量程 0–60 V / 0–8 A / ≤180 W；写帧前的 20H 远程模式应答链路门；全局锁。
- 内置 `sim` 模拟器，无需硬件即可演示与自测。
- 全源码 MIT 许可 + 逐文件 SPDX 头（`# Copyright (c) 2026 BreezeKitte` / `SPDX-License-Identifier: MIT`）。

### Notes
- 工具在 WorkBuddy 中暴露为 `mcp__it6721-control__it6721_*`（服务名与工具前缀重复出现 `it6721`，功能不受影响；未来可去掉工具声明前缀）。
- JLink 为第三方 npm 包，本仓库未纳入源码；后续可加 `jlink/` 子项目提供配置模板。
