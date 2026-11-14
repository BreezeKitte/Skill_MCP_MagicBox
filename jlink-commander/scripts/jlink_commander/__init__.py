# Copyright (c) 2026 BreezeKitte
# SPDX-License-Identifier: MIT

"""jlink_commander — J-Link Commander 技巧的可移植封装。

本包把在 S32K144 FOC 电机控制调试中沉淀下来的 J-Link Commander 硬经验
固化成可复用代码，核心是三条已验证铁律（详见 references/jlink_reference.md）：

  B20  connect 会通过 AIRCR.SYSRESETREQ 复位芯片；独立 w4 写入会在下一次
       connect 被 .data 重载冲掉。因此「写 RAM + 采样」必须在**同一个**
       Commander 会话里完成（connect → g → Sleep → w4 → mem → g → q）。
  B22  halt 只冻结 Cortex-M 内核，FTM/PDB/ADC 等外设仍在跑；在电机控制 ISR
       活跃时 halt 会导致 PDB 序列错误 → 固件误判为永久 FAULT。稳态验证一律
       用 **NoHalt** 读内存（mem 带 NoHalt 后缀），绝不 halt。
  R6   固件改结构后全局地址/成员偏移会整体漂移，必须靠 DWARF（.axf）重新
       解析绝对地址，不能写死。

这些技巧与具体 MCU/工程无关：只要目标是 ARM Cortex-M + J-Link + ELF/AXF，
把符号交给 DWARF 解析即可直接复用。
"""

__version__ = "1.0.0"

from .core import (  # noqa: F401
    resolve_exe,
    run_script,
    build_connect_lines,
    build_nohalt_sample_script,
    build_write_ram_script,
    build_flash_script,
    parse_mem_output,
    assemble_region,
)
from .dwarf import (  # noqa: F401
    resolve_globals,
    member_offset,
    resolve_elf,
)
from .selftest import selftest  # noqa: F401
