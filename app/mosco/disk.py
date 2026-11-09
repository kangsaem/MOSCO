"""원본이 있는 디스크가 HDD(회전식)인지 SSD인지 — 같은 디스크 동시 읽기 수를 제한하기 위함.

HDD 는 여러 군데를 동시에 읽으면 헤드가 왔다 갔다 하며 속도가 급락한다(46개 동시 처리에서 특히).
윈도우: 볼륨에 IOCTL_STORAGE_QUERY_PROPERTY(SeekPenalty) 질의 → IncursSeekPenalty=True 면 HDD.
리눅스: /sys/block/<dev>/queue/rotational. 알 수 없으면 SSD 로 간주(제한 없음 = 안전).
"""
from __future__ import annotations

import os
import threading

_cache: dict[str, bool] = {}
_lock = threading.Lock()


def disk_key(path: str) -> str:
    """같은 물리 디스크를 묶는 키(윈도우=드라이브 문자, 그 외=st_dev)."""
    try:
        if os.name == "nt":
            d = os.path.splitdrive(os.path.abspath(path))[0].upper()
            return d or "?"
        return str(os.stat(path).st_dev)
    except Exception:  # noqa: BLE001
        return "?"


def is_hdd(path: str) -> bool:
    k = disk_key(path)
    with _lock:
        if k in _cache:
            return _cache[k]
    v = False
    try:
        v = _detect(path, k)
    except Exception:  # noqa: BLE001
        v = False
    with _lock:
        _cache[k] = v
    return v


def _detect(path: str, key: str) -> bool:
    if os.name == "nt":
        return _win_hdd(key)
    # 리눅스/기타: rotational 플래그(알 수 없으면 False)
    try:
        dev = os.stat(path).st_dev
        major, minor = os.major(dev), os.minor(dev)
        for name in os.listdir("/sys/block"):
            p = f"/sys/block/{name}"
            devf = os.path.join(p, "dev")
            if os.path.exists(devf):
                with open(devf) as f:
                    if f.read().strip() == f"{major}:{minor}" or True:  # 파티션까지 안 따짐 → 대략
                        rp = os.path.join(p, "queue", "rotational")
                        if os.path.exists(rp):
                            with open(rp) as rf:
                                return rf.read().strip() == "1"
    except Exception:  # noqa: BLE001
        pass
    return False


def _win_hdd(drive: str) -> bool:
    """드라이브 문자(예: 'C:') → HDD 여부. 실패하면 False(SSD 로 간주)."""
    import ctypes
    from ctypes import wintypes

    if not drive or len(drive) < 2 or drive[1] != ":":
        return False
    k = ctypes.windll.kernel32
    k.CreateFileW.restype = wintypes.HANDLE
    k.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                              wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    OPEN_EXISTING, FILE_SHARE_RW = 3, 3
    h = k.CreateFileW(f"\\\\.\\{drive}", 0, FILE_SHARE_RW, None, OPEN_EXISTING, 0, None)
    if h == wintypes.HANDLE(-1).value or not h:
        return False
    try:
        # STORAGE_PROPERTY_QUERY{ PropertyId=StorageDeviceSeekPenaltyProperty(7), QueryType=PropertyStandardQuery(0) }
        query = (ctypes.c_byte * 12)()
        ctypes.cast(query, ctypes.POINTER(ctypes.c_ulong))[0] = 7
        ctypes.cast(query, ctypes.POINTER(ctypes.c_ulong))[1] = 0
        # DEVICE_SEEK_PENALTY_DESCRIPTOR{ Version, Size (ULONG), IncursSeekPenalty (BOOLEAN) }
        out = (ctypes.c_byte * 16)()
        ret = wintypes.DWORD(0)
        IOCTL = 0x2D1400
        ok = k.DeviceIoControl(h, IOCTL, query, ctypes.sizeof(query), out, ctypes.sizeof(out),
                               ctypes.byref(ret), None)
        if not ok:
            return False
        incurs = out[8]          # Version(4)+Size(4) 뒤 BOOLEAN
        return bool(incurs)
    finally:
        k.CloseHandle(h)


def any_hdd(paths) -> bool:
    """여러 소스 중 하나라도 HDD 면 True(보수적으로 제한 적용)."""
    seen = set()
    for p in paths:
        kk = disk_key(p)
        if kk in seen:
            continue
        seen.add(kk)
        if is_hdd(p):
            return True
    return False
