"""우선순위 점검(윈도우): v0.7.26 — ffmpeg 가 '보통'(NORMAL)으로 도는지(예전 '보통 이하' 아님)."""
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from mosco import ff, paths  # noqa: E402

NORMAL = 0x00000020
BELOW = 0x00004000


def prio(pid=None):
    import ctypes
    from ctypes import wintypes
    k = ctypes.windll.kernel32
    k.GetCurrentProcess.restype = wintypes.HANDLE
    k.OpenProcess.restype = wintypes.HANDLE
    k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k.GetPriorityClass.argtypes = [wintypes.HANDLE]
    k.GetPriorityClass.restype = wintypes.DWORD
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    if pid is None:
        return k.GetPriorityClass(k.GetCurrentProcess())
    h = k.OpenProcess(0x1000, False, pid)     # PROCESS_QUERY_LIMITED_INFORMATION
    try:
        return k.GetPriorityClass(h)
    finally:
        k.CloseHandle(h)


def main():
    if os.name != "nt":
        print("PRIORITY SKIP (not windows)")
        return
    # v0.7.26: ffmpeg 우선순위를 더 이상 낮추지 않음 → 부모(MOSCO) 와 같은 우선순위를 물려받아야 한다.
    # (CI 러너 자체가 '보통 이하'일 수 있어 NORMAL 고정값으로는 못 봄 → 부모와 같은지로 확인)
    own = prio()
    p = subprocess.Popen([paths.ffmpeg(), "-v", "error", "-f", "lavfi", "-i", "nullsrc=s=320x240:d=5",
                          "-f", "null", "-"], creationflags=ff.NOWIN)
    time.sleep(0.5)
    c = prio(p.pid)
    p.kill()
    assert c == own, f"ffmpeg 우선순위 {hex(c)} != 부모 {hex(own)} (물려받아야 함)"
    print(f"PRIORITY OK (ffmpeg = 부모와 같음 {hex(c)})")


if __name__ == "__main__":
    main()
