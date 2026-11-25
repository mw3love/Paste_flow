"""라이브 화면 판서용 창 도우미 — 클릭 통과 켜고 끄기 · 맨 위 차지 · 포그라운드 창 읽기.

전용 `WinDLL("user32")`을 쓴다(공유 `ctypes.windll.user32`에 argtypes를 걸면 모듈끼리 덮어쓴다 —
CLAUDE.md 「꼭 지킬 함정」). 클릭 통과는 창을 다시 만들지 않고 `WS_EX_TRANSPARENT`만 바꾼다
(0편 시험판 실측: 켜면 WindowFromPoint가 아래 창, 끄면 다시 이 창 — `spikes/live_draw/README.md`).
"""
import ctypes
from ctypes import wintypes

_user32 = ctypes.WinDLL("user32")
_user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
_user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
_user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
_user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
_user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                 ctypes.c_int, ctypes.c_int, ctypes.c_uint]
_user32.SetWindowPos.restype = wintypes.BOOL
_user32.GetForegroundWindow.restype = wintypes.HWND
_user32.IsWindow.argtypes = [wintypes.HWND]
_user32.IsWindow.restype = wintypes.BOOL

_GWL_EXSTYLE = -20
_WS_EX_TRANSPARENT = 0x20
_WS_EX_LAYERED = 0x80000
_HWND_TOPMOST = wintypes.HWND(-1)
_SWP_NOSIZE, _SWP_NOMOVE, _SWP_NOACTIVATE, _SWP_FRAMECHANGED = 0x0001, 0x0002, 0x0010, 0x0020


def set_click_through(hwnd: int, on: bool) -> None:
    """on=True면 이 창을 클릭이 그대로 지나간다(그림은 계속 보인다)."""
    ex = _user32.GetWindowLongPtrW(hwnd, _GWL_EXSTYLE)
    ex = (ex | _WS_EX_TRANSPARENT | _WS_EX_LAYERED) if on else (ex & ~_WS_EX_TRANSPARENT)
    _user32.SetWindowLongPtrW(hwnd, _GWL_EXSTYLE, ex)
    _user32.SetWindowPos(hwnd, _HWND_TOPMOST, 0, 0, 0, 0,
                         _SWP_NOSIZE | _SWP_NOMOVE | _SWP_NOACTIVATE | _SWP_FRAMECHANGED)


def claim_topmost(hwnd: int) -> None:
    """TOPMOST 창들 사이에서도 맨 앞으로(raise_()만으로는 패널 같은 다른 TOPMOST를 못 이긴다)."""
    _user32.SetWindowPos(hwnd, _HWND_TOPMOST, 0, 0, 0, 0, _SWP_NOSIZE | _SWP_NOMOVE | _SWP_NOACTIVATE)


def foreground_window() -> int:
    return int(_user32.GetForegroundWindow() or 0)


def is_window(hwnd: int) -> bool:
    return bool(hwnd) and bool(_user32.IsWindow(hwnd))


# ---- 휠 다시 넣기(판서 그리기 상태에서 아래 창 스크롤) ------------------------
class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class _KEYBDINPUT(ctypes.Structure):   # 공용체 크기를 맞추려고만 둔다
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


_INPUT_MOUSE = 0
_MOUSEEVENTF_WHEEL = 0x0800
_user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int]
_user32.SendInput.restype = wintypes.UINT


def send_wheel(delta: int) -> bool:
    """세로 휠 한 번을 운영체제에 다시 넣는다(커서 아래 창이 평소 휠처럼 받음). 성공하면 True."""
    inp = _INPUT(type=_INPUT_MOUSE)
    inp.u.mi = _MOUSEINPUT(0, 0, ctypes.c_ulong(int(delta)).value, _MOUSEEVENTF_WHEEL, 0, 0)
    return _user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT)) == 1
