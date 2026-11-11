"""작업 표시줄 아이콘 진행 막대(v0.16.31, Windows ITaskbarList3). 다른 OS·실패 = 아무것도 안 함.
set_progress(창, 0~1) · set_error(창) · clear(창) · flash(창) = 아이콘 깜빡임(창이 앞에 없을 때)."""
from __future__ import annotations

import ctypes
import os

_tb = None
_failed = False
TBPF_NOPROGRESS, TBPF_NORMAL, TBPF_ERROR = 0, 2, 4


def _get():
    """ITaskbarList3 COM 객체(vtable 포인터) 또는 None."""
    global _tb, _failed
    if _tb is not None or _failed or os.name != "nt":
        return _tb
    try:
        from ctypes import wintypes

        class GUID(ctypes.Structure):
            _fields_ = [("a", ctypes.c_uint32), ("b", ctypes.c_uint16), ("c", ctypes.c_uint16), ("d", ctypes.c_ubyte * 8)]

        def guid(s):
            import uuid
            u = uuid.UUID(s)
            g = GUID()
            ctypes.memmove(ctypes.byref(g), u.bytes_le, 16)
            return g
        ole = ctypes.OleDLL("ole32")
        ole.CoInitialize(None)
        clsid = guid("56FDF344-FD6D-11d0-958A-006097C9A090")      # CLSID_TaskbarList
        iid = guid("ea1afb91-9e28-4b86-90e9-9e9f8a5eefaf")        # IID_ITaskbarList3
        p = ctypes.c_void_p()
        ole.CoCreateInstance(ctypes.byref(clsid), None, 1, ctypes.byref(iid), ctypes.byref(p))   # CLSCTX_INPROC_SERVER
        vt = ctypes.cast(ctypes.cast(p, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))
        HrInit = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p)(vt[3])
        HrInit(p)
        _tb = {
            "p": p,
            "value": ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, wintypes.HWND, ctypes.c_ulonglong,
                                        ctypes.c_ulonglong)(vt[9]),
            "state": ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, wintypes.HWND, ctypes.c_int)(vt[10]),
        }
    except Exception:  # noqa: BLE001
        _failed = True
        _tb = None
    return _tb


def _hwnd(w) -> int:
    try:
        return int(w.window().winId())
    except Exception:  # noqa: BLE001
        return 0


def set_progress(w, frac: float, error: bool = False) -> None:
    t = _get()
    h = _hwnd(w)
    if not t or not h:
        return
    try:
        t["state"](t["p"], h, TBPF_ERROR if error else TBPF_NORMAL)
        t["value"](t["p"], h, int(max(0.0, min(1.0, frac)) * 1000), 1000)
    except Exception:  # noqa: BLE001
        pass


def clear(w) -> None:
    t = _get()
    h = _hwnd(w)
    if t and h:
        try:
            t["state"](t["p"], h, TBPF_NOPROGRESS)
        except Exception:  # noqa: BLE001
            pass


def flash(w) -> None:
    """창이 앞에 없으면 작업 표시줄 아이콘 깜빡임(Qt 기본 기능)."""
    try:
        from PySide6.QtWidgets import QApplication
        if not w.window().isActiveWindow():
            QApplication.alert(w.window(), 0)
    except Exception:  # noqa: BLE001
        pass
