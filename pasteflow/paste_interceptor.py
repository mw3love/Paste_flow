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
            self._running = False
            return

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
        """
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
                    if self._direct_paste_active:
                        pass
                    else:
                        # 키 반복 디바운스 (100ms)
                        now = time.monotonic()
                        if now - self._last_paste_time > 0.1:
                            self._last_paste_time = now
                            self._on_ctrl_v()

        # 키 이벤트를 절대 차단하지 않음 — 항상 CallNextHookEx
        return _user32.CallNextHookEx(
            self._hook, nCode, wParam, lParam
        )

    def _on_ctrl_v(self):
        """Ctrl+V 키다운 시 클립보드 교체"""
        next_item = self.queue.get_next()
        if next_item is None:
            return  # 큐 소진 → 개입 안 함 → OS 기본 동작

        # self_triggered는 _set_clipboard 내부에서 설정 (성공 시에만)
        self._set_clipboard(next_item)

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
        """눌려 있는 수정키를 해제하고 Ctrl+V를 보낸 뒤 원래 수정키를 복원"""

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [
                ("wVk", ctypes.wintypes.WORD),
                ("wScan", ctypes.wintypes.WORD),
                ("dwFlags", ctypes.wintypes.DWORD),
                ("time", ctypes.wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
            ]

        class INPUT(ctypes.Structure):
            class _INPUT_UNION(ctypes.Union):
                _fields_ = [("ki", KEYBDINPUT)]
            _fields_ = [
                ("type", ctypes.wintypes.DWORD),
                ("union", _INPUT_UNION),
            ]

        def make_key_input(vk, flags=0):
            inp = INPUT()
            inp.type = INPUT_KEYBOARD
            inp.union.ki.wVk = vk
            inp.union.ki.dwFlags = flags
            return inp

        def send(input_list):
            arr = (INPUT * len(input_list))(*input_list)
            _user32.SendInput(len(input_list), ctypes.byref(arr), ctypes.sizeof(INPUT))

        # 현재 눌려 있는 수정키 확인
        held_keys = []
        for vk in (VK_MENU, VK_CONTROL, VK_SHIFT):
            if _user32.GetAsyncKeyState(vk) & 0x8000:
                held_keys.append(vk)

        # 1) 눌려 있는 수정키 모두 해제
        if held_keys:
            send([make_key_input(vk, KEYEVENTF_KEYUP) for vk in held_keys])
            time.sleep(0.02)

        # 2) Ctrl+V 전송
        send([
            make_key_input(VK_CONTROL),
            make_key_input(VK_V),
            make_key_input(VK_V, KEYEVENTF_KEYUP),
            make_key_input(VK_CONTROL, KEYEVENTF_KEYUP),
        ])

    def _set_clipboard(self, item: ClipboardItem):
        """클립보드에 항목 설정 — 모든 형식 보존"""
        max_retries = 3
        for attempt in range(max_retries):
            try:
                win32clipboard.OpenClipboard()
                break
            except Exception:
                if attempt < max_retries - 1:
                    time.sleep(0.01)
                else:
                    return  # 실패 시 self_triggered 설정 안 함

        # 클립보드 열기 성공 → 자체 트리거 무시 설정 (500ms)
        if self.monitor:
            self.monitor.set_self_triggered(0.5)

        try:
            win32clipboard.EmptyClipboard()

            # 텍스트
            if item.text_content:
                win32clipboard.SetClipboardData(
                    win32con.CF_UNICODETEXT, item.text_content
                )

            # HTML
            if item.html_content:
                try:
                    html_bytes = item.html_content.encode("utf-8")
                    win32clipboard.SetClipboardData(CF_HTML, html_bytes)
                except Exception:
                    pass

            # RTF
            if item.rtf_content:
                try:
                    rtf_bytes = item.rtf_content.encode("utf-8")
                    win32clipboard.SetClipboardData(CF_RTF, rtf_bytes)
                except Exception:
                    pass

            # 이미지
            if item.image_data:
                try:
                    win32clipboard.SetClipboardData(
                        win32con.CF_DIB, item.image_data
                    )
                except Exception:
                    pass

        finally:
            win32clipboard.CloseClipboard()
