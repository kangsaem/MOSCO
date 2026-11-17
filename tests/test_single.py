"""v0.7.99 하나만 실행(mosco/single.py) 시험 — 실제 프로세스 여러 개로.
1) 기본: 첫 실행이 떠 있으면 두 번째는 False(종료) + 첫 실행에 'show' 도착
2) 동시 실행: 둘이 거의 동시에 시작(듣기 시작이 2초 늦음) → 잠금은 하나만, 다른 하나는 재시도 끝에 False
3) 강제 종료(kill) 뒤: 남은 잠금이 자동 해제돼 다음 실행이 첫 실행(owner True)
4) 멈춘 프로세스(잠금만 쥐고 듣지 않음): 두 번째는 재시도 시간 뒤 그냥 실행(True, owner False)"""
import os
import subprocess
import sys
import time
from pathlib import Path

APP = str(Path(__file__).resolve().parents[1] / "app")
CHILD = r'''
import os, sys, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, sys.argv[1])
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
app = QApplication([])
from mosco import single
name, retry, serve_delay, life, do_serve = sys.argv[2], float(sys.argv[3]), float(sys.argv[4]), float(sys.argv[5]), sys.argv[6] == "1"
ok = single.acquire(name, retry_s=retry, step_s=0.2)
print("ACQ", ok, single.owner(), flush=True)
if not ok:
    sys.exit(0)
time.sleep(serve_delay)
if do_serve:
    single.serve(name, lambda: print("SHOWN", flush=True))
QTimer.singleShot(int(life * 1000), app.quit)
app.exec()
single.release()
'''
NAME = f"MOSCOTEST{os.getpid()}"


def spawn(retry=8.0, serve_delay=0.0, life=6.0, serve=True):
    return subprocess.Popen([sys.executable, "-c", CHILD, APP, NAME, str(retry), str(serve_delay), str(life),
                             "1" if serve else "0"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def out(p, timeout=40):
    try:
        o, _ = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        p.kill()
        o, _ = p.communicate()
    return o


def acq(o):
    line = next((x for x in o.splitlines() if x.startswith("ACQ")), "")
    return tuple(line.split()[1:3])


def main():
    # 1) 기본
    a = spawn(life=6)
    time.sleep(2.5)
    b = spawn()
    ob, oa = out(b), out(a)
    assert acq(oa) == ("True", "True"), oa
    assert acq(ob) == ("False", "False"), ob
    assert "SHOWN" in oa, oa
    print("1 basic OK")

    # 2) 동시 실행(듣기 시작이 2초 늦음 = 무거운 import 흉내) — 3번
    for k in range(3):
        a, b = spawn(serve_delay=2.0, life=7), spawn(serve_delay=2.0, life=7)
        oa, ob = out(a), out(b)
        res = sorted([acq(oa), acq(ob)])
        assert res == [("False", "False"), ("True", "True")], (k, oa, ob)
    print("2 simultaneous x3 OK")

    # 3) 강제 종료 뒤 다시 실행
    a = spawn(life=30)
    time.sleep(2.5)
    a.kill()                                   # 윈도우 = TerminateProcess, 리눅스 = SIGKILL
    a.wait()
    c = spawn(life=2)
    oc = out(c)
    assert acq(oc) == ("True", "True"), oc
    print("3 after kill OK")

    # 4) 멈춘 프로세스(잠금만, 듣지 않음) → 재시도 뒤 그냥 실행(owner False)
    a = spawn(life=10, serve=False)
    time.sleep(2.5)
    t0 = time.monotonic()
    b = spawn(retry=1.5, life=1)
    ob = out(b)
    dt = time.monotonic() - t0
    out(a)
    assert acq(ob) == ("True", "False"), ob
    print(f"4 unresponsive -> run anyway OK ({dt:.1f}s)")
    print("SINGLE OK")


if __name__ == "__main__":
    main()
