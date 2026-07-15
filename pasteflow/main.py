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
from PyQt6.QtCore import QTimer, QObject, pyqtSignal, QBuffer, QRect, Qt

from pasteflow import web_search
from pasteflow import gdrive
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
from pasteflow.ui.image_annotator import _EditorMixin
from pasteflow.ui.text_preview import TextPreviewPopup
from pasteflow.ui.tray import TrayIcon
from pasteflow.ui.paste_hud import PasteHud
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


def _image_data_to_png_bytes(image_data: bytes) -> bytes:
    """image_data(PNG/JPEG/GIF/WebP/CF_DIB)를 PNG bytes로 변환.

    PIL이 직접 못 여는 CF_DIB raw는 BMP 파일 헤더를 조립해 인식시킨다(_create_thumbnail과
    동일 기법). 파일 저장(_save_image_to_folder)·이미지 AI 질의가 공유한다.
    """
    import io
    import struct
    from PIL import Image

    if any(image_data.startswith(sig) for sig in _DIRECT_OPEN_SIGNATURES):
        # PIL이 직접 인식 가능한 포맷 (PNG/JPEG/GIF/WebP/BMP 파일 등)
        img = Image.open(io.BytesIO(image_data))
    else:
        # CF_DIB raw → BMP 파일 헤더 조립 → Pillow로 인식
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

    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return buf.getvalue()


def _save_image_to_folder(image_data: bytes, folder: str) -> str:
    """image_data(PNG/JPEG/GIF/WebP/CF_DIB)를 folder에 PNG 파일로 저장. 저장 경로 반환."""
    from datetime import datetime

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_path = os.path.join(folder, f"clip_{ts}.png")
    path = base_path
    suffix = 0
    while os.path.exists(path):
        suffix += 1
        path = os.path.join(folder, f"clip_{ts}_{suffix}.png")

    with open(path, 'wb') as f:
        f.write(_image_data_to_png_bytes(image_data))

    return path


def _save_image_to_drop_temp(image_data: bytes) -> str:
    """image_data를 %TEMP%\\PasteFlow\\ 아래 PNG로 저장하고 절대경로 반환.
    Alt+드래그·우클릭 "파일로 저장 후 경로 복사"에서 공유 사용한다.
    Claude Code CLI 등 "경로 텍스트"를 첨부로 받는 앱에 넘기기 위한 임시 파일.
    """
    import tempfile
    folder = os.path.join(tempfile.gettempdir(), "PasteFlow")
    os.makedirs(folder, exist_ok=True)
    return _save_image_to_folder(image_data, folder)


def _read_image_from_clipboard() -> bytes | None:
    """현재 클립보드에서 이미지 bytes를 읽는다(PNG 우선, 없으면 CF_DIB raw).

    반환값은 그대로 ``_save_image_to_drop_temp`` 에 넘길 수 있다(PNG·DIB 둘 다 처리).
    이미지가 없거나 OpenClipboard 실패 시 None.
    """
    import win32clipboard
    cf_png = win32clipboard.RegisterClipboardFormat("PNG")
    for _ in range(3):
        try:
            win32clipboard.OpenClipboard()
            break
        except Exception:
            time.sleep(0.01)
    else:
        return None
    try:
        if win32clipboard.IsClipboardFormatAvailable(cf_png):
            return win32clipboard.GetClipboardData(cf_png)
        if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_DIB):
            return win32clipboard.GetClipboardData(win32clipboard.CF_DIB)
        # CF_HDROP: 탐색기에서 이미지 파일 1개 복사 — 파일 바이트를 읽어 이미지로 처리.
        # 히스토리 캡처(clipboard_monitor)와 같은 규칙이라, 패널에 이미지로 남는 것은
        # 핀에서도 이미지로 뜬다(경로 텍스트가 아니라).
        if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_HDROP):
            _IMAGE_EXTS = {'.png', '.jpg', '.jpeg', '.bmp', '.gif', '.webp', '.tiff', '.tif'}
            files = win32clipboard.GetClipboardData(win32clipboard.CF_HDROP)
            if files and len(files) == 1:
                fpath = files[0]
                if os.path.splitext(fpath)[1].lower() in _IMAGE_EXTS and os.path.exists(fpath):
                    with open(fpath, 'rb') as f:
                        return f.read()
        return None
    except Exception:
        return None
    finally:
        try:
            win32clipboard.CloseClipboard()
        except Exception:
            pass


def _render_text_to_png(text: str) -> bytes:
    """클립보드 텍스트를 다크 배경 이미지(PNG bytes)로 렌더링한다.

    Snipaste의 'paste text to screen'처럼, 텍스트도 화면 핀·주석이 가능하도록
    이미지화한다. 줄바꿈은 보존하고, 최대폭을 넘으면 워드랩한다.
    앱 전체가 다크 테마이므로 배경도 어둡게(테마 BASE) + 밝은 글자(TEXT)로 렌더한다.
    """
    from PyQt6.QtGui import QPixmap, QPainter, QFont, QFontMetrics, QColor
    from PyQt6.QtCore import QRect, Qt, QBuffer, QByteArray
    from pasteflow.ui.theme import BASE, TEXT

    pad = 16
    max_w = 700
    # 한글 글리프 보장 — 기본 QFont는 폴백이 불확실해 두부(□)로 렌더될 수 있음.
    # 맑은 고딕(Win 한글 시스템 폰트) 우선, 영문은 Segoe UI로 폴백.
    font = QFont()
    font.setFamilies(["Malgun Gothic", "Segoe UI"])
    font.setPixelSize(18)
    fm = QFontMetrics(font)
    flags = (int(Qt.TextFlag.TextWordWrap)
             | int(Qt.AlignmentFlag.AlignLeft)
             | int(Qt.AlignmentFlag.AlignTop))

    bounds = fm.boundingRect(QRect(0, 0, max_w, 1_000_000), flags, text)
    w = max(1, bounds.width()) + pad * 2
    h = max(1, bounds.height()) + pad * 2

    pm = QPixmap(w, h)
    pm.fill(QColor(BASE))
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    p.setFont(font)
    p.setPen(QColor(TEXT))  # 다크 배경 위 밝은 글자
    p.drawText(QRect(pad, pad, w - pad * 2, h - pad * 2), flags, text)
    p.end()

    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QBuffer.OpenModeFlag.WriteOnly)
    pm.save(buf, "PNG")
    return bytes(ba.data())


def _qpixmap_to_dib(pixmap) -> bytes:
    """QPixmap을 CF_DIB(BITMAPFILEHEADER 없는 BMP) 바이트로 변환.

    스크린샷은 불투명하므로 RGB32로 평탄화한다(알파 채널이 일부 앱에서 검게 처리되는 것 방지).
    클립보드 CF_DIB와 동일 포맷이라 _set_clipboard(PNG 시그니처 아니면 CF_DIB 처리)·
    _save_image_to_folder·_create_thumbnail에 그대로 넘길 수 있다.
    """
    from PyQt6.QtGui import QImage
    from PyQt6.QtCore import QBuffer, QByteArray
    img = pixmap.toImage().convertToFormat(QImage.Format.Format_RGB32)
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QBuffer.OpenModeFlag.WriteOnly)
    img.save(buf, "BMP")
    return bytes(ba.data())[14:]  # 14바이트 BITMAPFILEHEADER 제거 = CF_DIB


def _default_capture_folder() -> str:
    """캡처 기본 저장 폴더 — <사진>\\PasteFlow."""
    from PyQt6.QtCore import QStandardPaths
    pics = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.PicturesLocation)
    if not pics:
        pics = os.path.join(os.path.expanduser("~"), "Pictures")
    return os.path.join(pics, "PasteFlow")


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


# ── 시크릿 처리 ──────────────────────────────────────────────────────────────
# DPAPI(crypto.py)로 암호화해 로컬 DB에 저장하는 키 화이트리스트.
# 읽기/쓰기 시 자동 복호화/암호화. 다른 PC 복호화 불가(설계상 — 동기화 폐지됨).
_SECRET_KEYS = frozenset({
    "ocr_gemini_api_key_gateway",
    # 구글 드라이브 OAuth — client_secret과 refresh_token은 비밀이다.
    # client_id(`gdrive_client_id`)는 비밀이 아니라 평문으로 둔다(구글도 공개 취급).
    "gdrive_client_secret",
    "gdrive_refresh_token",
})

# 과거 빌드의 잔재로 DB에 남았으나 현재 코드 어디서도 참조하지 않는 고아 키.
# ocr_api_key·ocr_gemini_api_key는 평문 시크릿이라 P0(이미 노출), 나머지는 cruft 정리.
#
# ⚠ 아래 `ocr_gemini_*`(접미사 없는 옛 단일 키) 3종과 `ai_compare_model_*_{backend}` 4종은
# v1.50.0에서 삭제된 두 마이그레이션(`_migrate_split_gemini_keys`·`_migrate_compare_backend`)이
# 이전 후 지우던 것들이다. 그 마이그레이션이 사라진 지금 **아무도 안 지우므로** 여기서 purge한다.
# 특히 `ocr_gemini_api_key`는 암호화 이전 시절의 **평문 API 키**라 그냥 두면 DB에 영구히 남는다.
# (이미 마이그레이션된 DB에는 없는 키들이라 DELETE가 0행 — 무해.)
_ORPHAN_KEYS = (
    "ocr_api_key",
    "ocr_base_url",
    "hotkey_settings",
    "panel_always_on_top",
    # v1.39.0 이전의 단일 Gemini 키/모델/캐시 (backend별 슬롯으로 이전됐던 것)
    "ocr_gemini_api_key",
    "ocr_gemini_model",
    "ocr_gemini_model_cache",
    # v1.47.0 이전의 backend별 비교 모델 키 (평면 키로 이전됐던 것)
    "ai_compare_model_a_official",
    "ai_compare_model_a_gateway",
    "ai_compare_model_b_official",
    "ai_compare_model_b_gateway",
)


def _migrate_secrets(db):
    """1회 마이그레이션 — _SECRET_KEYS 평문 → DPAPI 암호화 + 고아 키 purge. Idempotent.

    - 이미 암호화된 값(`enc:v1:`)은 `crypto.protect`가 그대로 두므로 재실행 안전.
    - 빈 값/없는 키는 no-op.
    - 고아 키는 SELECT 없이 DELETE — 없으면 0행 영향, 있으면 1행 정리.
    """
    from pasteflow.crypto import protect, is_protected

    for key in _SECRET_KEYS:
        cur = db.get_setting(key, "") or ""
        if cur and not is_protected(cur):
            db.set_setting(key, protect(cur))

    with db._lock:
        placeholders = ",".join("?" * len(_ORPHAN_KEYS))
        db.conn.execute(
            f"DELETE FROM settings WHERE key IN ({placeholders})",
            _ORPHAN_KEYS,
        )
        db.conn.commit()


def _migrate_split_ocr_ai_model(db):
    """1회 마이그레이션: OCR 모델 슬롯(`ocr_model_gateway`)을 기존 AI 모델값으로 초기화.

    v1.39.0에서 OCR과 AI 질의가 모델 설정을 나눠 가지게 됐다. 기존 사용자는 모델 하나만
    갖고 있으므로 그 값을 OCR 슬롯에도 복사해 **동작 변화 0에서 시작**한다(둘 다 예전 모델).
    이후 사용자가 설정창에서 OCR 모델만 싼 것으로 바꾸면 그때부터 갈린다.

    옛 키(`ocr_gemini_model_gateway`)는 AI 질의가 계속 쓰므로 삭제하지 않는다.
    새 키가 이미 있으면 건드리지 않아 idempotent.
    """
    src = db.get_setting("ocr_gemini_model_gateway", "") or ""
    if src and not db.get_setting("ocr_model_gateway", ""):
        db.set_setting("ocr_model_gateway", src)
        print(f"[Migrate] OCR 모델 슬롯 초기화: {src}")


# v1.50.0에서 제거된 official(Google AI Studio) 백엔드가 DB에 남긴 키들.
# 크리덴셜·모델·캐시 + backend 선택값 전부. 한 번 지우면 어느 코드도 다시 쓰지 않는다.
_OFFICIAL_KEYS = (
    "ocr_gemini_api_key_official",
    "ocr_gemini_model_official",
    "ocr_gemini_model_cache_official",
    "ocr_model_official",
    "ocr_gemini_backend",
    "ocr_backend",
    "ai_compare_backend_a",
    "ai_compare_backend_b",
)


def _migrate_drop_official_backend(db):
    """1회 마이그레이션: official(Google AI Studio) 백엔드 잔재 제거. Idempotent.

    v1.50.0에서 backend 개념 자체를 없앴다(게이트웨이 단일). 남은 official 키는 어느
    코드도 읽지 않으므로 지운다.

    ⚠ **비교 모델(2·3)이 official을 가리키고 있었다면 그 모델명도 함께 비운다.** 그냥 두면
    official 전용 모델명이 게이트웨이로 날아가 404가 나는데, 사용자는 "왜 안 되지"만 보게
    된다. 비우면 그 슬롯이 '(사용 안 함)'이 되어 설정창에서 다시 고르면 된다(조용한 실패
    대신 눈에 보이는 빈칸).

    backend 키를 지우고 나면 다음 실행에선 `slot_backend`가 "" 라 아무것도 안 지운다.
    """
    cleared = []
    for slot in ("a", "b"):
        slot_backend = db.get_setting(f"ai_compare_backend_{slot}", "") or ""
        model = (db.get_setting(f"ai_compare_model_{slot}", "") or "").strip()
        if slot_backend == "official" and model:
            db.set_setting(f"ai_compare_model_{slot}", "")
            cleared.append(f"{slot}={model}")

    with db._lock:
        placeholders = ",".join("?" * len(_OFFICIAL_KEYS))
        cur = db.conn.execute(
            f"DELETE FROM settings WHERE key IN ({placeholders})",
            _OFFICIAL_KEYS,
        )
        db.conn.commit()
        removed = cur.rowcount

    if removed > 0 or cleared:
        msg = f"[Migrate] official 백엔드 제거: 키 {removed}개 삭제"
        if cleared:
            msg += f", 비교 모델 초기화({', '.join(cleared)})"
        print(msg)


# ─────────────────────────────────────────────────────────────────────────────


class _SignalBridge(QObject):
    """훅 스레드 → 메인 스레드 시그널 전달"""
    paste_happened     = pyqtSignal()
    new_item_saved     = pyqtSignal(object)  # 모든 복사 경로: DB + 큐 추가
    copy_toast         = pyqtSignal(object, int)  # 복사 알림 토스트 (item, 큐 개수)
    panel_toggle       = pyqtSignal()        # 패널 토글 단축키 (훅 스레드 → 메인)
    paste_queue_done   = pyqtSignal()        # 큐 소진 (붙여넣기 HUD fade-out용)
    plain_paste        = pyqtSignal()        # 일반 Ctrl+V 감지 (훅 스레드 → 메인: 큐 clear + UI 갱신)
    ocr_requested      = pyqtSignal()        # 훅 스레드 → 메인: OCR 오버레이 띄우기
    ocr_done           = pyqtSignal(str)     # 워커 스레드 → 메인: OCR 결과 텍스트
    ocr_error          = pyqtSignal(str)     # 워커 스레드 → 메인: 에러 메시지
    ocr_fallback       = pyqtSignal(str, str)  # 워커 스레드 → 메인: (실패 모델, 폴백 모델) 자동 폴백 알림
    image_to_path      = pyqtSignal()        # 훅 스레드 → 메인: 클립보드 이미지를 경로 텍스트로 교체 후 Ctrl+V
    seq_image_to_path  = pyqtSignal()        # 훅 스레드 → 메인: 큐에서 다음 항목을 꺼내 이미지면 경로 텍스트로 순차 붙여넣기
    pin_image          = pyqtSignal()        # 훅 스레드 → 메인: 클립보드 이미지를 화면에 핀(떠 있는 창)으로 띄우기
    seq_pin            = pyqtSignal()        # 훅 스레드 → 메인: 큐에서 다음 항목을 꺼내 화면에 순차 핀
    capture_requested  = pyqtSignal()        # 훅 스레드 → 메인: 영역 캡처 오버레이 띄우기
    ask_ai             = pyqtSignal()        # 훅 스레드 → 메인: AI 자유질문 입력창 띄우기
    ai_turn_done       = pyqtSignal(object)  # AI 워커 스레드 → 메인: 대화 턴 결과 dict(팝업·답변·히스토리)
    ai_error           = pyqtSignal(object)  # AI 워커 스레드 → 메인: 에러 dict({popup, msg})
    ai_searching       = pyqtSignal(str)     # AI 워커 스레드 → 메인: 웹 검색 시작(검색어) / 종료("")
    ai_prefetch_done   = pyqtSignal(object)  # 공유 검색 워커 → 메인: 검색 자료 dict(jobs·facts·available)


class PasteFlowApp:
    """PasteFlow 앱 오케스트레이션"""

    def __init__(self):
        self.app = QApplication(sys.argv)
        self.app.setQuitOnLastWindowClosed(False)
        # 다크 테마용 툴팁 스타일 (시스템 기본은 다크 배경 위 어두운 글자라 가독성 0)
        self.app.setStyleSheet(
            f"QToolTip {{"
            f" background-color: {COLORS['mantle']};"
            f" color: {COLORS['text']};"
            f" border: 1px solid {COLORS['surface1']};"
            f" padding: 4px 6px;"
            f"}}"
        )

        # 스레드 안전 시그널 브릿지
        self._bridge = _SignalBridge()
        self._bridge.paste_happened.connect(self._update_paste_ui)
        self._bridge.new_item_saved.connect(self._on_new_item_ui)
        self._bridge.copy_toast.connect(self._on_copy_toast)
        self._bridge.panel_toggle.connect(self._toggle_panel)
        self._bridge.paste_queue_done.connect(self._on_paste_queue_done)
        self._bridge.ocr_requested.connect(self._on_ocr_requested)
        self._bridge.ocr_done.connect(self._on_ocr_done)
        self._bridge.ocr_error.connect(self._on_ocr_error)
        self._bridge.ocr_fallback.connect(self._on_ocr_fallback)
        self._bridge.plain_paste.connect(self._on_plain_paste)
        self._bridge.image_to_path.connect(self._on_image_to_path_hotkey)
        self._bridge.seq_image_to_path.connect(self._on_seq_image_to_path_hotkey)
        self._bridge.pin_image.connect(self._on_pin_hotkey)
        self._bridge.seq_pin.connect(self._on_seq_pin_hotkey)
        self._bridge.capture_requested.connect(self._on_capture_requested)
        self._bridge.ask_ai.connect(self._on_ask_ai_hotkey)
        self._bridge.ai_turn_done.connect(self._on_ai_turn_done)
        self._bridge.ai_error.connect(self._on_ai_error)
        self._bridge.ai_searching.connect(self._on_ai_searching)
        self._bridge.ai_prefetch_done.connect(self._on_ai_prefetch_done)

        self._saved_panel_geometry = None
        self._image_preview_windows: dict[int, ImagePreviewPopup] = {}
        self._text_preview_windows: dict[int, TextPreviewPopup] = {}

        # 코어 모듈
        db_path = _resolve_db_path()
        self.db = Database(db_path)
        # 주석 편집기 마지막 값(두께·글자·번호 크기)을 DB에서 복원 + 변경 시 저장 콜백 등록
        # (재시작 후에도 유지 — 클래스 변수라 세션 중 이미지 간 공유는 그대로).
        _EditorMixin.load_last_values(
            self.db.get_setting("annot_last_width", ""),
            self.db.get_setting("annot_last_font_size", ""),
            self.db.get_setting("annot_last_badge_size", ""),
        )
        _EditorMixin._persist_cb = self._save_annot_last_values
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
            on_plain_paste=self._bridge.plain_paste.emit,
            on_image_to_path=self._bridge.image_to_path.emit,
            on_seq_image_to_path=self._bridge.seq_image_to_path.emit,
            on_pin_image=self._bridge.pin_image.emit,
            on_seq_pin=self._bridge.seq_pin.emit,
            on_capture=self._bridge.capture_requested.emit,
            on_ask_ai=self._bridge.ask_ai.emit,
        )
        self.hotkey_manager = HotkeyManager()

        # UI (패널이 기본 UI — 미니창 없음)
        self.panel = ClipboardPanel()
        # 네이티브 윈도우 핸들을 미리 생성 (show/hide 없이, 깜빡임 방지)
        self.panel.winId()
        self.tray = TrayIcon()

        # 순차 붙여넣기 진행 HUD (단일 인스턴스 재사용)
        self.paste_hud = PasteHud()
        self.paste_hud.winId()
        self.paste_hud.cancel_requested.connect(self._on_cancel_paste_queue)

        # OCR 오버레이
        from pasteflow.ui.ocr_overlay import OcrOverlay
        self._ocr_overlay = OcrOverlay()
        self._ocr_overlay.region_captured.connect(self._on_ocr_region_captured)
        # cancelled 시그널은 내부 close()로 충분 — 별도 콜백 불필요

        # 영역 캡처 오버레이 (마그네틱 — 커서 아래 요소 클릭 캡처. region_captured → _on_capture_region 무수정)
        from pasteflow.ui.capture_overlay import CaptureOverlay
        self._capture_overlay = CaptureOverlay()
        self._capture_overlay.region_captured.connect(self._on_capture_region)
        # cancelled는 오버레이 내부 정리로 충분 — 별도 콜백 불필요
        # 마지막 캡처 위치(논리 전역) — 그 직후 핀(Alt+F3)이 캡처 자리에 그대로 덮게 함.
        # 외부 복사가 들어오면 무효화(_on_new_clipboard_item)해 "방금 캡처한 그 이미지"일 때만 적용.
        self._pin_place_rect: QRect | None = None

        # OCR은 호출마다 새 스레드 — asyncio.run()을 재사용 스레드에서 반복 호출 시
        # WinRT 콜백 상태가 누적돼 두 번째 호출부터 빈 결과를 반환하는 문제 방지

        # 패널 열기 전 포커스된 윈도우 추적 (SetWinEventHook으로 연속 갱신)
        self._prev_foreground_hwnd = None
        self._fg_hook = None
        self._fg_proc = None

        # 이미지→경로(Ctrl+Shift+P) 임시 PNG 캐시: (item_id, saved_path) 또는 None.
        # 같은 최신 이미지에 반복 실행 시 디스크 재저장을 피하기 위함.
        self._img_to_path_cache = None

        # 직전 이미지→경로(Ctrl+Shift+P·Ctrl+Shift+[)로 클립보드에 올린 임시 PNG 경로.
        # Alt+F3 핀이 이 경로가 클립보드에 남아 있으면 경로 문자열이 아니라 원본 이미지를 핀한다.
        self._last_pasted_image_path: str | None = None

        # 구글 드라이브 액세스 토큰 캐시(만료 2분 전 자동 갱신 + 락).
        # 자격증명을 값이 아니라 콜러블로 넘긴다 — 설정창에서 재연결하면 다음 호출에 자동 반영.
        self._gdrive_tokens = gdrive.TokenCache(self._gdrive_creds)

        # DB에서 설정 로드 및 적용
        self._apply_settings_from_db()

        # 시그널 연결
        self._connect_signals()

    def _connect_signals(self):
        """모든 시그널 연결"""
        self.tray.quit_requested.connect(self._quit)
        self.tray.panel_toggle_requested.connect(self._toggle_panel)
        self.tray.ai_history_requested.connect(self._on_ai_history_requested)
        self.tray.settings_requested.connect(self._open_settings)

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
        self.panel.ocr_item_requested.connect(self._on_ocr_image_by_id)
        self.panel.ai_query_requested.connect(self._on_ai_query_requested)
        self.panel.copy_image_as_path_requested.connect(self._on_copy_image_as_path)
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

        img2path_hotkey = self.db.get_setting("hotkey_image_to_path", "ctrl+shift+p")
        self.interceptor.set_image_to_path_hotkey(img2path_hotkey)

        seq_img2path_hotkey = self.db.get_setting("hotkey_seq_image_to_path", "ctrl+shift+[")
        self.interceptor.set_seq_image_to_path_hotkey(seq_img2path_hotkey)

        pin_hotkey = self.db.get_setting("hotkey_pin_image", "alt+f3")
        self.interceptor.set_pin_hotkey(pin_hotkey)

        seq_pin_hotkey = self.db.get_setting("hotkey_seq_pin", "alt+shift+f3")
        self.interceptor.set_seq_pin_hotkey(seq_pin_hotkey)

        capture_hotkey = self.db.get_setting("hotkey_capture", "alt+f2")
        self.interceptor.set_capture_hotkey(capture_hotkey)

        ask_ai_hotkey = self.db.get_setting("hotkey_ask_ai", "alt+`")
        self.interceptor.set_ask_ai_hotkey(ask_ai_hotkey)


    def _persist_clipboard_item(self, item: ClipboardItem) -> ClipboardItem:
        """DB 저장 + 큐 추가 + 패널 갱신 시그널. 복사 토스트는 띄우지 않는다.

        OCR 결과처럼 자체 알림이 있는 경로가 이 메서드를 직접 호출해
        복사 토스트 중복을 피한다.
        """
        saved = self.db.save_item(item)
        self.queue.add_item(saved)
        self._bridge.new_item_saved.emit(saved)
        return saved

    def _on_new_clipboard_item(self, item: ClipboardItem):
        """클립보드 모니터 콜백 (실제 Ctrl+C) — 백그라운드 스레드. 저장 + 복사 토스트."""
        # 외부 복사가 들어오면 클립보드가 더 이상 "방금 캡처한 이미지"가 아니므로, 핀을
        # 캡처 자리에 덮는 좌표를 무효화한다(이 콜백은 self-triggered 캡처 경로엔 안 옴).
        self._pin_place_rect = None
        saved = self._persist_clipboard_item(item)
        if self._notify_on_copy:
            _, total = self.queue.get_status()
            self._bridge.copy_toast.emit(saved, total)

    def _on_copy_toast(self, item: ClipboardItem, queue_count: int):
        """메인 스레드: 복사 알림 토스트 표시"""
        from pasteflow.ui.toast import show_copy_toast
        show_copy_toast(item, queue_count)

    def _on_duplicate_clipboard_item(self):
        """중복 클립보드 콜백 — 무시 (중복은 큐/DB에 추가하지 않음)."""
        pass

    def _on_new_item_ui(self, saved: ClipboardItem):
        """메인 스레드에서 UI 갱신 — 패널 갱신 + 진행 HUD 정리"""
        if self.panel.isVisible():
            self._refresh_panel()
        # 새 복사가 발생하면 진행 중이던 붙여넣기 시퀀스는 무효 — HUD 정리
        self.paste_hud.finish()

    def _on_paste_from_hook(self, item: ClipboardItem):
        """붙여넣기 콜백 — 훅 스레드에서 호출됨 → 시그널로 메인 스레드 전달"""
        self._bridge.paste_happened.emit()
        pointer, total = self.queue.get_status()
        if pointer >= total and total > 0:
            self._bridge.paste_queue_done.emit()

    def _clear_queue_ui(self):
        """큐를 비우고 tray·패널 하이라이트를 초기화 (큐 포기/클리어 공통 경로).

        '큐를 언제 비울지' 정책의 '포기/클리어' 범주가 공유: 일반 Ctrl+V, 큐 소진 완료,
        HUD ✕ 취소, 우클릭 큐 해제, Ctrl+Shift+P 단발 경로 붙여넣기.
        """
        self.queue.clear()
        self.tray.update_queue_status(0, 0)
        self.panel.update_queue_highlight(0, 0, [])

    def _on_paste_queue_done(self):
        """큐 소진 완료 — 큐/포인터를 클리어하고 진행 HUD를 잠시 뒤 페이드.

        소진 후에도 큐가 남아 있으면 패널이 항목을 '큐에 들어있음'으로 계속 표시해
        우클릭 메뉴가 '큐 해제'로 뜨는 찌꺼기가 생긴다 → 소진 시 클리어로 정리.
        """
        self._clear_queue_ui()
        self.paste_hud.finish()

    def _on_cancel_paste_queue(self):
        """HUD ✕ 클릭 — 남은 붙여넣기 취소: 큐 비우기 + 표시 초기화 + HUD 즉시 닫기"""
        self._clear_queue_ui()
        self.paste_hud.dismiss()

    def _on_auto_close_changed(self, value: bool):
        self.db.set_setting("panel_auto_close", "1" if value else "0")

    # ── OCR ──

    def _on_ocr_requested(self):
        """메인 스레드: OCR 오버레이 시작"""
        self._ocr_overlay.start()

    def _on_ocr_region_captured(self, pixmap):
        """메인 스레드: 선택 영역 픽맵 → 워커 스레드에서 OCR"""
        # QPixmap/QBuffer는 메인 스레드에서만 안전 — PNG 변환을 여기서 완료
        buf = QBuffer()
        buf.open(QBuffer.OpenModeFlag.WriteOnly)
        pixmap.save(buf, "PNG")
        png_bytes = bytes(buf.data())
        buf.close()
        self._start_ocr_worker(png_bytes)

    # ── 영역 캡처 (Alt+F2) ──

    def _on_capture_requested(self):
        """메인 스레드: 영역 캡처 오버레이 시작 (마그네틱 CaptureOverlay)"""
        self._capture_overlay.start()

    def _on_capture_region(self, pixmap, rect):
        """메인 스레드: 선택 영역 픽맵 → 클립보드(DIB) + 히스토리·큐 + 파일 저장 + 토스트.

        클립보드·히스토리 처리는 OCR 결과 경로와 동일(_set_clipboard로 모니터 재감지 방지 후
        _persist_clipboard_item). 이미지는 붙여넣기 호환성이 가장 넓은 CF_DIB로 넣는다.
        rect(캡처한 논리 전역 사각형)를 기억해 직후 핀(Alt+F3)이 그 자리에 그대로 덮게 한다.
        """
        from pasteflow.ui.toast import ToastNotification

        self._pin_place_rect = QRect(rect) if rect is not None else None
        dib = _qpixmap_to_dib(pixmap)
        item = ClipboardItem(
            content_type="image",
            image_data=dib,
            thumbnail=self.monitor._create_thumbnail(dib),
        )
        self.interceptor._set_clipboard(item)
        self._persist_clipboard_item(item)

        # 지정 폴더에 PNG 저장 (없으면 생성, 미설정 시 <사진>\PasteFlow)
        folder = self.db.get_setting("capture_save_folder", "") or _default_capture_folder()
        saved_path = None
        try:
            os.makedirs(folder, exist_ok=True)
            saved_path = _save_image_to_folder(dib, folder)
        except Exception as e:
            ToastNotification(f"캡처 파일 저장 실패 — {e}", icon="📷")

        if saved_path:
            ToastNotification(
                f"캡처됨: {os.path.basename(saved_path)}",
                icon="", image_path=saved_path)
        else:
            ToastNotification("캡처를 클립보드에 복사했습니다", icon="📷")

    def _resolve_gemini_cfg(self, purpose: str = "ai",
                            model_override: str | None = None) -> tuple[str, str, str]:
        """게이트웨이 설정을 해석해 (api_key, base_url, model) 반환.

        OCR 워커와 AI 질의 워커가 공유하되 **모델만 용도별로 갈린다**(v1.39.0):
        - purpose="ocr" → `ocr_model_gateway`   (비전 가능 모델만 고를 수 있는 슬롯)
        - purpose="ai"  → `ocr_gemini_model_gateway` (전 모델. 기존 키 승계)

        분리 이유: 두 용도가 한 모델을 공유하면 AI 답변용으로 고른 비싼 모델(claude-opus 등)이
        OCR에도 그대로 쓰여 텍스트 추출 한 번에 과금이 커지고, 반대로 텍스트 전용 모델
        (solar-pro2 등)을 AI용으로 고르면 OCR이 400으로 깨진다.

        model_override가 주어지면 설정의 모델 슬롯 대신 그 모델을 쓴다(여러 모델 비교).
        DB 접근은 _lock으로 직렬화되어 워커 스레드에서 호출해도 안전.
        """
        base_url_saved = self.db.get_setting("ocr_gemini_base_url", "")

        if model_override is not None:
            model = model_override
        else:
            model_key = (
                "ocr_model_gateway" if purpose == "ocr" else "ocr_gemini_model_gateway"
            )
            model = self.db.get_setting(model_key, "")
        if model_override is None and purpose == "ocr" and not model:
            # OCR 슬롯이 아직 비었으면(마이그레이션 전/초기화됨) AI 모델을 그대로 쓴다.
            # 빈 문자열이면 OcrEngine이 기본 모델로 폴백하므로 여기서 강제하지 않는다.
            model = self.db.get_setting("ocr_gemini_model_gateway", "")

        return (self._get_secret("ocr_gemini_api_key_gateway"), base_url_saved, model)

    def _fetch_all_ai_models(self) -> list[str]:
        """AI 질문창 모델 드롭다운의 ↻가 백그라운드 스레드에서 호출 — 전체 모델 목록을
        반환한다(네트워크 호출, DB 접근은 _lock으로 스레드 안전).
        """
        from pasteflow.ocr_engine import OcrEngine
        api_key, base_url, _ = self._resolve_gemini_cfg("ai")
        return OcrEngine.list_gemini_models(api_key, base_url)

    def _start_ai_worker(self, question: str, context_text: str,
                         images: list[bytes] | None = None,
                         model: str | None = None):
        """AI 질의(첫 턴) — 답변창을 '생각 중' 상태로 즉시 띄우고 워커에 넘긴다.

        여러 모델 비교(`_start_compare_query`)와 동일한 흐름이다(v1.49.3 — 예전엔 커서
        진행 칩만 뜨다 완성된 창이 나중에 한 번에 나타났는데, 사용자가 비교 모드처럼
        "창이 먼저 뜨고 답이 채워지는" 쪽을 선호해 통일했다). 창을 `pending_question`으로
        먼저 열고 `_run_ai_turn`에 그 창을 넘기면, 답이 왔을 때 `_on_ai_turn_done`이
        `resolve_pending`으로 제자리에서 채운다.

        **크기도 비교 창처럼 고정**한다(v1.49.4 — 사용자 요청): `center=True`(자동
        크기산정)를 쓰면 '생각 중' 짧은 문구 기준으로 작게 뜬 창이 답 도착 시
        `_resize_to_content()`로 갑자기 커지는 게 어색해, 비교 창의 `place_rect`(고정
        사각형+wrap+세로스크롤) 방식을 그대로 가져와 처음부터 최종 크기로 뜬다. 짧은
        답변은 빈 공간이 남는 트레이드오프가 있지만(비교 창도 동일), '작았다가 갑자기
        커지는' 점프보다 사용자가 이쪽을 선호했다.

        첫 user 턴의 컨텐츠는 `build_ask_prompt`로 컨텍스트를 임베드한 프롬프트이고, 화면
        표시용 원문 질문은 `display`에 따로 담는다(트랜스크립트 렌더는 display를 쓴다).
        이후 후속 질문은 `_on_ai_followup`이 같은 히스토리에 쌓아 재질의한다.
        `images`는 여러 장 첨부 가능(첫 user 턴에만 멀티모달로 실림).
        `model`은 AI 질문창의 모델 드롭다운에서 고른 값(v1.49.1) — None이면 기본(모델 1)
        사용, `_run_ai_turn`이 그대로 override로 넘긴다.
        """
        from pasteflow.ocr_engine import build_ask_prompt
        from PyQt6.QtWidgets import QApplication
        prompt = build_ask_prompt(question, context_text)
        conversation = [{"role": "user", "content": prompt, "display": question}]

        anchor = getattr(self, "_ai_anchor", None) or self.panel.geometry()
        screen = QApplication.screenAt(anchor.center()) or QApplication.primaryScreen()
        avail = screen.availableGeometry()
        margin_v = max(24, int(avail.height() * 0.08))  # 비교 창과 동일한 상하 여백 비율
        box_h = avail.height() - margin_v * 2
        box_w = min(700, avail.width() - 80)
        place_rect = QRect(
            avail.left() + (avail.width() - box_w) // 2, avail.top() + margin_v,
            max(280, box_w), box_h)

        item = ClipboardItem(content_type="text", text_content="", preview_text=question[:200])
        popup = TextPreviewPopup.open_new(
            item, anchor, editable=False, markdown=True,
            pending_question=question, place_rect=place_rect)
        popup.copy_requested.connect(self._on_copy_item)
        popup.copy_as_image_requested.connect(self._on_answer_image_copy)
        popup.copy_text_requested.connect(self._on_copy_selected_text)
        popup.followup_requested.connect(
            lambda text, p=popup: self._on_ai_followup(p, text))

        self._run_ai_turn(conversation, images, popup=popup, model=model)

    def _run_ai_turn(self, conversation: list, images: list[bytes] | None, popup,
                     model: str | None = None, tools_enabled: bool = True):
        """대화 한 턴을 백그라운드에서 질의한다(첫 질문·후속 질문·비교 질의 공용).

        `tools_enabled=False`는 **공유 검색 모드**(여러 모델 비교) — 검색은 앞단에서 이미
        한 번 끝났고 그 자료가 프롬프트에 주입돼 있으므로, 모델이 또 검색하지 못하게 도구를
        뗀다. 그래야 세 모델이 같은 자료를 본다.

        OCR과 동일한 게이트웨이 배관(`OcrEngine.ask_messages`)을 재사용한다. OCR 엔진
        설정과 무관하게 항상 gemini 경로. `images`(여러 장 가능)는 첫 user 턴에만 실린다.
        `model`이 주어지면 설정 모델 대신 그 모델로 질의한다(비교 창은 자기 모델로 이어감).
        `popup`은 항상 이미 떠 있는(pending 또는 라이브) 답변창이다(v1.49.3 — 모든 호출자가
        먼저 창을 연다) — 그 창이 자체 '생각 중'을 표시하므로 여기서 별도 진행 칩을 띄우지
        않는다. 결과/에러는 `ai_turn_done`/`ai_error` 시그널로 메인 스레드에 통지(둘 다 팝업
        참조를 실어 여러 워커가 병렬로 돌아도 서로 섞이지 않는다).
        """
        # 엔진에는 role/content만 넘긴다(display는 표시 전용 — 트랜스크립트 렌더에서만 사용).
        messages = [{"role": t["role"], "content": t["content"]} for t in conversation]

        def _run():
            try:
                import time
                from pasteflow.ocr_engine import OcrEngine
                api_key, base_url, model_id = self._resolve_gemini_cfg(
                    model_override=model)
                system_prompt = self.db.get_setting("ai_system_prompt", "")
                # 드라이브 토큰(연결 안 했으면 "") — 있으면 검색 도구에 드라이브가 함께 실린다.
                # 만료 2분 전 자동 갱신, 갱신 실패도 ""라 AI 질의 자체는 절대 안 깨진다.
                engine = OcrEngine(kind="gemini", api_key=api_key, base_url=base_url,
                                   model=model_id, system_prompt=system_prompt,
                                   gdrive_token=self._gdrive_tokens.access_token())
                # 웹 검색이 끼면 응답이 2~3배 느려진다(LLM 왕복 2회 + 검색 2~4초).
                # "멈춘 게 아니라 검색 중"임을 진행 칩에 보여준다(첫 턴 한정 — 후속·비교
                # 창은 팝업 자체가 '생각 중'을 표시하므로 슬롯이 무시한다).
                engine.on_tool_progress = self._bridge.ai_searching.emit
                t0 = time.monotonic()
                answer = engine.ask_messages(messages, images=images,
                                             tools_enabled=tools_enabled)
                elapsed = time.monotonic() - t0  # 이 답변에 걸린 실제 시간(답변창 상단 표시)
                if engine.last_fallback_from and engine.last_used_model:
                    self._bridge.ocr_fallback.emit(engine.last_fallback_from, engine.last_used_model)
                new_conv = conversation + [{"role": "assistant", "content": answer}]
                self._bridge.ai_turn_done.emit({
                    "popup": popup, "answer": answer,
                    "conversation": new_conv, "images": images,
                    "model": model, "elapsed": elapsed,
                })
            except Exception as e:
                self._bridge.ai_error.emit({"popup": popup, "msg": str(e)})

        threading.Thread(target=_run, daemon=True, name="ai-worker").start()

    def _resolve_compare_models(self) -> list[str]:
        """여러 모델 비교에 쓸 모델명 목록 반환.

        [기본 AI 모델(모델 1), 비교 A(모델 2), 비교 B(모델 3)] 중 비어있지 않은 것을 순서대로,
        중복 제거해 반환한다. 2개 미만이면 비교가 무의미하므로 호출부가 그때 체크박스를 숨긴다.
        """
        raw = [
            self.db.get_setting("ocr_gemini_model_gateway", ""),
            self.db.get_setting("ai_compare_model_a", ""),
            self.db.get_setting("ai_compare_model_b", ""),
        ]
        models: list[str] = []
        for model in raw:
            model = (model or "").strip()
            if model and model not in models:
                models.append(model)
        return models

    def _start_compare_query(self, question: str, context_text: str,
                             images: list[bytes] | None, models: list[str]):
        """질문을 여러 모델로 동시에 질의하고 답변창을 모니터 N등분 타일로 나란히 띄운다.

        각 창은 '생각 중' 펜딩 상태로 먼저 뜨고(어느 모델이 아직인지 보임) 각자 답이 도착하면
        채워진다. 창마다 자기 모델을 기억해 '이어서 질문'도 그 모델로 이어간다.

        **검색은 앞단에서 한 번만 한다**(`_start_shared_search` → `web_search.prefetch`).
        모델별로 각자 검색하게 두면 같은 질문에도 서로 다른 수치가 나와, 비교가 "누가 더 잘
        정리하나"가 아니라 "각자 뭘 찾았나"로 오염된다(2026-07-11 실측: 같은 날씨 질문에
        36/25 · 36/24 · "인터넷 없어서 모름" 3인3색 → 공유 후 셋 다 36/24 일치).
        """
        from pasteflow.ocr_engine import build_ask_prompt
        from pasteflow.ui.text_preview import TextPreviewPopup
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtCore import QRect
        from PyQt6.QtGui import QCursor

        anchor = getattr(self, "_ai_anchor", None)
        pt = anchor.topLeft() if anchor else QCursor.pos()
        screen = QApplication.screenAt(pt) or QApplication.primaryScreen()
        avail = screen.availableGeometry()
        n = len(models)
        gap = 12
        margin_v = max(24, int(avail.height() * 0.08))
        tile_h = avail.height() - margin_v * 2
        col_w = max(280, (avail.width() - gap * (n + 1)) // n)

        prompt = build_ask_prompt(question, context_text)
        # 비교 그룹이 공유하는 검색 자료 캐시(질문 → 검색 결과). 후속 질문(Q2+)도 이 캐시를
        # 타야 "그럼 모레는?" 한 마디에 모델들이 각자 검색해 수치가 갈리는 것을 막는다.
        cache: dict[str, str] = {}
        jobs = []
        for i, model in enumerate(models):
            x = avail.left() + gap + i * (col_w + gap)
            rect = QRect(x, avail.top() + margin_v, col_w, tile_h)
            item = ClipboardItem(content_type="text", text_content="",
                                 preview_text=question[:200])
            popup = TextPreviewPopup.open_new(
                item, QRect(pt.x(), pt.y(), 1, 1), editable=False, markdown=True,
                model_title=model, pending_question=question, place_rect=rect)
            popup.copy_requested.connect(self._on_copy_item)
            popup.copy_as_image_requested.connect(self._on_answer_image_copy)
            popup.copy_text_requested.connect(self._on_copy_selected_text)
            popup.followup_requested.connect(
                lambda text, p=popup: self._on_ai_followup(p, text))
            popup._shared_cache = cache   # 후속 질문도 공유 검색 경로로
            conversation = [{"role": "user", "content": prompt, "display": question}]
            jobs.append({"popup": popup, "model": model, "conversation": conversation})

        # 한 번 검색해 같은 자료를 전 모델에게 물린다(정리력 비교).
        self._start_shared_search(question, jobs, images, cache)

    def _start_shared_search(self, question: str, jobs: list[dict],
                             images: list[bytes] | None, cache: dict):
        """비교 질의의 웹 검색을 **앞단에서 한 번만** 수행하고 그 자료로 전 모델을 질의한다.

        각 모델이 스스로 검색하면(v1.45.0까지의 동작) 같은 질문에도 서로 다른 자료를 찾아와
        수치가 갈리고, 비교가 "누가 더 잘 정리하나"가 아니라 "각자 뭘 찾았나"가 되어 버린다
        (2026-07-11 실측: 같은 날씨 질문에 36/25 · 36/24+출처불신 · "인터넷 없어서 모름" 3인3색).
        검색 비용도 모델 수만큼 든다. 그래서 `web_search.prefetch`(nano 심부름꾼)가 **검색
        필요 여부 판단과 검색을 한 콜로** 끝내고, 그 결과를 프롬프트에 주입한 뒤 모델 도구는
        끈다(`tools_enabled=False` — 안 끄면 모델이 또 검색해 공유가 무의미해진다).

        심부름꾼을 못 쓰면(공식 백엔드·nano 권한 없음·네트워크) `available=False`로 돌아오고,
        그때는 **현행 동작으로 열화**한다(각 모델이 자기 도구로 검색). "못 썼다"를 "검색
        불필요"로 읽으면 실시간 질문에 도구도 없이 답하게 되므로 둘을 구분한다.
        """
        def _run():
            facts = cache.get(question)
            available = True
            if facts is None:
                api_key, base_url, _ = self._resolve_gemini_cfg()
                res = web_search.prefetch(question, api_key=api_key, base_url=base_url,
                                          images=images,
                                          gdrive_token=self._gdrive_tokens.access_token())
                available, facts = res.available, res.facts
                if available:
                    cache[question] = facts   # ""(검색 불필요)도 캐시 — 재판정 비용 절약
            self._bridge.ai_prefetch_done.emit({
                "jobs": jobs, "facts": facts, "available": available, "images": images,
            })

        threading.Thread(target=_run, daemon=True, name="ai-prefetch").start()

    def _on_ai_prefetch_done(self, payload: dict):
        """공유 검색이 끝났다 — 같은 자료를 각 모델 프롬프트에 주입해 병렬 질의를 띄운다.

        검색 자료는 **그 턴의 user 메시지**에 끼운다(첫 턴이든 후속 턴이든 방금 던진 질문에
        대한 자료이므로). 자료가 실린 대화가 그대로 팝업 히스토리에 남아 다음 턴에서도 모델이
        무엇을 근거로 답했는지 기억한다.
        """
        from pasteflow.ocr_engine import build_facts_prompt

        facts, available = payload["facts"], payload["available"]
        images = payload["images"]
        for job in payload["jobs"]:
            conv = [dict(t) for t in job["conversation"]]
            if facts:
                conv[-1]["content"] = build_facts_prompt(conv[-1]["content"], facts)
            # 심부름꾼이 돌았으면 모델 도구를 뗀다(공유 자료만 보게). 못 썼으면 현행대로
            # 모델이 자기 도구로 검색하게 둔다 — 안 그러면 실시간 질문에 답할 길이 사라진다.
            self._run_ai_turn(conv, images, popup=job["popup"], model=job["model"],
                              tools_enabled=not available)

    def _start_cursor_progress(self, prefix: str, icon: str, anchor):
        """진행 칩 — 지속형 토스트(클릭 통과) + 0.5초 간격 경과시간 갱신.

        OCR·AI 질의가 공유. anchor(QPoint)로 **커서가 있는 모니터**를 고른 뒤 그 모니터
        정중앙에 표시한다(예측 가능·가장자리 잘림 없음). 예전엔 주 모니터 우하단 고정이라
        보조 모니터 작업 시 시선을 돌려야 했음.
        """
        import time
        from pasteflow.ui.toast import ToastNotification

        self._stop_cursor_progress()  # 중복 대비 이전 진행 칩 정리
        self._progress_prefix = prefix
        self._progress_toast = ToastNotification(
            f"{prefix} 0:00", icon=icon, duration_ms=0, anchor=anchor, center=True)
        self._progress_start = time.monotonic()
        self._progress_timer = QTimer()
        self._progress_timer.setInterval(500)
        self._progress_timer.timeout.connect(self._tick_cursor_progress)
        self._progress_timer.start()

    def _on_ai_searching(self, query: str):
        """AI가 웹 검색을 시작(query)/종료("")할 때 진행 칩 문구를 바꾼다.

        v1.49.3부터 모든 AI 질의가 답변창을 먼저 띄우고 그 창의 '생각 중' 탭으로 진행을
        표시하므로(`_start_ai_worker` 참고), 이 커서 진행 칩은 이제 AI 경로에서는 절대 뜨지
        않는다(`_progress_toast`가 항상 None) — OCR(`_start_cursor_progress` 다른 호출부)이
        떠 있을 때만 이 시그널이 온다면 조용히 무시하면 그만이라 가드는 그대로 둔다.
        """
        if getattr(self, "_progress_toast", None) is None:
            return
        self._progress_prefix = f"웹 검색: {query[:18]}…" if query else "AI 생각 중…"
        self._tick_cursor_progress()  # 다음 0.5초 틱을 기다리지 않고 즉시 반영

    def _tick_cursor_progress(self):
        import time

        toast = getattr(self, "_progress_toast", None)
        if toast is None:
            return
        elapsed = int(time.monotonic() - self._progress_start)
        m, s = divmod(elapsed, 60)
        # 점(●··) 애니메이션은 제거 — ●(넓음)··(좁음) 폭 차이로 매 틱 칩 폭이 바뀌어
        # 앵커 재중심(_place_anchored)이 좌우로 흔들렸다. 경과시간만으로 "작업 중"이 충분히 보인다.
        toast.set_message(f"{self._progress_prefix} {m}:{s:02d}")

    def _stop_cursor_progress(self):
        """진행 칩·타이머 즉시 정리 (idempotent — 결과/에러 도착 시 호출)."""
        timer = getattr(self, "_progress_timer", None)
        if timer is not None:
            timer.stop()
            self._progress_timer = None
        toast = getattr(self, "_progress_toast", None)
        if toast is not None:
            toast.dismiss()
            self._progress_toast = None

    def _finish_cursor_progress(self, message: str, hold_ms: int = 1500) -> bool:
        """진행 칩을 결과 메시지(✓ 등)로 전환 후 잠시 뒤 fade. 같은 칩 재사용으로 부드럽게 전환.

        진행 칩이 없으면(이미 정리됨) False 반환 — 호출자가 일반 토스트로 폴백할 수 있다.
        """
        timer = getattr(self, "_progress_timer", None)
        if timer is not None:
            timer.stop()
            self._progress_timer = None
        toast = getattr(self, "_progress_toast", None)
        if toast is None:
            return False
        toast.set_message(message)
        self._progress_toast = None  # 더는 진행 칩으로 추적 안 함 (fade 예약됨)
        QTimer.singleShot(hold_ms, toast.dismiss)
        return True

    def _start_ocr_worker(self, png_bytes: bytes):
        """공용 OCR 워커 — PNG bytes를 받아 백그라운드에서 OCR 수행.

        영역 캡처 OCR과 이미지 항목 OCR이 공유. 결과/에러는
        `_bridge.ocr_done` / `_bridge.ocr_error` 시그널로 메인 스레드에 통지.
        """
        import io
        from PIL import Image
        from PyQt6.QtGui import QCursor

        # 진행 칩을 OCR 트리거 시점 커서가 있는 모니터 정중앙에 띄운다(영역 선택 직후 / 우클릭 위치).
        self._start_cursor_progress("인식 중…", "🔤", QCursor.pos())

        def _run():
            # 호출마다 COM 초기화/해제 — asyncio/WinRT 상태 오염 방지
            ctypes.windll.ole32.CoInitializeEx(None, 0)
            try:
                pil_img = Image.open(io.BytesIO(png_bytes))

                from pasteflow.ocr_engine import OcrEngine
                # OCR은 별도 엔진 선택 없이 항상 AI(Gemini 공식 / Mindlogic 게이트웨이) API로 처리.
                # (WinRT 엔진 제거 — 설정에서 엔진/언어 선택 UI도 삭제됨. AI 답변과 동일 배관 재사용.)
                api_key, base_url, model = self._resolve_gemini_cfg("ocr")
                engine = OcrEngine(kind="gemini", api_key=api_key, base_url=base_url, model=model)
                text = engine.recognize(pil_img)
                if engine.last_fallback_from and engine.last_used_model:
                    self._bridge.ocr_fallback.emit(engine.last_fallback_from, engine.last_used_model)
                self._bridge.ocr_done.emit(text)
            except Exception as e:
                self._bridge.ocr_error.emit(str(e))
            finally:
                ctypes.windll.ole32.CoUninitialize()

        threading.Thread(target=_run, daemon=True, name="ocr-worker").start()

    def _on_ocr_image_item(self, item: ClipboardItem):
        """이미지 항목 우클릭 OCR — image_data(DIB/PNG)를 PNG bytes로 변환 후 공용 워커 호출."""
        import io
        from PIL import Image
        from pasteflow.ui.toast import ToastNotification

        # summary 항목 등으로 image_data가 비어 있을 가능성 → DB에서 풀 로드 재시도
        if not item.image_data:
            full = self.db.get_item(item.id) if item.id else None
            if full and full.image_data:
                item = full
            else:
                ToastNotification("이미지 데이터를 찾을 수 없습니다", icon="🔤")
                return

        try:
            img = Image.open(io.BytesIO(item.image_data))
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            png_bytes = buf.getvalue()
        except Exception as e:
            ToastNotification(f"이미지 변환 실패 — {e}", icon="🔤")
            return

        self._start_ocr_worker(png_bytes)

    def _on_ocr_image_by_id(self, item_id: int):
        """패널 우클릭(item_id 기반) → DB에서 풀 로드 후 OCR 위임."""
        from pasteflow.ui.toast import ToastNotification
        item = self.db.get_item(item_id)
        if not item:
            ToastNotification("항목을 찾을 수 없습니다", icon="🔤")
            return
        self._on_ocr_image_item(item)

    def _on_copy_image_as_path(self, item_id: int):
        """우클릭 "파일로 저장 후 경로 복사"(item_id 기반) → DB 로드 후 항목 기반 코어로 위임."""
        item = self.db.get_item(item_id)
        if item:
            self._copy_image_as_path_for_item(item)

    def _copy_image_as_path_for_item(self, item: ClipboardItem):
        """임시 PNG 저장 후 절대경로를 클립보드에 텍스트로 복사.
        Claude CLI 등 "경로 텍스트"를 첨부로 받는 앱에 사용자가 직접 Ctrl+V로 붙여넣기 위한 경로.
        DB id가 없는 임시 항목(화면 핀 등)도 받을 수 있도록 ClipboardItem을 직접 받는다.
        """
        from pasteflow.ui.toast import ToastNotification
        if not item or not item.image_data:
            ToastNotification("이미지 데이터를 찾을 수 없습니다", icon="🔤")
            return
        try:
            saved_path = _save_image_to_drop_temp(item.image_data)
        except Exception as e:
            ToastNotification(f"임시 파일 저장 실패 — {e}", icon="🔤")
            return
        path_item = ClipboardItem(
            content_type="text",
            text_content=saved_path,
            preview_text=saved_path[:200],
        )
        # _set_clipboard가 monitor._self_triggered를 설정해 히스토리 자동 추가 방지
        self.interceptor._set_clipboard(path_item)
        # 썸네일을 함께 띄워 "복사한 게 의도한 이미지가 맞는지" 그 자리에서 시각 확인
        ToastNotification(
            f"경로 복사됨: {os.path.basename(saved_path)}",
            icon="", image_path=saved_path)

    def _on_image_to_path_hotkey(self):
        """이미지→경로 단축키(기본 Ctrl+Shift+P) — 최신 히스토리 이미지를 임시 PNG로 저장 후
        절대경로 텍스트로 클립보드 교체 → 단축키를 누른 포그라운드 창에 자동 Ctrl+V.

        Claude Code CLI 등 "이미지 파일 경로를 첨부로 받는" 앱에 한 키로 바로 붙여넣기 위한 경로.

        **소스 = 라이브 클립보드가 아니라 최신 히스토리 항목**(Ctrl+V의 "마지막 복사물"에 대응).
        경로 텍스트는 히스토리에 안 남으므로(_set_clipboard의 self_triggered) 원본 이미지가
        최신 자리에 유지돼 이 키를 여러 번 눌러도 같은 이미지를 무한히 경로로 붙일 수 있다
        (Ctrl+V의 무한 반복과 대칭). 같은 이미지에 반복 실행 시 _img_to_path_cache로 임시 PNG를
        재사용해 디스크 재저장을 피한다. 최신 항목이 이미지가 아니면 토스트만 표시(경로 붙여넣기는
        이미지에만 의미) — 큐 기반 순차 경로 붙여넣기는 Ctrl+Shift+[가 담당.

        주의: 발화 시점에 사용자가 Ctrl+Shift를 여전히 누르고 있으므로 `_send_ctrl_v_plain`
        (수정키 처리 없는 단순 Ctrl+V)을 그대로 쓰면 OS가 Ctrl+Shift+V로 인식해 실패한다.
        Ctrl+Shift+V 순차 붙여넣기와 동일하게 `_send_clean_key(VK_V)`로 수정키 해제·복원·
        입력기 전환 마스킹을 거쳐 주입해야 한다.
        """
        from pasteflow.ui.toast import ToastNotification
        from pasteflow.paste_interceptor import VK_V

        recent = self.db.get_recent_items(limit=1)
        item = recent[0] if recent else None
        if item is None or item.content_type != "image" or not item.image_data:
            ToastNotification("최근 복사 항목이 이미지가 아닙니다", icon="🔤")
            return

        # 캐시된 경로가 같은 항목의 것이고 파일이 아직 있으면 재사용(반복 실행 디스크 절약)
        saved_path = None
        if self._img_to_path_cache is not None:
            cached_id, cached_path = self._img_to_path_cache
            if cached_id == item.id and item.id is not None and os.path.exists(cached_path):
                saved_path = cached_path
        if saved_path is None:
            try:
                saved_path = _save_image_to_drop_temp(item.image_data)
            except Exception as e:
                ToastNotification(f"임시 파일 저장 실패 — {e}", icon="🔤")
                return
            self._img_to_path_cache = (item.id, saved_path)

        # Alt+F3 핀이 "방금 붙여넣은 경로"를 원본 이미지로 되살리기 위해 경로를 기억
        self._last_pasted_image_path = saved_path

        path_item = ClipboardItem(
            content_type="text",
            text_content=saved_path,
            preview_text=saved_path[:200],
        )
        # _set_clipboard가 monitor._self_triggered를 설정해 히스토리 자동 추가 방지
        self.interceptor._set_clipboard(path_item)

        # 50ms 후 Ctrl+V 주입 — _send_clean_key가 사용자 Ctrl/Shift 해제 → Ctrl+V → 복원
        QTimer.singleShot(50, lambda: self.interceptor._send_clean_key(VK_V))
        # 단발 경로 붙여넣기는 큐가 아닌 현재 클립보드를 붙이는 '이탈' — 일반 Ctrl+V처럼
        # 큐를 클리어해 일관성 유지(큐 기반 경로 붙여넣기는 Ctrl+Shift+[가 담당)
        self._clear_queue_ui()
        # 썸네일을 함께 띄워 "의도한 이미지가 맞는지" 그 자리에서 시각 확인
        ToastNotification(
            f"경로 붙여넣음: {os.path.basename(saved_path)}",
            icon="", image_path=saved_path)

    def _on_seq_image_to_path_hotkey(self):
        """순차 경로 붙여넣기 단축키(기본 Ctrl+Shift+[) — 큐에서 다음 항목을 꺼내되
        이미지면 임시 PNG 경로 텍스트로 렌더해 붙여넣는다.

        Ctrl+Shift+V(순차)와 **같은 큐·포인터를 공유하는 '경로 버전'**. 캡처(Alt+F2)가
        이미 큐에 이미지로 쌓이므로(_on_capture_region → _persist_clipboard_item), 캡처
        여러 장을 이 키로 순서대로 경로 텍스트로 붙일 수 있다(예: 캡처1·2 → 이 키 두 번 →
        경로1·2). 이미지가 아닌 항목은 원본 그대로 붙여(Ctrl+Shift+V와 동일). 큐가 소진되면
        토스트만 표시하고 아무것도 하지 않는다(현재 클립보드 폴백은 일반 Ctrl+Shift+P가 담당 —
        '순차/일반'을 키로 구분하는 원칙 유지).

        클립보드 교체는 _set_clipboard(모니터 재감지 방지)로, 주입은 _send_clean_key(VK_V)로
        수정키(Ctrl+Shift+Alt) 해제·복원 + 입력기 전환 마스킹을 거친다(일반 경로 붙여넣기와 동일).
        """
        from pasteflow.ui.toast import ToastNotification
        from pasteflow.paste_interceptor import VK_V

        next_item = self.queue.get_next()
        if next_item is None:
            ToastNotification("순차 큐가 비었습니다", icon="🔤")
            return

        # summary 항목이면 전체 로드 (이미지 항목은 image_data가 인라인이라 대개 불필요)
        if not next_item.image_data and not next_item.extra_formats and next_item.id:
            full = self.db.get_item(next_item.id)
            if full:
                next_item = full

        if next_item.content_type == "image" and next_item.image_data:
            try:
                saved_path = _save_image_to_drop_temp(next_item.image_data)
            except Exception as e:
                ToastNotification(f"임시 파일 저장 실패 — {e}", icon="🔤")
                return
            # Alt+F3 핀이 "방금 붙여넣은 경로"를 원본 이미지로 되살리기 위해 경로를 기억
            self._last_pasted_image_path = saved_path
            path_item = ClipboardItem(
                content_type="text",
                text_content=saved_path,
                preview_text=saved_path[:200],
            )
            self.interceptor._set_clipboard(path_item)
            QTimer.singleShot(50, lambda: self.interceptor._send_clean_key(VK_V))
            ToastNotification(
                f"경로 붙여넣음: {os.path.basename(saved_path)}",
                icon="", image_path=saved_path)
        else:
            # 이미지가 아니면 원본 그대로 붙여넣기(Ctrl+Shift+V와 동일 처리)
            self.interceptor._set_clipboard(next_item)
            QTimer.singleShot(50, lambda: self.interceptor._send_clean_key(VK_V))

        # 진행 HUD 갱신 + 큐 소진 시 정리 (Ctrl+Shift+V 경로와 동일 표시)
        self._update_paste_ui()
        pointer, total = self.queue.get_status()
        if pointer >= total and total > 0:
            # Ctrl+Shift+V 소진과 동일하게 큐 클리어 + HUD 페이드 (찌꺼기 방지)
            self._on_paste_queue_done()

    def _on_ask_ai_hotkey(self):
        """AI 자유질문 단축키(기본 Alt+`) — 컨텍스트 없이 즉석에서 AI에게 질문한다.

        상시 켜져 있는 PasteFlow에서 한 키로 질문 입력창을 띄워 자유 질문하고 답변을 받는다.
        컨텍스트가 없으므로 `AiQueryDialog`는 컨텍스트 미리보기 없이 질문 입력칸만 표시하고,
        `_start_ai_worker(question, "")`가 `ocr_engine._ask_prompt`의 '컨텍스트 없음 → 질문만
        전송' 경로를 탄다(우클릭 "AI에게 질문"의 텍스트 분기와 동일 배관, 컨텍스트만 비움).
        답변 표시·위치(커서 모니터 정중앙)는 항목 질의와 동일한 `_on_ai_turn_done`을 공유한다.
        """
        self._open_ai_dialog()

    def _on_pin_hotkey(self):
        """화면에 핀 단축키(기본 Alt+F3) — 현재 클립보드 이미지를 화면에 떠 있는 창으로 띄운다.

        Snipaste의 'paste to screen'에 해당. 패널과 무관한 독립 창이라
        커서 근처에 띄우고, Space로 주석 편집·ESC로 닫기는 ImagePreviewPopup이 처리한다.
        """
        from PyQt6.QtGui import QCursor
        from pasteflow.ui.toast import ToastNotification

        image_bytes = _read_image_from_clipboard()
        text = self.app.clipboard().text() or ""
        # Ctrl+Shift+P·Ctrl+Shift+[로 방금 붙여넣은 이미지 경로가 아직 클립보드에 있으면,
        # 경로 문자열을 렌더하지 않고 그 원본 이미지를 핀한다(사용자 의도 = 이미지).
        if (not image_bytes and self._last_pasted_image_path
                and text.strip() == self._last_pasted_image_path
                and os.path.exists(self._last_pasted_image_path)):
            try:
                with open(self._last_pasted_image_path, "rb") as f:
                    image_bytes = f.read()
            except Exception:
                image_bytes = None
        # 방금 캡처한 이미지면(외부 복사로 무효화 안 됨) 캡처 자리에 그대로 덮는다.
        place_rect = self._pin_place_rect if image_bytes else None
        if not image_bytes:
            # 이미지가 없으면 클립보드 텍스트를 흰 배경 이미지로 렌더링해 핀(Snipaste 동작)
            if text.strip():
                try:
                    image_bytes = _render_text_to_png(text)
                except Exception as e:
                    ToastNotification(f"텍스트 렌더링 실패 — {e}", icon="📌")
                    return
            else:
                ToastNotification("클립보드에 이미지·텍스트가 없습니다", icon="📌")
                return

        item = ClipboardItem(content_type="image", image_data=image_bytes)

        # 커서 위치에 1px 앵커를 만들어 그 우측에 핀 창을 띄운다(compute_preview_pos 재사용).
        cursor_pos = QCursor.pos()
        anchor = QRect(cursor_pos.x(), cursor_pos.y(), 1, 1)
        # place_rect가 있으면 캡처 자리에 1:1로 정확히 덮고, 없으면 커서 옆에 1:1로 띄운다.
        popup = ImagePreviewPopup.open_new(item, anchor, native=True, place_rect=place_rect)
        # 핀 창에서도 복사·OCR·AI 질문·경로 복사·Space 주석 편집 후 복사/저장이 동작하도록 연결
        popup.copy_requested.connect(self._on_copy_item)
        popup.ocr_requested.connect(self._on_ocr_image_item)
        popup.ai_requested.connect(self._ai_query_for_item)
        popup.copy_as_path_requested.connect(self._copy_image_as_path_for_item)
        popup.annotated_copy_requested.connect(self._on_annotation_copy)
        popup.export_file_requested.connect(self._on_annotation_export)

    def _on_seq_pin_hotkey(self):
        """순차 핀 단축키(기본 Alt+Shift+F3) — 큐에서 다음 항목을 꺼내 화면에 핀한다.

        화면 핀(Alt+F3)의 '큐 버전'으로, 순차 붙여넣기(Ctrl+Shift+V)·순차 경로
        붙여넣기(Ctrl+Shift+[)와 **같은 큐·포인터를 공유**한다. 캡처(Alt+F2)를 여러 장
        찍어 두면 이 키를 누를 때마다 캡처1·2·3을 차례로 화면에 핀할 수 있다. 이미지가
        아닌 항목은 _render_text_to_png로 이미지화해 핀한다(텍스트도 핀·주석 가능 —
        Alt+F3와 동일). 큐가 소진되면 토스트만 표시(현재 클립보드 폴백은 Alt+F3가 담당 —
        '순차/일반'을 키로 구분하는 원칙 유지).

        핀은 '보기'라 캡처 자리 1:1 덮기(place_rect)를 쓰지 않고 커서 옆에 띄운다 —
        큐 항목마다 커서를 옮겨 배치를 손으로 정할 수 있다(공간 배치의 이점).
        """
        from PyQt6.QtGui import QCursor
        from pasteflow.ui.toast import ToastNotification

        next_item = self.queue.get_next()
        if next_item is None:
            ToastNotification("순차 큐가 비었습니다", icon="📌")
            return

        # summary 항목이면 전체 로드 (이미지 항목은 image_data가 인라인이라 대개 불필요)
        if not next_item.image_data and not next_item.extra_formats and next_item.id:
            full = self.db.get_item(next_item.id)
            if full:
                next_item = full

        if next_item.content_type == "image" and next_item.image_data:
            image_bytes = next_item.image_data
        elif next_item.text_content:
            try:
                image_bytes = _render_text_to_png(next_item.text_content)
            except Exception as e:
                ToastNotification(f"텍스트 렌더링 실패 — {e}", icon="📌")
                image_bytes = None
        else:
            image_bytes = None

        if image_bytes:
            item = ClipboardItem(content_type="image", image_data=image_bytes)
            cursor_pos = QCursor.pos()
            anchor = QRect(cursor_pos.x(), cursor_pos.y(), 1, 1)
            popup = ImagePreviewPopup.open_new(item, anchor, native=True)
            popup.copy_requested.connect(self._on_copy_item)
            popup.ocr_requested.connect(self._on_ocr_image_item)
            popup.ai_requested.connect(self._ai_query_for_item)
            popup.copy_as_path_requested.connect(self._copy_image_as_path_for_item)
            popup.annotated_copy_requested.connect(self._on_annotation_copy)
            popup.export_file_requested.connect(self._on_annotation_export)

        # 진행 HUD 갱신 + 큐 소진 시 정리 (Ctrl+Shift+V 경로와 동일 표시)
        self._update_paste_ui()
        pointer, total = self.queue.get_status()
        if pointer >= total and total > 0:
            self._on_paste_queue_done()

    def _on_ocr_done(self, text: str):
        """메인 스레드: OCR 결과 → 클립보드 + DB + 큐 + 정중앙 결과 칩(✓ 앞부분…)"""
        if not text.strip():
            # 진행 칩을 결과 메시지로 전환(칩이 없으면 일반 토스트로 폴백)
            if not self._finish_cursor_progress("인식된 텍스트 없음"):
                from pasteflow.ui.toast import ToastNotification
                ToastNotification("텍스트를 인식하지 못했습니다", icon="🔤")
            return

        item = ClipboardItem(
            content_type="text",
            text_content=text,
            preview_text=text[:200],
        )
        # 클립보드 먼저 입력 — _set_clipboard가 내부에서 monitor._self_triggered를 설정해
        # 클립보드 모니터가 동일 항목을 재감지(큐 중복 추가)하지 않도록 함
        self.interceptor._set_clipboard(item)
        # OCR은 아래에서 자체 칩을 띄우므로 복사 토스트 없는 경로 사용
        self._persist_clipboard_item(item)

        # 진행 칩을 "✓ 인식 앞부분…"으로 전환 — "내 OCR이 제대로 됐나" 시각 확인용.
        # 전체 텍스트는 클립보드·큐로 들어가므로 칩은 앞 24자 미리보기만 보여준다.
        flat = " ".join(text.split())  # 줄바꿈·연속 공백을 단일 공백으로
        preview = flat[:24]
        suffix = "…" if len(flat) > 24 else ""
        msg = f"✓ {preview}{suffix}"
        if not self._finish_cursor_progress(msg):
            from pasteflow.ui.toast import ToastNotification
            ToastNotification(msg, icon="🔤")

    def _on_ocr_fallback(self, failed_model: str, used_model: str):
        """모델 not_found로 폴백이 발동했을 때 사용자에게 알림.

        사용자가 다음 사용 시 모델 선택을 재고할 수 있게 한다 (DB 자체는 변경하지 않음 —
        잠깐의 게이트웨이 장애일 수도 있으므로 사용자 명시 선택을 유지).
        """
        from pasteflow.ui.toast import ToastNotification
        ToastNotification(
            f"{failed_model} → {used_model}로 폴백",
            icon="🔤",
        )

    def _on_ocr_error(self, msg: str):
        from pasteflow.ui.toast import ToastNotification

        self._stop_cursor_progress()  # 진행 칩 정리(지속형이라 수동 종료 필요)

        # API 키 미설정 → 토스트 후 설정 다이얼로그 자동 열기
        if "API 키" in msg:
            ToastNotification("API 키를 설정해 주세요", icon="🔤")
            QTimer.singleShot(300, self._open_settings)
            return

        if "미설치" in msg:
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
        if total > 0:
            self.paste_hud.show_progress(self.queue.get_items(), pointer)

    def _toggle_panel(self):
        """패널 토글 — 단축키/트레이로 열 때 마우스 근처에 표시"""
        if self.panel.isVisible():
            self.panel.hide()
        else:
            # _prev_foreground_hwnd는 SetWinEventHook이 연속 추적 — 별도 캡처 불필요
            self.panel._user_activated = True
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
        """패널 항목 붙여넣기(드래그·Enter) — auto_close 설정에 따라 패널 닫기 여부 결정.

        direct_paste가 클립보드를 그 항목으로 교체하므로, 복사와 동일하게 비고정 항목을
        히스토리 최상단으로 올려 "최상단 = 현재 클립보드" 불변식을 지킨다.
        """
        full_item = self.db.get_item(item.id) or item
        target_hwnd = self._prev_foreground_hwnd

        if not full_item.is_pinned and full_item.id is not None:
            self.db.bump_history_to_top(full_item.id)

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

        # 패널이 열린 채 유지되면(auto_close OFF) 최상단으로 올라간 순서를 즉시 반영.
        # ON이면 이미 숨겨졌으므로 다음에 열 때 DB 순서로 새로 그려진다.
        if not self.panel._auto_close:
            self._refresh_panel()

    def _on_copy_item(self, item: ClipboardItem):
        """고정/히스토리 항목 복사 → 클립보드 + 큐 추가 + (히스토리는) 최상단 이동 + 토스트.

        옛 히스토리 항목을 복사하면 클립보드가 실제로 그 내용이 되므로, 일반 Ctrl+C와
        동일하게 최상단으로 올려 "최상단 = 현재 클립보드" 불변식을 지키고 복사 토스트로
        피드백을 준다. self-triggered `_set_clipboard`라 모니터가 재감지하지 않으므로
        중복 항목은 생기지 않고, bump/토스트는 여기서 직접 처리한다.
        """
        full_item = self.db.get_item(item.id) or item
        self.interceptor._set_clipboard(full_item)
        self.queue.add_item(full_item)
        if not full_item.is_pinned and full_item.id is not None:
            self.db.bump_history_to_top(full_item.id)
        self._refresh_panel()
        if self._notify_on_copy:
            _, total = self.queue.get_status()
            self._on_copy_toast(full_item, total)

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
        self._clear_queue_ui()

    def _on_plain_paste(self):
        """일반 Ctrl+V 감지 → 큐 즉시 비우기 + UI 갱신 (훅 스레드 시그널 → 메인)"""
        self._clear_queue_ui()

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
        existing = self._image_preview_windows.get(item_id)
        if existing is not None:
            # 이미 열려 있으면 닫지 않고 편집 모드 토글(Space 두 번째 = 편집 진입). 닫기는 ESC.
            existing.activateWindow()
            existing.raise_()
            existing.toggle_edit_mode()
            return
        item = self.db.get_item(item_id)
        if item and item.image_data:
            popup = ImagePreviewPopup.open_new(item, self.panel.geometry())
            popup.copy_requested.connect(self._on_copy_item)
            popup.ocr_requested.connect(self._on_ocr_image_item)
            popup.ai_requested.connect(self._ai_query_for_item)
            popup.copy_as_path_requested.connect(self._copy_image_as_path_for_item)
            # 인라인 주석 편집(Space) 완료 액션 — 같은 창에서 emit
            popup.annotated_copy_requested.connect(self._on_annotation_copy)
            popup.export_file_requested.connect(self._on_annotation_export)
            self._image_preview_windows[item_id] = popup
            popup.destroyed.connect(lambda _=None, iid=item_id: self._image_preview_windows.pop(iid, None))

    def _on_annotation_copy(self, png: bytes):
        """주석본을 클립보드에 복사 + 히스토리에 새 항목으로 저장(썸네일 포함).

        _set_clipboard는 monitor.set_self_triggered를 켜 클립보드 모니터의 재감지를
        막으므로, 히스토리 추가는 _persist_clipboard_item(직접 DB+큐)이 담당한다.
        """
        from pasteflow.ui.toast import ToastNotification
        thumb = None
        if self.interceptor.monitor is not None:
            thumb = self.interceptor.monitor._create_thumbnail(png)
        item = ClipboardItem(content_type="image", image_data=png, thumbnail=thumb)
        self.interceptor._set_clipboard(item)   # 클립보드
        self._persist_clipboard_item(item)       # 히스토리 + 큐
        # png는 PNG라 QPixmap이 바로 로드 → 토스트에 썸네일을 실어 "무엇을 복사했나"를
        # 시각 확인(핀·경로 붙여넣기 토스트와 일관). 원본을 넘겨 96px 축소 시 선명.
        # 썸네일이 카테고리를 대신하므로 이모지 아이콘은 생략(icon="") — image_path 토스트
        # 5곳과 동일 규칙.
        ToastNotification("복사 + 히스토리 저장됨", icon="", image_bytes=png)

    def _on_annotation_export(self, png: bytes):
        """주석본을 PNG 파일로 저장(경로는 사용자 선택)."""
        from PyQt6.QtWidgets import QFileDialog
        from pasteflow.ui.toast import ToastNotification
        path, _ = QFileDialog.getSaveFileName(
            self.panel, "주석 이미지 저장", "annotation.png", "PNG 이미지 (*.png)")
        if not path:
            return
        if not path.lower().endswith(".png"):
            path += ".png"
        try:
            with open(path, "wb") as f:
                f.write(png)
        except Exception as e:
            ToastNotification(f"저장 실패 — {e}", icon="🖼")
            return
        ToastNotification(f"저장됨: {os.path.basename(path)}", icon="", image_path=path)

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

    def _on_ai_query_requested(self, item_id: int):
        """패널 우클릭 "AI에게 질문"(item_id 기반) → DB 로드 후 항목 기반 코어로 위임."""
        item = self.db.get_item(item_id)
        if item:
            self._ai_query_for_item(item)

    def _open_ai_dialog(self, context_text: str = "", image_png: "bytes | None" = None):
        """AI 질문 입력창을 **비모달로** 띄우고, 질문이 제출되면 그대로 AI 워커에 넘긴다.

        자유질문(Alt+`)·텍스트 항목 질의·이미지 항목 질의가 공유하는 단일 경로 — 셋의 차이는
        `context_text`(질문에 함께 실을 클립보드 텍스트)와 `image_png`(첫 첨부 이미지)뿐이다.

        ⚠ **`exec()`를 쓰지 않는다.** `QDialog.exec()`는 내부적으로 창을 모달로 표시하는데,
        모달리티가 `NonModal`이고 부모도 없으면 Qt가 이를 **ApplicationModal로 승격**시킨다
        (2026-07-13 PyQt6 실측: `setWindowModality(NonModal)` 후 `exec()` →
        `QWindow.modality == ApplicationModal`, `show()`만 NonModal 유지). 그래서 v1.49.4의
        `setWindowModality(NonModal)`은 무력했고, 질문창이 떠 있는 동안 **패널 클릭·영역
        캡처(Alt+F2)·AI 기록창이 전부 앱 모달에 막혀 있었다**(사용자 보고). 결과 수신을
        `finished` 콜백으로 바꿔 `show()`로 띄운다.

        `_ai_history_dialog`와 같은 재진입 가드를 둔다 — 비모달이라 질문창이 떠 있는 채로
        단축키·우클릭이 다시 들어올 수 있고, 그때 창을 또 만들면 앵커·워커가 뒤엉킨다.
        """
        from PyQt6.QtWidgets import QDialog
        from pasteflow.ui.ai_query import AiQueryDialog

        existing = getattr(self, "_ai_dialog", None)
        if existing is not None:
            existing.raise_()
            existing.activateWindow()
            return

        compare_models = self._resolve_compare_models()
        # parent=None — self.panel을 부모로 주면 Windows가 이 창을 패널의 "소유 창"으로 취급해
        # 패널 위에 항상 떠 있게 고정한다(모달과 무관한 별개의 Z-order 규칙) — 미리보기 팝업·
        # AI 기록창과 같은 독립 최상위 창 패턴으로 통일한다.
        dialog = AiQueryDialog(context_text, None, context_image=image_png,
                               compare_models=compare_models,
                               fetch_all_models=self._fetch_all_ai_models,
                               open_history=self._on_ai_history_requested)
        self._ai_dialog = dialog

        def _finished(code: int):
            self._ai_dialog = None
            if code == QDialog.DialogCode.Accepted:
                question = dialog.get_question()
                if question:
                    target = dialog.get_web_target()
                    if target:
                        # 브라우저 경로 — API에 묻지 않고 크롬에서 연다(web_open.py 참조).
                        # 첨부 이미지가 있으면 URL로 못 실으므로 주입 경로로 갈린다.
                        self._open_in_browser(target, question, dialog.get_images())
                        dialog.deleteLater()
                        return
                    # 답변창은 입력창이 "닫힐 때" 있던 자리(모니터) 기준으로 띄운다 — 입력 중
                    # 다른 모니터로 끌어다 놓았을 수 있어 트리거 시점 커서로는 부정확하다.
                    self._ai_anchor = dialog.frameGeometry()
                    images = dialog.get_images()  # 첨부(제거·추가·교체 결과를 존중)
                    if dialog.is_compare():
                        self._start_compare_query(question, context_text, images, compare_models)
                    else:
                        sel = dialog.get_selected_model()  # 없으면 기본 모델 1
                        self._start_ai_worker(question, context_text, images=images,
                                              model=sel or None)
            dialog.deleteLater()

        dialog.finished.connect(_finished)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _open_in_browser(self, target: str, question: str,
                         images: "list[bytes] | None" = None):
        """질문을 브라우저에서 연다 — 구글 검색 AI 모드 또는 내 구글 드라이브 검색.

        API 검색 경로(`web_search.py`)가 구조적으로 못 잡는 것을 위한 우회로다: 실시간
        시세는 페이지 본문에 없어 크롤링 텍스트를 읽는 검색 도구가 영영 볼 수 없는데,
        브라우저로 열면 구글이 금융 피드를 직접 물고 있어 정확한 값이 나온다(web_open.py
        모듈 주석의 2026-07-14 실측 참조).

        **이미지 첨부가 있으면 주입 경로로 갈린다.** 이미지는 URL에 실을 수 없지만 구글
        입력칸이 Ctrl+V로 이미지를 받으므로, AI 모드 빈 화면을 열고 클립보드+키를 주입한다
        (`_inject_to_google`). 텍스트만이면 견고한 URL 경로를 그대로 쓴다 — 주입은 타이밍에
        의존해 본질적으로 약하므로 필요할 때만 내려간다.

        답은 브라우저에 뜨고 끝난다 — PasteFlow가 그 텍스트를 읽지 못하므로 답변창·비교·
        이미지 복사는 이 경로에 적용되지 않는다(의도된 트레이드오프).
        """
        from pasteflow import web_open
        from pasteflow.ui.toast import ToastNotification

        if target == "google" and images:
            self._inject_to_google(question, images)
            return

        if target == "drive":
            url, label = web_open.drive_search_url(question), "내 드라이브에서 검색"
        else:
            url, label = web_open.google_ai_url(question), "구글 AI 모드로 검색"

        if web_open.open_url(url):
            ToastNotification(f"{label} — 브라우저에서 여는 중", icon="🌐")
        else:
            ToastNotification("브라우저를 열지 못했습니다", icon="🌐")

    # 페이지가 뜨고 입력칸에 포커스가 갈 때까지 기다리는 시간. 프로그램은 로드 완료 시점을
    # 알 수 없어(브라우저 밖에서 DOM을 못 본다) 고정 지연에 기댈 수밖에 없다 — 주입 경로가
    # URL 경로보다 본질적으로 약한 지점이다. 넉넉히 잡는 대신, 주입 직전 포그라운드가
    # 브라우저인지 검사해 엉뚱한 창에 쏘는 사고를 막는다.
    _BROWSER_LOAD_MS = 2500
    _INJECT_STEP_MS = 700   # 붙여넣기 사이 간격(구글이 첨부 칩을 만들 시간)

    def _inject_to_google(self, question: str, images: "list[bytes]"):
        """AI 모드 빈 화면을 열고 이미지 → 질문 → Enter를 순서대로 주입한다.

        클립보드를 두 번(이미지·텍스트) 갈아끼우는 방식이다 — 한 글자씩 타이핑하는 것보다
        단순하고, `_set_clipboard`가 `_self_triggered`를 세워 주므로 이 임시 클립보드가
        히스토리에 쌓이지도 않는다(금지 조항 5와 같은 경로).

        각 단계는 QTimer로 이어 붙인다(sleep으로 UI를 막지 않는다). 매 단계 직전
        포그라운드가 브라우저인지 확인하고, 아니면 즉시 중단한다 — 사용자가 그 사이 다른
        창을 클릭했다면 우리가 쏜 Ctrl+V·Enter가 그 창에 들어가 버리기 때문이다.
        """
        from PyQt6.QtCore import QTimer
        from pasteflow import web_open
        from pasteflow.paste_interceptor import VK_RETURN, VK_V
        from pasteflow.ui.toast import ToastNotification

        if not web_open.open_url(web_open.google_ai_home_url()):
            ToastNotification("브라우저를 열지 못했습니다", icon="🌐")
            return
        ToastNotification(f"구글 AI 모드로 검색 — 이미지 {len(images)}장 첨부 중", icon="🌐")

        # 붙여넣을 것들을 순서대로 — 이미지들 다음에 질문 텍스트.
        pastes: list[ClipboardItem] = [
            ClipboardItem(content_type="image", image_data=png) for png in images
        ]
        pastes.append(ClipboardItem(content_type="text", text_content=question))

        def _step(i: int):
            if not web_open.is_browser_foreground():
                # 사용자가 다른 창으로 갔다 — 남은 키를 쏘지 않고 조용히 멈춘다.
                ToastNotification("브라우저가 앞에 없어 중단했습니다 — 직접 붙여넣어 주세요",
                                  icon="🌐")
                return
            if i < len(pastes):
                self.interceptor._set_clipboard(pastes[i])
                self.interceptor._send_clean_key(VK_V)
                QTimer.singleShot(self._INJECT_STEP_MS, lambda: _step(i + 1))
            else:
                self.interceptor.send_plain_key(VK_RETURN)

        QTimer.singleShot(self._BROWSER_LOAD_MS, lambda: _step(0))

    def _ai_query_for_item(self, item: ClipboardItem):
        """항목을 컨텍스트로 질문 입력 다이얼로그 표시 → AI 워커.

        텍스트 항목은 텍스트를 컨텍스트로, 이미지 항목은 이미지를 멀티모달로 전송(시각 질의).
        DB id가 없는 임시 항목(화면 핀 등)도 받을 수 있도록 ClipboardItem을 직접 받는다.
        """
        from pasteflow.ui.toast import ToastNotification

        if item.content_type == "image":
            if not item.image_data:
                ToastNotification("이미지 데이터를 찾을 수 없습니다", icon="🤖")
                return
            try:
                image_png = _image_data_to_png_bytes(item.image_data)
            except Exception as e:
                ToastNotification(f"이미지 변환 실패 — {e}", icon="🤖")
                return
            # 이미지는 첨부로 싣고 텍스트 컨텍스트는 비운다(질문+이미지 멀티모달 질의).
            self._open_ai_dialog("", image_png)
            return

        self._open_ai_dialog(item.text_content or item.preview_text or "")

    def _on_ai_turn_done(self, payload: dict):
        """AI 대화 턴 결과 → 펜딩 탭을 실제 답변으로 채운다(첫 턴·후속 턴 공용).

        v1.49.3부터 모든 호출자(`_start_ai_worker`/`_start_compare_query`/`_on_ai_followup`)가
        `_run_ai_turn` 호출 전에 답변창을 이미 pending 상태로 띄워 두므로, `popup`은 여기서
        항상 살아 있다 — 창을 새로 만드는 대신 `resolve_pending`으로 제자리를 채우기만 한다.
        답변창은 대화를 '턴 탭'(Q1/Q2/…)으로 나눠 보여준다 — 이어질수록 스크롤이 무한정
        길어지지 않게(사용자 요청). 모델은 전체 대화(payload['conversation'])를 인지한다.
        """
        popup = payload["popup"]
        answer = payload["answer"]
        conversation = payload["conversation"]
        images = payload["images"]

        if not answer.strip():
            # 비교 창(단일 펜딩 턴)은 "답 없음"을 그 창에 남기고, 후속 턴은 펜딩 탭만 제거.
            popup.fail_pending("답변이 비어 있어요.")
            return

        # 후속 턴 — 엔터 즉시(또는 첫 질문 제출 즉시) 만든 펜딩 탭(생각 중)을 실제 답변으로 교체.
        popup.resolve_pending(answer)

        # 대화 상태를 답변창에 보관 — 다음 후속 질문에 사용(이미지는 첫 턴에만 실림).
        popup._conversation = conversation
        popup._images = images
        # 이 창이 후속 질문 시 쓸 모델(비교 창은 자기 모델로 이어감, 단일 창은 None=기본).
        popup._ai_model = payload.get("model")
        elapsed = payload.get("elapsed")
        if elapsed is not None:
            popup.set_elapsed(elapsed)  # 이 답변에 걸린 시간을 답변창 상단에 표시
        self._sync_ai_history(popup)

    def _sync_ai_history(self, popup):
        """popup._conversation을 ai_history에 자동 저장/갱신한다(v1.49.2) — 답변창을
        닫아도(표시 전용이라 DB 미보존) 트레이 'AI 기록'에서 다시 볼 수 있게. 이미지는
        저장하지 않는다(용량 때문 — 재열람 후 후속 질문은 텍스트만 이어간다).

        popup._ai_history_id가 없으면(이번이 첫 턴) 새 row를 만들고, 있으면(후속 질문 —
        방금 만든 창이든 트레이에서 재조회해 이어간 창이든) 그 row를 갱신한다.
        """
        conversation = getattr(popup, "_conversation", None)
        if not conversation:
            return
        conv_json = json.dumps(conversation, ensure_ascii=False)
        hid = getattr(popup, "_ai_history_id", None)
        if hid is None:
            title = (conversation[0].get("display") or conversation[0].get("content") or "")[:100]
            # backend 컬럼은 v1.50.0에서 backend 개념이 사라져 늘 ""로 저장한다. 컬럼 자체는
            # 남긴다 — 옛 기록의 값은 그 시절의 사실이므로 덮어쓰지 않는다.
            hid = self.db.save_ai_conversation(
                title, conv_json, popup._ai_model or "", "")
            popup._ai_history_id = hid
        else:
            self.db.update_ai_conversation(hid, conv_json)

    def _on_ai_history_requested(self, near: "QRect | None" = None):
        """트레이 'AI 기록' 또는 AI 질문창의 '🕘 기록' 버튼 — 저장된 AI 대화 목록을
        보여주고 더블클릭으로 재열람한다.

        parent를 주지 않는다(미리보기 팝업과 동일한 독립 최상위 창) — AI 질문창
        (`AiQueryDialog`)의 '🕘 기록' 버튼으로 열 때 같은 부모의 형제 창이면 모달 전파에
        함께 막힐 수 있기 때문이다(질문창은 `_open_ai_dialog`에서 비모달로 띄운다). 재진입
        가드(이미 열려 있으면 새로 안 만들고 앞으로 가져오기만)로 창이 중복되지 않게 한다.

        `near`가 주어지면(질문창 버튼 — 그 창의 `frameGeometry()`) 이미지 미리보기가 패널
        옆에 뜨는 것과 같은 배치 함수(`compute_preview_pos`)로 그 옆에 이어 붙여, 겹쳐서
        수동으로 옮겨야 하는 번거로움을 없앤다(사용자 요청, 2026-07-13). 트레이에서 열 때는
        `near`가 없으므로 커서 옆에 연다.
        """
        from pasteflow.ui.ai_history import AiHistoryDialog
        from pasteflow.ui.image_preview import compute_preview_pos
        from PyQt6.QtGui import QCursor
        from PyQt6.QtWidgets import QApplication

        existing = getattr(self, "_ai_history_dialog", None)
        if existing is not None:
            existing.raise_()
            existing.activateWindow()
            return
        dialog = AiHistoryDialog(self.db)
        dialog.open_requested.connect(self._open_ai_history_item)
        dialog.finished.connect(lambda *_: setattr(self, "_ai_history_dialog", None))
        self._ai_history_dialog = dialog

        anchor = near if isinstance(near, QRect) else QRect(QCursor.pos(), QCursor.pos())
        screen = QApplication.screenAt(anchor.center()) or QApplication.primaryScreen()
        dialog.adjustSize()  # 위치 계산에 쓸 sizeHint 확정(아직 show 전)
        if screen:
            dialog.move(compute_preview_pos(anchor, dialog.size(), screen))
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _open_ai_history_item(self, history_id: int):
        """AI 기록 목록에서 고른 대화를 답변창으로 다시 연다(읽기 전용 재구성).

        저장된 conversation(role/content/display 리스트)을 문답 쌍으로 재구성해 첫 턴은
        `initial_turn`으로, 나머지는 `add_turn`으로 채운다. `_conversation`/`_ai_model`/
        `_ai_history_id`도 그대로 복원해 이어서 질문(follow-up)이 실시간 답변창과 동일하게
        동작한다 — 단 이미지는 저장하지 않았으므로(`_sync_ai_history` 참고) 원래 이미지
        질의였어도 후속 질문은 텍스트만으로 이어간다.
        """
        row = self.db.get_ai_conversation(history_id)
        if row is None:
            return
        try:
            conversation = json.loads(row["conversation"])
        except (TypeError, ValueError):
            return
        turns = []
        for i in range(0, len(conversation) - 1, 2):
            q = conversation[i].get("display") or conversation[i].get("content", "")
            a = conversation[i + 1].get("content", "")
            turns.append((q, a))
        if not turns:
            return

        item = ClipboardItem(content_type="text", text_content="", preview_text=turns[-1][1][:200])
        popup = TextPreviewPopup.open_new(
            item, self.panel.geometry(), editable=False, markdown=True, center=True,
            initial_turn=turns[0])
        for q, a in turns[1:]:
            popup.add_turn(q, a)
        popup._conversation = conversation
        popup._images = None
        popup._ai_model = row.get("model") or None
        popup._ai_history_id = history_id
        popup.copy_requested.connect(self._on_copy_item)
        popup.copy_as_image_requested.connect(self._on_answer_image_copy)
        popup.copy_text_requested.connect(self._on_copy_selected_text)
        popup.followup_requested.connect(
            lambda text, p=popup: self._on_ai_followup(p, text))

    def _on_ai_followup(self, popup, text: str):
        """답변창 하단 입력칸 Enter → 이전 문답을 인지한 상태로 후속 질의.

        답변창이 보관한 대화 히스토리에 새 user 질문을 쌓아 같은 창을 대상으로 재질의한다.
        진행 표시는 팝업 입력칸의 'AI 생각 중…'(팝업이 이미 잠금) — 별도 진행 칩 없음.
        """
        conversation = getattr(popup, "_conversation", None)
        if conversation is None:
            return
        images = getattr(popup, "_images", None)
        model = getattr(popup, "_ai_model", None)      # 비교 창은 자기 모델로 이어감
        new_conv = conversation + [{"role": "user", "content": text, "display": text}]

        cache = getattr(popup, "_shared_cache", None)
        if cache is not None:
            # 비교 창 — 후속 질문도 공유 검색 경로로 보낸다. 첫 턴만 공유하고 후속을 모델에
            # 맡기면 "그럼 모레는?" 한 마디에 세 모델이 각자 검색해 수치 분기가 되살아난다.
            # 같은 후속 질문을 다른 창에도 던지면 캐시가 같은 자료를 재사용한다.
            job = {"popup": popup, "model": model, "conversation": new_conv}
            self._start_shared_search(text, [job], images, cache)
            return

        self._run_ai_turn(new_conv, images, popup=popup, model=model)

    def _on_copy_selected_text(self, text: str):
        """AI 답변창 '선택→복사' 모드 — 드래그로 선택한 부분 텍스트를 클립보드+히스토리에 저장.

        선택 즉시 복사되어 다른 곳에 바로 붙여넣을 수 있다(부분 발췌용). 클립보드·히스토리
        처리는 답변 이미지 복사와 동일 패턴(_set_clipboard로 모니터 재감지 차단 → 히스토리 저장).
        """
        from pasteflow.ui.toast import ToastNotification

        if not text.strip():
            return
        item = ClipboardItem(
            content_type="text",
            text_content=text,
            preview_text=text[:200],
        )
        self.interceptor._set_clipboard(item)
        self._persist_clipboard_item(item)
        ToastNotification("선택한 텍스트 복사됨", icon="📋")

    def _on_answer_image_copy(self, pixmap):
        """AI 답변창 우클릭 '이미지로 복사' — 렌더된 답변 픽맵을 클립보드(DIB)+히스토리에 저장.

        클립보드·히스토리 처리는 영역 캡처(_on_capture_region)와 동일 패턴: _set_clipboard로
        모니터 재감지를 막은 뒤 _persist_clipboard_item으로 히스토리·큐에 추가. 붙여넣기
        호환성이 가장 넓은 CF_DIB로 넣는다.
        """
        from pasteflow.ui.toast import ToastNotification

        if pixmap is None or pixmap.isNull():
            ToastNotification("이미지를 만들지 못했습니다", icon="🖼")
            return
        dib = _qpixmap_to_dib(pixmap)
        item = ClipboardItem(
            content_type="image",
            image_data=dib,
            thumbnail=self.monitor._create_thumbnail(dib),
        )
        self.interceptor._set_clipboard(item)
        self._persist_clipboard_item(item)
        # 렌더된 픽맵을 PNG로 실어 썸네일 표시(다른 이미지 복사 토스트와 일관).
        # 썸네일이 카테고리를 대신하므로 이모지 아이콘은 생략(icon="").
        from PyQt6.QtCore import QBuffer, QByteArray
        _ba = QByteArray()
        _buf = QBuffer(_ba)
        _buf.open(QBuffer.OpenModeFlag.WriteOnly)
        _png = pixmap.save(_buf, "PNG")
        _buf.close()
        _thumb = bytes(_ba) if _png else None
        ToastNotification("답변을 이미지로 복사 + 히스토리 저장됨",
                          icon="" if _thumb else "🖼", image_bytes=_thumb)

    def _on_ai_error(self, payload):
        """AI 질의 실패 — 항상 이미 떠 있는 답변창(pending)에 오류를 표시한다(v1.49.3부터
        popup이 없는 경로가 없다). 창 안에 표시되므로(비교 창은 어느 모델이 실패했는지 그대로
        보임) 별도 토스트로 도배하지 않는다 — API 키 미설정만 예외로 토스트+설정창을 띄운다.
        """
        from pasteflow.ui.toast import ToastNotification

        popup = payload.get("popup") if isinstance(payload, dict) else None
        msg = payload.get("msg", "") if isinstance(payload, dict) else str(payload)
        if popup is not None:
            popup.fail_pending(msg)

        # API 키 미설정 → 설정 다이얼로그 자동 열기 (OCR 에러 처리와 동일 패턴, 재진입 가드가
        # 여러 창의 동시 실패에서 중복 오픈을 막는다).
        if "API 키" in msg:
            ToastNotification("API 키를 설정해 주세요", icon="🤖")
            QTimer.singleShot(300, self._open_settings)

    def _on_clear_history(self):
        self.db.clear_history()
        self.queue.clear()
        self.tray.update_queue_status(0, 0)
        self._refresh_panel()

    def _on_drag_to_app(self, item_id: int, cursor_pos, alt_held: bool = False):
        """패널 항목 드래그 → 외부 앱 붙여넣기.
        - Alt+드래그 + 이미지: 임시 PNG로 저장 후 경로 텍스트 붙여넣기 (Claude CLI 등)
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

        # Alt+드래그 + 이미지 → 임시 PNG 저장 + 경로 텍스트 클립보드 + SendInput(Ctrl+V)
        # Windows Terminal의 claude CLI 등 "파일 경로 텍스트"를 첨부로 받는 앱 대응.
        # WM_PASTE는 터미널이 무시하므로 무조건 SendInput 경로로 통일.
        if alt_held and full_item.image_data and full_item.content_type == "image":
            from pasteflow.ui.toast import ToastNotification
            try:
                saved_path = _save_image_to_drop_temp(full_item.image_data)
            except Exception as e:
                ToastNotification(f"임시 파일 저장 실패 — {e}", icon="🔤")
                return
            path_item = ClipboardItem(
                content_type="text",
                text_content=saved_path,
                preview_text=saved_path[:200],
            )
            self.interceptor._set_clipboard(path_item)
            # 마우스 업 시점에 사용자가 Alt를 여전히 누르고 있으므로 수정키 해제 후 주입
            # (해제 없이 plain Ctrl+V를 쏘면 OS가 Ctrl+Alt+V로 오인해 붙여넣기 실패)
            _activate_and_send_ctrl_v(
                root_hwnd, sender=self.interceptor._release_modifiers_and_send_ctrl_v
            )
            return

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
        # 클립보드를 항목 그 자체로 교체하므로 복사·Enter와 동일하게 최상단으로 올린다
        # ("최상단 = 현재 클립보드"). Alt+드래그(경로 텍스트)·탐색기 저장(PNG)은 위에서
        # 먼저 return하므로 클립보드=항목인 이 경로에만 적용된다.
        self.interceptor._set_clipboard(full_item)
        if not full_item.is_pinned:
            self.db.bump_history_to_top(full_item.id)

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

        # 최상단으로 올라간 순서를 패널에 반영 (드래그 소스라 패널은 열려 있음)
        if self.panel.isVisible():
            self._refresh_panel()

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

    def _get_secret(self, key: str, default: str = "") -> str:
        """시크릿 키(DPAPI 암호화 저장) 복호화 읽기 헬퍼."""
        from pasteflow.crypto import unprotect
        return unprotect(self.db.get_setting(key, default) or default)

    def _gdrive_creds(self) -> tuple[str, str, str]:
        """드라이브 OAuth 자격증명 (client_id, client_secret, refresh_token).

        TokenCache가 **매 토큰 요청마다** 호출한다 — 값이 아니라 함수를 넘기는 이유가 이것이다.
        설정창에서 재연결하면 다음 호출이 곧바로 새 자격증명을 본다(앱 재시작 불필요).
        DB 접근은 Database._lock으로 직렬화되어 워커 스레드에서 호출해도 안전하다.
        """
        return (
            self.db.get_setting("gdrive_client_id", ""),
            self._get_secret("gdrive_client_secret"),
            self._get_secret("gdrive_refresh_token"),
        )

    def _save_annot_last_values(self, width, font, badge):
        """주석 편집기 마지막 값(두께·글자·번호 크기)을 DB에 저장 — 재시작 후에도 유지.
        _EditorMixin._persist_cb로 등록돼 값 변경(주석 위 휠) 시 호출된다."""
        self.db.set_setting("annot_last_width", str(width))
        self.db.set_setting("annot_last_font_size", str(font))
        self.db.set_setting("annot_last_badge_size", str(badge))

    def _apply_settings_from_db(self):
        """DB에서 설정 로드 → UI/동작에 적용."""
        # OCR/AI 모델 분리 마이그레이션 (기존 모델을 OCR 슬롯에 복사). 1회성·idempotent.
        _migrate_split_ocr_ai_model(self.db)

        # official(Google AI Studio) 백엔드 잔재 제거 (v1.50.0). 순서 주의: 위 OCR 슬롯
        # 초기화가 읽는 ocr_gemini_model_gateway는 안 건드리지만, 비교 슬롯의 backend 키를
        # 지우므로 그걸 읽는 마이그레이션보다 뒤에 와야 한다.
        _migrate_drop_official_backend(self.db)

        # 시크릿 암호화 + 고아 키 purge. Idempotent.
        _migrate_secrets(self.db)

        # 레지스트리 실제 상태로 auto_start DB 동기화
        self._sync_auto_start_from_registry()

        auto_close = self.db.get_setting("panel_auto_close", "1")
        self.panel.set_auto_close(auto_close == "1")

        self._notify_on_copy = self.db.get_setting("notify_on_copy", "1") == "1"

        # 큐 idle timeout (마지막 복사로부터 N초 지나면 다음 새 복사가 큐 첫 항목)
        try:
            idle_sec = float(self.db.get_setting("queue_idle_reset_sec", "10"))
        except (ValueError, TypeError):
            idle_sec = 10.0
        self.queue.set_idle_reset_sec(idle_sec)

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
        # 창-모달이라 트레이 메뉴·API 키 오류 자동 오픈이 안 막힌다 → 중첩 exec 방지
        dlg = getattr(self, "_settings_dialog", None)
        if dlg is not None:
            dlg.raise_()
            dlg.activateWindow()
            return
        current = {
            "hotkey_panel_toggle": self.db.get_setting("hotkey_panel_toggle", "ctrl+space"),
            "history_max": self.db.get_setting("history_max", "50"),
            "auto_start": self.db.get_setting("auto_start", "0"),
            "hotkey_ocr_trigger": self.db.get_setting("hotkey_ocr_trigger", "ctrl+shift+s"),
            "hotkey_image_to_path": self.db.get_setting("hotkey_image_to_path", "ctrl+shift+p"),
            "hotkey_seq_image_to_path": self.db.get_setting("hotkey_seq_image_to_path", "ctrl+shift+["),
            "hotkey_pin_image": self.db.get_setting("hotkey_pin_image", "alt+f3"),
            "hotkey_seq_pin": self.db.get_setting("hotkey_seq_pin", "alt+shift+f3"),
            "hotkey_capture": self.db.get_setting("hotkey_capture", "alt+f2"),
            "hotkey_ask_ai": self.db.get_setting("hotkey_ask_ai", "alt+`"),
            "capture_save_folder": self.db.get_setting("capture_save_folder", "") or _default_capture_folder(),
            "ocr_language": self.db.get_setting("ocr_language", "ko"),
            "ocr_engine": self.db.get_setting("ocr_engine", "gemini"),  # OCR은 항상 AI API(엔진 선택 제거)
            "ocr_gemini_base_url": self.db.get_setting("ocr_gemini_base_url", ""),
            "ocr_gemini_api_key_gateway": self._get_secret("ocr_gemini_api_key_gateway"),
            "ocr_gemini_model_gateway": self.db.get_setting("ocr_gemini_model_gateway", ""),
            "ocr_gemini_model_cache_gateway": self.db.get_setting("ocr_gemini_model_cache_gateway", ""),
            # OCR 전용 모델 슬롯 (AI 질의 모델과 분리 — 비전 가능 모델만 고를 수 있다)
            "ocr_model_gateway": self.db.get_setting("ocr_model_gateway", ""),
            # 여러 모델 비교(선택) — 기본 AI 모델에 더해 동시에 물어볼 모델 2개.
            "ai_compare_model_a": self.db.get_setting("ai_compare_model_a", ""),
            "ai_compare_model_b": self.db.get_setting("ai_compare_model_b", ""),
            "ai_system_prompt": self.db.get_setting("ai_system_prompt", ""),
            # 구글 드라이브 OAuth — secret 2종은 DPAPI 복호화, client_id는 평문.
            "gdrive_client_id": self.db.get_setting("gdrive_client_id", ""),
            "gdrive_client_secret": self._get_secret("gdrive_client_secret"),
            "gdrive_refresh_token": self._get_secret("gdrive_refresh_token"),
            "notify_on_copy": self.db.get_setting("notify_on_copy", "1"),
            "queue_idle_reset_sec": self.db.get_setting("queue_idle_reset_sec", "10"),
        }
        dlg = SettingsDialog(current, parent=self.panel)
        dlg.settings_changed.connect(self._on_settings_changed)
        dlg.raise_()
        dlg.activateWindow()
        # exec()는 기본이 application-modal이라 부모 없는 최상위 오버레이(캡처·OCR)까지
        # 입력을 막는다. 창-모달로 좁히면 부모(패널)만 잠기고 오버레이는 그대로 동작.
        dlg.setWindowModality(Qt.WindowModality.WindowModal)
        self._settings_dialog = dlg
        try:
            dlg.exec()
        finally:
            self._settings_dialog = None

    def _on_settings_changed(self, new_settings: dict):
        """설정 변경 적용"""
        # 단축키 비교는 DB 저장 전에 이전 값을 먼저 읽어야 함
        old_hotkey = self.db.get_setting("hotkey_panel_toggle", "ctrl+space")
        old_ocr_hotkey = self.db.get_setting("hotkey_ocr_trigger", "ctrl+shift+s")
        old_img2path_hotkey = self.db.get_setting("hotkey_image_to_path", "ctrl+shift+p")
        old_seq_img2path_hotkey = self.db.get_setting("hotkey_seq_image_to_path", "ctrl+shift+[")
        old_pin_hotkey = self.db.get_setting("hotkey_pin_image", "alt+f3")
        old_seq_pin_hotkey = self.db.get_setting("hotkey_seq_pin", "alt+shift+f3")
        old_capture_hotkey = self.db.get_setting("hotkey_capture", "alt+f2")
        old_ask_ai_hotkey = self.db.get_setting("hotkey_ask_ai", "alt+`")

        from pasteflow.crypto import protect
        for key, value in new_settings.items():
            if key in _SECRET_KEYS:
                value = protect(value)
            self.db.set_setting(key, value)

        # 패널 토글 단축키 재설정
        new_hotkey = new_settings.get("hotkey_panel_toggle", "ctrl+space")
        if old_hotkey != new_hotkey:
            self.interceptor.set_panel_hotkey(new_hotkey)

        # OCR 단축키 재설정
        new_ocr_hotkey = new_settings.get("hotkey_ocr_trigger", "ctrl+shift+s")
        if old_ocr_hotkey != new_ocr_hotkey:
            self.interceptor.set_ocr_hotkey(new_ocr_hotkey)

        # 이미지→경로 단축키 재설정
        new_img2path_hotkey = new_settings.get("hotkey_image_to_path", "ctrl+shift+p")
        if old_img2path_hotkey != new_img2path_hotkey:
            self.interceptor.set_image_to_path_hotkey(new_img2path_hotkey)

        # 순차 경로 붙여넣기 단축키 재설정
        new_seq_img2path_hotkey = new_settings.get("hotkey_seq_image_to_path", "ctrl+shift+[")
        if old_seq_img2path_hotkey != new_seq_img2path_hotkey:
            self.interceptor.set_seq_image_to_path_hotkey(new_seq_img2path_hotkey)

        # 화면에 핀 단축키 재설정
        new_pin_hotkey = new_settings.get("hotkey_pin_image", "alt+f3")
        if old_pin_hotkey != new_pin_hotkey:
            self.interceptor.set_pin_hotkey(new_pin_hotkey)

        # 순차 핀 단축키 재설정
        new_seq_pin_hotkey = new_settings.get("hotkey_seq_pin", "alt+shift+f3")
        if old_seq_pin_hotkey != new_seq_pin_hotkey:
            self.interceptor.set_seq_pin_hotkey(new_seq_pin_hotkey)

        # 영역 캡처 단축키 재설정
        new_capture_hotkey = new_settings.get("hotkey_capture", "alt+f2")
        if old_capture_hotkey != new_capture_hotkey:
            self.interceptor.set_capture_hotkey(new_capture_hotkey)

        # AI 자유질문 단축키 재설정
        new_ask_ai_hotkey = new_settings.get("hotkey_ask_ai", "alt+`")
        if old_ask_ai_hotkey != new_ask_ai_hotkey:
            self.interceptor.set_ask_ai_hotkey(new_ask_ai_hotkey)

        # 자동 시작
        auto_start = new_settings.get("auto_start", "0") == "1"
        self._set_auto_start(auto_start)

        # 복사 알림 토글
        if "notify_on_copy" in new_settings:
            self._notify_on_copy = new_settings["notify_on_copy"] == "1"

        # 큐 idle timeout 변경 즉시 반영
        if "queue_idle_reset_sec" in new_settings:
            try:
                self.queue.set_idle_reset_sec(float(new_settings["queue_idle_reset_sec"]))
            except (ValueError, TypeError):
                pass

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
