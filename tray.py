# -*- coding: utf-8 -*-
"""零依赖的 Windows 托盘图标（ctypes + Shell_NotifyIcon）。

托盘在独立线程里跑自己的消息循环；菜单点击通过回调 on_command(cmd_id) 交回主程序，
回调里只应往队列里放东西，不要直接操作 tkinter。
"""
import ctypes
import os
import struct
import sys
import threading
from ctypes import wintypes

WM_USER = 0x0400
WM_TRAY = WM_USER + 1
WM_CLOSE = 0x0010
WM_DESTROY = 0x0002
WM_RBUTTONUP = 0x0205
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP = 1, 2, 4
MF_STRING, MF_CHECKED, MF_SEPARATOR = 0, 8, 0x800
TPM_RETURNCMD, TPM_NONOTIFY, TPM_RIGHTBUTTON = 0x100, 0x80, 0x2

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HANDLE),
        ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HANDLE),
        ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR),
    ]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND), ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT), ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HANDLE), ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD), ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256), ("uVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64), ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", ctypes.c_byte * 16), ("hBalloonIcon", wintypes.HANDLE),
    ]


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def make_icon_file(path):
    """生成一个 32x32 的简单图标（青色圆 + 阴阳点），避免携带二进制资源。"""
    n = 32
    rows = []
    for y in range(n):
        row = bytearray()
        for x in range(n):
            dx, dy = x - 15.5, y - 15.5
            d = (dx * dx + dy * dy) ** 0.5
            if d > 15:
                px = (0, 0, 0, 0)
            elif d > 13:
                px = (60, 90, 40, 255)          # 外圈 BGRA
            else:
                left = dx < 0
                px = (235, 235, 235, 255) if left else (70, 60, 40, 255)
                # 小圆点
                for cy, light in ((-7.0, True), (7.0, False)):
                    if (dx ** 2 + (dy - cy) ** 2) ** 0.5 < 3.5:
                        px = (235, 235, 235, 255) if not light else (70, 60, 40, 255)
            row += bytes(px)
        rows.append(bytes(row))
    pixels = b"".join(reversed(rows))
    mask = b"\x00" * (n // 8 * n)
    bih = struct.pack("<IiiHHIIiiII", 40, n, n * 2, 1, 32, 0, len(pixels) + len(mask), 0, 0, 0, 0)
    data = bih + pixels + mask
    header = struct.pack("<HHH", 0, 1, 1)
    entry = struct.pack("<BBBBHHII", n, n, 0, 0, 1, 32, len(data), 22)
    with open(path, "wb") as f:
        f.write(header + entry + data)


class Tray:
    """menu: 返回 [(cmd_id, 文字, 是否勾选)] 或 None 表示分隔线 的函数（每次弹出时调用）。"""

    def __init__(self, tip, icon_path, menu_fn, on_command):
        self.tip = tip
        self.icon_path = icon_path
        self.menu_fn = menu_fn
        self.on_command = on_command
        self.hwnd = None
        self._ready = threading.Event()
        self._thread = None
        self._wndproc = WNDPROC(self._proc)  # 持有引用防止被回收
        self._nid = None

    def start(self):
        self._thread = threading.Thread(target=self._run, name="tray", daemon=True)
        self._thread.start()
        self._ready.wait(5)

    def stop(self):
        if self.hwnd:
            ctypes.windll.user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)

    def set_tip(self, tip):
        if self._nid is None:
            return
        self._nid.szTip = tip[:127]
        ctypes.windll.shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(self._nid))

    # --- 内部
    def _proc(self, hwnd, msg, wparam, lparam):
        u32 = ctypes.windll.user32
        u32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        u32.DefWindowProcW.restype = LRESULT
        if msg == WM_TRAY:
            if lparam == WM_RBUTTONUP:
                self._popup(hwnd)
            elif lparam in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                self.on_command("show")
            return 0
        if msg == WM_CLOSE:
            u32.DestroyWindow(hwnd)
            return 0
        if msg == WM_DESTROY:
            if self._nid is not None:
                ctypes.windll.shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._nid))
                self._nid = None
            u32.PostQuitMessage(0)
            return 0
        return u32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _popup(self, hwnd):
        u32 = ctypes.windll.user32
        u32.CreatePopupMenu.restype = wintypes.HMENU
        u32.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR]
        u32.TrackPopupMenu.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int,
                                       ctypes.c_int, wintypes.HWND, ctypes.c_void_p]
        hmenu = u32.CreatePopupMenu()
        ids = {}
        for i, item in enumerate(self.menu_fn(), start=1):
            if item is None:
                u32.AppendMenuW(hmenu, MF_SEPARATOR, 0, None)
                continue
            cmd, text, checked = item
            ids[i] = cmd
            u32.AppendMenuW(hmenu, MF_STRING | (MF_CHECKED if checked else 0), i, text)
        pt = POINT()
        u32.GetCursorPos(ctypes.byref(pt))
        u32.SetForegroundWindow(hwnd)  # 让菜单在点击别处时能正常消失
        chosen = u32.TrackPopupMenu(hmenu, TPM_RETURNCMD | TPM_NONOTIFY | TPM_RIGHTBUTTON,
                                    pt.x, pt.y, 0, hwnd, None)
        u32.PostMessageW(hwnd, 0, 0, 0)
        u32.DestroyMenu(hmenu)
        if chosen in ids:
            self.on_command(ids[chosen])

    def _run(self):
        u32, k32, s32 = ctypes.windll.user32, ctypes.windll.kernel32, ctypes.windll.shell32
        k32.GetModuleHandleW.restype = wintypes.HINSTANCE
        u32.CreateWindowExW.restype = wintypes.HWND
        u32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                        wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
        u32.LoadImageW.restype = wintypes.HANDLE
        u32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
                                   ctypes.c_int, ctypes.c_int, wintypes.UINT]
        hinst = k32.GetModuleHandleW(None)
        wc = WNDCLASSW()
        wc.lpfnWndProc = self._wndproc
        wc.hInstance = hinst
        wc.lpszClassName = "XiuxianTrayWnd"
        u32.RegisterClassW(ctypes.byref(wc))
        self.hwnd = u32.CreateWindowExW(0, wc.lpszClassName, "xiuxian", 0, 0, 0, 0, 0,
                                        None, None, hinst, None)
        hicon = u32.LoadImageW(None, self.icon_path, 1, 0, 0, 0x10)
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self.hwnd
        nid.uID = 1
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_TRAY
        nid.hIcon = hicon
        nid.szTip = self.tip[:127]
        s32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))
        self._nid = nid
        self._ready.set()
        msg = wintypes.MSG()
        while u32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            u32.TranslateMessage(ctypes.byref(msg))
            u32.DispatchMessageW(ctypes.byref(msg))
        self.hwnd = None


def autostart_enabled(name="XiuxianThinkingGame"):
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as k:
            winreg.QueryValueEx(k, name)
            return True
    except OSError:
        return False


def set_autostart(enable, script_path, name="XiuxianThinkingGame"):
    import winreg
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.exists(pyw):
        pyw = sys.executable
    cmd = '"%s" "%s"' % (pyw, script_path)
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run",
                        0, winreg.KEY_SET_VALUE) as k:
        if enable:
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, cmd)
        else:
            try:
                winreg.DeleteValue(k, name)
            except OSError:
                pass
