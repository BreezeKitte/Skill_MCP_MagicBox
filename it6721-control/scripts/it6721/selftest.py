# -*- coding: utf-8 -*-
"""端到端自测：全部走模拟设备，不接触真实硬件、不打开任何串口。"""

import os
import tempfile
import time
import unittest

from . import autotune as tunemod
from . import commands as cmdmod
from . import device as dv
from . import protocol as pr
from . import recorder as recmod
from . import sequence as seqmod
from . import serial_port as sp
from . import webui


class IT6721SelfTest(unittest.TestCase):
    def setUp(self):
        self.dev = dv.IT6721(poll_interval=0.05)
        self.dev.connect("SIM", dv.DEFAULT_BAUD, sim=True)
        self.dev.pause_poll(True)      # 避免后台轮询干扰 tx_count 断言

    def tearDown(self):
        self.dev.disconnect(quiet=True)

    # --- 串口层 / 协议帧 ---
    def test_01_sim_port_info_frame(self):
        p = sp.open_serial("SIM", dv.DEFAULT_BAUD, 0.1, sim=True)
        p.write(pr.build(0, pr.CMD_INFO))
        raw = p.read(256)
        p.close()
        r = pr.parse(raw)
        self.assertIsNotNone(r)
        self.assertEqual(r["cmd"], pr.CMD_INFO)
        self.assertIn("IT672", pr.decode_info(r["data"])["text"])

    def test_01b_frame_checksum_examples(self):
        """同族 26 字节协议公开帧例：用于锁定 sum8 校验算法。"""
        f = pr.build(0, 0x20, b"\x01")
        self.assertEqual(f[:4], b"\xAA\x00\x20\x01")
        self.assertEqual(f[25], 0xCB)
        s = pr.build(0, 0x12, b"\x80")
        self.assertEqual(s[25], 0x3C)
        self.assertEqual(len(f), 26)
        self.assertIsNotNone(pr.parse(f))
        self.assertIsNone(pr.parse(f[:-1] + b"\x00"))

    def test_02_validate(self):
        self.assertAlmostEqual(dv.validate(24.0, 3.0), 72.0)
        self.assertAlmostEqual(dv.validate(60.0, 3.0), 180.0)
        with self.assertRaises(dv.ValidationError):
            dv.validate(60.0, 3.001)
        with self.assertRaises(dv.ValidationError):
            dv.validate(60.01, 1.0)
        with self.assertRaises(dv.ValidationError):
            dv.validate(12.0, 8.001)
        self.assertAlmostEqual(dv.max_current_at(60.0), 3.0)
        self.assertAlmostEqual(dv.max_current_at(12.0), 8.0)

    # --- 命令解析 ---
    def test_03_parse_syntax(self):
        cases = [
            ("on", "out_on"), ("OFF", "out_off"),
            ("V 12", "set_v"), ("V=12V", "set_v"), ("v=24v", "set_v"),
            ("A 0.5", "set_i"), ("A=1.2", "set_i"), ("I 0.001", "set_i"),
            ("A=0.001", "set_i"), ("MEAS", "measure"), ("V?", "measure"),
            ("OVP 62", "ovp"), ("OCP 8.2", "ocp"), ("RCL 2", "rcl"),
            ("HELP", "help"), ("*IDN?", "scpi"), ("MEAS:VOLT?", "scpi"),
            ("LOCK ON", "lock"), ("BAUD 4800", "baud"),
        ]
        for line, kind in cases:
            acts = cmdmod.parse(line)
            self.assertEqual(len(acts), 1, line)
            self.assertEqual(acts[0]["kind"], kind, line)
        self.assertEqual(cmdmod.parse("wat?") [0]["kind"], "unknown")
        self.assertEqual(len(cmdmod.parse("V 24 ; A 0.5 ; on")), 3)

    def test_04_atomic_power_reject(self):
        before_tx = self.dev.tx_count
        before = (self.dev.set_v, self.dev.set_i)
        lines = cmdmod.execute(self.dev, "V 60 ; A 8")
        self.assertTrue(any(lv == "error" for lv, _ in lines))
        self.assertIn("480", "\n".join(t for _, t in lines))
        self.assertEqual((self.dev.set_v, self.dev.set_i), before)
        self.assertEqual(self.dev.tx_count, before_tx, "越限时一条都不应下发")

    def test_05_commands_execute(self):
        for line in ("V 24", "A 1.2", "on"):
            lines = cmdmod.execute(self.dev, line)
            self.assertFalse(any(lv == "error" for lv, _ in lines), line)
        self.assertAlmostEqual(self.dev.set_v, 24.0)
        self.assertAlmostEqual(self.dev.set_i, 1.2)
        self.assertTrue(self.dev.output)
        lines = cmdmod.execute(self.dev, "MEAS")
        self.assertTrue(any("V=24.000" in t for _, t in lines))
        cmdmod.execute(self.dev, "off")
        self.assertFalse(self.dev.output)

    def test_06_safe_order_when_dropping_current(self):
        """低压大电流 → 高压小电流：不能因 60 V × 8 A 的中间态被误拦。"""
        cmdmod.execute(self.dev, "V 5 ; A 8")          # 40 W，合法
        self.assertAlmostEqual(self.dev.set_i, 8.0)
        lines = cmdmod.execute(self.dev, "V 60 ; A 3")  # 180 W，恰在上限
        self.assertFalse(any(lv == "error" for lv, _ in lines), lines)
        self.assertAlmostEqual(self.dev.set_v, 60.0)
        self.assertAlmostEqual(self.dev.set_i, 3.0)

    def test_07_unknown_never_sent(self):
        before = self.dev.tx_count
        lines = cmdmod.execute(self.dev, "BLINK 9")
        self.assertTrue(any(lv == "error" for lv, _ in lines))
        self.assertEqual(self.dev.tx_count, before)

    # --- 自适应 / 轮询 ---
    def test_08_auto_connect_sim(self):
        d = dv.IT6721()
        r = d.auto_connect("SIM", sim=True)
        self.assertTrue(r["ok"])
        self.assertEqual(r["baud"], 4800)
        self.assertIn("IT6721", r["idn"])
        d.disconnect(quiet=True)

    def test_09_poll_updates_readout(self):
        self.dev.set_output(True)
        self.dev.pause_poll(False)
        time.sleep(0.45)
        s = self.dev.snapshot()
        self.assertGreater(s["v"], 0)
        self.assertGreater(s["p"], 0)
        self.assertEqual(s["mode"], "CV")
        self.assertGreater(len(self.dev.trend_tail()), 1)

    # --- 记录 / 序列 ---
    def test_10_recorder_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            rec = recmod.Recorder(tmp)
            path = rec.start()
            state = self.dev.snapshot()
            state.update({"settle": 0.3, "v": 99.9})   # 稳定窗口内应丢弃
            rec.on_sample(state)
            self.assertEqual(rec.rows, 0)
            state["settle"] = 0
            state["v"] = 1.79
            for _ in range(3):
                rec.on_sample(state)
            self.assertEqual(rec.rows, 3)
            rec.stop()
            with open(path, "r", encoding="utf-8-sig") as f:
                text = f.read()
            self.assertIn("time,V,I,P,mode,out", text)
            self.assertNotIn("99.9", text)
            self.assertEqual(len(text.strip().splitlines()), 4)

    def test_11_sequence_completes(self):
        events = []
        runner = seqmod.SequenceRunner(
            self.dev, steps=recmod.DEFAULT_STEPS, loops=1, speed=200.0,
            on_event=lambda text, level: events.append((level, text)))
        runner.start()
        runner.join(timeout=12)
        self.assertEqual(runner.status, "done", events)
        self.assertFalse(self.dev.output, "序列结束应自动 OUTP OFF")
        self.assertAlmostEqual(self.dev.set_v, 1.79)
        self.assertIn("≤ 180 W", runner.validate_all())
        self.assertAlmostEqual(max(s.v * s.i for s in runner.steps), 72.0)
        self.assertAlmostEqual(runner.estimate(), 55.0)

    # --- Web API / 跨平台 ---
    def test_12_web_api(self):
        from . import webui
        with tempfile.TemporaryDirectory() as tmp:
            app = webui.ConsoleApp(self.dev, tmp)
            s = app.handle("/api/state", {})
            self.assertIn("limits", s)
            self.assertEqual(len(s["steps"]), 4)
            self.assertEqual(len(app.handle("/api/cmd", {"cmd": "V 12"})["lines"]), 2)
            r = app.handle("/api/connect", {"port": "SIM", "sim": True, "auto": True})
            self.assertTrue(r["ok"])
            r = app.handle("/api/record", {"action": "start"})
            self.assertTrue(r["ok"])
            r = app.handle("/api/record", {"action": "stop"})
            self.assertTrue(r["ok"])
            r = app.handle("/api/sequence", {"action": "start", "loops": 1, "speed": 200})
            self.assertTrue(r["ok"])
            if app.sequence:
                app.sequence.stop()
                app.sequence.join(timeout=5)
            self.assertIsInstance(app.handle("/api/ports", {}), dict)
            self.assertIsInstance(app.handle("/api/help", {})["text"], str)

    def test_13_cross_platform_helpers(self):
        self.assertIsInstance(sp.list_ports(), list)
        self.assertIsInstance(sp.default_port(), str)
        self.assertEqual(dv.BAUD_CANDIDATES[0], 4800)
        self.assertEqual(len(dv.BAUD_CANDIDATES), 6)
        self.assertFalse(pr.SUMMARY["scpi"])
        self.assertEqual(pr.SUMMARY["baud"], 4800)

    # --- 物理层防护：RX 乱码绝不能变成假读数 ---
    def test_14_garbage_frame_never_becomes_readout(self):
        """校验和不符的 26 字节回环帧，绝不能被解析成电压读数。"""
        class GarbagePort(object):
            def __init__(self):
                self.sent = []
            def write(self, data):
                self.sent.append(bytes(data))
            def read(self, size):
                return b"\xAA" + b"\xFD" * 25 if self.sent else b""
            def flush_input(self):
                pass
            def close(self):
                pass

        d = dv.IT6721(poll_interval=0.05)
        d.connected = True
        d.port = GarbagePort()
        r = d._frame(pr.build(0, pr.CMD_READ))
        self.assertIsNotNone(r)
        self.assertTrue(r.get("check_bad"))
        self.assertEqual(d.query("*IDN?"), "")
        self.assertGreater(d.rx_garbage, 0)
        self.assertFalse(dv.is_printable_reply("\xabmw\xfd"))
        self.assertTrue(dv.is_printable_reply("ITECH,IT6721,V1.00"))
        self.assertFalse(d.read_measure())
        self.assertEqual(d.v, 0.0)
        self.assertEqual(len(d.history), 0)
        self.assertTrue(all(len(x) == 26 and x[0] == 0xAA
                            for x in d.port.sent))
        d.disconnect(quiet=True)

    def test_15_parity_and_address_configurable(self):
        p = sp.open_serial("SIM", 4800, 0.1, sim=True, parity="E")
        self.assertEqual(p.parity, "E")
        p.close()
        d = dv.IT6721(poll_interval=0.05)
        d.connect("SIM", 4800, sim=True, parity="O", address=1)
        self.assertEqual(d.parity, "O")
        self.assertEqual(d.baud, 4800)
        self.assertEqual(d.address, 1)
        reply = d.query("*IDN?")
        self.assertIn("IT6721", reply)
        s = d.snapshot()
        self.assertEqual(s["parity"], "O")
        self.assertEqual(s["protocol"], "26B-frame")
        self.assertIn("garbage", s)
        d.disconnect(quiet=True)

    # --- 26 字节协议编解码（v2.0 新增）---
    def test_16_frame_value_encoding(self):
        self.assertEqual(pr.enc_voltage(16.0), b"\x80\x3E\x00\x00")
        self.assertEqual(pr.enc_current(1.0), b"\xE8\x03")
        f = pr.command_from_text("VOLT 12.00", 0)[0]
        self.assertEqual(f[2], pr.CMD_SET_V)
        self.assertEqual(pr.dec_u32(f[3:7]), 12000)
        f = pr.command_from_text("CURR 1.200", 0)[0]
        self.assertEqual(f[2], pr.CMD_SET_I)
        self.assertEqual(pr.dec_u16(f[3:5]), 1200)
        self.assertEqual(pr.command_from_text("OUTP ON", 0)[0][3], 1)
        self.assertEqual(pr.command_from_text("OUTP OFF", 0)[0][3], 0)
        self.assertIsNone(pr.command_from_text("*RCL 2", 0))
        self.assertIsNone(pr.command_from_text("CURR:PROT 8.2", 0))

    def test_17_state_roundtrip(self):
        d = pr.encode_state(set_v=24.0, set_i=1.2, actual_v=23.98,
                            actual_i=1.0, output=True, remote=True, mode="CV")
        s = pr.decode_state(d)
        self.assertAlmostEqual(s["set_v"], 24.0)
        self.assertAlmostEqual(s["set_i"], 1.2)
        self.assertAlmostEqual(s["actual_v"], 23.98)
        self.assertTrue(s["output"])
        self.assertTrue(s["remote"])
        self.assertEqual(s["mode"], "CV")
        info = pr.decode_info(pr.encode_info())
        self.assertIn("IT672", info["model"])
        f = pr.build(1, pr.CMD_SET_ADDR, b"\x02")
        self.assertEqual(f[1], 1)
        self.assertEqual(f[2], pr.CMD_SET_ADDR)
        self.assertIsNotNone(pr.parse(f))

    # --- 手动微调 / 全局锁 / 可编辑序列（v2.2.0 新增）---
    def _wait_tuner(self, tuner, status, timeout=2.0):
        end = time.time() + timeout
        while time.time() < end:
            if tuner.state()["status"] == status:
                return True
            time.sleep(0.01)
        return False

    def test_18_autotune_up_sweep(self):
        t = tunemod.AutoTuner(self.dev)
        t.configure(target="V", vmin=1.00, vmax=1.05,
                    step=0.01, interval=0.01, value=1.00)
        r = t.start("up")
        self.assertEqual(r["status"], "running")
        self.assertTrue(self._wait_tuner(t, "done"))
        st = t.state()
        self.assertAlmostEqual(st["value"], 1.05, places=2)
        self.assertGreaterEqual(st["ticks"], 6)
        self.assertAlmostEqual(self.dev.set_v, 1.05, places=2)
        t.stop()

    def test_19_autotune_stops_when_locked(self):
        t = tunemod.AutoTuner(self.dev, can_write=lambda: False)
        t.configure(target="V", vmin=1.00, vmax=1.10,
                    step=0.01, interval=0.01)
        t.start("pingpong")
        self.assertTrue(self._wait_tuner(t, "stopped"))
        self.assertEqual(t.state()["mode"], "off")

    def test_20_autotune_rejects_invalid_config(self):
        t = tunemod.AutoTuner(self.dev)
        with self.assertRaises(dv.ValidationError):
            t.configure(target="X", vmin=0, vmax=10)
        t2 = tunemod.AutoTuner(self.dev)
        with self.assertRaises(dv.ValidationError):
            t2.configure(target="V", vmin=0, vmax=61)
        t3 = tunemod.AutoTuner(self.dev)
        with self.assertRaises(dv.ValidationError):
            t3.configure(target="V", vmin=5, vmax=1)
        t4 = tunemod.AutoTuner(self.dev)
        st = t4.configure(target="I", vmin=0, vmax=2, step=0.01, interval=0.001)
        self.assertEqual(st["interval"], 0.1)     # 最小间隔护栏
        self.assertEqual(st["target"], "I")
        self.assertEqual(tunemod.quant(1.234), 1.23)
        self.assertEqual(tunemod.quant(3.0), 3.00)

    def test_21_global_lock_and_sequence_steps(self):
        with tempfile.TemporaryDirectory() as td:
            app = webui.ConsoleApp(self.dev, td)
            s = app.handle("/api/state", {})
            self.assertFalse(s["locked"])
            self.assertIn("tune", s)
            self.assertEqual(len(s["steps"]), 4)
            self.assertEqual(app.set_lock(True)["locked"], True)
            r = app.handle("/api/apply", {"v": 5, "i": 1})
            self.assertFalse(r["ok"])
            self.assertTrue(r["locked"])
            self.assertTrue(app.handle("/api/output", {"on": False})["ok"])
            self.assertFalse(app.handle("/api/tune", {"action": "auto",
                                                      "mode": "up"})["ok"])
            app.handle("/api/cmd", {"cmd": "LOCK OFF"})
            self.assertFalse(app.locked)

            r = app.handle("/api/sequence/steps", {
                "steps": [{"v": 5, "i": 1, "hold": 1},
                          {"v": 12, "i": 2, "hold": 2}],
                "loops": 2, "speed": 1})
            self.assertTrue(r["ok"])
            self.assertEqual(len(app.steps), 2)
            self.assertEqual(app.handle("/api/state", {})["seq"]["loops"], 2)
            bad = app.handle("/api/sequence/steps", {
                "steps": [{"v": 60, "i": 8, "hold": 1}]})
            self.assertFalse(bad["ok"])
            self.assertIn("180", bad["error"])
            self.assertEqual(len(app.steps), 2)   # 越限不污染已有步骤

    def test_22_tune_api_set_nudge_auto(self):
        with tempfile.TemporaryDirectory() as td:
            app = webui.ConsoleApp(self.dev, td)
            base = {"target": "V", "vmin": 0, "vmax": 12,
                    "step": 0.01, "interval": 0.01}
            r = app.handle("/api/tune", dict(base, action="set", value=3.00))
            self.assertTrue(r["ok"])
            self.assertAlmostEqual(r["tune"]["value"], 3.00, places=2)
            r = app.handle("/api/tune", dict(base, action="nudge", delta=0.05))
            self.assertAlmostEqual(r["tune"]["value"], 3.05, places=2)
            r = app.handle("/api/tune", dict(base, action="auto", mode="up",
                                             vmin=3.00, vmax=3.03))
            self.assertTrue(r["tune"]["active"])
            self.assertTrue(self._wait_tuner(app.tuner, "done"))
            self.assertAlmostEqual(app.tuner.state()["value"], 3.03, places=2)
            app.set_lock(True)
            app.tuner.stop()


def run_all():
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(IT6721SelfTest)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    print("\n  自测结果：%d 通过 / %d 失败 / %d 错误"
          % (result.testsRun - len(result.failures) - len(result.errors),
             len(result.failures), len(result.errors)))
    return 0 if result.wasSuccessful() else 1
