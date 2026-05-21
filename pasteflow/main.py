"""PasteFlow 진입점 — 모듈 오케스트레이션

클립보드 모니터 → DB → 큐 → UI 간 이벤트 흐름 관리.
"""
import sys
import os
import ctypes
import ctypes.wintypes
import threading
import time
import json
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QTimer, QObject, pyqtSignal, QBuffer

from pasteflow.database import Database
from pasteflow.models import ClipboardItem
from pasteflow.paste_queue import PasteQueue
from pasteflow.clipboard_monitor import ClipboardMonitor
from pasteflow.paste_interceptor import PasteInterceptor
from pasteflow.hotkey_manager import HotkeyManager
from pasteflow.ui.panel import (
    ClipboardPanel, EditItemDialog, PANEL_MIN_WIDTH, PANEL_MIN_HEIGHT,
)
from pasteflow.ui.image_preview import ImagePreviewPopup
from pasteflow.ui.text_preview import TextPreviewPopup
from pasteflow.ui.tray import TrayIcon
from pasteflow.ui.settings_dialog import SettingsDialog
from pasteflow.ui.theme import COLORS

# ── 드래그 붙여넣기 헬퍼 ──────────────────────────────────────────────────────

_MSGBOX_DARK_STYLE = (
    f"QMessageBox {{ background-color: {COLORS['base']}; color: {COLORS['text']}; }}"
    f" QMessageBox QLabel {{ color: {COLORS['text']}; }}"
    f" QMessageBox QPushButton {{"
    f"   background-color: {COLORS['surface0']}; color: {COLORS['text']};"
    f"   border: 1px solid {COLORS['surface1']}; border-radius: 4px;"
    f"   padding: 4px 12px; min-width: 60px; }}"
    f" QMessageBox QPushButton:hover {{ background-color: {COLORS['surface1']}; }}"
    f" QTextEdit {{ background-color: {COLORS['surface0']}; color: {COLORS['text']};"
    f"   border: 1px solid {COLORS['surface1']}; }}"
)

_CHROMIUM_CLASS_PREFIXES = (
    "Chrome_RenderWidgetHostHWND",
    "Chrome_WidgetWin_",
    "CefBrowserWindow",
    "CEF",
    "Intermediate D3D Window",
)

_EXPLORER_CLASSES = {"CabinetWClass"}
_DESKTOP_CLASSES = {"Progman", "WorkerW"}


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


def _activate_and_send_ctrl_v(hwnd):
    """AttachThreadInput으로 포그라운드 잠금을 우회한 뒤 SendInput(Ctrl+V).
    Qt 메인 스레드에서만 호출해야 한다.
    """
    import win32gui
    import win32process
    from pasteflow.paste_interceptor import _send_ctrl_v_plain

    fg_hwnd = win32gui.GetForegroundWindow()
    current_tid = ctypes.windll.kernel32.GetCurrentThreadId()
    fg_tid = win32process.GetWindowThreadProcessId(fg_hwnd)[0]

    attached = False
    try:
        if fg_tid and fg_tid != current_tid:
            win32process.AttachThreadInput(current_tid, fg_tid, True)
            attached = True
        win32gui.SetForegroundWindow(hwnd)
        win32gui.BringWindowToTop(hwnd)
    except Exception:
        pass
    finally:
        if attached:
            try:
                win32process.AttachThreadInput(current_tid, fg_tid, False)
            except Exception:
                pass

    # 창 활성화 후 80ms 대기 → SendInput(Ctrl+V)
    def _send():
        # 현재 포그라운드가 타겟인지 확인
        try:
            current_fg = win32gui.GetForegroundWindow()
            if current_fg != hwnd and win32gui.GetParent(current_fg) != hwnd:
                return  # 다른 창이 활성화됐으면 전송 안 함
        except Exception:
            pass
        _send_ctrl_v_plain()

    QTimer.singleShot(80, _send)


def _get_explorer_subfolder_at_cursor(lv_hwnd: int, screen_pt: tuple, current_folder: str):
    """SysListView32에서 screen_pt 위치의 서브폴더 경로 반환 (크로스 프로세스 LVM_HITTEST).
    커서 위치에 폴더 아이콘이 없으면 None.
    """
    LVM_HITTEST = 0x1000 + 18
    LVM_GETITEMTEXTW = 0x1000 + 115
    LVHT_ONITEM = 0x000E
    LVIF_TEXT = 0x0001
    PROCESS_VM = 0x0008 | 0x0010 | 0x0020  # VM_OPERATION | VM_READ | VM_WRITE

    class _HT(ctypes.Structure):
        _fields_ = [
            ("x", ctypes.c_long), ("y", ctypes.c_long),
            ("flags", ctypes.c_uint),
            ("iItem", ctypes.c_int), ("iSubItem", ctypes.c_int),
        ]

    class _LVI(ctypes.Structure):
        _fields_ = [
            ("mask", ctypes.c_uint), ("iItem", ctypes.c_int),
            ("iSubItem", ctypes.c_int), ("state", ctypes.c_uint),
            ("stateMask", ctypes.c_uint),
            ("pszText", ctypes.c_void_p),   # 8 bytes on 64-bit (4 bytes padding before this)
            ("cchTextMax", ctypes.c_int), ("iImage", ctypes.c_int),
            ("lParam", ctypes.c_ssize_t),   # LPARAM
            ("iIndent", ctypes.c_int),
        ]

    HT_SZ = ctypes.sizeof(_HT)
    LVI_SZ = ctypes.sizeof(_LVI)
    TXT_SZ = 1024  # 512 wchars

    try:
        import win32gui, win32process
        pt = win32gui.ScreenToClient(lv_hwnd, screen_pt)
        _, pid = win32process.GetWindowThreadProcessId(lv_hwnd)

        k32 = ctypes.windll.kernel32
        h_proc = k32.OpenProcess(PROCESS_VM, False, pid)
        if not h_proc:
            return None

        TOTAL = HT_SZ + LVI_SZ + TXT_SZ
        remote = k32.VirtualAllocEx(h_proc, None, TOTAL, 0x3000, 0x04)
        if not remote:
            k32.CloseHandle(h_proc)
            return None

        try:
            w = ctypes.c_size_t(0)

            # LVHITTESTINFO 쓰기
            ht = _HT(); ht.x = pt[0]; ht.y = pt[1]; ht.iItem = -1
            k32.WriteProcessMemory(h_proc, remote, ctypes.byref(ht), HT_SZ, ctypes.byref(w))

            idx = ctypes.windll.user32.SendMessageW(lv_hwnd, LVM_HITTEST, 0, remote)
            if idx < 0:
                return None

            ht_out = _HT()
            k32.ReadProcessMemory(h_proc, remote, ctypes.byref(ht_out), HT_SZ, ctypes.byref(w))
            if not (ht_out.flags & LVHT_ONITEM):
                return None

            # LVITEMW + text buffer 쓰기
            lvi_remote = remote + HT_SZ
            txt_remote = remote + HT_SZ + LVI_SZ

            lvi = _LVI()
            lvi.mask = LVIF_TEXT
            lvi.iItem = idx
            lvi.pszText = txt_remote
            lvi.cchTextMax = 512
            k32.WriteProcessMemory(h_proc, lvi_remote, ctypes.byref(lvi), LVI_SZ, ctypes.byref(w))
            ctypes.windll.user32.SendMessageW(lv_hwnd, LVM_GETITEMTEXTW, idx, lvi_remote)

            raw = (ctypes.c_char * TXT_SZ)()
            k32.ReadProcessMemory(h_proc, txt_remote, ctypes.byref(raw), TXT_SZ, ctypes.byref(w))

            name = bytes(raw).decode('utf-16-le').rstrip('\x00')
            if not name:
                return None

            path = os.path.join(current_folder, name)
            return path if os.path.isdir(path) else None

        finally:
            k32.VirtualFreeEx(h_proc, remote, 0, 0x8000)
            k32.CloseHandle(h_proc)

    except Exception:
        return None


def _get_explorer_folder(hwnd: int, screen_pt: tuple = None):
    """CabinetWClass HWND → 드롭 대상 폴더 경로 반환. 실패 시 None.

    screen_pt 제공 시: 커서 위치에 서브폴더 아이콘이 있으면 그 경로,
    빈 공간이면 현재 탐색 중인 폴더 경로.
    Qt 메인 스레드 전용 (COM이 MTA로 이미 초기화된 환경에서 호출).
    """
    candidates = []  # [(location_name, path), ...]
    try:
        import win32com.client
        import win32gui as _wg
        import win32con as _wc
        shell = win32com.client.Dispatch("Shell.Application")
        for window in shell.Windows():
            try:
                w_hwnd = int(window.HWND)
                if w_hwnd == 0:
                    continue  # 유효하지 않은 HWND (IE 잔재 COM 항목 등) 건너뜀
                # 직접 HWND 일치 OR 탭 자체 HWND의 root ancestor가 일치 (탭 여러 개 케이스)
                w_root = _wg.GetAncestor(w_hwnd, _wc.GA_ROOT)
                if w_hwnd == hwnd or w_root == hwnd:
                    candidates.append((window.LocationName, window.Document.Folder.Self.Path))
            except Exception:
                continue
    except Exception:
        pass

    if not candidates:
        return None

    if len(candidates) == 1:
        current_folder = candidates[0][1]
    else:
        # 탭 여러 개: 창 제목 startswith + 최장 매칭으로 활성 탭 선택
        # 창 제목 형태: '{활성탭명} 및 추가 탭 N - 파일 탐색기'
        current_folder = None
        try:
            title = _wg.GetWindowText(hwnd)
            best_path, best_len = None, 0
            for loc_name, path in candidates:
                if title.startswith(loc_name) and len(loc_name) > best_len:
                    best_path, best_len = path, len(loc_name)
            current_folder = best_path  # 매칭 실패 시 None → 잘못된 폴더에 저장 방지
        except Exception:
            pass

    if not current_folder or not screen_pt:
        return current_folder

    # SysListView32 찾기 → 서브폴더 히트 테스트
    lv_hwnd = [None]
    def _cb(h, _):
        try:
            import win32gui as _wg
            if _wg.GetClassName(h) == 'SysListView32':
                lv_hwnd[0] = h
                return False
        except Exception:
            pass
        return True
    try:
        import win32gui
        win32gui.EnumChildWindows(hwnd, _cb, None)
    except Exception:
        pass

    if lv_hwnd[0]:
        sub = _get_explorer_subfolder_at_cursor(lv_hwnd[0], screen_pt, current_folder)
        if sub:
            print(f"[DBG] 서브폴더 히트: {sub!r}")
            return sub

    return current_folder


def _get_desktop_path() -> str:
    """사용자 바탕화면 경로 반환.
    Qt 메인 스레드 전용 (COM이 MTA로 이미 초기화된 환경에서 호출).
    """
    try:
        import win32com.client
        return win32com.client.Dispatch("WScript.Shell").SpecialFolders("Desktop")
    except Exception:
        return os.path.expanduser("~/Desktop")


_DIRECT_OPEN_SIGNATURES = (
    b'\xff\xd8\xff',      # JPEG
    b'GIF8',              # GIF
    b'RIFF',              # WebP (RIFF....WEBP)
    b'\x89PNG',           # PNG
    b'BM',                # BMP 파일 헤더 있는 경우
    b'\x00\x00\x01\x00',  # ICO
)


def _save_image_to_folder(image_data: bytes, folder: str) -> str:
    """image_data(PNG/JPEG/GIF/WebP/CF_DIB)를 folder에 PNG 파일로 저장. 저장 경로 반환."""
    import io
    import struct
    from PIL import Image
    from datetime import datetime

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_path = os.path.join(folder, f"clip_{ts}.png")
    path = base_path
    suffix = 0
    while os.path.exists(path):
        suffix += 1
        path = os.path.join(folder, f"clip_{ts}_{suffix}.png")

    if any(image_data.startswith(sig) for sig in _DIRECT_OPEN_SIGNATURES):
        # PIL이 직접 인식 가능한 포맷 (PNG/JPEG/GIF/WebP/BMP 파일 등)
        img = Image.open(io.BytesIO(image_data))
        img.save(path, 'PNG')
    else:
        # CF_DIB raw → BMP 파일 헤더 조립 → Pillow로 PNG 변환
        bih_size = struct.unpack_from('<I', image_data, 0)[0]
        bit_count = struct.unpack_from('<H', image_data, 14)[0]
        colors_used = struct.unpack_from('<I', image_data, 32)[0]
        n_colors = colors_used if (colors_used > 0 or bit_count > 8) else (1 << bit_count)
        if bit_count > 8:
            n_colors = colors_used  # 보통 0
        pixel_offset = 14 + bih_size + n_colors * 4
        file_size = 14 + len(image_data)
        file_header = struct.pack('<2sIHHI', b'BM', file_size, 0, 0, pixel_offset)
        img = Image.open(io.BytesIO(file_header + image_data))
        img.save(path, 'PNG')

    return path


# ── 로컬 데이터 경로 (DB·로그) ────────────────────────────────────────────────


def _get_local_data_dir() -> str:
    """%LOCALAPPDATA%\\PasteFlow 폴더 경로. 없으면 생성.
    Drive에 있는 코드와 별개로 PC별 로컬 데이터(DB·로그·설정 캐시)를 보관한다.
    """
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~\\AppData\\Local")
    path = os.path.join(base, "PasteFlow")
    os.makedirs(path, exist_ok=True)
    return path


def _resolve_db_path() -> str:
    """DB 경로 결정. 로컬에 없고 Drive에 기존 DB가 있으면 1회 마이그레이션."""
    local_db = os.path.join(_get_local_data_dir(), "pasteflow.db")
    if not os.path.exists(local_db):
        legacy_db = os.path.join(os.path.dirname(__file__), "..", "pasteflow.db")
        if os.path.exists(legacy_db):
            try:
                import shutil
                shutil.copy2(legacy_db, local_db)
            except Exception as e:
                print(f"[DB] 레거시 DB 마이그레이션 실패: {e}")
    return local_db


# ── settings.json (Drive 공유 설정) ──────────────────────────────────────────
# 5대 PC가 같은 코드(Drive)를 쓰면서 일부 설정만 공유하기 위한 화이트리스트.
# 본질적으로 PC별인 키(auto_start=레지스트리 바인딩, panel_geometry=모니터 종속,
# ocr_gemini_model_cache=네트워크 캐시)는 동기화하지 않는다.
_SYNC_KEYS = frozenset({
    "ocr_gemini_api_key",
    "ocr_gemini_base_url",
    "ocr_gemini_model",
    "ocr_engine",
    "ocr_language",
    "hotkey_panel_toggle",
    "hotkey_ocr_trigger",
    "history_max",
    "panel_auto_close",
})


def _settings_json_path() -> str:
    """Drive 공유 settings.json 경로 (코드와 같은 위치 = project root)."""
    return os.path.join(os.path.dirname(__file__), "..", "settings.json")


def _load_shared_settings() -> dict:
    """settings.json 읽어 dict 반환. 파일 없거나 파싱 실패 시 빈 dict."""
    path = _settings_json_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        print(f"[Settings] settings.json 로드 실패: {e}")
        return {}


def _save_shared_settings(updates: dict):
    """settings.json에 화이트리스트 키만 병합 저장. 다른 키는 무시."""
    path = _settings_json_path()
    current = _load_shared_settings()
    changed = False
    for k, v in updates.items():
        if k in _SYNC_KEYS and current.get(k) != v:
            current[k] = v
            changed = True
    if not changed:
        return
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(current, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[Settings] settings.json 저장 실패: {e}")


# ─────────────────────────────────────────────────────────────────────────────


class _SignalBridge(QObject):
    """훅 스레드 → 메인 스레드 시그널 전달"""
    paste_happened     = pyqtSignal()
    new_item_saved     = pyqtSignal(object)  # 모든 복사 경로: DB + 큐 추가
    panel_toggle       = pyqtSignal()        # 패널 토글 단축키 (훅 스레드 → 메인)
    paste_queue_popped = pyqtSignal()        # 첫 순차 붙여넣기 발생 (패널 팝업용)
    paste_queue_done   = pyqtSignal()        # 큐 소진 (패널 자동 숨기기용)
    ocr_requested      = pyqtSignal()        # 훅 스레드 → 메인: OCR 오버레이 띄우기
    ocr_done           = pyqtSignal(str)     # 워커 스레드 → 메인: OCR 결과 텍스트
    ocr_error          = pyqtSignal(str)     # 워커 스레드 → 메인: 에러 메시지


class PasteFlowApp:
    """PasteFlow 앱 오케스트레이션"""

    def __init__(self):
        self.app = QApplication(sys.argv)
        self.app.setQuitOnLastWindowClosed(False)

        # 스레드 안전 시그널 브릿지
        self._bridge = _SignalBridge()
        self._bridge.paste_happened.connect(self._update_paste_ui)
        self._bridge.new_item_saved.connect(self._on_new_item_ui)
        self._bridge.panel_toggle.connect(self._toggle_panel)
        self._bridge.paste_queue_popped.connect(self._on_paste_queue_popped)
        self._bridge.paste_queue_done.connect(self._on_paste_queue_done)
        self._bridge.ocr_requested.connect(self._on_ocr_requested)
        self._bridge.ocr_done.connect(self._on_ocr_done)
        self._bridge.ocr_error.connect(self._on_ocr_error)

        self._auto_hide_timer = QTimer()
        self._auto_hide_timer.setSingleShot(True)
        self._auto_hide_timer.setInterval(1000)
        self._auto_hide_timer.timeout.connect(self._auto_hide_panel)
        self._panel_opened_by_paste = False  # 순차 붙여넣기로 열린 패널인지 추적
        self._saved_panel_geometry = None
        self._image_preview_windows: dict[int, ImagePreviewPopup] = {}
        self._text_preview_windows: dict[int, TextPreviewPopup] = {}

        # 코어 모듈
        db_path = _resolve_db_path()
        self.db = Database(db_path)
        self.queue = PasteQueue()
        self.monitor = ClipboardMonitor(
            on_new_item=self._on_new_clipboard_item,
            on_duplicate=self._on_duplicate_clipboard_item,
        )
        self.interceptor = PasteInterceptor(
            paste_queue=self.queue,
            clipboard_monitor=self.monitor,
            on_paste=self._on_paste_from_hook,
            get_full_item=self.db.get_item,
            on_toggle_panel=self._bridge.panel_toggle.emit,
            on_ocr_trigger=self._bridge.ocr_requested.emit,
        )
        self.hotkey_manager = HotkeyManager()

        # UI (패널이 기본 UI — 미니창 없음)
        self.panel = ClipboardPanel()
        # 네이티브 윈도우 핸들을 미리 생성 (show/hide 없이, 깜빡임 방지)
        self.panel.winId()
        self.tray = TrayIcon()

        # OCR 오버레이
        from pasteflow.ui.ocr_overlay import OcrOverlay
        self._ocr_overlay = OcrOverlay()
        self._ocr_overlay.region_captured.connect(self._on_ocr_region_captured)
        # cancelled 시그널은 내부 close()로 충분 — 별도 콜백 불필요

        # OCR은 호출마다 새 스레드 — asyncio.run()을 재사용 스레드에서 반복 호출 시
        # WinRT 콜백 상태가 누적돼 두 번째 호출부터 빈 결과를 반환하는 문제 방지

        # 패널 열기 전 포커스된 윈도우 추적 (SetWinEventHook으로 연속 갱신)
        self._prev_foreground_hwnd = None
        self._fg_hook = None
        self._fg_proc = None

        # DB에서 설정 로드 및 적용
        self._apply_settings_from_db()

        # 시그널 연결
        self._connect_signals()

    def _connect_signals(self):
        """모든 시그널 연결"""
        self.tray.quit_requested.connect(self._quit)
        self.tray.panel_toggle_requested.connect(self._toggle_panel)
        self.tray.settings_requested.connect(self._open_settings)

        self.panel.panel_hidden.connect(self._on_panel_hidden)
        self.panel.paste_item_requested.connect(self._on_panel_paste)
        self.panel.copy_item_requested.connect(self._on_copy_item)
        self.panel.combine_copy_requested.connect(self._on_combine_copy)
        self.panel.pin_item_requested.connect(self._on_pin_item)
        self.panel.unpin_item_requested.connect(self._on_unpin_item)
        self.panel.delete_item_requested.connect(self._on_delete_item)
        self.panel.pin_reorder_requested.connect(self._on_pin_reorder)
        self.panel.history_reorder_requested.connect(self._on_hist_reorder)
        self.panel.edit_item_requested.connect(self._on_edit_item)
        self.panel.preview_image_requested.connect(self._on_preview_image)
        self.panel.preview_text_requested.connect(self._on_preview_text)
        self.panel.open_settings_requested.connect(self._open_settings)
        self.panel.quit_requested.connect(self._quit)
        self.panel.clear_history_requested.connect(self._on_clear_history)
        self.panel.drag_to_app_requested.connect(self._on_drag_to_app)
        self.panel.queue_select_requested.connect(self._on_queue_select)
        self.panel.queue_deselect_requested.connect(self._on_queue_deselect)
        self.panel.auto_close_changed.connect(self._on_auto_close_changed)

        panel_hotkey = self.db.get_setting("hotkey_panel_toggle", "ctrl+space")
        self.interceptor.set_panel_hotkey(panel_hotkey)

        ocr_hotkey = self.db.get_setting("hotkey_ocr_trigger", "ctrl+shift+s")
        self.interceptor.set_ocr_hotkey(ocr_hotkey)


    def _on_new_clipboard_item(self, item: ClipboardItem):
        """Ctrl+Shift+C 경로: DB 저장 + 큐 추가 — 백그라운드 스레드에서 호출됨."""
        saved = self.db.save_item(item)
        self.queue.add_item(saved)
        self._bridge.new_item_saved.emit(saved)

    def _on_duplicate_clipboard_item(self):
        """중복 클립보드 콜백 — 무시 (중복은 큐/DB에 추가하지 않음)."""
        pass

    def _on_new_item_ui(self, saved: ClipboardItem):
        """메인 스레드에서 UI 갱신 — 패널이 열려 있을 때만 갱신"""
        if self.panel.isVisible():
            self._refresh_panel()

    def _on_paste_from_hook(self, item: ClipboardItem):
        """붙여넣기 콜백 — 훅 스레드에서 호출됨 → 시그널로 메인 스레드 전달"""
        self._bridge.paste_happened.emit()
        pointer, total = self.queue.get_status()
        if pointer == 1:
            self._bridge.paste_queue_popped.emit()
        if pointer >= total and total > 0:
            self._bridge.paste_queue_done.emit()

    def _on_paste_queue_popped(self):
        """첫 순차 붙여넣기 발생 — 패널이 닫혀 있으면 마우스 근처에 팝업"""
        if not self.panel.isVisible():
            self._panel_opened_by_paste = True
            self._apply_saved_panel_size()
            self.panel.show_near_cursor()
            self._refresh_panel()

    def _on_paste_queue_done(self):
        """큐 소진 — 순차 붙여넣기로 열린 패널이면 1초 후 숨기기"""
        if self._panel_opened_by_paste and self.panel.isVisible():
            self._auto_hide_timer.start()

    def _auto_hide_panel(self):
        """자동 숨기기 타이머 만료 — 패널 숨기기"""
        if self._panel_opened_by_paste:
            self._panel_opened_by_paste = False
            if self.panel.isVisible():
                self.panel.hide_immediate()

    def _on_panel_hidden(self):
        """패널이 닫힐 때 자동 숨기기 타이머 취소 및 플래그 초기화"""
        self._auto_hide_timer.stop()
        self._panel_opened_by_paste = False

    def _on_auto_close_changed(self, value: bool):
        self.db.set_setting("panel_auto_close", "1" if value else "0")

    # ── OCR ──

    def _on_ocr_requested(self):
        """메인 스레드: OCR 오버레이 시작"""
        self._ocr_overlay.start()

    def _on_ocr_region_captured(self, pixmap):
        """메인 스레드: 선택 영역 픽맵 → 워커 스레드에서 OCR"""
        import io
        from PIL import Image
        from pasteflow.ui.toast import ToastNotification

        # QPixmap/QBuffer는 메인 스레드에서만 안전 — PNG 변환을 여기서 완료
        buf = QBuffer()
        buf.open(QBuffer.OpenModeFlag.WriteOnly)
        pixmap.save(buf, "PNG")
        png_bytes = bytes(buf.data())
        buf.close()

        ToastNotification("OCR 인식 중…")

        def _run():
            # 호출마다 COM 초기화/해제 — asyncio/WinRT 상태 오염 방지
            ctypes.windll.ole32.CoInitializeEx(None, 0)
            try:
                pil_img = Image.open(io.BytesIO(png_bytes))

                from pasteflow.ocr_engine import OcrEngine
                lang = self.db.get_setting("ocr_language", "ko")
                engine_kind = self.db.get_setting("ocr_engine", "winrt")
                if engine_kind == "gemini":
                    api_key = self.db.get_setting("ocr_gemini_api_key", "")
                    base_url = self.db.get_setting("ocr_gemini_base_url", "")
                    model = self.db.get_setting("ocr_gemini_model", "")
                else:
                    api_key = ""
                    base_url = ""
                    model = ""
                engine = OcrEngine(kind=engine_kind, api_key=api_key, base_url=base_url, language=lang, model=model)
                text = engine.recognize(pil_img)
                self._bridge.ocr_done.emit(text)
            except Exception as e:
                self._bridge.ocr_error.emit(str(e))
            finally:
                ctypes.windll.ole32.CoUninitialize()

        threading.Thread(target=_run, daemon=True, name="ocr-worker").start()

    def _on_ocr_done(self, text: str):
        """메인 스레드: OCR 결과 → 클립보드 + DB + 큐 + 토스트"""
        from pasteflow.ui.toast import ToastNotification

        if not text.strip():
            ToastNotification("OCR: 텍스트를 인식하지 못했습니다")
            return

        item = ClipboardItem(
            content_type="text",
            text_content=text,
            preview_text=text[:200],
        )
        # 클립보드 먼저 입력 — _set_clipboard가 내부에서 monitor._self_triggered를 설정해
        # 클립보드 모니터가 동일 항목을 재감지(큐 중복 추가)하지 않도록 함
        self.interceptor._set_clipboard(item)
        self._on_new_clipboard_item(item)

        preview = text[:30].replace("\n", " ")
        suffix = "..." if len(text) > 30 else ""
        ToastNotification(f"OCR: {preview}{suffix}")

    def _on_ocr_error(self, msg: str):
        from pasteflow.ui.toast import ToastNotification

        # API 키 미설정 → 토스트 후 설정 다이얼로그 자동 열기
        if "API 키" in msg:
            ToastNotification("OCR: API 키를 설정해 주세요")
            QTimer.singleShot(300, self._open_settings)
            return

        # WinRT 언어팩 미설치 오류 — "언어팩"은 _recognize_winrt에서만 발생
        if "언어팩" in msg:
            from PyQt6.QtWidgets import QMessageBox
            import os
            dlg = QMessageBox(self.panel)
            dlg.setStyleSheet(_MSGBOX_DARK_STYLE)
            dlg.setWindowTitle("OCR 언어팩 미설치")
            dlg.setIcon(QMessageBox.Icon.Warning)
            dlg.setText("선택한 언어의 OCR 언어팩이 설치되지 않았습니다.")
            dlg.setDetailedText(msg)
            open_btn = dlg.addButton(
                "Windows 언어 설정 열기", QMessageBox.ButtonRole.ActionRole
            )
            dlg.addButton(QMessageBox.StandardButton.Close)
            dlg.exec()
            if dlg.clickedButton() == open_btn:
                os.startfile("ms-settings:regionlanguage")
        elif "미설치" in msg:
            # AI OCR 패키지 미설치 (google-generativeai, openai 등)
            from PyQt6.QtWidgets import QMessageBox
            import re
            # "xxx 패키지 미설치: pip install yyy" 패턴에서 pip 명령 추출
            m = re.search(r"(pip install \S+)", msg)
            pip_cmd = m.group(1) if m else ""
            body = "OCR에 필요한 패키지가 설치되지 않았습니다."
            if pip_cmd:
                body += f"\n\n터미널에서 실행하세요:\n  {pip_cmd}"
            dlg = QMessageBox(self.panel)
            dlg.setStyleSheet(_MSGBOX_DARK_STYLE)
            dlg.setWindowTitle("OCR 패키지 미설치")
            dlg.setIcon(QMessageBox.Icon.Information)
            dlg.setText(body)
            dlg.addButton(QMessageBox.StandardButton.Close)
            dlg.exec()
        else:
            from PyQt6.QtWidgets import QMessageBox
            dlg = QMessageBox(self.panel)
            dlg.setStyleSheet(_MSGBOX_DARK_STYLE)
            dlg.setWindowTitle("OCR 오류")
            dlg.setIcon(QMessageBox.Icon.Warning)
            dlg.setText(msg[:200])
            if len(msg) > 200:
                dlg.setDetailedText(msg)
            dlg.addButton(QMessageBox.StandardButton.Close)
            dlg.exec()

    def _update_paste_ui(self):
        """메인 스레드에서 붙여넣기 UI 업데이트"""
        pointer, total = self.queue.get_status()
        self.tray.update_queue_status(pointer, total)
        if self.panel.isVisible():
            self.panel.update_queue_status(pointer, total)

    def _toggle_panel(self):
        """패널 토글 — 단축키/트레이로 열 때 마우스 근처에 표시"""
        if self.panel.isVisible():
            self._panel_opened_by_paste = False
            self._auto_hide_timer.stop()
            self.panel.hide()
        else:
            # _prev_foreground_hwnd는 SetWinEventHook이 연속 추적 — 별도 캡처 불필요
            self.panel._user_activated = True
            self._panel_opened_by_paste = False
            self._apply_saved_panel_size()
            self.panel.show_near_cursor()
            self._refresh_panel()

    def _refresh_panel(self):
        """패널 데이터 갱신"""
        pinned = self.db.get_pinned_items_summary()
        history = self.db.get_recent_items_summary()
        pointer, total = self.queue.get_status()
        queue_item_ids = [item.id for item in self.queue.get_items()]
        self.panel.refresh(pinned, history, pointer, total, queue_item_ids)

    def _on_panel_paste(self, item: ClipboardItem):
        """패널 항목 클릭 붙여넣기 — auto_close 설정에 따라 패널 닫기 여부 결정"""
        full_item = self.db.get_item(item.id) or item
        target_hwnd = self._prev_foreground_hwnd

        if self.panel._auto_close:
            # 자동 닫기 ON: 즉시 숨기고 붙여넣기 (fade 대기 시 포그라운드 잠금 문제 발생)
            self.panel.hide_immediate()
            def _do_paste():
                try:
                    self.interceptor.direct_paste(full_item, target_hwnd)
                except Exception as e:
                    print(f"[PanelPaste] Error: {e}")
        else:
            # 자동 닫기 OFF: 패널 유지
            # _paste_in_progress 먼저 → SetForegroundWindow 순서 보장 (changeEvent 선행 방지)
            self.panel._paste_in_progress = True
            # 메인 스레드에서 포커스 이동 (포그라운드 잠금 우회)
            if target_hwnd:
                ctypes.windll.user32.SetForegroundWindow(target_hwnd)
            def _do_paste():
                try:
                    # 포커스 이미 이동됐으므로 target_hwnd=None
                    self.interceptor.direct_paste(full_item, None)
                except Exception as e:
                    print(f"[PanelPaste] Error: {e}")
                finally:
                    QTimer.singleShot(0, lambda: setattr(self.panel, '_paste_in_progress', False))

        threading.Thread(target=_do_paste, daemon=True).start()

    def _on_copy_item(self, item: ClipboardItem):
        """고정 항목 클릭 → 클립보드 복사 + 큐 추가"""
        full_item = self.db.get_item(item.id) or item
        self.interceptor._set_clipboard(full_item)
        self.queue.add_item(full_item)
        self._refresh_panel()

    def _on_queue_select(self, item_id: int):
        """패널 항목 클릭 → 큐 설정.
        - 히스토리 항목: 선택한 항목~최신까지 큐로 설정
        - 고정 항목: 선택한 항목~pin1까지 역순 큐로 설정 (히스토리와 동일 패턴)
        """
        history = self.panel.history_items
        hist_ids = [item.id for item in history]
        if item_id in hist_ids:
            i = hist_ids.index(item_id)
            # history[0:i+1] = [newest, ..., selected] → reverse → [selected, ..., newest]
            queue_items = list(reversed(history[0 : i + 1]))
            self.queue.set_queue(queue_items)
        else:
            pinned = self.panel.pinned_items
            pin_ids = [item.id for item in pinned]
            if item_id not in pin_ids:
                return
            i = pin_ids.index(item_id)
            # pinned[0:i+1] = [pin1, ..., pinN] → reverse → [pinN, ..., pin1]
            queue_items = list(reversed(pinned[0 : i + 1]))
            self.queue.set_queue(queue_items)
        pointer, total = self.queue.get_status()
        queue_item_ids = [item.id for item in self.queue.get_items()]
        self.tray.update_queue_status(pointer, total)
        self.panel.update_queue_highlight(pointer, total, queue_item_ids)

    def _on_queue_deselect(self, item_id: int):
        self.queue.clear()
        self.tray.update_queue_status(0, 0)
        self.panel.update_queue_highlight(0, 0, [])

    def _on_combine_copy(self, item: ClipboardItem):
        """F6: 다중 선택 결합 복사 → DB 저장 + 클립보드 + 큐"""
        saved = self.db.save_item(item)
        self.interceptor._set_clipboard(saved)
        self.queue.add_item(saved)
        self._refresh_panel()

    def _on_pin_item(self, item_id: int):
        self.db.pin_item(item_id)
        self._refresh_panel()

    def _on_unpin_item(self, item_id: int):
        self.db.unpin_item(item_id)
        self._refresh_panel()

    def _on_delete_item(self, item_id: int):
        self.db.delete_item(item_id)
        self._refresh_panel()

    def _on_pin_reorder(self, id_order_list: list):
        self.db.update_pin_orders(id_order_list)
        self._refresh_panel()

    def _on_hist_reorder(self, id_order_list: list):
        self.db.update_history_orders(id_order_list)
        # 패널 레이아웃은 이미 라이브 스왑으로 반영됨 — refresh 불필요

    def _on_edit_item(self, item_id: int, new_text: str):
        self.db.update_item_text(item_id, new_text)
        self._refresh_panel()

    def _on_preview_image(self, item_id: int):
        existing = self._image_preview_windows.pop(item_id, None)
        if existing is not None:
            existing.close()
            return
        item = self.db.get_item(item_id)
        if item and item.image_data:
            popup = ImagePreviewPopup.open_new(item.image_data, self.panel.geometry())
            self._image_preview_windows[item_id] = popup
            popup.destroyed.connect(lambda _=None, iid=item_id: self._image_preview_windows.pop(iid, None))

    def _on_preview_text(self, item_id: int):
        existing = self._text_preview_windows.pop(item_id, None)
        if existing is not None:
            existing.close()
            return
        item = self.db.get_item(item_id)
        if not item:
            return
        popup = TextPreviewPopup.open_new(item, self.panel.geometry())
        popup.copy_requested.connect(self._on_copy_item)
        popup.edit_requested.connect(self._on_preview_edit_request)
        self._text_preview_windows[item_id] = popup
        popup.destroyed.connect(lambda _=None, iid=item_id: self._text_preview_windows.pop(iid, None))

    def _on_preview_edit_request(self, item_id: int):
        """텍스트 미리보기 우클릭 메뉴 `수정` → 편집 다이얼로그 → 변경 시 기존 _on_edit_item으로 위임."""
        from PyQt6.QtWidgets import QDialog
        item = self.db.get_item(item_id)
        if not item:
            return
        dialog = EditItemDialog(item.text_content or "", self.panel)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            new_text = dialog.get_text()
            if new_text != (item.text_content or ""):
                self._on_edit_item(item_id, new_text)

    def _on_clear_history(self):
        self.db.clear_history()
        self.queue.clear()
        self.tray.update_queue_status(0, 0)
        self._refresh_panel()

    def _on_drag_to_app(self, item_id: int, cursor_pos):
        """패널 항목 드래그 → 외부 앱 붙여넣기.
        - 이미지 + Explorer/바탕화면: PNG 파일로 저장
        - Win32/WinUI3: 재귀 탐색으로 찾은 최하위 컨트롤에 WM_PASTE
        - Electron/Chromium: AttachThreadInput + SetForegroundWindow + SendInput(Ctrl+V)
        """
        full_item = self.db.get_item(item_id)
        if not full_item:
            return

        import win32gui
        import win32con

        screen_pt = (cursor_pos.x(), cursor_pos.y())
        hwnd = win32gui.WindowFromPoint(screen_pt)
        if not hwnd:
            return

        # 최상위 창 클래스 확인
        root_hwnd = win32gui.GetAncestor(hwnd, win32con.GA_ROOT)
        root_class = ""
        try:
            root_class = win32gui.GetClassName(root_hwnd)
        except Exception:
            pass

        # 이미지 항목 + Explorer/바탕화면 → PNG 파일 저장
        if full_item.image_data and full_item.content_type == "image":
            folder = None
            if root_class in _EXPLORER_CLASSES:
                folder = _get_explorer_folder(root_hwnd, screen_pt=screen_pt)
            elif root_class in _DESKTOP_CLASSES:
                folder = _get_desktop_path()
            if folder:
                try:
                    _save_image_to_folder(full_item.image_data, folder)
                except Exception:
                    pass
                else:
                    return  # 저장 성공 시에만 반환; 실패 시 클립보드 경로로 fall-through

        # 기존 붙여넣기 경로 (텍스트/기타 항목, 또는 이미지→일반 앱)
        self.interceptor._set_clipboard(full_item)

        target = _find_deepest_child(hwnd, screen_pt)
        class_name = ""
        try:
            class_name = win32gui.GetClassName(target)
        except Exception:
            pass

        # 이미지 fallthrough + Win32 경로: 2.0초로 연장 (0.5초 덮어쓰기 방지)
        # Chromium 경로 제외: SendInput 이후 사용자 복사가 억제되는 것 방지
        if full_item.content_type == "image" and self.interceptor.monitor and not _is_chromium_window(class_name):
            self.interceptor.monitor.set_self_triggered(2.0)

        if _is_chromium_window(class_name):
            # Electron/Chromium: 포그라운드 활성화 후 SendInput(Ctrl+V)
            top_hwnd = win32gui.GetAncestor(target, win32con.GA_ROOT)
            _activate_and_send_ctrl_v(top_hwnd)
        else:
            # Win32 / WinUI3: WM_PASTE 직접 전송
            win32gui.SendMessage(target, win32con.WM_PASTE, 0, 0)

    def _apply_saved_panel_size(self):
        if self._saved_panel_geometry:
            try:
                w = max(PANEL_MIN_WIDTH, int(self._saved_panel_geometry["w"]))
                h = max(PANEL_MIN_HEIGHT, int(self._saved_panel_geometry["h"]))
                if self.panel.width() != w or self.panel.height() != h:
                    self.panel.resize(w, h)
            except (KeyError, ValueError):
                pass

    # ── 설정 ──

    def _apply_settings_from_db(self):
        """DB에서 설정 로드 → UI/동작에 적용.
        DB 읽기 전에 Drive의 settings.json을 화이트리스트 기준으로 DB에 덮어써서
        다른 PC에서 변경한 설정(API 키·단축키 등)을 이 PC에 반영한다.
        """
        shared = _load_shared_settings()
        for k, v in shared.items():
            if k in _SYNC_KEYS:
                self.db.set_setting(k, str(v))

        # 레지스트리 실제 상태로 auto_start DB 동기화
        self._sync_auto_start_from_registry()

        auto_close = self.db.get_setting("panel_auto_close", "1")
        self.panel.set_auto_close(auto_close == "1")

        # 패널 위치/크기 — show_near_cursor() 시 적용하도록 저장만 해둠
        geo_json = self.db.get_setting("panel_geometry")
        if geo_json:
            try:
                self._saved_panel_geometry = json.loads(geo_json)
            except Exception:
                self._saved_panel_geometry = None
        else:
            self._saved_panel_geometry = None

    def _open_settings(self):
        """설정 다이얼로그 열기"""
        current = {
            "hotkey_panel_toggle": self.db.get_setting("hotkey_panel_toggle", "ctrl+space"),
            "history_max": self.db.get_setting("history_max", "50"),
            "auto_start": self.db.get_setting("auto_start", "0"),
            "hotkey_ocr_trigger": self.db.get_setting("hotkey_ocr_trigger", "ctrl+shift+s"),
            "ocr_language": self.db.get_setting("ocr_language", "ko"),
            "ocr_engine": self.db.get_setting("ocr_engine", "winrt"),
            "ocr_gemini_api_key": self.db.get_setting("ocr_gemini_api_key", ""),
            "ocr_gemini_base_url": self.db.get_setting("ocr_gemini_base_url", ""),
            "ocr_gemini_model": self.db.get_setting("ocr_gemini_model", ""),
            "ocr_gemini_model_cache": self.db.get_setting("ocr_gemini_model_cache", ""),
        }
        dlg = SettingsDialog(current, parent=self.panel)
        dlg.settings_changed.connect(self._on_settings_changed)
        dlg.raise_()
        dlg.activateWindow()
        dlg.exec()

    def _on_settings_changed(self, new_settings: dict):
        """설정 변경 적용"""
        # 단축키 비교는 DB 저장 전에 이전 값을 먼저 읽어야 함
        old_hotkey = self.db.get_setting("hotkey_panel_toggle", "ctrl+space")
        old_ocr_hotkey = self.db.get_setting("hotkey_ocr_trigger", "ctrl+shift+s")

        for key, value in new_settings.items():
            self.db.set_setting(key, value)

        # 화이트리스트 키는 Drive의 settings.json에도 반영(다른 PC에서 다음 부팅 시 적용)
        _save_shared_settings(new_settings)

        # 패널 토글 단축키 재설정
        new_hotkey = new_settings.get("hotkey_panel_toggle", "ctrl+space")
        if old_hotkey != new_hotkey:
            self.interceptor.set_panel_hotkey(new_hotkey)

        # OCR 단축키 재설정
        new_ocr_hotkey = new_settings.get("hotkey_ocr_trigger", "ctrl+shift+s")
        if old_ocr_hotkey != new_ocr_hotkey:
            self.interceptor.set_ocr_hotkey(new_ocr_hotkey)

        # 자동 시작
        auto_start = new_settings.get("auto_start", "0") == "1"
        self._set_auto_start(auto_start)

    # 부팅 후 Drive 마운트 대기 시간(초). 이 시간이 지난 뒤 launcher VBS가 PasteFlow를 실행한다.
    _AUTOSTART_DRIVE_WAIT_SEC = 15

    def _write_autostart_launcher_vbs(self, target_cmd: str) -> str:
        """%LOCALAPPDATA%\\PasteFlow\\autostart_launcher.vbs 생성/갱신.
        VBS는 _AUTOSTART_DRIVE_WAIT_SEC초 대기 후 target_cmd를 hidden으로 실행한다.
        wscript.exe로 실행되므로 콘솔 창이 일절 뜨지 않는다.
        실행 단계마다 %LOCALAPPDATA%\\PasteFlow\\logs\\autostart.log에 로그를 남겨
        boot 시 자동 시작 실패 원인(Drive 마운트 지연·경로 깨짐 등)을 추적 가능.
        """
        wait_ms = self._AUTOSTART_DRIVE_WAIT_SEC * 1000
        escaped = target_cmd.replace('"', '""')  # VBS 문자열 내 큰따옴표 이스케이프
        vbs = (
            'Dim fso, logPath, logDir\r\n'
            'Set fso = CreateObject("Scripting.FileSystemObject")\r\n'
            'logDir = CreateObject("WScript.Shell").ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\\PasteFlow\\logs"\r\n'
            'If Not fso.FolderExists(logDir) Then fso.CreateFolder(logDir)\r\n'
            'logPath = logDir & "\\autostart.log"\r\n'
            'Sub LogMsg(msg)\r\n'
            '    Dim f\r\n'
            '    Set f = fso.OpenTextFile(logPath, 8, True)\r\n'
            '    f.WriteLine Now & "  " & msg\r\n'
            '    f.Close\r\n'
            'End Sub\r\n'
            f'LogMsg "VBS triggered, sleeping {self._AUTOSTART_DRIVE_WAIT_SEC}s"\r\n'
            f'WScript.Sleep {wait_ms}\r\n'
            'On Error Resume Next\r\n'
            'Dim objShell\r\n'
            'Set objShell = CreateObject("WScript.Shell")\r\n'
            f'objShell.Run "{escaped}", 0, False\r\n'
            'If Err.Number <> 0 Then\r\n'
            '    LogMsg "Run FAILED: " & Err.Number & " " & Err.Description\r\n'
            'Else\r\n'
            '    LogMsg "Run dispatched OK"\r\n'
            'End If\r\n'
        )
        path = os.path.join(_get_local_data_dir(), "autostart_launcher.vbs")
        # UTF-16 LE + BOM으로 저장해야 한국어 Windows의 wscript.exe가 한글 경로를
        # CP949로 잘못 해석해 objShell.Run이 조용히 실패하는 문제를 막을 수 있다.
        with open(path, "w", encoding="utf-16") as f:
            f.write(vbs)
        return path

    def _set_auto_start(self, enable: bool):
        import winreg
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        try:
            reg_key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE)
            if enable:
                if getattr(sys, "frozen", False):
                    target_cmd = f'"{sys.executable}"'
                else:
                    pythonw = sys.executable
                    if pythonw.lower().endswith("python.exe"):
                        pythonw = pythonw[:-len("python.exe")] + "pythonw.exe"
                    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                    run_pyw = os.path.join(project_root, "run.pyw")
                    target_cmd = f'"{pythonw}" "{run_pyw}"'
                vbs_path = self._write_autostart_launcher_vbs(target_cmd)
                cmd = f'wscript.exe "{vbs_path}"'
                winreg.SetValueEx(reg_key, "PasteFlow", 0, winreg.REG_SZ, cmd)
                # 작업 관리자 → 시작 앱 탭에서 disabled로 토글된 상태가 있으면 정리.
                # 이 플래그가 있으면 Run 키가 등록되어 있어도 logon 시 Windows가 차단한다.
                self._clear_startup_approved_flag()
            else:
                try:
                    winreg.DeleteValue(reg_key, "PasteFlow")
                except FileNotFoundError:
                    pass
            winreg.CloseKey(reg_key)
        except Exception as e:
            print(f"[Settings] 자동 시작 설정 실패: {e}")

    def _clear_startup_approved_flag(self):
        """HKCU\\...\\Explorer\\StartupApproved\\Run\\PasteFlow 값을 삭제.
        해당 값의 첫 바이트가 03이면 사용자가 작업 관리자에서 disabled 처리한 것이며,
        이 상태에서는 Run 키가 정상 등록되어도 Windows가 logon 시 발화시키지 않는다.
        값이 없으면 Windows는 enabled로 간주하므로 삭제만으로 충분하다.
        """
        import winreg
        approved_path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, approved_path, 0, winreg.KEY_SET_VALUE)
            try:
                winreg.DeleteValue(key, "PasteFlow")
            except FileNotFoundError:
                pass
            winreg.CloseKey(key)
        except OSError:
            pass

    def _sync_auto_start_from_registry(self):
        import winreg
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        registered = False
        try:
            reg_key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ)
            try:
                winreg.QueryValueEx(reg_key, "PasteFlow")
                registered = True
            except FileNotFoundError:
                pass
            winreg.CloseKey(reg_key)
        except OSError:
            pass
        self.db.set_setting("auto_start", "1" if registered else "0")

    # ── 포그라운드 창 추적 ──

    # 트레이 클릭, 시작 메뉴 등 시스템 UI가 포그라운드를 가져갈 때 무시할 클래스명
    _FG_IGNORE_CLASSES = frozenset({
        "Shell_TrayWnd",           # 작업 표시줄
        "NotifyIconOverflowWindow", # 트레이 오버플로우
        "DV2ControlHost",          # 시작 메뉴 (Win10)
        "Windows.UI.Core.CoreWindow", # 시작 메뉴 / 액션 센터 (Win10/11)
        "TaskListThumbnailWnd",    # 작업 표시줄 썸네일
        "SysShadow",               # 그림자 창
        "#32768",                  # 시스템 드롭다운 메뉴
    })

    def _start_foreground_tracker(self):
        """SetWinEventHook(EVENT_SYSTEM_FOREGROUND)으로 포그라운드 창 연속 추적.

        시스템 창·자기 프로세스 창을 제외한 실제 앱 창이 포그라운드를 가져올 때마다
        _prev_foreground_hwnd를 갱신한다. WINEVENT_OUTOFCONTEXT이므로 _pump_messages가
        DispatchMessageW를 처리할 때 콜백이 호출된다.
        """
        my_pid = ctypes.windll.kernel32.GetCurrentProcessId()
        ignore = self._FG_IGNORE_CLASSES

        WinEventProc = ctypes.WINFUNCTYPE(
            None,
            ctypes.wintypes.HANDLE,
            ctypes.wintypes.DWORD,
            ctypes.wintypes.HWND,
            ctypes.wintypes.LONG,
            ctypes.wintypes.LONG,
            ctypes.wintypes.DWORD,
            ctypes.wintypes.DWORD,
        )

        def _on_fg_changed(hHook, event, hwnd, idObject, idChild, tid, ts):
            if not hwnd:
                return
            try:
                pid_buf = ctypes.wintypes.DWORD()
                ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid_buf))
                if pid_buf.value == my_pid:
                    return  # 패널·팝업 등 PasteFlow 자신의 창 무시
                import win32gui
                cls = win32gui.GetClassName(hwnd)
                if not cls or cls in ignore:
                    return
                self._prev_foreground_hwnd = hwnd
            except Exception:
                pass

        self._fg_proc = WinEventProc(_on_fg_changed)  # GC 방지
        EVENT_SYSTEM_FOREGROUND = 0x0003
        WINEVENT_OUTOFCONTEXT = 0x0000
        self._fg_hook = ctypes.windll.user32.SetWinEventHook(
            EVENT_SYSTEM_FOREGROUND, EVENT_SYSTEM_FOREGROUND,
            None, self._fg_proc, 0, 0, WINEVENT_OUTOFCONTEXT,
        )

    def _quit(self):
        # 패널 위치/크기 저장
        geo = self.panel.get_geometry_dict()
        self.db.set_setting("panel_geometry", json.dumps(geo))

        if self._fg_hook:
            ctypes.windll.user32.UnhookWinEvent(self._fg_hook)
            self._fg_hook = None

        self.interceptor.stop()
        self.monitor.stop()
        self.hotkey_manager.destroy()
        self.tray.hide()
        self.db.close()
        self.app.quit()

    def _start_ipc_server(self):
        """Named pipe 서버 — 두 번째 인스턴스 실행 시 패널 토글 신호 수신."""
        threading.Thread(target=self._ipc_loop, name="ipc-server", daemon=True).start()

    def _ipc_loop(self):
        import win32pipe, win32file, pywintypes
        pipe_name = r"\\.\pipe\PasteFlow_IPC"
        while True:
            try:
                h = win32pipe.CreateNamedPipe(
                    pipe_name,
                    win32pipe.PIPE_ACCESS_INBOUND,
                    win32pipe.PIPE_TYPE_BYTE | win32pipe.PIPE_WAIT,
                    1, 64, 64, 0, None,
                )
                win32pipe.ConnectNamedPipe(h, None)
                win32file.CloseHandle(h)
                self._bridge.panel_toggle.emit()
            except pywintypes.error:
                time.sleep(0.1)

    def run(self):
        self.monitor.start()
        self.interceptor.start()
        self._start_ipc_server()
        self._start_foreground_tracker()
        self.tray.show()

        self._msg_timer = QTimer()
        self._msg_timer.timeout.connect(self._pump_messages)
        self._msg_timer.start(1)

        # 시작 알림 토스트
        def _show_startup_toast():
            from pasteflow.ui.toast import ToastNotification
            self._startup_toast = ToastNotification("PasteFlow 시작됨  ·  Ctrl+Shift+V로 붙여넣기")

        QTimer.singleShot(500, _show_startup_toast)

        return self.app.exec()

    def _pump_messages(self):
        msg = ctypes.wintypes.MSG()
        while ctypes.windll.user32.PeekMessageW(
            ctypes.byref(msg), None, 0, 0, 1
        ):
            ctypes.windll.user32.TranslateMessage(ctypes.byref(msg))
            ctypes.windll.user32.DispatchMessageW(ctypes.byref(msg))


def main():
    # 단일 인스턴스 보장 — Windows 뮤텍스
    _mutex = ctypes.windll.kernel32.CreateMutexW(None, True, "PasteFlow_SingleInstance")
    if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        ctypes.windll.kernel32.CloseHandle(_mutex)
        # 실행 중인 인스턴스에 패널 토글 신호 전송
        try:
            import win32file
            h = win32file.CreateFile(
                r"\\.\pipe\PasteFlow_IPC",
                win32file.GENERIC_WRITE, 0, None,
                win32file.OPEN_EXISTING, 0, None,
            )
            win32file.CloseHandle(h)
        except Exception:
            pass
        sys.exit(0)

    app = PasteFlowApp()
    app._single_instance_mutex = _mutex  # GC 방지 — 프로세스 종료 시까지 유지
    sys.exit(app.run())


if __name__ == "__main__":
    main()
