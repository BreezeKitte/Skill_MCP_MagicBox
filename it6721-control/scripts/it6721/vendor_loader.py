# -*- coding: utf-8 -*-
"""可选依赖装载器：把 vendor/ 目录里的离线包加入 sys.path。

本程序本身零第三方依赖，vendor/ 默认是空的。
但如果以后要加包、或者目标机器完全不能联网，可以直接把
  · 解压后的包目录，或
  · 官方 .whl 文件（本质是 zip，Python 可直接 import）
丢进 vendor/，启动时会自动识别，无需 pip、无需管理员权限。

示例：把 pyserial-3.5-py2.py3-none-any.whl 放进 vendor/，
      再用 --pyserial 启动，就会优先走 pyserial 后端。
"""

import os
import sys


def load(directory=None):
    """返回成功装载的条目列表。"""
    if directory is None:
        directory = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "vendor")
    if not os.path.isdir(directory):
        return []
    loaded = []
    for name in sorted(os.listdir(directory)):
        path = os.path.join(directory, name)
        if os.path.isdir(path) and not name.startswith((".", "__")):
            if path not in sys.path:
                sys.path.insert(0, path)
                loaded.append(name)
        elif name.lower().endswith((".whl", ".zip")):
            if path not in sys.path:
                sys.path.insert(0, path)
                loaded.append(name)
    return loaded
