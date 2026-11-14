# Copyright (c) 2026 BreezeKitte
# SPDX-License-Identifier: MIT

"""J-Link Commander 环境自检：不碰真机，只验证工具链可用。"""
import os
import re
import platform

from .core import resolve_exe, run_script


def selftest(jlink_exe=None):
    """返回环境信息字典。仅做「只读/不连真机」的探针：版本探测运行 `q` 脚本，
    JLink.exe 未 connect 时不访问目标，安全。"""
    info = {"python_version": platform.python_version()}
    exe = resolve_exe(jlink_exe)
    info["jlink_exe"] = exe
    info["jlink_exe_exists"] = os.path.isfile(exe)
    if info["jlink_exe_exists"]:
        try:
            out = run_script(exe, "q\n", timeout=60)
            m = re.search(r"V\d+\.\d+", out)
            info["jlink_version"] = m.group(0) if m else "unknown"
            info["probe_ok"] = True
        except Exception as e:  # noqa: BLE001
            info["probe_ok"] = False
            info["probe_error"] = str(e)
    else:
        info["probe_ok"] = False
        info["probe_error"] = "JLink.exe 未找到，请用 JLINK_EXE 或默认安装位"
    try:
        import elftools  # noqa: F401
        info["pyelftools"] = True
    except Exception:
        info["pyelftools"] = False
    return info
