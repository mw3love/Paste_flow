"""Ctrl+V 감지 → 클립보드 교체 실행

핵심 규칙: Ctrl+V 키 이벤트를 절대 차단(block/suppress)하지 않는다.
키다운 시점에 클립보드 내용만 교체하고, 키 이벤트는 그대로 통과시킨다.
"""
import ctypes
import ctypes.wintypes
import threading
import time
from typing import Optional, Callable

import win32clipboard
import win32con

from pasteflow.models import ClipboardItem
from pasteflow.paste_queue import PasteQueue

CF_HTML = win32clipboard.RegisterClipboardFormat("HTML Format")
CF_RTF = win32clipboard.RegisterClipboardFormat("Rich Text Format")

# --- ctypes 클립보드 API (훅 스레드용 — pywin32 C 확장 우회) ---
_CF_UNICODETEXT = 13
_CF_DIB = 8
_GMEM_MOVEABLE = 0x0002

# 저수준 키보드 훅 상수
WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
VK_V = 0x56
VK_CONTROL = 0x11
VK_SHIFT = 0x10
VK_MENU = 0x12  # Alt

# SendInput 관련 상수
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002


# --- SendInput 구조체 (64비트 호환 — union 크기 정확히 맞춤) ---

class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.wintypes.DWORD),
        ("dwFlags", ctypes.wintypes.DWORD),
        ("time", ctypes.wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.wintypes.WORD),
        ("wScan", ctypes.wintypes.WORD),
        ("dwFlags", ctypes.wintypes.DWORD),
        ("time", ctypes.wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]

class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", ctypes.wintypes.DWORD),
        ("wParamL", ctypes.wintypes.WORD),
        ("wParamH", ctypes.wintypes.WORD),
    ]

class _INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("mi", MOUSEINPUT),
        ("ki", KEYBDINPUT),
        ("hi", HARDWAREINPUT),
    ]

class INPUT(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.wintypes.DWORD),
        ("union", _INPUT_UNION),
    ]


# --- ctypes 타입 정의 (64비트 호환) ---
LRESULT = ctypes.c_ssize_t  # 포인터 크기 (64비트에서 8바이트)
HOOKPROC = ctypes.CFUNCTYPE(
    LRESULT,
    ctypes.c_int,
    ctypes.wintypes.WPARAM,
    ctypes.wintypes.LPARAM,
)

_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32

_user32.SetWindowsHookExW.argtypes = [
    ctypes.c_int, HOOKPROC, ctypes.wintypes.HINSTANCE, ctypes.wintypes.DWORD
]
_user32.SetWindowsHookExW.restype = ctypes.c_void_p

_user32.CallNextHookEx.argtypes = [
    ctypes.c_void_p, ctypes.c_int, ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM
]
_user32.CallNextHookEx.restype = LRESULT

_user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
_user32.UnhookWindowsHookEx.restype = ctypes.wintypes.BOOL

_user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
_user32.GetAsyncKeyState.restype = ctypes.c_short

_kernel32.GetModuleHandleW.argtypes = [ctypes.wintypes.LPCWSTR]
_kernel32.GetModuleHandleW.restype = ctypes.wintypes.HMODULE

_user32.SetForegroundWindow.argtypes = [ctypes.wintypes.HWND]
_user32.SetForegroundWindow.restype = ctypes.wintypes.BOOL

# 클립보드 API (ctypes — 훅 스레드에서 pywin32 우회용)
_user32.OpenClipboard.argtypes = [ctypes.wintypes.HWND]
_user32.OpenClipboard.restype = ctypes.wintypes.BOOL
_user32.CloseClipboard.argtypes = []
_user32.CloseClipboard.restype = ctypes.wintypes.BOOL
_user32.EmptyClipboard.argtypes = []
_user32.EmptyClipboard.restype = ctypes.wintypes.BOOL
_user32.SetClipboardData.argtypes = [ctypes.wintypes.UINT, ctypes.wintypes.HANDLE]
_user32.SetClipboardData.restype = ctypes.wintypes.HANDLE

_kernel32.GlobalAlloc.argtypes = [ctypes.wintypes.UINT, ctypes.c_size_t]
_kernel32.GlobalAlloc.restype = ctypes.wintypes.HGLOBAL
_kernel32.GlobalLock.argtypes = [ctypes.wintypes.HGLOBAL]
_kernel32.GlobalLock.restype = ctypes.c_void_p
_kernel32.GlobalUnlock.argtypes = [ctypes.wintypes.HGLOBAL]
_kernel32.GlobalUnlock.restype = ctypes.wintypes.BOOL
_kernel32.GlobalFree.argtypes = [ctypes.wintypes.HGLOBAL]
_kernel32.GlobalFree.restype = ctypes.wintypes.HGLOBAL


# --- SendInput 헬퍼 ---

def _make_key_input(vk, flags=0):
    inp = INPUT()
    inp.type = INPUT_KEYBOARD
    inp.union.ki.wVk = vk
    inp.union.ki.dwFlags = flags
    return inp


def _send_inputs(input_list):
    arr = (INPUT * len(input_list))(*input_list)
    result = _user32.SendInput(len(input_list), ctypes.byref(arr), ctypes.sizeof(INPUT))
    return result


class PasteInterceptor:
    """Ctrl+V 키다운 → 클립보드 교체 (저수준 키보드 훅)

    keyboard 라이브러리 대신 Win32 SetWindowsHookEx를 직접 사용.
    훅 프로시저 안에서 동기적으로 클립보드를 교체한 뒤 키를 통과시킨다.
    """

    def __init__(
        self,
        paste_queue: PasteQueue,
        clipboard_monitor=None,
        on_paste: Optional[Callable[[ClipboardItem], None]] = None,
    ):
        self.queue = paste_queue
        self.monitor = clipboard_monitor
        self.on_paste = on_paste
        self._hook = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._last_paste_time = 0.0
        self._direct_paste_active = False  # direct_paste 중 훅 무시 플래그
        # 콜백 참조 유지 (GC 방지)
        self._hook_proc = HOOKPROC(self._low_level_keyboard_proc)

    def start(self):
        """저수준 키보드 훅 시작 (별도 스레드)"""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._hook_thread, daemon=True)
        self._thread.start()

    def stop(self):
        """훅 해제"""
        self._running = False
        if self._hook:
            _user32.UnhookWindowsHookEx(self._hook)
            self._hook = None

    def _hook_thread(self):
        """훅 메시지 루프 스레드"""
        h_mod = _kernel32.GetModuleHandleW(None)
        self._hook = _user32.SetWindowsHookExW(
            WH_KEYBOARD_LL,
            self._hook_proc,
            h_mod,
            0,
        )
        if not self._hook:
            print(f"[Hook] 훅 설치 실패! GetLastError={ctypes.GetLastError()}")
            self._running = False
            return

        print("[Hook] 키보드 훅 설치 성공")

        msg = ctypes.wintypes.MSG()
        while self._running:
            ret = _user32.GetMessageW(
                ctypes.byref(msg), None, 0, 0
            )
            if ret <= 0:
                break
            _user32.TranslateMessage(ctypes.byref(msg))
            _user32.DispatchMessageW(ctypes.byref(msg))

    def _low_level_keyboard_proc(self, nCode, wParam, lParam):
        """저수준 키보드 훅 프로시저

        Ctrl+V 키다운 시 클립보드 교체 후 키 이벤트를 그대로 통과시킨다.
        절대 키를 차단하지 않는다.

        중요: ctypes 콜백 안에서 예외가 C 레벨로 전파되면 프로세스 크래시.
        반드시 모든 예외를 잡아야 한다.
        """
        try:
            if nCode >= 0 and wParam == WM_KEYDOWN:
                # KBDLLHOOKSTRUCT에서 vkCode 추출 (첫 4바이트)
                vk_code = ctypes.cast(
                    lParam, ctypes.POINTER(ctypes.c_ulong)
                ).contents.value

                if vk_code == VK_V:
                    # Ctrl 키 눌림 확인
                    ctrl_pressed = _user32.GetAsyncKeyState(VK_CONTROL) & 0x8000
                    shift_pressed = _user32.GetAsyncKeyState(VK_SHIFT) & 0x8000
                    alt_pressed = _user32.GetAsyncKeyState(VK_MENU) & 0x8000

                    # Ctrl+V만 처리 (Ctrl+Shift+V 등은 무시)
                    if ctrl_pressed and not shift_pressed and not alt_pressed:
                        # direct_paste가 보낸 Ctrl+V는 무시
                        if not self._direct_paste_active:
                            # 키 반복 디바운스 (100ms)
                            now = time.monotonic()
                            if now - self._last_paste_time > 0.1:
                                self._last_paste_time = now
                                self._on_ctrl_v()
        except Exception as e:
            print(f"[Hook] 훅 프로시저 예외 (무시): {e}")

        # 키 이벤트를 절대 차단하지 않음 — 항상 CallNextHookEx
        return _user32.CallNextHookEx(
            self._hook, nCode, wParam, lParam
        )

    def _on_ctrl_v(self):
        """Ctrl+V 키다운 시 클립보드 교체 (훅 콜백 내부 — 빠르게 완료해야 함)"""
        next_item = self.queue.get_next()
        if next_item is None:
            print("[Interceptor] 큐 소진 — 기본 동작")
            return  # 큐 소진 → 개입 안 함 → OS 기본 동작

        preview = (next_item.preview_text or "")[:30]
        print(f"[Interceptor] 순차 붙여넣기: '{preview}'")

        # fast=True: 훅 콜백이므로 재시도/sleep 없이 단일 시도
        self._set_clipboard(next_item, fast=True)

        # 붙여넣기 콜백 (UI 업데이트 등)
        if self.on_paste:
            try:
                self.on_paste(next_item)
            except Exception:
                pass

    def direct_paste(self, item: ClipboardItem, target_hwnd=None):
        """항목을 클립보드에 설정 후 SendInput으로 Ctrl+V 전송 (F5)

        순차 큐 포인터에 영향 없음. Alt+1~9 또는 패널 더블클릭에서 사용.
        target_hwnd가 주어지면 해당 윈도우에 포커스를 먼저 설정한다.
        """
        self._set_clipboard(item)

        # 대상 윈도우로 포커스 이동 (패널에서 호출 시)
        if target_hwnd:
            try:
                _user32.SetForegroundWindow(target_hwnd)
                time.sleep(0.1)
            except Exception:
                pass

        # 눌려 있는 수정키(Alt, Shift, Ctrl) 해제 후 Ctrl+V 전송
        time.sleep(0.05)
        self._direct_paste_active = True
        try:
            self._release_modifiers_and_send_ctrl_v()
        finally:
            # SendInput이 훅에 전달될 시간 확보
            time.sleep(0.05)
            self._direct_paste_active = False

    def _release_modifiers_and_send_ctrl_v(self):
        """눌려 있는 수정키를 해제하고 Ctrl+V를 전송 후 수정키 복원"""

        # 현재 눌려 있는 수정키 확인
        held_keys = []
        for vk in (VK_MENU, VK_CONTROL, VK_SHIFT):
            if _user32.GetAsyncKeyState(vk) & 0x8000:
                held_keys.append(vk)

        # 1) 눌려 있는 수정키 모두 해제
        if held_keys:
            _send_inputs([_make_key_input(vk, KEYEVENTF_KEYUP) for vk in held_keys])
            time.sleep(0.02)

        # 2) Ctrl+V 전송
        _send_inputs([
            _make_key_input(VK_CONTROL),
            _make_key_input(VK_V),
            _make_key_input(VK_V, KEYEVENTF_KEYUP),
            _make_key_input(VK_CONTROL, KEYEVENTF_KEYUP),
        ])

        # 3) Alt가 눌려있었으면 복원 (연속 Alt+N 지원)
        #    Ctrl+V 완료 후이므로 간섭 없음
        if VK_MENU in held_keys:
            time.sleep(0.02)
            _send_inputs([_make_key_input(VK_MENU)])

    def send_ctrl_v_to(self, target_hwnd):
        """대상 윈도우에 포커스 후 Ctrl+V 전송 (메인 스레드에서 포커스 이동 후 호출)"""
        time.sleep(0.1)
        self._direct_paste_active = True
        try:
            _send_inputs([
                _make_key_input(VK_CONTROL),
                _make_key_input(VK_V),
                _make_key_input(VK_V, KEYEVENTF_KEYUP),
                _make_key_input(VK_CONTROL, KEYEVENTF_KEYUP),
            ])
        finally:
            time.sleep(0.05)
            self._direct_paste_active = False

    def _set_clipboard(self, item: ClipboardItem, *, fast: bool = False):
        """클립보드에 항목 설정 — 모든 형식 보존

        fast=True: 훅 콜백에서 호출 — raw ctypes로 pywin32 C 확장 우회.
        fast=False: direct_paste 등 일반 경로 — pywin32 사용, 최대 3회 재시도.
        """
        if fast:
            self._set_clipboard_ctypes(item)
        else:
            self._set_clipboard_pywin32(item)

    def _set_clipboard_ctypes(self, item: ClipboardItem):
        """ctypes 기반 클립보드 설정 (훅 스레드 전용 — pywin32 우회)"""
        if not _user32.OpenClipboard(None):
            print("[Interceptor] ctypes 클립보드 열기 실패")
            return

        if self.monitor:
            self.monitor.set_self_triggered(0.5)

        try:
            _user32.EmptyClipboard()

            # 텍스트
            if item.text_content:
                data = item.text_content.encode("utf-16-le") + b"\x00\x00"
                h = _kernel32.GlobalAlloc(_GMEM_MOVEABLE, len(data))
                if h:
                    p = _kernel32.GlobalLock(h)
                    if p:
                        ctypes.memmove(p, data, len(data))
                        _kernel32.GlobalUnlock(h)
                        _user32.SetClipboardData(_CF_UNICODETEXT, h)
                    else:
                        _kernel32.GlobalFree(h)

            # HTML
            if item.html_content:
                try:
                    html_bytes = item.html_content.encode("utf-8") + b"\x00"
                    h = _kernel32.GlobalAlloc(_GMEM_MOVEABLE, len(html_bytes))
                    if h:
                        p = _kernel32.GlobalLock(h)
                        if p:
                            ctypes.memmove(p, html_bytes, len(html_bytes))
                            _kernel32.GlobalUnlock(h)
                            _user32.SetClipboardData(CF_HTML, h)
                        else:
                            _kernel32.GlobalFree(h)
                except Exception:
                    pass

            # RTF
            if item.rtf_content:
                try:
                    rtf_bytes = item.rtf_content.encode("utf-8") + b"\x00"
                    h = _kernel32.GlobalAlloc(_GMEM_MOVEABLE, len(rtf_bytes))
                    if h:
                        p = _kernel32.GlobalLock(h)
                        if p:
                            ctypes.memmove(p, rtf_bytes, len(rtf_bytes))
                            _kernel32.GlobalUnlock(h)
                            _user32.SetClipboardData(CF_RTF, h)
                        else:
                            _kernel32.GlobalFree(h)
                except Exception:
                    pass

            # 이미지 (DIB)
            if item.image_data:
                try:
                    h = _kernel32.GlobalAlloc(_GMEM_MOVEABLE, len(item.image_data))
                    if h:
                        p = _kernel32.GlobalLock(h)
                        if p:
                            ctypes.memmove(p, item.image_data, len(item.image_data))
                            _kernel32.GlobalUnlock(h)
                            _user32.SetClipboardData(_CF_DIB, h)
                        else:
                            _kernel32.GlobalFree(h)
                except Exception:
                    pass
        finally:
            _user32.CloseClipboard()

    def _set_clipboard_pywin32(self, item: ClipboardItem):
        """pywin32 기반 클립보드 설정 (메인/일반 스레드용)"""
        for attempt in range(3):
            try:
                win32clipboard.OpenClipboard()
                break
            except Exception:
                if attempt < 2:
                    time.sleep(0.01)
                else:
                    print("[Interceptor] 클립보드 열기 실패")
                    return

        if self.monitor:
            self.monitor.set_self_triggered(0.5)

        try:
            win32clipboard.EmptyClipboard()

            if item.text_content:
                win32clipboard.SetClipboardData(
                    win32con.CF_UNICODETEXT, item.text_content
                )

            if item.html_content:
                try:
                    win32clipboard.SetClipboardData(
                        CF_HTML, item.html_content.encode("utf-8")
                    )
                except Exception:
                    pass

            if item.rtf_content:
                try:
                    win32clipboard.SetClipboardData(
                        CF_RTF, item.rtf_content.encode("utf-8")
                    )
                except Exception:
                    pass

            if item.image_data:
                try:
                    win32clipboard.SetClipboardData(
                        win32con.CF_DIB, item.image_data
                    )
                except Exception:
                    pass
        finally:
            win32clipboard.CloseClipboard()
