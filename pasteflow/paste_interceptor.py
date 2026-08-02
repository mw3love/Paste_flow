"""Ctrl+Shift+V / 패널 토글 / OCR / 이미지→경로 단축키 감지

단축키 체계:
  Ctrl+Shift+V — 순차 붙여넣기 (suppress + 클립보드 교체 + Ctrl+V 주입)
  패널 토글    — 설정 가능 (기본 Ctrl+Space). RegisterHotKey 대신 WH_KEYBOARD_LL로
                 감지하여 Windows 탐색기 등 모든 포그라운드 앱에서 동작 보장.
  OCR 트리거   — 설정 가능 (기본 Ctrl+Shift+S).
  이미지→경로 — 설정 가능 (기본 Ctrl+Shift+P). 현재 클립보드 이미지를 임시 PNG로
                 저장 → 경로 텍스트로 교체 → 자동 Ctrl+V (Claude CLI 등 경로 첨부 앱용).

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
from pasteflow.clipboard_monitor import is_encoded_image

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
WM_SYSKEYDOWN = 0x0104  # Alt 조합 키(Alt+F1 등) — Alt 누른 채 다른 키 누를 때
WM_KEYUP = 0x0101
WM_SYSKEYUP = 0x0105    # Alt 조합 키의 keyup
VK_V = 0x56
VK_C = 0x43
VK_CONTROL = 0x11
VK_SHIFT = 0x10
VK_MENU = 0x12  # Alt
# 좌우 구분 변형 — 로우레벨 훅이 모디파이어를 어느 코드로 보고하는지가 Windows 빌드·
# 드라이버에 따라 갈릴 수 있어(특히 Alt는 SYSKEY라 더) 훅 자체 상태추적(_mod_alt 등,
# 아래 _low_level_keyboard_proc)이 제네릭/좌우 코드를 전부 매칭하도록 함께 정의한다.
VK_LCONTROL = 0xA2
VK_RCONTROL = 0xA3
VK_LSHIFT = 0xA0
VK_RSHIFT = 0xA1
VK_LMENU = 0xA4
VK_RMENU = 0xA5
VK_MASK = 0xE8  # 미할당 가상 키 — Ctrl+Shift 조합을 더럽혀 입력기 전환 팝업 방지
VK_RETURN = 0x0D
LLKHF_INJECTED = 0x10  # KBDLLHOOKSTRUCT.flags 비트 — SendInput 등으로 주입된 키


class KBDLLHOOKSTRUCT(ctypes.Structure):
    """저수준 키보드 훅 구조체 — flags(LLKHF_INJECTED 등) 검사용"""
    _fields_ = [
        ("vkCode",      ctypes.wintypes.DWORD),
        ("scanCode",    ctypes.wintypes.DWORD),
        ("flags",       ctypes.wintypes.DWORD),
        ("time",        ctypes.wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]

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


def _send_ctrl_v_plain():
    """수정키 처리 없이 Ctrl+V 키 이벤트만 전송한다."""
    _send_inputs([
        _make_key_input(VK_CONTROL),
        _make_key_input(VK_V),
        _make_key_input(VK_V, KEYEVENTF_KEYUP),
        _make_key_input(VK_CONTROL, KEYEVENTF_KEYUP),
    ])


def _image_to_dib(image_bytes: bytes) -> Optional[bytes]:
    """파일 포맷 이미지(PNG·JPEG·…) bytes → CF_DIB bytes (24bpp BMP에서 14바이트 파일 헤더 제거).

    클립보드에 "PNG" 등록 포맷만 올리면 그것을 읽는 앱(크롬 계열 등)에서만 붙고
    그림판·한글 등 CF_DIB만 읽는 앱에선 붙여넣기가 무반응이라, PNG 항목은
    CF_DIB를 병행 등재한다. DIB 24bpp는 알파가 없으므로 투명 픽셀은 흰 배경 합성.
    """
    try:
        import io
        from PIL import Image
        img = Image.open(io.BytesIO(image_bytes))
        if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
            rgba = img.convert("RGBA")
            bg = Image.new("RGB", rgba.size, (255, 255, 255))
            bg.paste(rgba, mask=rgba.getchannel("A"))
            img = bg
        elif img.mode != "RGB":
            img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="BMP")
        return buf.getvalue()[14:]  # BITMAPFILEHEADER(14B) 제거 → DIB
    except Exception:
        return None


class PasteInterceptor:
    """Ctrl+Shift+V / 패널 토글 / OCR 단축키 감지 (저수준 키보드 훅)

    keyboard 라이브러리 대신 Win32 SetWindowsHookEx를 직접 사용.
    전용 단축키만 suppress하고, 일반 Ctrl+V/C 에는 개입하지 않는다.
    """

    # self._mod_alt가 True로 남은 채 이만큼(초) 지나면 GetAsyncKeyState로 재확인한다
    # (_low_level_keyboard_proc의 Alt 스테일 보정 참고). Alt+F2 레이스는 두 keydown
    # 사이 수십ms 안에 끝나므로 이보다 훨씬 짧아 이 마진에 걸리지 않는다.
    _MOD_ALT_STALE_S = 0.2

    def __init__(
        self,
        paste_queue: PasteQueue,
        clipboard_monitor=None,
        on_paste: Optional[Callable[[ClipboardItem], None]] = None,
        get_full_item: Optional[Callable[[int], Optional[ClipboardItem]]] = None,
        on_toggle_panel: Optional[Callable[[], None]] = None,
        on_ocr_trigger: Optional[Callable[[], None]] = None,
        on_plain_paste: Optional[Callable[[], None]] = None,
        on_image_to_path: Optional[Callable[[], None]] = None,
        on_seq_image_to_path: Optional[Callable[[], None]] = None,
        on_pin_image: Optional[Callable[[], None]] = None,
        on_seq_pin: Optional[Callable[[], None]] = None,
        on_capture: Optional[Callable[[], None]] = None,
        on_ask_ai: Optional[Callable[[], None]] = None,
        on_record_gif: Optional[Callable[[], None]] = None,
        on_stt_start: Optional[Callable[[], None]] = None,
        on_stt_stop: Optional[Callable[[], None]] = None,
    ):
        self.queue = paste_queue
        self.monitor = clipboard_monitor
        self.on_paste = on_paste
        self.get_full_item = get_full_item
        self.on_toggle_panel = on_toggle_panel
        self.on_ocr_trigger = on_ocr_trigger
        self.on_plain_paste = on_plain_paste
        self.on_image_to_path = on_image_to_path
        self.on_seq_image_to_path = on_seq_image_to_path
        self.on_pin_image = on_pin_image
        self.on_seq_pin = on_seq_pin
        self.on_capture = on_capture
        self.on_ask_ai = on_ask_ai
        self.on_record_gif = on_record_gif
        self.on_stt_start = on_stt_start
        self.on_stt_stop = on_stt_stop
        self._hook = None
        self._thread: Optional[threading.Thread] = None
        self._hook_thread_id: int = 0
        self._running = False
        self._last_paste_time = 0.0
        self._direct_paste_active = False  # direct_paste 중 훅 무시 플래그
        # keydown을 suppress한 키의 vk — 그 짝 keyup도 함께 막기 위해 기억한다(_suppress 참고)
        self._suppressed_vks: set[int] = set()
        # Alt 눌림 상태 — 훅이 자기 눈으로 본 keydown/keyup으로 직접 추적한다(Ctrl/Shift는
        # 이 레이스가 보고되지 않아 기존 GetAsyncKeyState 그대로 둔다 — 범위를 실제 증상에만
        # 맞춘다). GetAsyncKeyState 대신인 이유는 _low_level_keyboard_proc 주석 참고
        # (2026-07-29 사용자 실측: Alt+F2가 항상 두 번째 누름에야 작동 — GetAsyncKeyState가
        # Alt의 SYSKEYDOWN을 전역 테이블에 아직 못 반영한 순간의 레이스. Alt는 시스템키라
        # 유독 이 레이스를 탄다).
        self._mod_alt = False
        self._mod_alt_ts = 0.0  # self._mod_alt가 True로 바뀐 시각(모노토닉) — 아래 스테일 보정용
        # 패널 토글 단축키 (파싱된 상태로 저장)
        self._panel_vk: int = 0
        self._panel_need_ctrl: bool = False
        self._panel_need_shift: bool = False
        self._panel_need_alt: bool = False
        # OCR 단축키 (패널 토글과 동일 구조)
        self._ocr_vk: int = 0
        self._ocr_need_ctrl: bool = False
        self._ocr_need_shift: bool = False
        self._ocr_need_alt: bool = False
        # 이미지→경로 단축키 (패널 토글과 동일 구조)
        self._img2path_vk: int = 0
        self._img2path_need_ctrl: bool = False
        self._img2path_need_shift: bool = False
        self._img2path_need_alt: bool = False
        # 순차 경로 붙여넣기 단축키 (이미지→경로의 큐 버전, 동일 구조)
        self._seqimg2path_vk: int = 0
        self._seqimg2path_need_ctrl: bool = False
        self._seqimg2path_need_shift: bool = False
        self._seqimg2path_need_alt: bool = False
        # 화면에 핀(이미지 띄우기) 단축키 (패널 토글과 동일 구조)
        self._pin_vk: int = 0
        self._pin_need_ctrl: bool = False
        self._pin_need_shift: bool = False
        self._pin_need_alt: bool = False
        # 순차 핀 단축키 (화면 핀의 큐 버전, 동일 구조)
        self._seqpin_vk: int = 0
        self._seqpin_need_ctrl: bool = False
        self._seqpin_need_shift: bool = False
        self._seqpin_need_alt: bool = False
        # 영역 캡처 단축키 (패널 토글과 동일 구조)
        self._capture_vk: int = 0
        self._capture_need_ctrl: bool = False
        self._capture_need_shift: bool = False
        self._capture_need_alt: bool = False
        # AI 자유질문 단축키 (패널 토글과 동일 구조)
        self._ask_ai_vk: int = 0
        self._ask_ai_need_ctrl: bool = False
        self._ask_ai_need_shift: bool = False
        self._ask_ai_need_alt: bool = False
        # GIF 녹화 단축키 (패널 토글과 동일 구조)
        self._record_vk: int = 0
        self._record_need_ctrl: bool = False
        self._record_need_shift: bool = False
        self._record_need_alt: bool = False
        # 음성 입력(STT) 단축키 — 다른 단축키와 달리 keydown(녹음 시작)·keyup(녹음 종료+전송)
        # 둘 다 동작하는 푸시투토크 패턴이라 진행 상태(_stt_active)를 별도로 추적한다.
        self._stt_vk: int = 0
        self._stt_need_ctrl: bool = False
        self._stt_need_shift: bool = False
        self._stt_need_alt: bool = False
        self._stt_active: bool = False  # 키 반복(auto-repeat) keydown 무시 + 정확한 keyup 매칭용
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

    def set_ocr_hotkey(self, hotkey_str: str):
        """OCR 단축키 설정 — WH_KEYBOARD_LL에서 감지할 키 조합을 파싱"""
        parts = hotkey_str.lower().replace(" ", "").split("+")
        self._ocr_need_ctrl  = any(p in ("ctrl", "control") for p in parts)
        self._ocr_need_shift = "shift" in parts
        self._ocr_need_alt   = "alt" in parts
        key_parts = [p for p in parts if p not in ("ctrl", "control", "shift", "alt")]
        if key_parts:
            key = key_parts[-1]
            self._ocr_vk = _SPECIAL_KEY_MAP.get(key, ord(key.upper()) if len(key) == 1 else 0)
        else:
            self._ocr_vk = 0

    def set_image_to_path_hotkey(self, hotkey_str: str):
        """이미지→경로 단축키 설정 — 현재 클립보드 이미지를 임시 PNG로 저장 후
        절대경로를 클립보드 텍스트로 교체하고 자동 Ctrl+V."""
        parts = hotkey_str.lower().replace(" ", "").split("+")
        self._img2path_need_ctrl  = any(p in ("ctrl", "control") for p in parts)
        self._img2path_need_shift = "shift" in parts
        self._img2path_need_alt   = "alt" in parts
        key_parts = [p for p in parts if p not in ("ctrl", "control", "shift", "alt")]
        if key_parts:
            key = key_parts[-1]
            self._img2path_vk = _SPECIAL_KEY_MAP.get(key, ord(key.upper()) if len(key) == 1 else 0)
        else:
            self._img2path_vk = 0

    def set_seq_image_to_path_hotkey(self, hotkey_str: str):
        """순차 경로 붙여넣기 단축키 설정 — 큐에서 다음 항목을 꺼내 이미지면 경로 텍스트로 붙여넣기."""
        parts = hotkey_str.lower().replace(" ", "").split("+")
        self._seqimg2path_need_ctrl  = any(p in ("ctrl", "control") for p in parts)
        self._seqimg2path_need_shift = "shift" in parts
        self._seqimg2path_need_alt   = "alt" in parts
        key_parts = [p for p in parts if p not in ("ctrl", "control", "shift", "alt")]
        if key_parts:
            key = key_parts[-1]
            self._seqimg2path_vk = _SPECIAL_KEY_MAP.get(key, ord(key.upper()) if len(key) == 1 else 0)
        else:
            self._seqimg2path_vk = 0

    def set_seq_pin_hotkey(self, hotkey_str: str):
        """순차 핀 단축키 설정 — 큐에서 다음 항목을 꺼내 화면에 핀(이미지면 그대로, 텍스트면 이미지화)."""
        parts = hotkey_str.lower().replace(" ", "").split("+")
        self._seqpin_need_ctrl  = any(p in ("ctrl", "control") for p in parts)
        self._seqpin_need_shift = "shift" in parts
        self._seqpin_need_alt   = "alt" in parts
        key_parts = [p for p in parts if p not in ("ctrl", "control", "shift", "alt")]
        if key_parts:
            key = key_parts[-1]
            self._seqpin_vk = _SPECIAL_KEY_MAP.get(key, ord(key.upper()) if len(key) == 1 else 0)
        else:
            self._seqpin_vk = 0

    def set_pin_hotkey(self, hotkey_str: str):
        """화면에 핀 단축키 설정 — 현재 클립보드 이미지를 화면에 떠 있는 창으로 띄운다."""
        parts = hotkey_str.lower().replace(" ", "").split("+")
        self._pin_need_ctrl  = any(p in ("ctrl", "control") for p in parts)
        self._pin_need_shift = "shift" in parts
        self._pin_need_alt   = "alt" in parts
        key_parts = [p for p in parts if p not in ("ctrl", "control", "shift", "alt")]
        if key_parts:
            key = key_parts[-1]
            self._pin_vk = _SPECIAL_KEY_MAP.get(key, ord(key.upper()) if len(key) == 1 else 0)
        else:
            self._pin_vk = 0

    def set_capture_hotkey(self, hotkey_str: str):
        """영역 캡처 단축키 설정 — 캡처 오버레이를 띄워 드래그 영역을 클립보드·파일로 저장."""
        parts = hotkey_str.lower().replace(" ", "").split("+")
        self._capture_need_ctrl  = any(p in ("ctrl", "control") for p in parts)
        self._capture_need_shift = "shift" in parts
        self._capture_need_alt   = "alt" in parts
        key_parts = [p for p in parts if p not in ("ctrl", "control", "shift", "alt")]
        if key_parts:
            key = key_parts[-1]
            self._capture_vk = _SPECIAL_KEY_MAP.get(key, ord(key.upper()) if len(key) == 1 else 0)
        else:
            self._capture_vk = 0

    def set_record_gif_hotkey(self, hotkey_str: str):
        """GIF 녹화 단축키 설정 — 영역을 드래그로 선택해 라이브로 녹화, GIF로 저장."""
        parts = hotkey_str.lower().replace(" ", "").split("+")
        self._record_need_ctrl  = any(p in ("ctrl", "control") for p in parts)
        self._record_need_shift = "shift" in parts
        self._record_need_alt   = "alt" in parts
        key_parts = [p for p in parts if p not in ("ctrl", "control", "shift", "alt")]
        if key_parts:
            key = key_parts[-1]
            self._record_vk = _SPECIAL_KEY_MAP.get(key, ord(key.upper()) if len(key) == 1 else 0)
        else:
            self._record_vk = 0

    def set_ask_ai_hotkey(self, hotkey_str: str):
        """AI 자유질문 단축키 설정 — 컨텍스트 없이 즉석에서 AI 질문 입력창을 띄운다."""
        parts = hotkey_str.lower().replace(" ", "").split("+")
        self._ask_ai_need_ctrl  = any(p in ("ctrl", "control") for p in parts)
        self._ask_ai_need_shift = "shift" in parts
        self._ask_ai_need_alt   = "alt" in parts
        key_parts = [p for p in parts if p not in ("ctrl", "control", "shift", "alt")]
        if key_parts:
            key = key_parts[-1]
            self._ask_ai_vk = _SPECIAL_KEY_MAP.get(key, ord(key.upper()) if len(key) == 1 else 0)
        else:
            self._ask_ai_vk = 0

    def set_stt_hotkey(self, hotkey_str: str):
        """음성 입력(STT) 단축키 설정 — 누르고 있는 동안 녹음(푸시투토크), 떼면 인식+전송."""
        parts = hotkey_str.lower().replace(" ", "").split("+")
        self._stt_need_ctrl  = any(p in ("ctrl", "control") for p in parts)
        self._stt_need_shift = "shift" in parts
        self._stt_need_alt   = "alt" in parts
        key_parts = [p for p in parts if p not in ("ctrl", "control", "shift", "alt")]
        if key_parts:
            key = key_parts[-1]
            self._stt_vk = _SPECIAL_KEY_MAP.get(key, ord(key.upper()) if len(key) == 1 else 0)
        else:
            self._stt_vk = 0

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

    def _suppress(self, vk_code: int) -> int:
        """이 keydown을 앱에 전달하지 않는다(return 1) + **그 짝 keyup도 막도록 기억**한다.

        keydown만 막고 keyup을 흘리면, 웹 페이지의 포커스된 버튼이 **Space keyup에서 클릭
        처리**되어 우리가 막은 단축키가 그대로 발동한다(2026-07-13 실측: 유튜브 재생 버튼에
        포커스가 있을 때 Space keyup *하나만* 보내도 재생/멈춤이 토글 — Ctrl+Space 패널 토글이
        가끔 유튜브를 정지시키던 원인). keyup은 물리 키라면 포커스와 무관하게 항상 저수준
        훅에 도달하므로 이 집합에 찌꺼기가 남지 않는다(키 반복은 keydown이 여러 번, keyup은
        한 번 — 집합이라 중복 무해).

        수정키(Ctrl/Shift/Alt)의 keyup은 절대 막지 않는다 — 앱이 알아야 하는 상태다.
        """
        self._suppressed_vks.add(vk_code)
        return 1

    def _low_level_keyboard_proc(self, nCode, wParam, lParam):
        """저수준 키보드 훅 프로시저

        Ctrl+Shift+V / 패널 토글 / OCR 단축키만 가로채고 suppress(_suppress → return 1)한다.
        일반 Ctrl+C / Ctrl+V 에는 개입하지 않는다.

        중요: ctypes 콜백 안에서 예외가 C 레벨로 전파되면 프로세스 크래시.
        반드시 모든 예외를 잡아야 한다.
        """
        try:
            # keydown을 막은 키의 keyup도 함께 막는다 (_suppress 참고)
            # ⚠ **주입된 keyup은 막지 않는다** — Ctrl+Shift+V는 V의 물리 keydown을 막은 직후
            # 스스로 Ctrl+V를 SendInput으로 주입하는데, 그 주입된 V의 keyup까지 삼키면 대상
            # 앱이 'V가 눌린 채'로 남는다. 물리 키(비주입)만 걸러내면 이 자기충돌이 없다.
            if nCode >= 0 and wParam in (WM_KEYUP, WM_SYSKEYUP):
                kbd = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                if kbd.vkCode in (VK_MENU, VK_LMENU, VK_RMENU):
                    self._mod_alt = False
                # 음성 입력 푸시투토크 종료 — 시작시켰던 그 키의 물리 keyup에서만 정지
                # (주입 keyup·다른 키의 keyup 무관, auto-repeat keydown과도 무관)
                if (self._stt_active and kbd.vkCode == self._stt_vk
                        and not (kbd.flags & LLKHF_INJECTED)):
                    self._stt_active = False
                    if self.on_stt_stop:
                        try:
                            self.on_stt_stop()
                        except Exception:
                            pass
                if (kbd.vkCode in self._suppressed_vks
                        and not (kbd.flags & LLKHF_INJECTED)):
                    self._suppressed_vks.discard(kbd.vkCode)
                    return 1

            if nCode >= 0 and wParam in (WM_KEYDOWN, WM_SYSKEYDOWN):
                vk_code = ctypes.cast(
                    lParam, ctypes.POINTER(ctypes.c_ulong)
                ).contents.value

                # Alt는 훅이 직접 추적한 상태를 쓴다(GetAsyncKeyState 미사용) — 그 조회가
                # Alt 자신의 SYSKEYDOWN 직후 전역 비동기 키 상태 테이블에 아직 반영되기 전인
                # 순간과 겹치면 "Alt 안 눌림"으로 오판해, Alt+F2 같은 조합이 **두 번째 누름에야
                # 작동**했다(2026-07-29 사용자 실측 — Alt만 SYSKEY라 이 레이스를 탄다. Ctrl·
                # Shift는 증상이 없어 그대로 GetAsyncKeyState를 쓴다).
                if vk_code in (VK_MENU, VK_LMENU, VK_RMENU):
                    self._mod_alt = True
                    self._mod_alt_ts = time.monotonic()

                ctrl_pressed = bool(_user32.GetAsyncKeyState(VK_CONTROL) & 0x8000)
                shift_pressed = bool(_user32.GetAsyncKeyState(VK_SHIFT) & 0x8000)
                alt_pressed = self._mod_alt
                if alt_pressed and (time.monotonic() - self._mod_alt_ts) > self._MOD_ALT_STALE_S:
                    # 훅 자체 추적이 오래 True였는데 실제 물리 상태가 아니면(Alt+Tab 등으로
                    # keyup을 훅이 못 받은 예외) 물리 상태로 재보정한다 — 레이스는 수십ms 안에
                    # 끝나므로 이 마진을 건드리지 않고, 자가치유만 이 경로로 들어온다.
                    if not (_user32.GetAsyncKeyState(VK_MENU) & 0x8000):
                        self._mod_alt = False
                        alt_pressed = False

                if ctrl_pressed and shift_pressed and not alt_pressed:
                    now = time.monotonic()
                    debounce_ok = (now - self._last_paste_time) > 0.1

                    if vk_code == VK_V and debounce_ok:
                        self._last_paste_time = now
                        self._on_ctrl_shift_v()
                        # suppress — Ctrl+Shift+V를 앱에 전달하지 않음(V의 물리 keyup까지)
                        return self._suppress(vk_code)

                # 일반 Ctrl+V 관찰 (suppress 안 함 — 그대로 통과)
                # PasteFlow 자체 주입(SendInput)은 LLKHF_INJECTED 플래그로 제외해
                # Ctrl+Shift+V / direct_paste 경로의 Ctrl+V를 잘못 잡지 않는다.
                if (vk_code == VK_V
                        and ctrl_pressed and not shift_pressed and not alt_pressed
                        and self.on_plain_paste):
                    kbd = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                    if not (kbd.flags & LLKHF_INJECTED):
                        try:
                            self.on_plain_paste()
                        except Exception:
                            pass
                    # fall through → CallNextHookEx로 통과

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
                    return self._suppress(vk_code)  # suppress (짝 keyup까지)

                # OCR 단축키 감지
                if (self._ocr_vk and vk_code == self._ocr_vk
                        and ctrl_pressed  == self._ocr_need_ctrl
                        and shift_pressed == self._ocr_need_shift
                        and alt_pressed   == self._ocr_need_alt):
                    # 훅 스레드에서 포그라운드 잠금 해제 — 오버레이가 SetForegroundWindow 가능하도록
                    _user32.AllowSetForegroundWindow(0xFFFFFFFF)  # ASFW_ANY
                    if self.on_ocr_trigger:
                        try:
                            self.on_ocr_trigger()
                        except Exception:
                            pass
                    return self._suppress(vk_code)  # suppress (짝 keyup까지)

                # 이미지→경로 단축키 감지
                if (self._img2path_vk and vk_code == self._img2path_vk
                        and ctrl_pressed  == self._img2path_need_ctrl
                        and shift_pressed == self._img2path_need_shift
                        and alt_pressed   == self._img2path_need_alt):
                    if self.on_image_to_path:
                        try:
                            self.on_image_to_path()
                        except Exception:
                            pass
                    return self._suppress(vk_code)  # suppress (짝 keyup까지)

                # 순차 경로 붙여넣기 단축키 감지 (기본 Ctrl+Shift+[) — 이미지→경로의 큐 버전
                if (self._seqimg2path_vk and vk_code == self._seqimg2path_vk
                        and ctrl_pressed  == self._seqimg2path_need_ctrl
                        and shift_pressed == self._seqimg2path_need_shift
                        and alt_pressed   == self._seqimg2path_need_alt):
                    if self.on_seq_image_to_path:
                        try:
                            self.on_seq_image_to_path()
                        except Exception:
                            pass
                    return self._suppress(vk_code)  # suppress (짝 keyup까지)

                # 화면에 핀(이미지 띄우기) 단축키 감지 (기본 Alt+F3)
                if (self._pin_vk and vk_code == self._pin_vk
                        and ctrl_pressed  == self._pin_need_ctrl
                        and shift_pressed == self._pin_need_shift
                        and alt_pressed   == self._pin_need_alt):
                    # 새 핀 창이 포그라운드를 잡을 수 있도록 잠금 해제 (OCR 트리거와 동일)
                    _user32.AllowSetForegroundWindow(0xFFFFFFFF)  # ASFW_ANY
                    if self.on_pin_image:
                        try:
                            self.on_pin_image()
                        except Exception:
                            pass
                    return self._suppress(vk_code)  # suppress (짝 keyup까지)

                # 순차 핀 단축키 감지 (기본 Alt+Shift+F3) — 화면 핀의 큐 버전
                if (self._seqpin_vk and vk_code == self._seqpin_vk
                        and ctrl_pressed  == self._seqpin_need_ctrl
                        and shift_pressed == self._seqpin_need_shift
                        and alt_pressed   == self._seqpin_need_alt):
                    # 새 핀 창이 포그라운드를 잡을 수 있도록 잠금 해제 (핀 트리거와 동일)
                    _user32.AllowSetForegroundWindow(0xFFFFFFFF)  # ASFW_ANY
                    if self.on_seq_pin:
                        try:
                            self.on_seq_pin()
                        except Exception:
                            pass
                    return self._suppress(vk_code)  # suppress (짝 keyup까지)

                # 영역 캡처 단축키 감지 (기본 Alt+F2)
                if (self._capture_vk and vk_code == self._capture_vk
                        and ctrl_pressed  == self._capture_need_ctrl
                        and shift_pressed == self._capture_need_shift
                        and alt_pressed   == self._capture_need_alt):
                    # 캡처 오버레이가 포그라운드를 잡을 수 있도록 잠금 해제 (OCR 트리거와 동일)
                    _user32.AllowSetForegroundWindow(0xFFFFFFFF)  # ASFW_ANY
                    if self.on_capture:
                        try:
                            self.on_capture()
                        except Exception:
                            pass
                    return self._suppress(vk_code)  # suppress (짝 keyup까지)

                # AI 자유질문 단축키 감지 (기본 Alt+`)
                if (self._ask_ai_vk and vk_code == self._ask_ai_vk
                        and ctrl_pressed  == self._ask_ai_need_ctrl
                        and shift_pressed == self._ask_ai_need_shift
                        and alt_pressed   == self._ask_ai_need_alt):
                    # 질문 입력 다이얼로그가 포그라운드를 잡도록 잠금 해제 (OCR 트리거와 동일)
                    _user32.AllowSetForegroundWindow(0xFFFFFFFF)  # ASFW_ANY
                    if self.on_ask_ai:
                        try:
                            self.on_ask_ai()
                        except Exception:
                            pass
                    return self._suppress(vk_code)  # suppress (짝 keyup까지)

                # GIF 녹화 단축키 감지 (기본 Ctrl+Shift+G)
                if (self._record_vk and vk_code == self._record_vk
                        and ctrl_pressed  == self._record_need_ctrl
                        and shift_pressed == self._record_need_shift
                        and alt_pressed   == self._record_need_alt):
                    # 선택 오버레이·정지 컨트롤러가 포그라운드를 잡도록 잠금 해제 (캡처 트리거와 동일)
                    _user32.AllowSetForegroundWindow(0xFFFFFFFF)  # ASFW_ANY
                    if self.on_record_gif:
                        try:
                            self.on_record_gif()
                        except Exception:
                            pass
                    return self._suppress(vk_code)  # suppress (짝 keyup까지)

                # 음성 입력(STT) 단축키 감지 — 푸시투토크: keydown=시작, keyup=종료(위에서 처리)
                if (self._stt_vk and vk_code == self._stt_vk
                        and ctrl_pressed  == self._stt_need_ctrl
                        and shift_pressed == self._stt_need_shift
                        and alt_pressed   == self._stt_need_alt):
                    if not self._stt_active:  # auto-repeat keydown은 무시 — 최초 눌림에만 시작
                        self._stt_active = True
                        if self.on_stt_start:
                            try:
                                self.on_stt_start()
                            except Exception:
                                pass
                    return self._suppress(vk_code)  # suppress (짝 keyup까지)
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

        Ctrl+Shift 마스킹: V를 suppress하면 Windows는 "벌거벗은 Ctrl+Shift"로
        인식해 키보드 입력기 전환(한컴↔MS) 팝업을 띄운다. 미할당 키(VK_MASK)를
        Ctrl+Shift가 눌린 상태에서 한 번 눌러 조합을 더럽히면 이후 Ctrl+Shift up이
        전환 제스처로 해석되지 않는다. 해제 직전·복원 직후 두 번 넣어 우리가
        주입하는 해제와 사용자의 실제 해제 양쪽을 모두 막는다.
        """
        # 현재 눌려 있는 수정키 확인 (Ctrl 포함 — Ctrl+V 전송 후 Ctrl-up 때문에 복원 필요)
        held_ctrl  = bool(_user32.GetAsyncKeyState(VK_CONTROL) & 0x8000)
        held_shift = bool(_user32.GetAsyncKeyState(VK_SHIFT) & 0x8000)
        held_alt   = bool(_user32.GetAsyncKeyState(VK_MENU) & 0x8000)

        # Ctrl+Shift가 함께 눌려 있을 때만 입력기 전환 마스킹이 필요
        need_mask = held_ctrl and held_shift

        inputs = []
        # 0) 해제 직전 마스킹 — 곧 주입할 Ctrl+Shift 해제가 전환으로 해석되는 것 방지
        if need_mask:
            inputs += [_make_key_input(VK_MASK),
                       _make_key_input(VK_MASK, KEYEVENTF_KEYUP)]
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
        # 4) 복원 직후 마스킹 — 사용자가 나중에 Ctrl+Shift를 뗄 때도 전환 방지
        if need_mask:
            inputs += [_make_key_input(VK_MASK),
                       _make_key_input(VK_MASK, KEYEVENTF_KEYUP)]
        _send_inputs(inputs)

    def direct_paste(self, item: ClipboardItem, target_hwnd=None):
        """항목을 클립보드에 설정 후 SendInput으로 Ctrl+V 전송

        순차 큐 포인터에 영향 없음. 더블클릭·드래그 경로에서 사용.
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
        _send_ctrl_v_plain()

        # 3) Alt가 눌려있었으면 복원 (연속 Alt+N 지원)
        #    Ctrl+V 완료 후이므로 간섭 없음
        if VK_MENU in held_keys:
            time.sleep(0.02)
            _send_inputs([_make_key_input(VK_MENU)])

    def send_plain_key(self, vk: int):
        """수정키 없이 키 하나를 눌렀다 뗀다(SendInput) — 브라우저 주입의 Enter용.

        `_send_clean_key`는 Ctrl+{key} 조합을 보내므로 단독 키(Enter)에는 못 쓴다.
        포그라운드 창에 그대로 들어가므로 호출자가 대상 창을 먼저 확인해야 한다
        (main._inject_to_google이 크롬이 앞에 있는지 검사한 뒤 부른다).
        """
        _send_inputs([_make_key_input(vk), _make_key_input(vk, KEYEVENTF_KEYUP)])

    def send_ctrl_v_to(self, target_hwnd):
        """대상 윈도우에 포커스 이동 후 Ctrl+V 전송"""
        if target_hwnd:
            _user32.SetForegroundWindow(target_hwnd)
            time.sleep(0.05)
        self._direct_paste_active = True
        try:
            _send_ctrl_v_plain()
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
            # 시간창 + 해시 백스톱을 함께 건다 — 늦게 도착한 WM_CLIPBOARDUPDATE가
            # 0.5초 창을 넘겨도 해시로 걸러져 이중 저장을 막는다(Alt+F2 캡처 등 직접 저장 경로).
            self.monitor.mark_self_write(item)

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

            # 이미지 — 원본 포맷 복원.
            #  · PNG   : CF_PNG + 변환한 CF_DIB 병행 등재("PNG"를 못 읽는 그림판·한글 대응)
            #  · 그 외 파일 포맷(JPEG·GIF·WebP…): raw DIB가 아니므로 CF_DIB로 그대로 올리면
            #    받는 앱이 앞 40바이트를 BITMAPINFOHEADER로 잘못 읽어 붙여넣기가 깨진다
            #    (탐색기에서 .jpg 복사 시 CF_HDROP 경로가 파일 바이트를 그대로 싣는다).
            #    → DIB로 변환해 등재.
            #  · raw CF_DIB: 그대로.
            if item.image_data:
                data0 = item.image_data
                if data0[:4] == b'\x89PNG':
                    payloads = [(CF_PNG, data0)]
                    dib = _image_to_dib(data0)
                    if dib:
                        payloads.append((_CF_DIB, dib))
                elif is_encoded_image(data0):
                    dib = _image_to_dib(data0)
                    payloads = [(_CF_DIB, dib)] if dib else []
                else:
                    payloads = [(_CF_DIB, data0)]
                for cf, data in payloads:
                    try:
                        h = _kernel32.GlobalAlloc(_GMEM_MOVEABLE, len(data))
                        if h:
                            p = _kernel32.GlobalLock(h)
                            if p:
                                ctypes.memmove(p, data, len(data))
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

