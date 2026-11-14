# Changelog · jlink-commander

本项目遵循语义化版本（SemVer）。版本标签使用**仓库内命名空间**格式：`jlink-commander/vX.Y.Z`，
以便在本仓库（[`Skill_MCP_MagicBox`](https://github.com/BreezeKitte/Skill_MCP_MagicBox)）内与多个项目独立管理、互不干扰。

---

## [jlink-commander/v1.1.0] - 2026-11-14

### Changed
- **MCP 注册名由 `jlink-commander` 改为 `Jlink-Commander`**（大写 C）：系统里与另一个 `jlink` node MCP
  区分，避免多个 MCP 同名无法辨识。工具调用前缀相应变为 `mcp__Jlink-Commander__jlink_*`。
- **去除个人环境依赖，便于分享**：
  - 新增跨平台启动器 `scripts/jlink_commander_mcp_launcher.{bat,sh}`，自动探测 Python
    （优先 WorkBuddy 自带，其次系统 PATH），不再写死用户名 / Python 版本路径；
    示例连接器配置改为指向启动器。
  - `core.resolve_exe` 自动探测最新 `JLink_V*` 安装（Linux/macOS 探测 `/opt/SEGGER/JLink` 与
    PATH），去掉写死的 `JLink_V988` 路径；`JLINK_EXE` 仍可作覆盖。
  - `jlink_nohalt_sample` 输出目录默认改为系统临时目录下的 `jlink_commander_captures`
    （原先相对 CWD 的 `captures`），避免依赖不可预期的 MCP 工作目录。

### Notes
- 对已在用 v1.0.0 的用户是**破坏性改名**：工具前缀变化，需在连接器管理页重新 Trust `Jlink-Commander`
  （删除旧 `jlink-commander` 连接器）。

---

## [jlink-commander/v1.0.0] - 2026-11-14

### Added
- 初始发布：`jlink-commander` Skill + 纯标准库 stdio MCP 服务（Python）。
- **8 个 MCP 工具**：`jlink_selftest`（环境自检，不碰硬件）、`jlink_build_script`（生成脚本不执行）、
  `jlink_dwarf_resolve`（DWARF 解析全局/成员绝对地址，R6）、`jlink_flash`（烧录）、
  `jlink_nohalt_sample`（稳态 NoHalt 采样 + 可选同会话 `writes` 在线 A/B）、
  `jlink_write_ram`（同会话写 RAM 读回校验，B20）、`jlink_read_memory`（单次 NoHalt 读）、
  `jlink_exec`（执行任意 Commander 命令列表）。
- **核心模块** `scripts/jlink_commander/`：`core.py`（脚本生成 + 执行 + mem 解析）、
  `dwarf.py`（DWARF 全局/成员偏移解析，基于 pyelftools，可选依赖）、`selftest.py`（环境探针）。
- **知识资产** `references/jlink_reference.md`：官方命令速查（J-Link Commander V9.88 一手核对）、
  铁律台账（B20 同会话写读 / B22 禁 halt / B21 Keil 收纳 / R6 地址重算 / stale 数据）、
  标准工作流（烧录 / NoHalt 采样 / 在线 A/B / 定点读）、跨 MCU 可移植方法、安全边界。
- 内置硬经验：源自已验证的 `capture_global.py`（NoHalt 采样）与 `dwarf_offsets.py`（DWARF 解析），
  以及记忆台账 `B20/B21/B22/B23~B26` 与另一模型交叉评审结论；全程**禁用 J-Link GDB**。
- 全源码 MIT 许可 + 逐文件 SPDX 头。

### Notes
- 与 `it6721-control` 同范式（stdio JSON-RPC 2.0、自包含、MIT）。
- 本 skill 只管 J-Link，程控电源由 `it6721-control` 负责；台架动作前/后由后者确保断电。
- DWARF 解析依赖 `pyelftools`（`pip install pyelftools`）；显式 `addr_map` 采样模式不依赖它。
