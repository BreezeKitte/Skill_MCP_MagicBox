# -*- coding: utf-8 -*-
# Copyright (c) 2026 BreezeKitte
# SPDX-License-Identifier: MIT
"""服务端自动微调引擎：按范围 / 步进 / 间隔自动递增、递减或往复扫描。

放在服务端而不是浏览器里的原因：
1. 刷新页面、切换标签页不会中断扫描，也不会出现多个定时器同时下发；
2. 每一步都统一走 dev.apply() 的功率校验与全局锁，前端无法绕过；
3. 断开连接、锁定、越限时能立即停机并留下日志。
"""

import threading

from . import device as dv

MODES = ("off", "up", "down", "pingpong", "jump")
MIN_INTERVAL = 0.10          # 100 ms，防止把串口刷爆
PRECISION = 0.01             # 0.01 精度（V / A 都按此量化）


def quant(x):
    return round(round(float(x) / PRECISION) * PRECISION, 2)


class AutoTuner(object):
    def __init__(self, dev, on_event=None, can_write=None):
        self.dev = dev
        self.on_event = on_event or (lambda text, level: None)
        # 全局锁钩子：返回 False 时立刻停机
        self.can_write = can_write or (lambda: True)
        self._lock = threading.RLock()
        self._stop_evt = threading.Event()
        self._wake_evt = threading.Event()
        self._thread = None

        self.target = "V"          # V / I
        self.value = 1.79
        self.min = 0.00
        self.max = 12.00
        self.step = 0.01
        self.interval = 1.00
        self.mode = "off"
        self.status = "idle"       # idle / running / done / stopped / error
        self.direction = 1
        self.ticks = 0
        self.last_error = ""
        self.jump_points = []        # 跳跃模式的目标值序列（V 或 I）
        self._jump_idx = 0

    # ------------------------------------------------------------ 配置
    def configure(self, target=None, value=None, vmin=None, vmax=None,
                  step=None, interval=None, jump_points=None):
        with self._lock:
            if target is not None:
                t = str(target).upper()[:1]
                if t not in ("V", "I"):
                    raise dv.ValidationError("调整对象只能是 V（电压）或 I（电流）")
                self.target = t
            if vmin is not None:
                self.min = quant(vmin)
            if vmax is not None:
                self.max = quant(vmax)
            if step is not None:
                self.step = quant(step)
            if interval is not None:
                self.interval = max(MIN_INTERVAL, round(float(interval), 3))
            if jump_points is not None:
                pts = list(jump_points)
                if not pts or not all(isinstance(p, (int, float)) for p in pts):
                    raise dv.ValidationError("跳跃点必须是非空数字列表")
                limit = dv.V_MAX if self.target == "V" else dv.I_MAX
                for p in pts:
                    if p < 0 or p > limit:
                        raise dv.ValidationError("跳跃点 %.2f 超出设备量程 0–%.2f %s"
                                                 % (p, limit, self.target))
                self.jump_points = [quant(p) for p in pts]
            if value is not None:
                self.value = quant(value)

            limit = dv.V_MAX if self.target == "V" else dv.I_MAX
            if self.min < 0 or self.max > limit:
                raise dv.ValidationError("范围超出设备量程 0–%.2f %s"
                                         % (limit, self.target))
            if self.min > self.max:
                raise dv.ValidationError("下限不能大于上限")
            if self.step <= 0:
                raise dv.ValidationError("步进必须大于 0.00")
            if self.value < self.min or self.value > self.max:
                self.value = min(self.max, max(self.min, self.value))
            self._wake_evt.set()       # 让等待中的循环立即采用新间隔
            return self.state()

    # ------------------------------------------------------------ 单步动作
    def _apply(self, value):
        """只改一个量，另一个保持当前设定；越限由 dev.apply() 拒绝。"""
        value = quant(value)
        if self.target == "V":
            self.dev.apply(v=value, i=None)
        else:
            self.dev.apply(v=None, i=value)
        self.value = value
        self.ticks += 1
        return value

    def set_value(self, value):
        with self._lock:
            v = quant(value)
            if not (self.min <= v <= self.max):
                raise dv.ValidationError("设定值 %.2f 超出范围 %.2f–%.2f"
                                         % (v, self.min, self.max))
            self._apply(v)
            self.status = "idle" if self.mode == "off" else self.status
            self.on_event("手动设定 %s = %.2f" % (self.target, v), "ok")
            return self.state()

    def nudge(self, delta):
        with self._lock:
            v = quant(self.value + quant(delta))
            v = min(self.max, max(self.min, v))
            self._apply(v)
            self.on_event("步进 %+.2f → %s = %.2f" % (delta, self.target, v), "ok")
            return self.state()

    # ------------------------------------------------------------ 扫描控制
    def start(self, mode):
        mode = str(mode).lower()
        if mode not in MODES:
            raise dv.ValidationError("未知自动模式：%s" % mode)
        with self._lock:
            if mode == "off":
                return self.stop()
            if mode == "jump":
                if len(self.jump_points) < 2:
                    raise dv.ValidationError("跳跃模式至少需要 2 个跳跃点")
                self.mode = "jump"
                self.status = "running"
                self.ticks = 0
                self.last_error = ""
                self._jump_idx = 0
                self.value = self.jump_points[0]
                try:
                    self._apply(self.value)
                except Exception as exc:
                    self.status = "error"
                    self.last_error = str(exc)
                    self.on_event("跳跃扫描未启动：%s" % exc, "error")
                    return self.state()
                self._stop_evt.clear()
                self._wake_evt.set()
                self.on_event("跳跃扫描启动：%s 按 %s 顺序跳变，间隔 %.2f s"
                              % (self.target,
                                 "→".join("%.2f" % p for p in self.jump_points),
                                 self.interval), "ok")
                if not (self._thread and self._thread.is_alive()):
                    self._thread = threading.Thread(
                        target=self._loop, name="it6721-autotune", daemon=True)
                    self._thread.start()
                return self.state()
            if self.min == self.max:
                raise dv.ValidationError("上下限相同，没有可扫描区间")
            self.mode = mode
            self.status = "running"
            self.ticks = 0
            self.last_error = ""
            self.direction = -1 if mode == "down" else 1
            self.value = self.max if mode == "down" else self.min
            try:
                self._apply(self.value)       # 立即落到起点，不等第一个间隔
            except Exception as exc:
                self.status = "error"
                self.last_error = str(exc)
                self.on_event("自动扫描未启动：%s" % exc, "error")
                return self.state()
            self._stop_evt.clear()
            self._wake_evt.set()
            self.on_event("自动%s启动：%s %.2f→%.2f，步进 %.2f，间隔 %.2f s"
                          % ({"up": "递增", "down": "递减",
                              "pingpong": "往复"}[mode],
                             self.target, self.value,
                             self.max if mode == "up" else self.min,
                             self.step, self.interval), "ok")
            if not (self._thread and self._thread.is_alive()):
                self._thread = threading.Thread(
                    target=self._loop, name="it6721-autotune", daemon=True)
                self._thread.start()
            return self.state()

    def stop(self, reason="手动停止"):
        with self._lock:
            was = self.mode != "off" or self.status == "running"
            self.mode = "off"
            self._stop_evt.set()
            self._wake_evt.set()
            if was:
                self.status = "stopped"
                self.on_event("自动扫描已停止（%s），共 %d 步" % (reason, self.ticks),
                              "warn")
            return self.state()

    def _loop(self):
        while not self._stop_evt.is_set():
            with self._lock:
                interval = self.interval
            if self._wake_evt.wait(interval):
                self._wake_evt.clear()
                continue
            if self._stop_evt.is_set():
                break
            if not self.can_write():
                self.stop("全局锁定")
                break
            if not self.dev.connected or self.dev.link_lost:
                self.stop("设备断开")
                break
            try:
                done = self._tick()
            except Exception as exc:
                with self._lock:
                    self.status = "error"
                    self.last_error = str(exc)
                    self.mode = "off"
                self.on_event("自动扫描异常停止：%s" % exc, "error")
                break
            if done:
                break

    def _tick(self):
        with self._lock:
            if self.mode == "off":
                return True
            if self.mode == "jump":
                idx = self._jump_idx + 1
                if idx >= len(self.jump_points):
                    self.status = "done"
                    self.mode = "off"
                    self.on_event("跳跃扫描完成，共 %d 步，已停止" % self.ticks, "ok")
                    return True
                self._jump_idx = idx
                v = self.jump_points[idx]
                self._apply(v)
                return False
            # 用整数步数推进，避免浮点累加漂移
            n = int(round((self.value - self.min) / self.step)) + self.direction
            raw = self.min + n * self.step
            if self.mode == "pingpong":
                if raw >= self.max:
                    self.direction = -1
                    n = int(round((self.max - self.min) / self.step))
                elif raw <= self.min:
                    self.direction = 1
                    n = 0
                raw = self.min + n * self.step
                v = min(self.max, max(self.min, quant(raw)))
                self._apply(v)
                return False
            if self.direction > 0:
                v = min(self.max, quant(raw))
                self._apply(v)
                done = v >= self.max
            else:
                v = max(self.min, quant(raw))
                self._apply(v)
                done = v <= self.min
            if done:
                self.status = "done"
                self.mode = "off"
                self.on_event("自动扫描到达边界 %.2f，共 %d 步，已停止"
                              % (v, self.ticks), "ok")
            return done

    # ------------------------------------------------------------ 状态
    def state(self):
        with self._lock:
            return {
                "active": bool(self.status == "running"),
                "mode": self.mode,
                "status": self.status,
                "target": self.target,
                "value": round(self.value, 2),
                "min": round(self.min, 2),
                "max": round(self.max, 2),
                "step": round(self.step, 2),
                "interval": round(self.interval, 2),
                "ticks": self.ticks,
                "last_error": self.last_error,
                "jump_points": [round(p, 2) for p in self.jump_points],
            }
