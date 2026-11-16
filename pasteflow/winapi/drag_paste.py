"""드래그 붙여넣기 도우미 — 끌어다 놓은 창의 가장 안쪽 자식 찾기·크로미움 판별·Ctrl+V 보내기
(2026-11-16 pasteflow/main.py에서 글자 그대로 옮김 — docs/plan/done/20261116-폴더-구조-1편.md)
"""
import ctypes

from PyQt6.QtCore import QTimer


_CHROMIUM_CLASS_PREFIXES = (
    "Chrome_RenderWidgetHostHWND",
    "Chrome_WidgetWin_",
    "CefBrowserWindow",
    "CEF",
    "Intermediate D3D Window",
)


def _find_deepest_child(hwnd, screen_pt, visited=None, depth=0):
    """커서 위치의 가장 깊은 자식 HWND를 재귀 탐색.
    visited set + MAX_DEPTH 20으로 무한루프 방지.
    """
    if visited is None:
        visited = set()
    if depth > 20 or hwnd in visited:
        return hwnd
    visited.add(hwnd)
    try:
        import win32gui
        client_pt = win32gui.ScreenToClient(hwnd, screen_pt)
        child = win32gui.ChildWindowFromPoint(hwnd, client_pt)
        if child and child != hwnd:
            return _find_deepest_child(child, screen_pt, visited, depth + 1)
    except Exception:
        pass
    return hwnd


def _is_chromium_window(class_name: str) -> bool:
    """창 클래스명이 Electron/Chromium 계열인지 판별."""
    return any(class_name.startswith(p) for p in _CHROMIUM_CLASS_PREFIXES)


def _activate_and_send_ctrl_v(hwnd, sender=None):
    """AttachThreadInput으로 포그라운드 잠금을 우회한 뒤 SendInput(Ctrl+V).
    Qt 메인 스레드에서만 호출해야 한다.

    sender: 실제 키 주입 함수(인자 없음). 기본은 수정키 처리 없는 _send_ctrl_v_plain.
    Alt+드래그처럼 호출 시점에 수정키가 눌려 있는 경로는
    interceptor._release_modifiers_and_send_ctrl_v를 넘겨 Alt 해제 후 주입해야
    OS가 Ctrl+Alt+V로 오인하지 않는다.
    """
    import win32gui
    import win32process
    from pasteflow.paste_interceptor import _send_ctrl_v_plain

    sender = sender or _send_ctrl_v_plain

    current_tid = ctypes.windll.kernel32.GetCurrentThreadId()
    # 타겟 창의 스레드에 입력 큐를 붙여야 SetForegroundWindow가 포커스까지 확실히
    # 넘긴다. 드래그 시점 포그라운드는 PasteFlow 패널 자신(같은 스레드)이라, 거기에
    # 붙는 것은 무효였다.
    target_tid = win32process.GetWindowThreadProcessId(hwnd)[0]

    attached = False
    try:
        if target_tid and target_tid != current_tid:
            win32process.AttachThreadInput(current_tid, target_tid, True)
            attached = True
        win32gui.SetForegroundWindow(hwnd)
        win32gui.BringWindowToTop(hwnd)
        try:
            win32gui.SetFocus(hwnd)
        except Exception:
            pass
    except Exception:
        pass
    finally:
        if attached:
            try:
                win32process.AttachThreadInput(current_tid, target_tid, False)
            except Exception:
                pass

    def _send():
        # 현재 포그라운드가 타겟인지 확인
        try:
            current_fg = win32gui.GetForegroundWindow()
            if current_fg != hwnd and win32gui.GetParent(current_fg) != hwnd:
                return  # 다른 창이 활성화됐으면 전송 안 함
        except Exception:
            pass
        sender()

    # Alt가 물리적으로 떨어질 때까지 폴링한 뒤 Ctrl+V 주입.
    # 사용자가 드롭 시점에 Alt를 누르고 있으면 가상 KEYUP으로는 해제되지 않으므로
    # (GetAsyncKeyState는 물리 키 기준), 실제로 손을 뗄 때까지 기다려야
    # 타겟이 Ctrl+V를 Ctrl+Alt+V로 오인하지 않는다. QTimer 재예약이라 UI 비차단.
    _VK_MENU = 0x12
    _POLL_MS = 25
    _MAX_WAIT_MS = 1500
    _wait_state = {"ms": 0}

    def _wait_alt_release_then_send():
        alt_down = ctypes.windll.user32.GetAsyncKeyState(_VK_MENU) & 0x8000
        if alt_down and _wait_state["ms"] < _MAX_WAIT_MS:
            _wait_state["ms"] += _POLL_MS
            QTimer.singleShot(_POLL_MS, _wait_alt_release_then_send)
            return
        _send()

    # 창 활성화 후 80ms 대기 → Alt 해제 대기 → SendInput(Ctrl+V)
    QTimer.singleShot(80, _wait_alt_release_then_send)
