# jlink-commander

把 **J-Link Commander** 在 S32K144 FOC 电机控制等 Cortex-M 调试中沉淀的实战技巧，封装成
**Skill + 纯标准库 Python MCP**：烧录、不停机读 RAM 全局/结构体字段（NoHalt）、同一会话
内写 RAM 做在线单变量 A/B、DWARF 解析全局地址。核心立场：**只用 Commander，禁用 J-Link GDB**
（GDB 会 halt 内核、冻住 ISR 触发 PDB 序列错误伪影）。

版本：`jlink-commander/v1.2.0` ｜ 路径：`jlink-commander/`
注册名（MCP）：**`Jlink-Commander`**（连接器里显示的名字，大写 C，与另一个 `jlink` node MCP 区分）

---

## 安装（从本仓库）

```bash
# 1) 克隆（只需一次；所有项目共用同一份克隆）
git clone https://github.com/BreezeKitte/Skill_MCP_MagicBox.git
cd Skill_MCP_MagicBox

# 2) 把 jlink-commander 复制到 WorkBuddy 用户级技能目录
cp -r jlink-commander "$HOME/.workbuddy/skills/jlink-commander"

# 3) 复制连接器配置模板，改成你本机的启动器绝对路径
cp jlink-commander/examples/jlink-commander.mcp.json "$HOME/.workbuddy/Jlink-Commander.json"
#   然后编辑该文件，把 command 里的 <你的用户名> 改成你自己的用户名（脚本路径由启动器 %~dp0 推算，无需改）
```

然后**完全重启 WorkBuddy** → 打开**连接器管理页（右上角）→ 自定义连接器** → 找到
`Jlink-Commander` → 点 **Trust** 启用。工具以 `mcp__Jlink-Commander__jlink_*` 暴露。

> 中央 `~/.workbuddy/mcp.json` 也同步列出该服务（与 `it6721-control` 并列，二者注册名不同、互不混淆）。

---

## 前置依赖

- **J-Link Software and Documentation Pack**：脚本会**自动探测最新安装的 `JLink_V*`**（不再写死版本号）；
  特殊安装位可用环境变量 `JLINK_EXE` 覆盖。Linux/macOS 下也会探测 `/opt/SEGGER/JLink` 与 PATH。
- **Python ≥ 3.10**：由随附启动器 `scripts/jlink_commander_mcp_launcher.{bat,sh}` 自动定位
  （优先 WorkBuddy 自带 python，其次系统 PATH 的 `python`/`py`）。**无需把 Python 加进 PATH**，
  也无需写死任何用户名 / 版本路径——这正是本 skill 可分享给他人的关键。
- **可选**：`pip install pyelftools` —— 仅 `jlink_dwarf_resolve` 需要；显式 `addr_map` 采样模式
  不依赖它。

---

## 工具清单（13 个）

| 工具 | 碰真机？ | 作用 |
|---|---|---|
| `jlink_selftest` | 否 | 环境自检：JLink.exe 是否存在、版本探针、pyelftools 就绪 |
| `jlink_build_script` | 否 | 按参数生成 Commander 脚本文本（不执行），便于审阅 |
| `jlink_dwarf_resolve` | 否（只读文件） | 解析 .axf/.elf，取全局基址 + 成员绝对地址（R6） |
| `jlink_flash` | 是 | `connect → r → loadfile → r → g` 烧录 |
| `jlink_nohalt_sample` | 是 | 稳态 NoHalt 采样，可选同会话 `writes` 做 A/B，输出 CSV/JSON |
| `jlink_write_ram` | 是 | 同会话写 RAM 字并读回校验（B20）；`width` 1/2/4/8 → w1/w2/w4/w8 |
| `jlink_read_memory` | 是 | 单次读内存，默认 NoHalt；`width` 8/16/32 → mem/mem16/mem32 |
| `jlink_save_memory` | 是 | `savebin` 把内存区间存成 .bin（离线分析 / Flash 备份） |
| `jlink_verify_flash` | 是 | R6 读回校验：.bin→verifybin；.hex→解析后逐记录回读比对 |
| `jlink_erase_flash` | 是 | `erase` 整片或区间擦除 |
| `jlink_hw_status` | 仅 probe | `ShowHWStatus` 读 VTref/ITarget，台架前电源预检 |
| `jlink_read_registers` | 是 | `regs` 读 CPU 寄存器（会隐式 halt，非实时场景用） |
| `jlink_exec` | 是 | 执行任意 Commander 命令列表（RSetType/exec/SetBP 等高级用法） |

> 内存读写宽度命令（`mem8/mem16/mem32`、`w1/w2/w8`）与 `savebin/verifybin/erase/
> ShowHWStatus/regs` 的精确语法见 `references/jlink_reference.md` §6（均取自 JLink.exe V9.88
> 内置 `?` 帮助，非凭记忆）。

---

## 冒烟自检（不碰硬件）

```bash
# 1) 定位一个 Python 解释器（WorkBuddy 自带或系统 PATH 上的 python3）
PY=$(ls -d ~/.workbuddy/binaries/python/versions/*/python.exe 2>/dev/null | sort -r | head -1)
: "${PY:=$(command -v python3 || command -v python)}"
SK=~/.workbuddy/skills/jlink-commander/scripts   # 或本仓库 jlink-commander/scripts

# 2) 语法编译
"$PY" -m py_compile $SK/jlink_commander_mcp.py $SK/jlink_commander/*.py

# 3) 环境自检（会探针 JLink 版本，不连目标）
"$PY" -c "import sys; sys.path.insert(0,'$SK'); from jlink_commander import selftest; import json; print(json.dumps(selftest(), indent=2, ensure_ascii=False))"

# 4) 生成一段 no-halt 采样脚本看看（不执行）
"$PY" -c "import sys; sys.path.insert(0,'$SK'); from jlink_commander import build_nohalt_sample_script as b; print(b([('drv',0x20000000,16)], samples=3, period_ms=100, init_ms=500))"
```

全部无报错即说明服务可用。真机动作在 WorkBuddy 内经由 MCP 工具触发。

---

## 最小用法示例（伪代码）

```text
# 1) 解析 S32K144 工程的观测层地址
jlink_dwarf_resolve(axf="MCSPTE1AK144_PMSM_FOC_2Sh_Keil.axf",
                    globals={"g_obsTel":"ObsTel_t","g_coreCfg":"ObsCoreCfg_t"},
                    member_paths={"g_obsTel":[["dTheta",["dTheta"]],["omegaSelf",["omegaSelf"]]],
                                  "g_coreCfg":[["mode",["mode"]]]})

# 2) 稳态采样 60 帧，并把 mode 在线改成 2.0（自主角度接管），不重烧
jlink_nohalt_sample(axf=..., samples=60, period_ms=250, init_ms=5000,
                    regions_spec=[{global:"g_obsTel",member:"dTheta",length:4},
                                  {global:"g_obsTel",member:"omegaSelf",length:4}],
                    writes=[{addr:"0x1FFF8974", value:"2.0"}],
                    decode=[{region:"g_obsTel",type:"f32",offset:80,name:"dTheta"},
                            {region:"g_obsTel",type:"f32",offset:76,name:"omegaSelf"}])
```

---

## 铁律速记（详见 `references/jlink_reference.md`）

- **B20**：`connect` 复位冲掉独立 `w4` → 写 RAM 与读回必须在同一 Commander 会话。
- **B22**：稳态采样默认 `NoHalt`，**绝不 `h` halt 内核**（否则 PDB SEQERR 伪影）。
- **R6**：改结构后地址漂移 → 用 `jlink_dwarf_resolve` 重算，禁止写死。
- **禁 GDB**：全程 Commander，不用 J-Link GDB。

---

## 许可证

[MIT](../../LICENSE) © 2026 BreezeKitte ｜ 源码头带 SPDX：`SPDX-License-Identifier: MIT`
