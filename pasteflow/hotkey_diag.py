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


class _RECT(ctypes.Structure):
    _fields_ = [("l", ctypes.c_long), ("t", ctypes.c_long), ("r", ctypes.c_long), ("b", ctypes.c_long)]


_user32.GetWindowRect.argtypes = [ctypes.wintypes.HWND, ctypes.POINTER(_RECT)]
_user32.GetWindow.argtypes = [ctypes.wintypes.HWND, ctypes.c_uint]
_user32.GetWindow.restype = ctypes.wintypes.HWND
_user32.IsWindowVisible.argtypes = [ctypes.wintypes.HWND]
_user32.IsIconic.argtypes = [ctypes.wintypes.HWND]
_user32.GetWindowTextW.argtypes = [ctypes.wintypes.HWND, ctypes.wintypes.LPWSTR, ctypes.c_int]
_user32.GetWindowLongW.argtypes = [ctypes.wintypes.HWND, ctypes.c_int]
_GW_HWNDPREV = 3


def _name(hwnd) -> str:
    c = ctypes.create_unicode_buffer(64)
    _user32.GetClassNameW(hwnd, c, 64)
    t = ctypes.create_unicode_buffer(40)
    _user32.GetWindowTextW(hwnd, t, 40)
    return f"{c.value}'{t.value}'"


def describe(hwnd: int) -> str:
    """창이 실제로 맨 앞인지 — 위치, 맨 앞 창이 이 창인지, 이 창을 덮는 위쪽 창들(Z 순서)"""
    try:
        r = _RECT()
        _user32.GetWindowRect(hwnd, ctypes.byref(r))
        fg = _user32.GetForegroundWindow()
        covers = []
        h = _user32.GetWindow(hwnd, _GW_HWNDPREV)
        while h and len(covers) < 6:
            if _user32.IsWindowVisible(h) and not _user32.IsIconic(h):
                o = _RECT()
                _user32.GetWindowRect(h, ctypes.byref(o))
                if o.l < r.r and r.l < o.r and o.t < r.b and r.t < o.b:  # 겹치는 창만
                    top = "T" if _user32.GetWindowLongW(h, -20) & 0x8 else ""
                    covers.append(f"{_name(h)}{top}({o.l},{o.t},{o.r},{o.b})")
            h = _user32.GetWindow(h, _GW_HWNDPREV)
        fg_desc = "self" if fg == hwnd else _name(fg) if fg else "-"
        return (f"rect=({r.l},{r.t},{r.r},{r.b}) iconic={bool(_user32.IsIconic(hwnd))}"
                f" fg={fg_desc} covered_by=[{'; '.join(covers)}]")
    except Exception as e:
        return f"describe failed: {e}"


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
