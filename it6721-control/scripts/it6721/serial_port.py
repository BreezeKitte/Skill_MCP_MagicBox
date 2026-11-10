# -*- coding: utf-8 -*-
"""
零第三方依赖的跨平台串口层。

Windows : ctypes 调用 kernel32 (CreateFileW / SetCommState / ReadFile / WriteFile)
Linux/macOS : termios + fcntl + select，直接操作 /dev/tty*
Simulation  : 纯软件模拟的 IT6721，用于无硬件演示与自测

只使用 Python 标准库，因此不需要 pip install 任何东西。
"""

import glob
import os
import sys
import time

from . import protocol as pr

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

__all__ = ["SerialError", "list_ports", "open_serial", "SimPort", "IS_WIN"]


class SerialError(Exception):
    pass


# --------------------------------------------------------------------------
# 端口枚举
# --------------------------------------------------------------------------
def _win_probe(name):
    """用 CreateFile 试探某个 COM 口能否打开。"""
    try:
        h = _k32.CreateFileW("\\\\.\\" + name, 0xC0000000, 0, None, 3, 0, None)
        if h in (None, 0, _INVALID):
            return False
        _k32.CloseHandle(h)
        return True
    except Exception:
        return False


def list_ports():
    """返回本机可用串口名列表（不打开、不占用设备）。"""
    if IS_WIN:
        found = []
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DEVICEMAP\SERIALCOMM"
            )
            i = 0
            while True:
                try:
                    _name, value, _type = winreg.EnumValue(key, i)
                    found.append(value)
                    i += 1
                except OSError:
                    break
            winreg.CloseKey(key)
        except Exception:
            pass
        if not found:
            found = ["COM%d" % n for n in range(1, 33) if _win_probe("COM%d" % n)]
        return sorted(set(found), key=lambda s: (len(s), s))

    patterns = [
        "/dev/ttyUSB*", "/dev/ttyACM*", "/dev/ttyS*",
        "/dev/serial/by-id/*", "/dev/cu.usbserial*", "/dev/cu.wchusbserial*",
    ]
    out = []
    for pat in patterns:
        out.extend(glob.glob(pat))
    return sorted(set(out))


# 设备名里出现这些字样 → USB 转串口；\Device\Serial0/1 这类是主板自带口
_USB_DEVICE_HINTS = (
    "VCP", "ProlificSerial", "Prolific", "USBSER", "FTDIBUS", "FTDI",
    "Silabser", "CH340", "usbser", "SerialUSB", "WCH",
)


def _com_number(name):
    digits = "".join(c for c in str(name) if c.isdigit())
    return int(digits) if digits else 0


def _win_port_devices():
    """返回 [(设备名, COM口)]，用于区分 USB 串口与主板自带串口。"""
    pairs = []
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DEVICEMAP\SERIALCOMM"
        )
        i = 0
        while True:
            try:
                name, value, _ = winreg.EnumValue(key, i)
                pairs.append((name, value))
                i += 1
            except OSError:
                break
        winreg.CloseKey(key)
    except Exception:
        pass
    return pairs


def default_port():
    """挑最可能是电源的那个串口。

    按「USB 串口 > 主板自带串口」排序，存在多个 USB 串口时取编号最大的
    （通常是最后插入的那个）。不要在这里硬编码端口名：换模块后编号会变。
    """
    ports = list_ports()
    if not ports:
        return "" if IS_WIN else "/dev/ttyUSB0"
    if IS_WIN:
        usb = [com for dev, com in _win_port_devices()
               if com in ports
               and any(h.lower() in dev.lower() for h in _USB_DEVICE_HINTS)]
        if usb:
            return sorted(set(usb), key=_com_number)[-1]
        # 无法识别设备类型时，至少避开主板自带的低编号串口
        others = [p for p in ports if _com_number(p) > 3]
        if others:
            return sorted(others, key=_com_number)[-1]
        return ports[-1]
    for p in ports:
        if "USB" in p.upper() or "ACM" in p.upper():
            return p
    return ports[-1]


# --------------------------------------------------------------------------
# Windows 后端
# --------------------------------------------------------------------------
if IS_WIN:
    import ctypes
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _INVALID = ctypes.c_void_p(-1).value

    GENERIC_READ = 0x80000000
    GENERIC_WRITE = 0x40000000
    OPEN_EXISTING = 3
    PURGE_RXCLEAR = 0x0008
    PURGE_TXCLEAR = 0x0004
    MAXDWORD = 0xFFFFFFFF

    class DCB(ctypes.Structure):
        # 位域部分合并成一个 DWORD：bit0 fBinary, bit1 fParity,
        # bits4-5 fDtrControl, bits12-13 fRtsControl
        _fields_ = [
            ("DCBlength", wintypes.DWORD),
            ("BaudRate", wintypes.DWORD),
            ("flags", wintypes.DWORD),
            ("wReserved", wintypes.WORD),
            ("XonLim", wintypes.WORD),
            ("XoffLim", wintypes.WORD),
            ("ByteSize", ctypes.c_ubyte),
            ("Parity", ctypes.c_ubyte),
            ("StopBits", ctypes.c_ubyte),
            ("XonChar", ctypes.c_char),
            ("XoffChar", ctypes.c_char),
            ("ErrorChar", ctypes.c_char),
            ("EofChar", ctypes.c_char),
            ("EvtChar", ctypes.c_char),
            ("wReserved1", wintypes.WORD),
        ]

    class COMMTIMEOUTS(ctypes.Structure):
        _fields_ = [
            ("ReadIntervalTimeout", wintypes.DWORD),
            ("ReadTotalTimeoutMultiplier", wintypes.DWORD),
            ("ReadTotalTimeoutConstant", wintypes.DWORD),
            ("WriteTotalTimeoutMultiplier", wintypes.DWORD),
            ("WriteTotalTimeoutConstant", wintypes.DWORD),
        ]

    _k32.CreateFileW.restype = wintypes.HANDLE
    _k32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
        wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    _k32.CloseHandle.restype = wintypes.BOOL
    _k32.GetCommState.argtypes = [wintypes.HANDLE, ctypes.POINTER(DCB)]
    _k32.SetCommState.argtypes = [wintypes.HANDLE, ctypes.POINTER(DCB)]
    _k32.SetCommTimeouts.argtypes = [wintypes.HANDLE, ctypes.POINTER(COMMTIMEOUTS)]
    _k32.PurgeComm.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _k32.WriteFile.argtypes = [
        wintypes.HANDLE, ctypes.c_char_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID,
    ]
    _k32.ReadFile.argtypes = [
        wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID,
    ]

    # DCB.Parity 取值：NONE / ODD / EVEN
    PARITY_CODE = {"N": 0, "O": 1, "E": 2}

    class WinSerial(object):
        def __init__(self, port, baudrate=4800, timeout=0.30, parity="N",
                     stop_bits=1, handshake=True):
            self.name = port
            self.baudrate = baudrate
            self.timeout = timeout
            self.parity = str(parity).upper()[:1]
            self.stop_bits = 2 if int(stop_bits) == 2 else 1
            self.handshake = bool(handshake)
            path = port if port.startswith("\\\\") else "\\\\.\\" + port
            self._h = _k32.CreateFileW(
                path, GENERIC_READ | GENERIC_WRITE, 0, None, OPEN_EXISTING, 0, None
            )
            if self._h in (None, 0, _INVALID):
                raise SerialError(
                    "无法打开 %s（错误码 %d）。请确认端口未被其他程序占用。"
                    % (port, ctypes.get_last_error())
                )
            try:
                dcb = DCB()
                dcb.DCBlength = ctypes.sizeof(DCB)
                if not _k32.GetCommState(self._h, ctypes.byref(dcb)):
                    raise SerialError("GetCommState 失败：%d" % ctypes.get_last_error())
                pcode = PARITY_CODE.get(self.parity, 0)
                dcb.BaudRate = int(baudrate)
                dcb.ByteSize = 8
                dcb.Parity = pcode        # NONE / ODD / EVEN，手册说明由面板菜单设定
                # ONESTOPBIT=0 / TWOSTOPBITS=2；IT6720 系列固定 1，2 仅供诊断
                dcb.StopBits = 0 if self.stop_bits == 1 else 2
                # fBinary；handshake=True 时拉高 DTR/RTS，False 时两线全关。
                # 部分仪器的 RS232 口把 DTR/RTS 当唤醒/握手信号，拉高会引发误动作，
                # 因此诊断阶段必须能把它们关掉做对比。
                dcb.flags = 0x0001
                if self.handshake:
                    dcb.flags |= 0x0010 | 0x1000
                if pcode:
                    dcb.flags |= 0x0002
                if not _k32.SetCommState(self._h, ctypes.byref(dcb)):
                    raise SerialError("SetCommState 失败：%d" % ctypes.get_last_error())
                to = COMMTIMEOUTS()
                to.ReadIntervalTimeout = MAXDWORD
                to.ReadTotalTimeoutMultiplier = 0
                to.ReadTotalTimeoutConstant = max(1, int(timeout * 1000))
                to.WriteTotalTimeoutMultiplier = 0
                to.WriteTotalTimeoutConstant = max(100, int(timeout * 1000))
                _k32.SetCommTimeouts(self._h, ctypes.byref(to))
                _k32.PurgeComm(self._h, PURGE_RXCLEAR | PURGE_TXCLEAR)
            except Exception:
                _k32.CloseHandle(self._h)
                self._h = None
                raise

        def write(self, data):
            written = wintypes.DWORD(0)
            ok = _k32.WriteFile(
                self._h, bytes(data), len(data), ctypes.byref(written), None
            )
            if not ok:
                raise SerialError("WriteFile 失败：%d" % ctypes.get_last_error())
            return written.value

        def read(self, size=256):
            buf = ctypes.create_string_buffer(size)
            got = wintypes.DWORD(0)
            ok = _k32.ReadFile(self._h, buf, size, ctypes.byref(got), None)
            if not ok:
                raise SerialError("ReadFile 失败：%d" % ctypes.get_last_error())
            return buf.raw[: got.value]

        def flush_input(self):
            _k32.PurgeComm(self._h, PURGE_RXCLEAR)

        def close(self):
            if self._h:
                _k32.CloseHandle(self._h)
                self._h = None

else:
    import fcntl
    import select
    import struct
    import termios

    class PosixSerial(object):
        def __init__(self, port, baudrate=4800, timeout=0.30, parity="N",
                     stop_bits=1, handshake=True):
            self.name = port
            self.baudrate = baudrate
            self.timeout = timeout
            self.parity = str(parity).upper()[:1]
            self.stop_bits = 2 if int(stop_bits) == 2 else 1
            self.handshake = bool(handshake)
            flags = os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK
            try:
                self._fd = os.open(port, flags)
            except OSError as exc:
                raise SerialError(
                    "无法打开 %s：%s。Linux 下常需把用户加入 dialout 组："
                    "sudo usermod -aG dialout $USER（重新登录后生效）" % (port, exc)
                )
            try:
                speed = getattr(termios, "B" + str(int(baudrate)), None)
                if speed is None:
                    raise SerialError("内核不支持波特率 %s" % baudrate)
                a = termios.tcgetattr(self._fd)
                a[0] &= ~(termios.IGNBRK | termios.BRKINT | termios.PARMRK
                          | termios.ISTRIP | termios.INLCR | termios.IGNCR
                          | termios.ICRNL | termios.IXON)
                a[1] &= ~termios.OPOST
                a[2] &= ~(termios.CSIZE | termios.PARENB | termios.PARODD
                          | termios.CSTOPB)
                a[2] |= termios.CS8 | termios.CLOCAL | termios.CREAD
                if self.stop_bits == 2:
                    a[2] |= termios.CSTOPB
                if self.parity == "E":
                    a[2] |= termios.PARENB
                elif self.parity == "O":
                    a[2] |= termios.PARENB | termios.PARODD
                a[3] &= ~(termios.ECHO | termios.ECHONL | termios.ICANON
                          | termios.ISIG | termios.IEXTEN)
                termios.cfsetispeed(a, speed)
                termios.cfsetospeed(a, speed)
                termios.tcsetattr(self._fd, termios.TCSANOW, a)
                if not self.handshake:
                    try:
                        bits = struct.pack(
                            "i", termios.TIOCM_DTR | termios.TIOCM_RTS)
                        fcntl.ioctl(self._fd, termios.TIOCMBIC, bits)
                    except Exception:
                        pass
                fcntl.fcntl(self._fd, fcntl.F_SETFL, os.O_NONBLOCK)
                termios.tcflush(self._fd, termios.TCIOFLUSH)
            except Exception:
                os.close(self._fd)
                self._fd = None
                raise

        def write(self, data):
            view = memoryview(bytes(data))
            total = 0
            deadline = time.time() + 1.0
            while total < len(view) and time.time() < deadline:
                try:
                    total += os.write(self._fd, view[total:])
                except BlockingIOError:
                    time.sleep(0.002)
            return total

        def read(self, size=256):
            ready, _, _ = select.select([self._fd], [], [], self.timeout)
            if not ready:
                return b""
            try:
                return os.read(self._fd, size)
            except (BlockingIOError, InterruptedError):
                return b""

        def flush_input(self):
            try:
                termios.tcflush(self._fd, termios.TCIFLUSH)
            except Exception:
                pass

        def close(self):
            if self._fd is not None:
                os.close(self._fd)
                self._fd = None


# --------------------------------------------------------------------------
# 模拟设备（无硬件时用于界面演示 / 自动化测试）
# --------------------------------------------------------------------------
class SimPort(object):
    """模拟一台 IT6721：按 26 字节二进制帧协议应答（不是 SCPI）。

    CV 模式下输出设定电压，负载电流固定 1.000 A；每条命令都回 26 字节帧。
    """

    def __init__(self, port="SIM", baudrate=4800, timeout=0.30, parity="N",
                 stop_bits=1, handshake=True):
        self.name = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.parity = parity
        self.stop_bits = 2 if int(stop_bits) == 2 else 1
        self.handshake = bool(handshake)
        self.set_v = 1.79
        self.set_i = 2.000
        self.ovp = 62.00
        self.ocp = 8.20
        self.output = True
        self.remote = False
        self.load_i = 1.000
        self._rx = bytearray()

    def _reply(self, frame):
        self._rx.extend(bytes(frame))

    @staticmethod
    def _ok(addr, code=pr.STATUS_OK):
        return pr.build(addr, pr.CMD_STATUS, bytes([code]))

    def write(self, data):
        b = bytes(data)
        n = len(b)
        while len(b) >= pr.FRAME_LEN:
            raw, b = b[:pr.FRAME_LEN], b[pr.FRAME_LEN:]
            r = pr.parse(raw)
            if r is None:
                self._reply(self._ok(0, pr.STATUS_BAD_CHECKSUM))
                continue
            addr, cmd, d = r["addr"], r["cmd"], r["data"]
            if cmd == pr.CMD_MODE:
                self.remote = bool(d[0])
                self._reply(self._ok(addr))
            elif cmd == pr.CMD_OUTPUT:
                self.output = bool(d[0])
                self._reply(self._ok(addr))
            elif cmd == pr.CMD_SET_V:
                self.set_v = pr.dec_u32(d) / 1000.0
                self._reply(self._ok(addr))
            elif cmd == pr.CMD_SET_I:
                self.set_i = pr.dec_u16(d) / 1000.0
                self._reply(self._ok(addr))
            elif cmd == pr.CMD_MAX_V:
                self.ovp = pr.dec_u32(d) / 1000.0
                self._reply(self._ok(addr))
            elif cmd == pr.CMD_READ:
                state = pr.encode_state(
                    set_v=self.set_v, set_i=self.set_i,
                    actual_v=self.set_v if self.output else 0.0,
                    actual_i=self.load_i if self.output else 0.0,
                    output=self.output, remote=self.remote)
                self._reply(pr.build(addr, pr.CMD_READ, state))
            elif cmd == pr.CMD_INFO:
                self._reply(pr.build(addr, pr.CMD_INFO, pr.encode_info()))
            else:
                self._reply(self._ok(addr, pr.STATUS_INVALID))
        return n

    def read(self, size=256):
        time.sleep(0.002)  # 模拟一次往返传播延迟
        if not self._rx:
            return b""
        out = bytes(self._rx[:size])
        del self._rx[:size]
        return out

    def flush_input(self):
        self._rx.clear()

    def close(self):
        self._rx.clear()


def open_serial(port, baudrate=4800, timeout=0.30, sim=False,
                use_pyserial=False, parity="N", stop_bits=1, handshake=True):
    """统一工厂。返回具备 write/read/flush_input/close 的对象。

    parity: "N" 无校验 / "O" 奇校验 / "E" 偶校验。
    IT6720 系列手册固定 8N1（无校验、1 停止位）；保留 O/E 与 2 停止位
    仅用于诊断某些改装/兼容线缆，正常接线不需要改。

    stop_bits: 1 / 2。手册固定 1。
    handshake: 是否拉高 DTR / RTS。诊断时可关掉做对比，排除握手线误触发。

    use_pyserial=True 时优先使用系统里已安装的 pyserial（可选，非必需）。
    """
    pcode = str(parity).upper()[:1]
    nstop = 2 if int(stop_bits) == 2 else 1
    hs = bool(handshake)
    if not port and not sim:
        raise SerialError("未发现可用串口，请插入 USB-TTL 模块或用 --port 指定")
    if sim or str(port).upper() == "SIM":
        return SimPort(port, baudrate, timeout, pcode, nstop, hs)
    if use_pyserial:
        try:
            import serial  # type: ignore
            p = serial.Serial(port=port, baudrate=baudrate, bytesize=8,
                              parity={"N": "N", "O": "O", "E": "E"}.get(pcode, "N"),
                              stopbits=nstop, timeout=timeout,
                              write_timeout=timeout)
            p.flushInput()
            return p
        except ImportError:
            pass  # 静默回退到内置后端
    if IS_WIN:
        return WinSerial(port, baudrate, timeout, pcode, nstop, hs)
    return PosixSerial(port, baudrate, timeout, pcode, nstop, hs)
