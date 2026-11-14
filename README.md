# Skill_MCP_MagicBox

一个 **WorkBuddy 技能 / MCP 服务集合仓库（mono-repo）**，把多个可独立安装、独立版本化的
Skill 与 MCP 服务放在一起，方便统一管理、分享与协作。

每个子项目是一个**自包含的目录**，可单独复制安装，互不依赖。

---

## 项目目录

| 项目 | 说明 | 版本 | 路径 |
|---|---|---|---|
| **it6721-control** | ITECH IT6721 程控直流电源（0–60V/8A/180W）的 Skill + 纯标准库 MCP 服务，20 个工具、内置模拟器、三重安全边界 | `it6721-control/v1.0.0` | [`it6721-control/`](it6721-control/) |
| **jlink-commander** | J-Link Commander 实战技巧 Skill + 纯标准库 Python MCP：烧录、不停机 NoHalt 读 RAM、同会话写 RAM 在线 A/B、DWARF 解析绝对地址。**禁用 J-Link GDB** | `jlink-commander/v1.0.0` | [`jlink-commander/`](jlink-commander/) |

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

- [x] **jlink-commander**：已将 J-Link Commander 实战技巧（NoHalt 采样 / 同会话写读 B20 / DWARF 地址重算 R6 / 禁 GDB）封装为 `jlink-commander/` 子项目（Skill + Python MCP，`jlink-commander/v1.0.0`）。本机另有一个第三方 npm 包 `jlink-mcp@0.8.0` 仅作通用 Commander 封装，与本 skill 互补、不冲突。
- [ ] 工具声明名去重：当前 `it6721-control` 暴露为 `mcp__it6721-control__it6721_*`，服务名与工具前缀重复 `it6721`；如需更短名字可机械去掉工具声明前缀。
- [ ] 真机写指令级实测文档（判通硬指标达成后的端到端范例）。
