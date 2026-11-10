# IT6721 MCP 工具参考

所有工具名以 `it6721_` 前缀，结果统一包在 `result.content[0].text`（一个 JSON 字符串），
需解开后使用。返回值里的 `error` 字段非空即失败；被全局锁拦截时 `isError=true` 且含 `locked:true`。

## 连接与状态

| 工具 | 参数 | 说明 |
|------|------|------|
| `it6721_list_ports` | — | 枚举本机串口 + 默认猜测端口 + 候选波特率。只读、安全。 |
| `it6721_connect` | `port?`, `sim?`, `baud?`, `auto?`, `address?`, `parity?`, `stop_bits?`, `no_handshake?`, `check_mode?` | 连接。`sim:true` 进内置模拟器；否则传端口（如 `COM6`）。`auto` 默认 true：4800 8N1 逐档自适应握手，仅发 31H 只读帧，命中后才可写。返回 `sim / idn / remote`。 |
| `it6721_disconnect` | — | 断开并释放串口（尽力交还前面板控制权）。安全。 |
| `it6721_status` | — | 完整状态：V/I/P、设定值、输出开关、远程模式、链接状态、自动微调状态、序列状态、CSV 记录状态、全局锁状态。只读。 |

## 设定与输出

| 工具 | 参数 | 说明 |
|------|------|------|
| `it6721_set_voltage` | `v` (0–60) | 设电压。经量程/功率校验；写前需进入 PC 远程模式。锁定时拒绝。 |
| `it6721_set_current` | `i` (0–8) | 设电流。锁定时拒绝。 |
| `it6721_apply` | `v?`, `i?` | 原子设定（可只给其一）。先整体校验最终态再按安全顺序下发，避免中间态功率越限。锁定时拒绝。 |
| `it6721_output` | `on` (bool) | 开关输出（OUTP ON/OFF）。**急停 `on:false` 在锁下仍放行**；开输出会被锁拦截。 |

## 自动微调（AutoTune）

| 工具 | 参数 | 说明 |
|------|------|------|
| `it6721_autotune_configure` | `target?`, `value?`, `min?`, `max?`, `step?`, `interval?`, `jump_points?` | 配置对象 V/I、起点/上下限/步进/间隔、跳跃点序列。锁定时拒绝。 |
| `it6721_autotune_set` | `value` | 手动单步设定到指定值。锁定时拒绝。 |
| `it6721_autotune_nudge` | `delta` | 相对当前值 ±delta。锁定时拒绝。 |
| `it6721_autotune_start` | `mode` (`off`/`up`/`down`/`pingpong`/`jump`) | 启动扫描。锁定时拒绝。 |
| `it6721_autotune_stop` | — | 停止（急停类，锁下仍放行）。 |

模式要点：`up` 递增、`down` 递减、`pingpong` 往复、`jump` 按 `jump_points` 序列跳变
（如 `[2,5,9,16,8]`，可自由设定区间，步进/间隔对 jump 无意义）。`interval` 最小 0.1 s。

## 序列（Sequence）

| 工具 | 参数 | 说明 |
|------|------|------|
| `it6721_sequence_set_steps` | `steps: [{v,i,hold}]` | 设步骤，每项含电压/电流/保持秒。全部经功率校验，任一越限即报错。锁定时拒绝。 |
| `it6721_sequence_run` | `loops?`, `speed?` | 按当前步骤运行（可指定循环次数/速度倍率）。锁定时拒绝。 |
| `it6721_sequence_stop` | — | 停止（急停类，锁下仍放行）。 |
| `it6721_sequence_pause` | `on?` | 暂停/恢复。锁定时拒绝（仅停止放行）。 |

## 记录与锁

| 工具 | 参数 | 说明 |
|------|------|------|
| `it6721_record_start` | — | 开始把轮询结果写 CSV。锁定时拒绝。 |
| `it6721_record_stop` | — | 停止记录并返回路径与行数（锁下仍放行）。 |
| `it6721_set_lock` | `on` (bool) | 设全局操作锁。`on:true` 拦截所有写接口（急停除外）；`on:false` 解锁。始终放行。 |

## 硬件与协议安全边界

- **规格**：IT6721 = 0–60 V / 0–8 A / ≤180 W。IT6720 经济型数控系列专有 **26 字节二进制帧**，
  4800 8N1，**不是 SCPI**（不要发 `*IDN?`）。
- **判通硬指标**（来自实测）：连续 5 次合法 26 字节应答帧 ≥4，且 31H 含型号信息；达成前
  **不得**下发 20H/21H/23H/24H 写帧。MCP 的 `it6721_connect`（auto 模式）已实现此门限。
- **后面板 DB9 是 TTL 电平**：必须用 IT-E121(RS232)/IT-E122(USB) 原装模块，或第三方 USB-TTL
  （CH340/CP2102/FT232）+ 自制交叉线。**禁止标准 RS232(±12V) 直连**。线序需实测确认
  （手册只给引脚编号不给信号名）。
- **COM 口号不写死**：不同机器/模块编号不同，由 `auto_connect` 自动选 USB 串口或用户显式传入。
- **全局锁建议**：真机操作前 `it6721_set_lock on:true`；仅在做具体改动那一步 `on:false` 解锁，
  改完再上锁，避免 Agent 误改。
- **退出清理**：MCP 进程被父进程关闭 stdin（EOF）或退出时，自动 `disconnect` 释放串口并尽力
  交还前面板控制权，不会残留占用。

## 服务注册与包位置
MCP 服务注册名为 **`it6721-control`**（stdio）。配置文件为 `~/.workbuddy/it6721-control.json`
（连接器管理页实际读取的每服务配置；中央 `~/.workbuddy/mcp.json` 同步列出）。工具声明名均以
`it6721_` 前缀；WorkBuddy 调用时再叠加 `mcp__it6721-control__` 前缀。

**自包含（默认）**：`it6721` 引擎已随仓库内嵌于 `scripts/it6721/`，开箱即用，**无需任何环境变量**。
`scripts/it6721_mcp.py` 默认把该目录加入 `sys.path` 后 `import it6721`。

**可选覆盖（环境变量，按优先级）**：
1. `IT6721_PACKAGE_DIR` —— 直接指向自有 `it6721/` 包目录（最显式）。
2. `IT6721_CONSOLE_DIR` —— 指向含 `it6721/` 的 IT6721_Console 仓库根（兼容旧用法）。
3. 均未设置时回退到内嵌副本。
记录 CSV 落盘目录：`IT6721_DATA_DIR` > `IT6721_CONSOLE_DIR/data`（旧兼容）> `~/it6721_recordings`。
