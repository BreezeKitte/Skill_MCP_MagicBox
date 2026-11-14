# J-Link Commander 命令速查与踩坑台账

本文件把在 **S32K144 + GD3000 + LINIX 45ZWN24-40 电机控制** 调试中（含另一模型的
交叉评审）沉淀下来的 J-Link Commander 技巧全部固化。来源标注：一手官方文档
（`UM08001_JLink.pdf`、本机 `JLink.exe V9.88` 内置 `?` 帮助）、工程 `.jlink` 脚本、
`capture_global.py` / `dwarf_offsets.py` 实测、记忆台账 `B20/B21/B22/B23/B24/B25/B26` 与
`proven-false.md`。

> 核心立场：**只用 Commander，禁用 J-Link GDB**。GDB 会 halt 内核、冻住电机控制 ISR，
> 读出假零值并触发 PDB 序列错误伪影（B22）。所有「读 live 变量 / 在线改配置」都走
> Commander 脚本 + `--no-halt` / `NoHalt` 读内存。

---

## 1. 官方命令速查（J-Link Commander V9.88，已实机核对）

| 命令 | 语法 | 说明 / 注意 |
|---|---|---|
| 连接 | `device <Dev>` → `si <n>` → `speed <kHz>` → `connect` | `si 1`=SWD，`si 0`=JTAG；`connect` 会执行 `Reset via AIRCR.SYSRESETREQ`（B20） |
| 复位 | `r` | 系统复位；`loadfile` 前后各一次确保干净启动 |
| 运行/停 | `g`（go）/ `h`（halt） | 见 B22：**稳态验证不要 `h`** |
| 等待 | `Sleep <ms>` | 毫秒；启动早期留 5000ms 等 ISR 跑顺（见 B22） |
| 读内存 | `mem <Addr>,<NumBytes>[ NoHalt]` | **`NumBytes` 实测按十进制解析**；加 `NoHalt` 在核运行时不冻结（核心技巧） |
| 写 RAM | `w4 <Addr>,<Value>` | 32-bit 字，值十六进制；必须与读回在**同一会话**（B20） |
| 烧录 | `loadfile <file>[,<addr>]` | 支持 .hex/.bin/.srec；`verifybin` 另查 |
| 退出 | `q` | 结束会话 |
| 脚本 | `JLink.exe -CommandFile <f> -ExitOnError 0` | `-ExitOnError 0` 出错不立即退出，方便回收错误信息 |

CLI 选项（命令行，非交互）：`-CommandFile`、`-ExitOnError`、`-nohalt`（连上时不 halt）、
`-device`、`-if`、`-speed`。本 skill 统一走 `-CommandFile` 把整段脚本喂给 `JLink.exe`。

输出解析：每行 `20000028 = 04 00 00 00   ....`，按 `<8位地址> = <十六进制字节对>  <ascii>` 提取；
一段 mem 跨多行（每行 16 字节，地址递增），按地址拼接回连续字节（`capture_global.py::parse_mem_output`）。

---

## 2. 铁律与结论（已证，禁止重犯）

### B20 — connect 复位冲掉独立 w4
`connect` 触发 `AIRCR.SYSRESETREQ`，.data 段重载。若先 `connect` 写 RAM 再 `q`，下一次
`connect`（采样时）会把刚写的配置冲掉。**结论**：写 RAM + 读回 + 采样必须放在**同一个**
Commander 会话：`connect → g → Sleep → w4 → mem → g → q`（`build_write_ram_script` 已固化）。
在线单变量 A/B（`jlink_nohalt_sample` 的 `writes`）同理插在 connect 之后、采样之前。

### B22 — halt 致 PDB SEQERR 伪影（最贵的一条）
`h` 只冻结 Cortex-M 内核，**FTM/PDB/ADC 等外设照常运行**。电机控制 ISR 活跃时 halt，
未读 ADC 结果会锁存 PDB 序列错误 → 固件误判永久 FAULT（“电机不转 / 一采样就 fault”）。
**结论**：稳态验证一律 `NoHalt` 读内存（mem 带 `NoHalt`），绝不 `h`；`jlink_nohalt_sample`
默认 `halt=False`。启动早期（前 5000ms）同理不可 halt——官方 `bench_capture.jlink` 等
5000ms 后才首 halt，正是此因。`jlink_read_memory` 默认 `NoHalt`。

### B21 — Keil GUI 不自动收纳新 .c
往 `.uvprojx` 加源文件后，GUI 编译链不会自动包含；必须在 Source 组登记
（`<FileName>/<FileType>1</FileType>/<FilePath>`），否则编译“成功”却漏编新文件。
（属工程构建门禁，非 Commander 本身，但与台架交付强相关。）

### B23~B26 / proven-false 相关
- 读回验证（R6）：任何写（PDB 延迟 / RAM 配置 / 镜像）都必须读回，不一致即停。
- DWARF 动态地址（R6）：改结构后全局地址/偏移整体漂移，禁止写死；
  `jlink_dwarf_resolve` 从 .axf 重算绝对地址。
- 采样必须 `--no-halt`（即 Commander 的 `NoHalt` 后缀），GDB 全禁。
- stale 数据：动完电源/烧录后的第一读可能处于过渡态，至少重读一次确认
  （MCP 侧：单次工具调用内即完成 写→读 同会话，天然规避）。

---

## 3. 标准工作流

### 3.1 烧录
`jlink_flash{hex_path, device}`，内部 `connect → r → loadfile → r → g → q`。
烧录后**必须读回校验**（sha256 / 读关键寄存器），与预期镜像逐字节一致才判有效（R6）。

### 3.2 稳态 NoHalt 采样（最核心）
`jlink_nohalt_sample{axf, regions_spec / addr_map, samples, period_ms, init_ms=5000, writes?}`
1. `connect → g → Sleep(init_ms)`
2. 若 `writes`：同一会话内 `w4`（在线单变量 A/B）
3. 循环 `samples` 次：`Sleep(period_ms)` → `mem <addr>,<len> NoHalt`
4. `q`，解析 mem 行 → 拼 region → 写 CSV/JSON
输出用 DWARF 解析的 `regions_spec`（如 `{global:"g_obsTel", member:"dTheta", length:4}`）
或显式 `addr_map`。`decode` 把原始字节解成 u32/i32/f32 等数值。

### 3.3 在线单变量 A/B（不重烧）
在 3.2 的 `writes` 里给变量 DWARF 地址 + 浮点/16 进制值，单次采样会话内生效，
换值只改 `writes` 重跑，**一轮只改一个变量**（R9）。已验证范例：`g_coreCfg.mode`
`0x1FFF8974` 写 `2.0` 切到自主角度接管；`pllKp` `0x1FFF8940` 由 300→100 压低转速纹波。

### 3.4 读 live 全局（单次）
`jlink_read_memory{addr, length}`，默认 `NoHalt`。适合定点抽查某个变量当前值。

---

## 4. 可移植到其他 MCU / 工程

技巧与具体芯片无关，迁移只需：
1. 换 `device`（如 `S32K144` → `STM32F4xx`）、`iface`（`si`）、`speed`；
2. 提供新工程的 `.axf/.elf` 给 `jlink_dwarf_resolve` 重算地址；
3. 显式 `addr_map` 模式完全不依赖 DWARF，可跨工具链直接用。
本机 JLink 安装位：脚本会**自动探测最新** `C:/Program Files/SEGGER/JLink_V*/JLink.exe`
（Linux/macOS 探测 `/opt/SEGGER/JLink`），无需写死版本号；特殊安装位用环境变量
`JLINK_EXE` 覆盖即可。

---

## 5. 安全与边界

- **电源**：本 skill 只管 J-Link，不碰程控电源；电机供电由 `it6721-control` 管，每次
  台架动作前/后务必 `output=false` 并读回（`link_lost=false`）再动手。
- **NoHalt 读取不保证字节严格同时刻**：AHB-AP 读是总线快照，高速量（如 10kHz ISR 刷的
  变量）相邻字段可能差一拍；需要严格同拍时由固件自报打包结构体（本工程 `ObsTel_t`）。
- **DWARF 依赖 pyelftools**（`pip install pyelftools`）；未装时 `jlink_dwarf_resolve` 报错，
  但 `addr_map` 显式模式不受影响。
- 任何写 RAM / 烧录都会改芯片状态，**先确认前置条件**（镜像存在、sha 一致、电源状态、
  接线正确）再执行（动手前 gate）。

---

## 6. 补充常用命令（官方 JLink.exe V9.88 内置 `?` 帮助实测语法）

> 以下语法均来自**本机 JLink.exe V9.88 的 `?` 帮助输出**（一手权威）。每条标注
> 「[MCP]」表示已有对应 MCP 工具；未标者作为知识沉淀，按需用 `jlink_exec` 走同一会话执行。

### 6.1 宽度感知内存读写（[MCP]）
| 命令 | 语法（帮助原文） | 说明 / 坑 |
|---|---|---|
| 读字节 | `Mem [<Zone>:]<Addr>, <NumBytes>` | 默认；已用于 NoHalt 采样 |
| 读 8-bit | `Mem8 [<Zone>:]<Addr>, <NumBytes>` | 逐字节 |
| 读 16-bit | `Mem16 [<Zone>:]<Addr>, <NumItems>` | 每项是半字（输出每 token 4 hex，小端）→ 解析需翻转 |
| 读 32-bit | `Mem32 [<Zone>:]<Addr>, <NumItems>` | 每项是字（输出每 token 8 hex，小端）→ 解析需翻转 |
| 写 8-bit | `Write1 / W1 [<Zone>:]<Addr>, <Data>` | |
| 写 16-bit | `Write2 / W2 [<Zone>:]<Addr>, <Data>` | |
| 写 32-bit | `Write4 / W4 [<Zone>:]<Addr>, <Data>` | 原 B20 同会话写读用这个 |
| 写 64-bit | `Write8 / W8 [<Zone>:]<Addr>, <Data>` | 64-bit 原子写（如 64-bit 时间戳/计数器） |

- **[MCP]** `jlink_read_memory` 加 `width`：8/16/32 → 自动选 `mem`/`mem16`/`mem32`，解析按宽度翻转还原字节。
- **[MCP]** `jlink_write_ram` 加 `width`：1/2/4/8 → `w1`/`w2`/`w4`/`w8`，读回用同宽度命令。
- **坑（R4 实测）**：帮助把长度标注 `(hex)`，但本工程实测 `mem`/`mem8` 长度按**十进制**解析（与 V988 实测一致）。脚本里长度一律按十进制传，不写 `0x` 前缀。

### 6.2 保存 / 校验 / 擦除 Flash（[MCP]）
| 命令 | 语法 | 说明 |
|---|---|---|
| 存内存到文件 | `SaveBin <filename>, <addr>, <NumBytes>` | [MCP] `jlink_save_memory`：离线分析 / Flash 备份 |
| 校验 bin | `VerifyBin <filename>, <addr>` | [MCP] `jlink_verify_flash`（.bin）：烧录后 R6 读回校验 |
| 校验 hex | （需先解析 HEX 再回读比对） | [MCP] `jlink_verify_flash`（.hex）：解析 Intel HEX → 逐记录 `mem` 回读 → 逐字节比对 |
| 擦除 | `Erase [<SAddr>, <EAddr>]` | [MCP] `jlink_erase_flash`：无参=整片擦；给 SAddr+EAddr=区间擦 |

- **R6 铁律落地**：`jlink_flash` 烧录后务必跟一次 `jlink_verify_flash`（.hex 自动比对），
  逐记录 `all_match=true` 才判烧录有效；任一条 `match=false` 立即停，不轻信「loadfile 成功」。

### 6.3 硬件状态 / 预检（[MCP]）
| 命令 | 语法 | 说明 |
|---|---|---|
| 硬件状态 | `ShowHWStatus` | [MCP] `jlink_hw_status`：**无需目标板供电**（probe 在 USB 即可运行）。重点看 `VTref`（目标供电电压）与 `ITarget`（目标电流）。 |
| 目标供电开关 | `Power <On\|Off> [perm]` | J-Link 的 5V-Supply 引脚给目标供电；`Power Off` 等价于断电。台架收尾可借 J-Link 断目标电（但本项目目标电归 it6721-control 管，勿混用）。 |

- **台架动作前电源预检**：先 `jlink_hw_status`，若 `VTref=0.000V` 说明目标未上电（符合本 skill 不碰电源的边界）；若期望上电却为 0，先查 it6721 再动 J-Link。

### 6.4 寄存器 / 断点（部分 [MCP]）
| 命令 | 语法 | 说明 |
|---|---|---|
| 显示寄存器 | `Regs` | [MCP] `jlink_read_registers`：**会隐式 halt 内核**，仅用于固件已停（故障/断点后）或可接受短暂冻结；**勿在 ISR 活跃稳态观测用**（见 B22） |
| 写寄存器 | `WReg <RegName>, <Value>` | 配合 `Regs` 用 |
| 读单寄存器 | `RReg <RegName>` | |
| 设断点 | `SetBP <addr> [A/T] [S/H]` | 断点命中即 halt；**B22 警示**：ISR 活跃时断点会冻结内核→PDB SEQERR 伪影。仅非实时场景用 |
| 设观察点 | `SetWP <Addr> [R/W] [<Data> [<D-Mask>] [A-Mask]]]` | 数据断点，命中亦 halt，同理慎用 |
| 清断点 | `ClearBP <BP_Handle>` / `ClrBP` | |
| 查运行状态 | `IsHalted` / `WaitHalt [<TimeoutMs>]` | 不扰动地确认目标是否在跑（NoHalt 采样前可先 `IsHalted` 确认已在运行） |

### 6.5 复位策略 / 高级（知识沉淀，用 `jlink_exec` 走）
| 命令 | 语法 | 说明 |
|---|---|---|
| 复位类型 | `RSetType <Type>` | 连不上目标时换复位策略（0=Normal/1=Core/2=ResetPin…）。排障 connect 失败首选 |
| 复位+延时 | `ResetX <DelayAfterReset>` | |
| 执行命令串 | `Exec <CommandString>` | J-Link 内部命令串，如 `exec EnableFlashBP`（Flash 断点）、`exec SetSkipInit`、`exec SetDisableAutoUnlock`。高级/排障用 |
| 测速 | `TestWSpeed` / `TestRSpeed` / `TestCSpeed` | 下载/上传/CPU 速度基准 |

> 注：上述 §6.4/§6.5 未单独做 MCP 工具（避免暴露危险的 halt 类操作面），需要时统一经
> `jlink_exec` 在同一会话内执行，并自行遵守 B20/B22 铁律。
