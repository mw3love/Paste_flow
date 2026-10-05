"""새 메모 단축키(Alt+`)가 "가끔 안 먹는" 원인을 잡기 위한 임시 진단 로그 (2026-10-05).

%LOCALAPPDATA%\\PasteFlow\\logs\\memo_hotkey.log 에 남긴다.
- 훅: ` 키(메모 단축키 VK)가 눌릴 때마다 수식키 판정·일치 여부·그 순간 맨 앞 창 클래스
- main: 단축키 신호를 받은 시각, 창을 띄운 뒤 메모창이 실제 맨 앞인지

안 될 때 로그에 그 시각 줄이 없으면 키가 훅에 닿지 않은 것(원격 데스크톱·관리자 앱 등),
훅 줄은 있는데 matched=False면 수식키 판정 문제, main 줄까지 있으면 창 표시 문제다.
원인을 찾으면 이 모듈과 호출부를 지운다.
"""
import ctypes
import ctypes.wintypes
import os
import time

_user32 = ctypes.WinDLL("user32")  # 공유 windll에 argtypes를 걸지 않는다(CLAUDE.md 함정)
_user32.GetForegroundWindow.restype = ctypes.wintypes.HWND
_user32.GetClassNameW.argtypes = [ctypes.wintypes.HWND, ctypes.wintypes.LPWSTR, ctypes.c_int]

_MAX_BYTES = 256 * 1024  # 넘으면 비우고 새로 쓴다


def _path() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~\\AppData\\Local")
    return os.path.join(base, "PasteFlow", "logs", "memo_hotkey.log")


def foreground_class() -> str:
    try:
        hwnd = _user32.GetForegroundWindow()
        if not hwnd:
            return "-"
        buf = ctypes.create_unicode_buffer(128)
        _user32.GetClassNameW(hwnd, buf, 128)
        return buf.value or "?"
    except Exception:
        return "?"


def log(msg: str):
    try:
        path = _path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        mode = "w" if os.path.exists(path) and os.path.getsize(path) > _MAX_BYTES else "a"
        t = time.time()
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t)) + f".{int(t * 1000) % 1000:03d}"
        with open(path, mode, encoding="utf-8") as f:
            f.write(f"{stamp} {msg}\n")
    except Exception:
        pass
