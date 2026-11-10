# -*- coding: utf-8 -*-
"""CSV 数据记录：复用轮询结果写文件，不额外占用串口。"""

import csv
import os
import threading
from datetime import datetime

from . import device as dv

FIELDS = ["time", "V", "I", "P", "mode", "out"]


class Recorder(object):
    def __init__(self, directory="."):
        self.directory = directory
        self.path = ""
        self._file = None
        self._writer = None
        self._lock = threading.Lock()
        self.rows = 0

    @property
    def recording(self):
        return self._file is not None

    def start(self, directory=None):
        self.stop()
        d = directory or self.directory
        os.makedirs(d, exist_ok=True)
        name = "IT6721_%s.csv" % datetime.now().strftime("%Y%m%d_%H%M%S")
        self.path = os.path.join(d, name)
        # 内容全为 ASCII，用纯 UTF-8（不带 BOM），保证 Python / pandas 读到的
        # 列名就是 "time" 而不是 "\ufefftime"；Excel 也能正常识别。
        self._file = open(self.path, "w", newline="", encoding="utf-8")
        self._writer = csv.writer(self._file)
        self._writer.writerow(FIELDS)
        self.rows = 0
        return self.path

    def stop(self):
        with self._lock:
            if self._file:
                self._file.flush()
                self._file.close()
            self._file = None
            self._writer = None
        return self.path

    def on_sample(self, state):
        """由 device.on_sample 回调驱动；稳定窗口内的过渡点直接丢弃。"""
        if not self.recording or state.get("settle", 0) > 0:
            return
        with self._lock:
            if self._writer is None:
                return
            self._writer.writerow([
                datetime.now().strftime("%H:%M:%S.%f")[:-3],
                "%.3f" % state["v"],
                "%.4f" % state["i"],
                "%.3f" % state["p"],
                state["mode"],
                "ON" if state["output"] else "OFF",
            ])
            self.rows += 1
            if self.rows % 50 == 0:
                self._file.flush()

    def preview(self, n=4):
        if not self.recording or not self.path:
            return []
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                return [l.rstrip("\n") for _, l in zip(range(n), f)]
        except Exception:
            return []


def steps_from_csv(path):
    """读取序列 CSV：表头 step,V,I,hold_s（兼容中文列号）。"""
    steps = []
    with open(path, "r", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            def pick(*keys):
                for k in keys:
                    for real in row:
                        if real and real.strip().lower() == k:
                            return row[real]
                return None
            v = pick("v", "volt", "voltage", "电压")
            i = pick("i", "curr", "current", "电流")
            h = pick("hold_s", "hold", "seconds", "保持", "保持s")
            if v is None or i is None:
                continue
            s = SequenceStep(float(v), float(i), float(h or 1.0))
            dv.validate(s.v, s.i)
            steps.append(s)
    if not steps:
        raise ValueError("CSV 中没有有效步骤（需要 V 与 I 两列）")
    return steps


class SequenceStep(object):
    def __init__(self, v, i, hold):
        self.v = float(v)
        self.i = float(i)
        self.hold = float(hold)

    def as_dict(self):
        return {"v": self.v, "i": self.i, "hold": self.hold,
                "p": round(self.v * self.i, 2)}


DEFAULT_STEPS = [
    SequenceStep(5.00, 1.000, 10),
    SequenceStep(12.00, 2.000, 15),
    SequenceStep(24.00, 3.000, 20),
    SequenceStep(1.79, 1.000, 10),
]
