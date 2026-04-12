"""Ctrl+Shift+V / 패널 토글 감지 → 순차 붙여넣기 / 패널 열닫기

단축키 체계:
  Ctrl+Shift+V — 순차 붙여넣기 (suppress + 클립보드 교체 + Ctrl+V 주입)
  패널 토글    — 설정 가능 (기본 Ctrl+Space). RegisterHotKey 대신 WH_KEYBOARD_LL로
                 감지하여 Windows 탐색기 등 모든 포그라운드 앱에서 동작 보장.

일반 Ctrl+C는 모든 복사를 큐에 추가한다. PasteFlow가 Ctrl+C에 개입하지 않는다.
"""
import ctypes
import ctypes.wintypes
import threading
import time
from typing import Optional, Callable

import win32clipboard

from pasteflow.models import ClipboardItem
from pasteflow.paste_queue import PasteQueue
from pasteflow.hotkey_manager import _SPECIAL_KEY_MAP

CF_HTML = win32clipboard.RegisterClipboardFormat("HTML Format")
CF_RTF = win32clipboard.RegisterClipboardFormat("Rich Text Format")
CF_PNG = win32clipboard.RegisterClipboardFormat("PNG")

# --- ctypes 클립보드 API (훅 스레드용 — pywin32 C 확장 우회) ---
_CF_UNICODETEXT = 13
_CF_DIB = 8
_GMEM_MOVEABLE = 0x0002

# 저수준 키보드 훅 상수
WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
VK_V = 0x56
VK_C = 0x43
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

WM_QUIT = 0x0012

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

_user32.PostThreadMessageW.argtypes = [
    ctypes.wintypes.DWORD, ctypes.wintypes.UINT,
    ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM,
]
_user32.PostThreadMessageW.restype = ctypes.wintypes.BOOL

_kernel32.GetCurrentThreadId.argtypes = []
_kernel32.GetCurrentThreadId.restype = ctypes.wintypes.DWORD

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
    """Ctrl+Shift+{V,C,X,Z} 감지 → 순차 붙여넣기 / 큐 관리 (저수준 키보드 훅)

    keyboard 라이브러리 대신 Win32 SetWindowsHookEx를 직접 사용.
    전용 단축키만 suppress하고, 일반 Ctrl+V/C 에는 개입하지 않는다.
    """

    def __init__(
        self,
        paste_queue: PasteQueue,
        clipboard_monitor=None,
        on_paste: Optional[Callable[[ClipboardItem], None]] = None,
        get_full_item: Optional[Callable[[int], Optional[ClipboardItem]]] = None,
        on_toggle_panel: Optional[Callable[[], None]] = None,
    ):
        self.queue = paste_queue
        self.monitor = clipboard_monitor
        self.on_paste = on_paste
        self.get_full_item = get_full_item
        self.on_toggle_panel = on_toggle_panel
        self._hook = None
        self._thread: Optional[threading.Thread] = None
        self._hook_thread_id: int = 0
        self._running = False
        self._last_paste_time = 0.0
        self._direct_paste_active = False  # direct_paste 중 훅 무시 플래그
        # 패널 토글 단축키 (파싱된 상태로 저장)
        self._panel_vk: int = 0
        self._panel_need_ctrl: bool = False
        self._panel_need_shift: bool = False
        self._panel_need_alt: bool = False
        # 콜백 참조 유지 (GC 방지)
        self._hook_proc = HOOKPROC(self._low_level_keyboard_proc)

    def set_panel_hotkey(self, hotkey_str: str):
        """패널 토글 단축키 설정 — WH_KEYBOARD_LL에서 감지할 키 조합을 파싱"""
        parts = hotkey_str.lower().replace(" ", "").split("+")
        self._panel_need_ctrl  = any(p in ("ctrl", "control") for p in parts)
        self._panel_need_shift = "shift" in parts
        self._panel_need_alt   = "alt" in parts
        key_parts = [p for p in parts if p not in ("ctrl", "control", "shift", "alt")]
        if key_parts:
            key = key_parts[-1]
            self._panel_vk = _SPECIAL_KEY_MAP.get(key, ord(key.upper()) if len(key) == 1 else 0)
        else:
            self._panel_vk = 0

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
        # GetMessageW 블로킹 중인 훅 스레드를 깨운다
        if self._hook_thread_id:
            _user32.PostThreadMessageW(self._hook_thread_id, WM_QUIT, 0, 0)
            self._hook_thread_id = 0

    def _hook_thread(self):
        """훅 메시지 루프 스레드"""
        self._hook_thread_id = _kernel32.GetCurrentThreadId()
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

        Ctrl+Shift+{V,C,X,Z} 만 가로채고 suppress(return 1)한다.
        일반 Ctrl+C / Ctrl+V 에는 개입하지 않는다.

        중요: ctypes 콜백 안에서 예외가 C 레벨로 전파되면 프로세스 크래시.
        반드시 모든 예외를 잡아야 한다.
        """
        try:
            if nCode >= 0 and wParam == WM_KEYDOWN:
                vk_code = ctypes.cast(
                    lParam, ctypes.POINTER(ctypes.c_ulong)
                ).contents.value

                ctrl_pressed = bool(_user32.GetAsyncKeyState(VK_CONTROL) & 0x8000)
                shift_pressed = bool(_user32.GetAsyncKeyState(VK_SHIFT) & 0x8000)
                alt_pressed = bool(_user32.GetAsyncKeyState(VK_MENU) & 0x8000)

                if ctrl_pressed and shift_pressed and not alt_pressed:
                    now = time.monotonic()
                    debounce_ok = (now - self._last_paste_time) > 0.1

                    if vk_code == VK_V and debounce_ok:
                        self._last_paste_time = now
                        self._on_ctrl_shift_v()
                        return 1  # suppress — Ctrl+Shift+V를 앱에 전달하지 않음

                # 패널 토글 단축키 감지 (RegisterHotKey 대체 — 탐색기 등 모든 앱에서 동작)
                if (self._panel_vk and vk_code == self._panel_vk
                        and ctrl_pressed  == self._panel_need_ctrl
                        and shift_pressed == self._panel_need_shift
                        and alt_pressed   == self._panel_need_alt):
                    if self.on_toggle_panel:
                        try:
                            self.on_toggle_panel()
                        except Exception:
                            pass
                    return 1  # suppress
        except Exception as e:
            print(f"[Hook] 훅 프로시저 예외 (무시): {e}")

        return _user32.CallNextHookEx(self._hook, nCode, wParam, lParam)

    # --- 단축키 핸들러 ---

    def _on_ctrl_shift_v(self):
        """Ctrl+Shift+V: 순차 붙여넣기 — 큐에서 다음 항목을 클립보드에 설정 후 Ctrl+V 주입"""
        if self._direct_paste_active:
            return
        next_item = self.queue.get_next()
        if next_item is None:
            print("[Interceptor] 큐 소진 — 순차 붙여넣기 없음")
            return

        preview = (next_item.preview_text or "")[:30]
        print(f"[Interceptor] 순차 붙여넣기: '{preview}'")

        # 큐에 summary 항목이 들어있을 수 있으므로 실제 붙여넣기 시점에 전체 데이터 로드
        if self.get_full_item and not next_item.image_data and not next_item.extra_formats:
            full = self.get_full_item(next_item.id)
            if full:
                next_item = full

        self._set_clipboard(next_item)
        self._send_clean_key(VK_V)  # Shift 해제 후 Ctrl+V 주입

        if self.on_paste:
            try:
                self.on_paste(next_item)
            except Exception:
                pass

    def _send_clean_key(self, vk_key: int):
        """수정키를 해제하고 Ctrl+{vk_key}를 SendInput으로 주입한 뒤 수정키 복원

        Ctrl+Shift+V → Ctrl+V 주입, Ctrl+Shift+C → Ctrl+C 주입에 사용.

        수정키를 복원하지 않으면 SendInput의 가상 key-up 이벤트가 남아
        사용자가 Ctrl+Shift를 계속 누른 채 V를 반복할 때 두 번째 입력부터
        GetAsyncKeyState가 수정키 미입력으로 반환해 훅이 가로채지 못한다.
        """
        # 현재 눌려 있는 수정키 확인 (Ctrl 포함 — Ctrl+V 전송 후 Ctrl-up 때문에 복원 필요)
        held_ctrl  = bool(_user32.GetAsyncKeyState(VK_CONTROL) & 0x8000)
        held_shift = bool(_user32.GetAsyncKeyState(VK_SHIFT) & 0x8000)
        held_alt   = bool(_user32.GetAsyncKeyState(VK_MENU) & 0x8000)

        inputs = []
        # 1) 모든 수정키 해제 (순수 Ctrl+key만 앱에 전달하기 위해)
        if held_alt:   inputs.append(_make_key_input(VK_MENU,    KEYEVENTF_KEYUP))
        if held_shift: inputs.append(_make_key_input(VK_SHIFT,   KEYEVENTF_KEYUP))
        if held_ctrl:  inputs.append(_make_key_input(VK_CONTROL, KEYEVENTF_KEYUP))
        # 2) Ctrl+key 전송
        inputs += [
            _make_key_input(VK_CONTROL),
            _make_key_input(vk_key),
            _make_key_input(vk_key, KEYEVENTF_KEYUP),
            _make_key_input(VK_CONTROL, KEYEVENTF_KEYUP),
        ]
        # 3) 수정키 복원 — 사용자가 물리적으로 누른 채 반복 입력할 때 가상 상태 동기화
        if held_ctrl:  inputs.append(_make_key_input(VK_CONTROL))
        if held_shift: inputs.append(_make_key_input(VK_SHIFT))
        if held_alt:   inputs.append(_make_key_input(VK_MENU))
        _send_inputs(inputs)

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
        """대상 윈도우에 포커스 이동 후 Ctrl+V 전송"""
        if target_hwnd:
            _user32.SetForegroundWindow(target_hwnd)
            time.sleep(0.05)
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

    def _set_clipboard(self, item: ClipboardItem):
        """클립보드에 항목 설정 — ctypes 기반 (pywin32 ACCESS VIOLATION 방지)"""
        self._set_clipboard_ctypes(item)

    def _set_clipboard_ctypes(self, item: ClipboardItem):
        """ctypes 기반 클립보드 설정 (재시도 포함 — pywin32 우회)"""
        for attempt in range(3):
            if _user32.OpenClipboard(None):
                break
            if attempt < 2:
                time.sleep(0.01)
        else:
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

            # 이미지 (PNG 또는 DIB — 원본 포맷 복원)
            if item.image_data:
                is_png = item.image_data[:4] == b'\x89PNG'
                cf = CF_PNG if is_png else _CF_DIB
                try:
                    h = _kernel32.GlobalAlloc(_GMEM_MOVEABLE, len(item.image_data))
                    if h:
                        p = _kernel32.GlobalLock(h)
                        if p:
                            ctypes.memmove(p, item.image_data, len(item.image_data))
                            _kernel32.GlobalUnlock(h)
                            _user32.SetClipboardData(cf, h)
                        else:
                            _kernel32.GlobalFree(h)
                except Exception:
                    pass

            # 기타 포맷 복원 (노션 등 앱 전용)
            if item.extra_formats:
                for fmt, data in item.extra_formats.items():
                    try:
                        h = _kernel32.GlobalAlloc(_GMEM_MOVEABLE, len(data))
                        if h:
                            p = _kernel32.GlobalLock(h)
                            if p:
                                ctypes.memmove(p, data, len(data))
                                _kernel32.GlobalUnlock(h)
                                _user32.SetClipboardData(fmt, h)
                            else:
                                _kernel32.GlobalFree(h)
                    except Exception:
                        pass
        finally:
            _user32.CloseClipboard()

