# -*- coding: utf-8 -*-
"""纯终端 REPL：无浏览器 / SSH 到 Linux 时使用。只依赖标准库。"""

import sys

from . import commands as cmdmod
from . import device as dv
from . import serial_port as sp

BANNER = """IT6721 程控电源 · 命令终端
  0–60 V / 0–8 A / 180 W   ·   功率约束 P = U × I ≤ 180 W
  输入 HELP 查看语法，输入 exit 退出。"""

PREFIX = {"ok": "  OK  ", "error": " ERR  ", "warn": " WARN ", "info": "      "}


def run(dev, autorun=None):
    try:
        import readline  # noqa: F401  # 提供上下键历史，POSIX 自带
    except ImportError:
        pass
    print(BANNER)
    print("  链路：%s  ·  %d · 8N1%s\n"
          % (dev.port_name or "未连接", dev.baud, "  ·  模拟设备" if dev.sim else ""))
    if autorun:
        for line in autorun:
            print("> " + line)
            _emit(cmdmod.execute(dev, line))
    while True:
        try:
            line = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n退出。")
            break
        if line.lower() in ("exit", "quit", "q"):
            try:
                if dev.connected:
                    dev.output_off()
                    dev._log("SYS", "退出前已下发 OUTP OFF")
            except Exception:
                pass
            break
        if not line:
            continue
        if line.lower() == "state":
            s = dev.snapshot()
            print("  V %.3f  I %.4f  P %.3f  %s  OUT=%s  TX=%d RX=%d ERR=%d"
                  % (s["v"], s["i"], s["p"], s["mode"],
                     "ON" if s["output"] else "OFF",
                     s["tx"], s["rx"], s["errors"]))
            continue
        if line.lower() == "ports":
            print("  " + ", ".join(sp.list_ports()))
            continue
        _emit(cmdmod.execute(dev, line))


def _emit(lines):
    for level, text in lines:
        prefix = PREFIX.get(level, "      ")
        if level == "error" and text.startswith("ERR"):
            prefix = " "      # 文本里已带 ERR，避免重复成 "ERR  ERR  ..."
        sys.stdout.write(prefix + text + "\n")
    sys.stdout.flush()
