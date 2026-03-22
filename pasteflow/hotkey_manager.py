"""글로벌 단축키 등록/해제 — Win32 RegisterHotKey + 히든 윈도우

RegisterHotKey를 히든 윈도우에 등록하여 WM_HOTKEY를
윈도우 프로시저에서 직접 처리한다. Qt 이벤트 루프가
스레드 메시지를 소비하는 문제를 방지.
"""
import ctypes
import ctypes.wintypes
from typing import Callable

import win32gui
import win32api

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_NOREPEAT = 0x4000

WM_HOTKEY = 0x0312

_MODIFIER_MAP = {
    "alt": MOD_ALT,
    "ctrl": MOD_CONTROL,
    "control": MOD_CONTROL,
    "shift": MOD_SHIFT,
}

_user32 = ctypes.windll.user32


class HotkeyManager:
    """Win32 RegisterHotKey + 히든 윈도우 기반 글로벌 단축키"""

    def __init__(self):
        self._hotkeys: dict[int, Callable] = {}  # hotkey_id → callback
        self._next_id = 1
        self._hwnd = None
        self._wnd_proc_map = None  # GC 방지
        self._create_window()

    def _create_window(self):
        """WM_HOTKEY 수신용 히든 윈도우 생성"""
        self._wnd_proc_map = {WM_HOTKEY: self._on_wm_hotkey}

        wc = win32gui.WNDCLASS()
        wc.lpfnWndProc = self._wnd_proc_map
        wc.lpszClassName = "PasteFlowHotkeyManager"
        wc.hInstance = win32api.GetModuleHandle(None)

        try:
            class_atom = win32gui.RegisterClass(wc)
        except Exception:
            return

        self._hwnd = win32gui.CreateWindow(
            class_atom, "PasteFlow Hotkeys",
            0, 0, 0, 0, 0, 0, 0, wc.hInstance, None
        )

    def _on_wm_hotkey(self, hwnd, msg, wparam, lparam):
        """WM_HOTKEY 윈도우 프로시저 핸들러"""
        callback = self._hotkeys.get(wparam)
        if callback:
            try:
                callback()
            except Exception:
                pass
        return 0

    def register(self, hotkey: str, callback: Callable):
        """단축키 등록 (예: "alt+v", "alt+1")"""
        if not self._hwnd:
            return

        modifiers, vk = self._parse_hotkey(hotkey)
        hotkey_id = self._next_id
        self._next_id += 1

        result = _user32.RegisterHotKey(
            self._hwnd, hotkey_id, modifiers | MOD_NOREPEAT, vk
        )
        if result:
            self._hotkeys[hotkey_id] = callback

    def unregister(self, hotkey: str):
        """단축키 해제 (이름 기반)"""
        pass

    def unregister_all(self):
        """모든 단축키 해제"""
        if self._hwnd:
            for hotkey_id in list(self._hotkeys.keys()):
                _user32.UnregisterHotKey(self._hwnd, hotkey_id)
        self._hotkeys.clear()

    def destroy(self):
        """윈도우 파괴"""
        self.unregister_all()
        if self._hwnd:
            win32gui.DestroyWindow(self._hwnd)
            self._hwnd = None

    def _parse_hotkey(self, hotkey_str: str) -> tuple[int, int]:
        """단축키 문자열 → (modifiers, virtual_key_code)"""
        parts = hotkey_str.lower().replace(" ", "").split("+")
        modifiers = 0
        vk = 0

        for part in parts:
            if part in _MODIFIER_MAP:
                modifiers |= _MODIFIER_MAP[part]
            elif len(part) == 1:
                vk = ord(part.upper())

        return modifiers, vk
