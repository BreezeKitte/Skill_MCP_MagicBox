# -*- coding: utf-8 -*-
"""序列引擎：上位机侧定时按步下发，不依赖设备内置 LIST 指令。"""

import threading
import time

from . import device as dv
from .recorder import DEFAULT_STEPS


class SequenceRunner(threading.Thread):
    def __init__(self, dev, steps=None, loops=1, on_event=None, speed=1.0):
        super(SequenceRunner, self).__init__(name="it6721-sequence", daemon=True)
        self.dev = dev
        self.steps = list(steps or DEFAULT_STEPS)
        self.loops = max(1, int(loops))
        self.speed = max(0.1, float(speed))
        self.on_event = on_event or (lambda text, level: None)
        self._stop_evt = threading.Event()
        self._pause_evt = threading.Event()
        self.status = "idle"
        self.loop = 0
        self.step = 0
        self.elapsed = 0.0

    # -- 外部控制 --
    def stop(self):
        self._stop_evt.set()
        self._pause_evt.clear()
        self.status = "stopping"

    def pause(self, on=True):
        if on:
            self._pause_evt.set()
            self.status = "paused"
        else:
            self._pause_evt.clear()

    # -- 预检 --
    def estimate(self):
        one = sum(s.hold for s in self.steps)
        return one * self.loops

    def validate_all(self):
        for idx, s in enumerate(self.steps, 1):
            dv.validate(s.v, s.i)
        return "全部 %d 步通过校验：最大 %.2f W ≤ 180 W" % (
            len(self.steps), max(s.v * s.i for s in self.steps))

    def _wait(self, seconds, note=""):
        """可被暂停 / 停止打断的等待。"""
        deadline = time.time() + seconds / self.speed
        while time.time() < deadline:
            if self._stop_evt.is_set():
                return False
            if self._pause_evt.is_set():
                time.sleep(0.05)
                deadline += 0.05
                continue
            self.elapsed = max(self.elapsed, 0)
            time.sleep(min(0.05, max(0.0, deadline - time.time())))
        return True

    def run(self):
        self.status = "running"
        self._stop_evt.clear()
        t0 = time.time()
        try:
            self.validate_all()
        except dv.ValidationError as exc:
            self.on_event("序列拒绝启动：%s" % exc, "error")
            self.status = "error"
            return

        self.on_event("序列开始：%d 步 × %d 循环，预计 %.0f s"
                      % (len(self.steps), self.loops, self.estimate()), "ok")
        try:
            for loop in range(1, self.loops + 1):
                self.loop = loop
                for idx, s in enumerate(self.steps, 1):
                    if self._stop_evt.is_set():
                        raise StopIteration
                    self.step = idx
                    self.on_event("循环 %d/%d  步 %d/%d  →  V %.2f  I %.3f  保持 %.0f s"
                                  % (loop, self.loops, idx, len(self.steps),
                                     s.v, s.i, s.hold), "info")
                    # 先按最终态校验并安全顺序下发 23H/24H 设定帧，再 21H 输出 ON。
                    # 用 apply_step 而非逐条 set_，避免升压时与旧电流形成越限中间态。
                    self.dev.apply_step(s.v, s.i)
                    time.sleep(0.2)
                    self.dev.output_on()
                    if not self._wait(s.hold):
                        raise StopIteration
                self.elapsed = time.time() - t0
            self.status = "done"
            self.on_event("序列完成，耗时 %.1f s，自动 OUTP OFF" % (time.time() - t0), "ok")
        except StopIteration:
            self.status = "stopped"
            self.on_event("序列被手动停止，已下发 OUTP OFF", "warn")
        except Exception as exc:
            self.status = "error"
            self.on_event("序列异常中断：%s（已尝试 OUTP OFF）" % exc, "error")
        finally:
            try:
                self.dev.output_off()
            except Exception:
                pass
            self.dev.pause_poll(False)

    def state(self):
        return {"status": self.status, "loop": self.loop, "step": self.step,
                "loops": self.loops, "steps": len(self.steps),
                "elapsed": round(self.elapsed, 1)}
