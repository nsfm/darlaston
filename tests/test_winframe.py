"""Our own Windows title bar, on a real window handle.

The caption buttons are drawn by us and answered through non-client
messages, and nothing short of a real HWND can exercise that path: the
offscreen platform has no non-client area. So this runs only on Windows
with the native platform plugin, which the CI job provides. The first
Windows tester reported the X doing nothing; this is the test that
would have caught it, or will say the handler is not where the fault is.
"""
from __future__ import annotations

import os
import sys
import time

import pytest

pytestmark = pytest.mark.wincam

if not sys.platform.startswith("win") or not os.environ.get("DARLASTON_WINFRAME"):
    pytest.skip("needs Windows and the native platform plugin "
                "(DARLASTON_WINFRAME=1)", allow_module_level=True)

WM_NCHITTEST = 0x0084
WM_NCLBUTTONDOWN = 0x00A1
WM_NCLBUTTONUP = 0x00A2
HTCLOSE = 20
HTCAPTION = 2


def _pump(seconds: float, until=None) -> None:
    from PySide6 import QtWidgets

    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        QtWidgets.QApplication.processEvents()
        if until is not None and until():
            return
        time.sleep(0.02)


def _user32():
    import ctypes
    from ctypes import wintypes

    u = ctypes.windll.user32
    u.SendMessageW.restype = ctypes.c_ssize_t
    u.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t]
    u.PostMessageW.restype = wintypes.BOOL
    u.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t]
    return u


def _screen_lparam(win, x: int, y: int) -> int:
    """A client point, as the screen-coordinate lParam WM_NCHITTEST wants."""
    from PySide6 import QtCore

    at = win.mapToGlobal(QtCore.QPoint(x, y))
    ratio = win.devicePixelRatioF() or 1.0
    sx, sy = int(round(at.x() * ratio)), int(round(at.y() * ratio))
    return ((sy & 0xFFFF) << 16) | (sx & 0xFFFF)


def test_the_close_button_closes_the_window(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from darlaston.camera.mock import MockCamera
    from darlaston.ui.main import MainWindow

    win = MainWindow(lambda: MockCamera(fps=30.0))
    closed = []
    try:
        win.resize(900, 600)
        assert win.take_native_frame(), "the frame was not taken on this machine"
        win.show()
        _pump(1.0)
        frame = win._frame.frame()
        assert len(frame.buttons) == 3, frame
        bx, bw = frame.buttons[2]
        x, y = bx + bw // 2, frame.bar // 2
        hwnd = int(win.winId())
        u = _user32()
        lparam = _screen_lparam(win, x, y)

        # Three rungs, so a failure says which one broke. The frame's own
        # hit test, in Python; the window's nativeEvent override, handed a
        # real MSG; and the message through Windows itself.
        import ctypes
        from ctypes import wintypes

        direct = win._frame._hit_test(lparam)
        assert direct == (True, HTCLOSE), f"frame._hit_test answered {direct}; geometry {frame}"

        msg = wintypes.MSG()
        msg.hwnd, msg.message, msg.wParam, msg.lParam = hwnd, WM_NCHITTEST, 0, lparam
        via_override = win.nativeEvent(b"windows_generic_MSG", ctypes.addressof(msg))
        assert tuple(via_override)[:2] == (True, HTCLOSE), \
            f"nativeEvent answered {via_override!r} for a real MSG"

        rect = wintypes.RECT()
        u.GetWindowRect(hwnd, ctypes.byref(rect))
        hit = u.SendMessageW(hwnd, WM_NCHITTEST, 0, lparam)
        assert hit == HTCLOSE, (f"hit test over the X answered {hit}; window rect "
                                f"{rect.left},{rect.top}-{rect.right},{rect.bottom}, "
                                f"lParam x={lparam & 0xFFFF} y={lparam >> 16}, "
                                f"visible={win.isVisible()} minimised={win.isMinimized()}")
        mid = u.SendMessageW(hwnd, WM_NCHITTEST, 0,
                             _screen_lparam(win, frame.width // 2, y))
        assert mid in (HTCAPTION, 1), f"the bar's middle answered {mid}"

        win.destroyed.connect(lambda *_: closed.append(True))
        assert u.PostMessageW(hwnd, WM_NCLBUTTONDOWN, HTCLOSE, lparam)
        assert u.PostMessageW(hwnd, WM_NCLBUTTONUP, HTCLOSE, lparam)
        _pump(5.0, until=lambda: not win.isVisible())
        assert not win.isVisible(), "the X was pressed and the window stayed"
    finally:
        try:
            win.shutdown()
        except Exception:
            pass
