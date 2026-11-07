"""우선순위 점검(윈도우): MOSCO 자신과 ffmpeg 가 '보통 이하'(BELOW_NORMAL)로 도는지."""
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from mosco import ff, paths  # noqa: E402

BELOW = 0x00004000


def prio(handle_or_pid, own=False):
    import ctypes
    k = ctypes.windll.kernel32
    if own:
        return k.GetPriorityClass(k.GetCurrentProcess())
    h = k.OpenProcess(0x1000, False, handle_or_pid)     # PROCESS_QUERY_LIMITED_INFORMATION
    try:
        return k.GetPriorityClass(h)
    finally:
        k.CloseHandle(h)


def main():
    if os.name != "nt":
        print("PRIORITY SKIP (not windows)")
        return
    assert ff.low_priority(), "SetPriorityClass 실패"
    assert prio(0, own=True) == BELOW, hex(prio(0, own=True))
    p = subprocess.Popen([paths.ffmpeg(), "-v", "error", "-f", "lavfi", "-i", "nullsrc=s=320x240:d=5",
                          "-f", "null", "-"], creationflags=ff.NOWIN)
    time.sleep(0.5)
    c = prio(p.pid)
    p.kill()
    assert c == BELOW, f"ffmpeg 우선순위 {hex(c)}"
    print("PRIORITY OK")


if __name__ == "__main__":
    main()
