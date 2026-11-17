"""하나만 실행(중복 실행 막기) — 앱 전용 import 없는 독립 파일. MOSCO·DTME 공용(DTME 는 그대로 복사).

v0.7.99 (MOSCO·DTME 상의 2026-11-17 확정):
  1) 잠금(QLockFile, 임시 폴더 '<앱>-single-<사용자>.lock')으로 '하나만 통과' — 동시에 두 번 실행해도 하나만 얻음.
     setStaleLockTime(0): 시간으로는 만료 안 됨. 잠근 프로세스가 죽었으면 Qt 가 자동으로 낡은 잠금으로 보고 가져옴.
  2) 로컬 소켓(QLocalServer/QLocalSocket, 같은 이름)으로 '기존 창 앞으로' — 두 번째 실행이 "show" 보내고 "ok" 받으면 종료.
  3) 잠금을 못 얻었는데 retry_s 동안 답이 없으면(창 없이 멈춘 옛 프로세스) 막지 않고 그냥 실행 — 이때 owner() = False
     (DTME: 시작 정리(캐시 삭제)를 건너뛸 것 — 살아 있는 옛 프로세스의 파일을 지우지 않게).
  버전이 달라도 하나만(이름에 버전 없음). 예외: 환경 변수 '<앱>_MULTI=1'(시험·빌드 점검·버전 나란히 비교).

사용(QApplication 만든 직후, 무거운 import 전에):
    from single import acquire, serve, release, owner
    if not acquire("MOSCO"):
        sys.exit(0)                      # 기존 창이 앞으로 옴
    serve("MOSCO", on_show)              # on_show() = 지금 보이는 창을 맨 앞으로(앱이 정함)
    ...                                  # owner() 가 False 면 시작 정리 건너뜀
    release()                            # 종료 직전(안 불러도 프로세스가 죽으면 자동 해제)
"""
from __future__ import annotations

import os
import time

from PySide6.QtCore import QCoreApplication, QLockFile, QStandardPaths
from PySide6.QtNetwork import QLocalServer, QLocalSocket

_lock: QLockFile | None = None
_owner = False
_server: QLocalServer | None = None


def name(app_name: str) -> str:
    user = os.environ.get("USERNAME") or os.environ.get("USER") or "user"
    return f"{app_name}-single-{user}"


def lock_path(app_name: str) -> str:
    tmp = QStandardPaths.writableLocation(QStandardPaths.TempLocation) or os.environ.get("TEMP") or "."
    return os.path.join(tmp, name(app_name) + ".lock")


def owner() -> bool:
    """이 프로세스가 잠금을 가진 '진짜 첫 실행'인지. False = 잠금 없이 실행 중(시작 정리 건너뛸 것)."""
    return _owner


def _ask_show(app_name: str, wait_ms: int) -> bool:
    """떠 있는 쪽에 'show' → 'ok' 받으면 True."""
    s = QLocalSocket()
    s.connectToServer(name(app_name))
    if not s.waitForConnected(min(500, wait_ms)):
        return False
    s.write(b"show\n")
    s.flush()
    got = b""
    t_end = time.monotonic() + wait_ms / 1000
    while time.monotonic() < t_end and b"ok" not in got:
        if s.waitForReadyRead(100):
            got += bytes(s.readAll())
    s.disconnectFromServer()
    return b"ok" in got


def acquire(app_name: str, retry_s: float = 8.0, step_s: float = 0.3, skip: bool = False) -> bool:
    """True = 이 실행을 계속. False = 이미 실행 중이고 그 창이 앞으로 옴 → 이 실행은 끝낼 것.
    skip=True 또는 '<앱>_MULTI=1' 이면 검사 안 함(항상 True, 잠금은 얻을 수 있으면 얻음)."""
    global _lock, _owner
    _lock = QLockFile(lock_path(app_name))
    _lock.setStaleLockTime(0)
    got = _lock.tryLock(0)
    _owner = bool(got)
    if got or skip or os.environ.get(f"{app_name.upper()}_MULTI") == "1":
        return True
    if _lock.error() != QLockFile.LockFailedError:       # 잠금 파일을 못 만듦(권한 등) → 막지 않음
        return True
    t_end = time.monotonic() + retry_s
    while True:                                           # 첫 실행이 아직 시작 중일 수 있음 → 듣기 시작할 때까지 재시도
        if _ask_show(app_name, 1500):
            return False
        if time.monotonic() >= t_end:
            return True                                   # 끝내 답 없음 = 멈춘 옛 프로세스 → 그냥 실행(owner False)
        if _lock.tryLock(0):                              # 그 사이 첫 실행이 끝났으면 내가 첫 실행
            _owner = True
            return True
        time.sleep(step_s)


def serve(app_name: str, on_show, parent=None) -> QLocalServer | None:
    """첫 실행이 듣는 쪽. 두 번째 실행이 오면 'ok' 답하고 on_show() 호출."""
    global _server
    if _owner:
        QLocalServer.removeServer(name(app_name))         # 리눅스·맥: 죽은 프로세스의 소켓 파일 정리(잠금 가진 쪽만)
    srv = QLocalServer(parent or QCoreApplication.instance())
    if not srv.listen(name(app_name)):
        return None

    def on_conn():
        c = srv.nextPendingConnection()
        if c is None:
            return

        def answer():
            c.readAll()
            c.write(b"ok\n")
            c.flush()
            try:
                on_show()
            except Exception:  # noqa: BLE001
                pass
        c.readyRead.connect(answer)
    srv.newConnection.connect(on_conn)
    _server = srv
    return srv


def release():
    """종료 직전 잠금 해제(안 불러도 프로세스가 죽으면 다음 실행이 낡은 잠금으로 보고 가져감)."""
    global _owner
    try:
        if _server is not None:
            _server.close()
        if _lock is not None and _owner:
            _lock.unlock()
    except Exception:  # noqa: BLE001
        pass
    _owner = False
