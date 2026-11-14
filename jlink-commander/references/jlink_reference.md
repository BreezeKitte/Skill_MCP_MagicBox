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
本机 JLink 安装位：`C:/Program Files/SEGGER/JLink_V988/JLink.exe`（环境变量 `JLINK_EXE` 可覆盖）。

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
