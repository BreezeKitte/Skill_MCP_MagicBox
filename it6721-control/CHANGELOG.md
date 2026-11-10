# Changelog · it6721-control

本项目遵循语义化版本（SemVer）。版本标签使用**仓库内命名空间**格式：`it6721-control/vX.Y.Z`，
以便在本仓库（[`Skill_MCP_MagicBox`](https://github.com/BreezeKitte/Skill_MCP_MagicBox)）内与多个项目独立管理、互不干扰。

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
