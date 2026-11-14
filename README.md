# Skill_MCP_MagicBox

一个 **WorkBuddy 技能 / MCP 服务集合仓库（mono-repo）**，把多个可独立安装、独立版本化的
Skill 与 MCP 服务放在一起，方便统一管理、分享与协作。

每个子项目是一个**自包含的目录**，可单独复制安装，互不依赖。

---

## 项目目录

| 项目 | 说明 | 版本 | 路径 |
|---|---|---|---|
| **it6721-control** | ITECH IT6721 程控直流电源（0–60V/8A/180W）的 Skill + 纯标准库 MCP 服务，20 个工具、内置模拟器、三重安全边界 | `it6721-control/v1.0.0` | [`it6721-control/`](it6721-control/) |
| **jlink-commander** | J-Link Commander 实战技巧 Skill + 纯标准库 Python MCP：烧录、不停机 NoHalt 读 RAM、同会话写 RAM 在线 A/B、DWARF 解析绝对地址、宽度读写(mem8/16/32,w1-8)、savebin/verifybin/erase、ShowHWStatus 电源预检。**禁用 J-Link GDB**。MCP 注册名 **`Jlink-Commander`**（大写 C，与系统里 `jlink` node MCP 区分） | `jlink-commander/v1.2.0` | [`jlink-commander/`](jlink-commander/) |

> 新增项目时，在仓库根目录下新建一个与项目同名的子目录，结构参照 `it6721-control/`
> （`SKILL.md` + `README.md` + `CHANGELOG.md` + `examples/` + `references/` + `scripts/`）。

---

## 通用安装（安装其中任意一个项目）

```bash
# 1) 克隆（只需一次；所有项目共用同一份克隆）
git clone https://github.com/BreezeKitte/Skill_MCP_MagicBox.git
cd Skill_MCP_MagicBox

# 2) 把目标项目目录复制到 WorkBuddy 用户级技能目录
cp -r <项目名> "$HOME/.workbuddy/skills/<项目名>"

# 3) 复制该项目的连接器配置模板，改成你本机的脚本绝对路径
cp <项目名>/examples/*.mcp.json "$HOME/.workbuddy/<项目名>.json"
```

然后**完全重启 WorkBuddy** → 打开**连接器管理页（右上角）→ 自定义连接器** → 找到对应服务 → 点 **Trust** 启用。

---

## 版本与迭代约定（多项目独立管理）

为了让多个项目在**同一个仓库**里各自独立演进，采用以下约定：

- **命名空间标签**：每个项目的版本标签格式为 `<项目名>/vX.Y.Z`
  （例如 `it6721-control/v1.0.0`）。这样各项目可独立打 tag，互不覆盖。
- **MCP / Skill 注册名必须唯一、可辨识**（重要）：每个新增的 Skill + MCP，其 MCP `serverInfo.name`
  与连接器 key（即 `~/.workbuddy/mcp.json` 里的键，以及 `examples/*.mcp.json` 的键）**必须使用一个
  与其他 MCP 不重名、人类一眼能分辨的名字**，不允许多个 MCP 共用一个泛化名字（否则在连接器列表里
  无法区分）。命名建议：`项目功能 + 实现方式` 的清晰组合，例如本仓库的 `it6721-control`、
  `Jlink-Commander`。Skill 目录名可保持 kebab-case（如 `jlink-commander`），但暴露在系统里的 MCP
  注册名要足够独特。改动 serverInfo.name 属**破坏性变更**，须在 CHANGELOG 标注并提醒重新 Trust。
- **每个项目自带 `CHANGELOG.md`**，只记录该项目的变更；仓库级变动（如新增项目、改索引）才动 root 提交。
- **分支**：`main` = 稳定基线，`dev` = 日常迭代（可针对某项目开 `dev-<项目名>` 分支）。
- **提交规范**：conventional commits（`feat:` / `fix:` / `docs:` / `chore:`），提交信息建议带项目名前缀，如 `feat(it6721-control): ...`。
- **更新某个项目的工作流**：
  1. 在 `dev`（或 `dev-<项目名>`）改动该项目；
  2. 本地跑自检（见各项目 README 的"冒烟自检"）；
  3. 规范提交 → `git tag <项目名>/vX.Y.Z` → 推分支与 tag；
  4. 稳定后合入 `main`，并把 `scripts/` 重新同步到 `~/.workbuddy/skills/<项目名>/`。

---

## 许可证

[MIT](LICENSE) © 2026 BreezeKitte

仓库级 LICENSE 覆盖整体；每个源码文件头另带 SPDX 标识：

```text
# Copyright (c) 2026 BreezeKitte
# SPDX-License-Identifier: MIT
```

源码被单独复制、分发时，许可声明随文件一起走，符合 MIT 要求。

---

## 路线图

- [x] **jlink-commander**：已将 J-Link Commander 实战技巧（NoHalt 采样 / 同会话写读 B20 / DWARF 地址重算 R6 / 禁 GDB）封装为 `jlink-commander/` 子项目（Skill + Python MCP，`jlink-commander/v1.2.0`）。MCP 注册名 `Jlink-Commander`，并提供跨平台启动器、自动探测 J-Link/Python，便于分享。v1.2.0 吸收官方 V9.88 常用命令（宽度读写、savebin/verifybin/erase、ShowHWStatus 电源预检、regs），共 13 个工具。本机另有一个第三方 npm 包 `jlink-mcp@0.8.0` 仅作通用 Commander 封装，与本 skill 互补、不冲突。
- [x] **MCP 注册名唯一化约定**：已写入上方「版本与迭代约定」，新增 Skill/MCP 必须暴露不重名、可辨识的注册名，避免系统里多个 MCP 同名无法区分。
- [ ] 工具声明名去重：当前 `it6721-control` 暴露为 `mcp__it6721-control__it6721_*`，服务名与工具前缀重复 `it6721`；如需更短名字可机械去掉工具声明前缀。
- [ ] 真机写指令级实测文档（判通硬指标达成后的端到端范例）。
