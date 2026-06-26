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
from PyQt6.QtCore import QTimer, QObject, pyqtSignal, QBuffer, QRect

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
        return None
    except Exception:
        return None
    finally:
        try:
            win32clipboard.CloseClipboard()
        except Exception:
            pass


def _render_text_to_png(text: str) -> bytes:
    """클립보드 텍스트를 흰 배경 이미지(PNG bytes)로 렌더링한다.

    Snipaste의 'paste text to screen'처럼, 텍스트도 화면 핀·주석이 가능하도록
    이미지화한다. 줄바꿈은 보존하고, 최대폭을 넘으면 워드랩한다.
    """
    from PyQt6.QtGui import QPixmap, QPainter, QFont, QFontMetrics, QColor
    from PyQt6.QtCore import QRect, Qt, QBuffer, QByteArray

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
    pm.fill(QColor("#ffffff"))
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    p.setFont(font)
    p.setPen(QColor("#1e1e2e"))  # 흰 배경 위 어두운 글자
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
    "ocr_gemini_api_key_official",
    "ocr_gemini_api_key_gateway",
})

# 과거 빌드의 잔재로 DB에 남았으나 현재 코드 어디서도 참조하지 않는 고아 키.
# ocr_api_key는 평문 시크릿이라 P0(이미 노출), 나머지는 cruft 정리.
_ORPHAN_KEYS = (
    "ocr_api_key",
    "ocr_base_url",
    "hotkey_settings",
    "panel_always_on_top",
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


def _migrate_split_gemini_keys(db):
    """1회 마이그레이션: 단일 ocr_gemini_api_key / model / model_cache를 backend별 분리 키로 이전.

    base_url 유무로 official/gateway 결정. 새 키가 이미 채워져 있으면 옛 값으로 덮어쓰지 않음
    (이미 마이그레이션됐거나 사용자가 새 설정을 입력한 경우). 마지막에 옛 키를 db에서 삭제하며,
    이후 settings.json 화이트리스트에서도 제외되어 있어 다시 db로 들어오지 않는다.
    """
    old_api = db.get_setting("ocr_gemini_api_key", "") or ""
    old_model = db.get_setting("ocr_gemini_model", "") or ""
    old_cache = db.get_setting("ocr_gemini_model_cache", "") or ""
    if not (old_api or old_model or old_cache):
        return  # 마이그레이션 대상 없음

    base_url = (db.get_setting("ocr_gemini_base_url", "") or "").strip()
    backend = "gateway" if base_url else "official"

    if backend == "gateway":
        new_api_key, new_model_key, new_cache_key = (
            "ocr_gemini_api_key_gateway",
            "ocr_gemini_model_gateway",
            "ocr_gemini_model_cache_gateway",
        )
    else:
        new_api_key, new_model_key, new_cache_key = (
            "ocr_gemini_api_key_official",
            "ocr_gemini_model_official",
            "ocr_gemini_model_cache_official",
        )

    if old_api and not db.get_setting(new_api_key, ""):
        from pasteflow.crypto import protect
        db.set_setting(new_api_key, protect(old_api))
    if old_model and not db.get_setting(new_model_key, ""):
        db.set_setting(new_model_key, old_model)
    if old_cache and not db.get_setting(new_cache_key, ""):
        db.set_setting(new_cache_key, old_cache)
    if not db.get_setting("ocr_gemini_backend", ""):
        db.set_setting("ocr_gemini_backend", backend)

    with db._lock:
        db.conn.execute(
            "DELETE FROM settings WHERE key IN (?, ?, ?)",
            ("ocr_gemini_api_key", "ocr_gemini_model", "ocr_gemini_model_cache"),
        )
        db.conn.commit()
    print(f"[Migrate] ocr_gemini 키 분리 완료: backend={backend}")


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
    pin_image          = pyqtSignal()        # 훅 스레드 → 메인: 클립보드 이미지를 화면에 핀(떠 있는 창)으로 띄우기
    capture_requested  = pyqtSignal()        # 훅 스레드 → 메인: 영역 캡처 오버레이 띄우기
    ai_done            = pyqtSignal(str, str)  # AI 워커 스레드 → 메인: (질문, 답변)
    ai_error           = pyqtSignal(str)     # AI 워커 스레드 → 메인: 에러 메시지


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
        self._bridge.pin_image.connect(self._on_pin_hotkey)
        self._bridge.capture_requested.connect(self._on_capture_requested)
        self._bridge.ai_done.connect(self._on_ai_done)
        self._bridge.ai_error.connect(self._on_ai_error)

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
            on_plain_paste=self._bridge.plain_paste.emit,
            on_image_to_path=self._bridge.image_to_path.emit,
            on_pin_image=self._bridge.pin_image.emit,
            on_capture=self._bridge.capture_requested.emit,
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

        pin_hotkey = self.db.get_setting("hotkey_pin_image", "alt+f3")
        self.interceptor.set_pin_hotkey(pin_hotkey)

        capture_hotkey = self.db.get_setting("hotkey_capture", "alt+f2")
        self.interceptor.set_capture_hotkey(capture_hotkey)


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

    def _on_paste_queue_done(self):
        """큐 소진 — 진행 HUD를 잠시 뒤 숨김"""
        self.paste_hud.finish()

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

    def _on_capture_region(self, pixmap):
        """메인 스레드: 선택 영역 픽맵 → 클립보드(DIB) + 히스토리·큐 + 파일 저장 + 토스트.

        클립보드·히스토리 처리는 OCR 결과 경로와 동일(_set_clipboard로 모니터 재감지 방지 후
        _persist_clipboard_item). 이미지는 붙여넣기 호환성이 가장 넓은 CF_DIB로 넣는다.
        """
        from pasteflow.ui.toast import ToastNotification

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

    def _resolve_gemini_cfg(self) -> tuple[str, str, str]:
        """게이트웨이/공식 백엔드 설정을 해석해 (api_key, base_url, model) 반환.

        OCR 워커와 AI 질의 워커가 공유. backend 명시값 우선, 미설정 시 base_url 유무로
        추론(레거시 호환). 공식 API는 base_url을 무시하므로 ""로 강제한다.
        DB 접근은 _lock으로 직렬화되어 워커 스레드에서 호출해도 안전.
        """
        backend = self.db.get_setting("ocr_gemini_backend", "")
        base_url_saved = self.db.get_setting("ocr_gemini_base_url", "")
        if backend not in ("official", "gateway"):
            backend = "gateway" if (base_url_saved or "").strip() else "official"
        if backend == "gateway":
            return (
                self._get_secret("ocr_gemini_api_key_gateway"),
                base_url_saved,
                self.db.get_setting("ocr_gemini_model_gateway", ""),
            )
        return (
            self._get_secret("ocr_gemini_api_key_official"),
            "",
            self.db.get_setting("ocr_gemini_model_official", ""),
        )

    def _start_ai_worker(self, question: str, context_text: str, image_png: bytes | None = None):
        """AI 질의 워커 — 질문+컨텍스트(텍스트 또는 이미지)를 받아 백그라운드에서 Gemini에 질의.

        OCR과 동일한 게이트웨이/공식 배관(`OcrEngine.ask`)을 재사용한다. OCR 엔진 설정과
        무관하게 항상 gemini 경로를 사용(이미지→텍스트 OCR이 winrt여도 AI 질의는 게이트웨이).
        `image_png`가 주어지면 이미지를 멀티모달로 함께 전송(시각 질의).
        결과/에러는 `_bridge.ai_done` / `_bridge.ai_error` 시그널로 메인 스레드에 통지.
        """
        # 지속형 진행 토스트 + 경과 시간 카운터 시작 (답이 올 때까지 유지).
        # "다운된 게 아니라 작업 중"임을 시각적으로 알린다(비스트리밍이라 %는 불가).
        self._start_ai_progress()

        def _run():
            try:
                from pasteflow.ocr_engine import OcrEngine
                api_key, base_url, model = self._resolve_gemini_cfg()
                engine = OcrEngine(kind="gemini", api_key=api_key, base_url=base_url, model=model)
                answer = engine.ask(question, context_text, image_png=image_png)
                if engine.last_fallback_from and engine.last_used_model:
                    self._bridge.ocr_fallback.emit(engine.last_fallback_from, engine.last_used_model)
                self._bridge.ai_done.emit(question, answer)
            except Exception as e:
                self._bridge.ai_error.emit(str(e))

        threading.Thread(target=_run, daemon=True, name="ai-worker").start()

    def _start_ai_progress(self):
        """AI 질의 진행 표시 — 지속형 토스트 + 0.5초 간격 경과 시간/애니메이션 갱신."""
        import time
        from pasteflow.ui.toast import ToastNotification

        self._stop_ai_progress()  # 중복 질의 대비 이전 진행 토스트 정리
        self._ai_progress_toast = ToastNotification(
            "AI 생각 중… 0:00 ●··", icon="🤖", duration_ms=0)
        self._ai_progress_start = time.monotonic()
        self._ai_progress_dots = 1
        self._ai_progress_timer = QTimer()
        self._ai_progress_timer.setInterval(500)
        self._ai_progress_timer.timeout.connect(self._tick_ai_progress)
        self._ai_progress_timer.start()

    def _tick_ai_progress(self):
        import time

        toast = getattr(self, "_ai_progress_toast", None)
        if toast is None:
            return
        elapsed = int(time.monotonic() - self._ai_progress_start)
        m, s = divmod(elapsed, 60)
        self._ai_progress_dots = (self._ai_progress_dots % 3) + 1
        dots = "●" * self._ai_progress_dots + "·" * (3 - self._ai_progress_dots)
        toast.set_message(f"AI 생각 중… {m}:{s:02d} {dots}")

    def _stop_ai_progress(self):
        """진행 토스트·타이머 정리 (idempotent — 답변/에러 도착 시 호출)."""
        timer = getattr(self, "_ai_progress_timer", None)
        if timer is not None:
            timer.stop()
            self._ai_progress_timer = None
        toast = getattr(self, "_ai_progress_toast", None)
        if toast is not None:
            toast.dismiss()
            self._ai_progress_toast = None

    def _start_ocr_worker(self, png_bytes: bytes):
        """공용 OCR 워커 — PNG bytes를 받아 백그라운드에서 OCR 수행.

        영역 캡처 OCR과 이미지 항목 OCR이 공유. 결과/에러는
        `_bridge.ocr_done` / `_bridge.ocr_error` 시그널로 메인 스레드에 통지.
        """
        import io
        from PIL import Image
        from pasteflow.ui.toast import ToastNotification

        ToastNotification("인식 중…", icon="🔤")

        def _run():
            # 호출마다 COM 초기화/해제 — asyncio/WinRT 상태 오염 방지
            ctypes.windll.ole32.CoInitializeEx(None, 0)
            try:
                pil_img = Image.open(io.BytesIO(png_bytes))

                from pasteflow.ocr_engine import OcrEngine
                lang = self.db.get_setting("ocr_language", "ko")
                engine_kind = self.db.get_setting("ocr_engine", "winrt")
                if engine_kind == "gemini":
                    api_key, base_url, model = self._resolve_gemini_cfg()
                else:
                    api_key = ""
                    base_url = ""
                    model = ""
                engine = OcrEngine(kind=engine_kind, api_key=api_key, base_url=base_url, language=lang, model=model)
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
        """이미지→경로 단축키(기본 Ctrl+Shift+P) — 현재 클립보드 이미지를 임시 PNG로 저장 후
        절대경로 텍스트로 클립보드 교체 → 단축키를 누른 포그라운드 창에 자동 Ctrl+V.

        Claude Code CLI 등 "이미지 파일 경로를 첨부로 받는" 앱에 한 키로 바로 붙여넣기 위한 경로.

        주의: 발화 시점에 사용자가 Ctrl+Shift를 여전히 누르고 있으므로 `_send_ctrl_v_plain`
        (수정키 처리 없는 단순 Ctrl+V)을 그대로 쓰면 OS가 Ctrl+Shift+V로 인식해 실패한다.
        Ctrl+Shift+V 순차 붙여넣기와 동일하게 `_send_clean_key(VK_V)`로 수정키 해제·복원·
        입력기 전환 마스킹을 거쳐 주입해야 한다.
        """
        from pasteflow.ui.toast import ToastNotification
        from pasteflow.paste_interceptor import VK_V

        image_bytes = _read_image_from_clipboard()
        if not image_bytes:
            ToastNotification("클립보드에 이미지가 없습니다", icon="🔤")
            return

        try:
            saved_path = _save_image_to_drop_temp(image_bytes)
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

        # 50ms 후 Ctrl+V 주입 — _send_clean_key가 사용자 Ctrl/Shift 해제 → Ctrl+V → 복원
        QTimer.singleShot(50, lambda: self.interceptor._send_clean_key(VK_V))
        # 썸네일을 함께 띄워 "의도한 이미지가 맞는지" 그 자리에서 시각 확인
        ToastNotification(
            f"경로 붙여넣음: {os.path.basename(saved_path)}",
            icon="", image_path=saved_path)

    def _on_pin_hotkey(self):
        """화면에 핀 단축키(기본 Alt+F3) — 현재 클립보드 이미지를 화면에 떠 있는 창으로 띄운다.

        Snipaste의 'paste to screen'에 해당. 패널과 무관한 독립 창이라
        커서 근처에 띄우고, Space로 주석 편집·ESC로 닫기는 ImagePreviewPopup이 처리한다.
        """
        from PyQt6.QtGui import QCursor
        from pasteflow.ui.toast import ToastNotification

        image_bytes = _read_image_from_clipboard()
        if not image_bytes:
            # 이미지가 없으면 클립보드 텍스트를 흰 배경 이미지로 렌더링해 핀(Snipaste 동작)
            text = self.app.clipboard().text() or ""
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
        # 캡처한 크기 그대로(1:1, 화면 초과 시만 축소) 띄운다.
        popup = ImagePreviewPopup.open_new(item, anchor, native=True)
        # 핀 창에서도 복사·OCR·AI 질문·경로 복사·Space 주석 편집 후 복사/저장이 동작하도록 연결
        popup.copy_requested.connect(self._on_copy_item)
        popup.ocr_requested.connect(self._on_ocr_image_item)
        popup.ai_requested.connect(self._ai_query_for_item)
        popup.copy_as_path_requested.connect(self._copy_image_as_path_for_item)
        popup.annotated_copy_requested.connect(self._on_annotation_copy)
        popup.export_file_requested.connect(self._on_annotation_export)

    def _on_ocr_done(self, text: str):
        """메인 스레드: OCR 결과 → 클립보드 + DB + 큐 + 토스트"""
        from pasteflow.ui.toast import ToastNotification

        if not text.strip():
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
        # OCR은 아래에서 자체 토스트를 띄우므로 복사 토스트 없는 경로 사용
        self._persist_clipboard_item(item)

        preview = text[:30].replace("\n", " ")
        suffix = "..." if len(text) > 30 else ""
        ToastNotification(f"{preview}{suffix}", icon="🔤")

    def _on_ocr_fallback(self, failed_model: str, used_model: str):
        """모델 not_found로 폴백이 발동했을 때 사용자에게 알림.

        사용자가 다음 사용 시 모델 선택을 재고할 수 있게 한다 (DB 자체는 변경하지 않음 —
        잠깐의 게이트웨이 장애일 수도 있으므로 사용자 명시 선택을 유지).
        """
        from pasteflow.ui.toast import ToastNotification
        ToastNotification(
            f"{failed_model} 없음 → {used_model}로 폴백",
            icon="🔤",
        )

    def _on_ocr_error(self, msg: str):
        from pasteflow.ui.toast import ToastNotification

        # API 키 미설정 → 토스트 후 설정 다이얼로그 자동 열기
        if "API 키" in msg:
            ToastNotification("API 키를 설정해 주세요", icon="🔤")
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

    def _on_plain_paste(self):
        """일반 Ctrl+V 감지 → 큐 즉시 비우기 + UI 갱신 (훅 스레드 시그널 → 메인)"""
        self.queue.mark_plain_paste()
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
        ToastNotification("복사 + 히스토리 저장됨", icon="🖼")

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

    def _ai_query_for_item(self, item: ClipboardItem):
        """항목을 컨텍스트로 질문 입력 다이얼로그 표시 → AI 워커.

        텍스트 항목은 텍스트를 컨텍스트로, 이미지 항목은 이미지를 멀티모달로 전송(시각 질의).
        DB id가 없는 임시 항목(화면 핀 등)도 받을 수 있도록 ClipboardItem을 직접 받는다.
        """
        from PyQt6.QtWidgets import QDialog
        from PyQt6.QtGui import QCursor
        from PyQt6.QtCore import QRect
        from pasteflow.ui.ai_query import AiQueryDialog
        from pasteflow.ui.toast import ToastNotification

        # 답변창을 입력창이 떠 있던 자리(커서)에 띄우기 위해 제출 시점 커서를 기록.
        # 답변은 비동기 도착이라 그때의 커서는 이동했을 수 있어 제출 시점을 쓴다.
        cur = QCursor.pos()
        self._ai_anchor = QRect(cur.x(), cur.y(), 1, 1)

        if item.content_type == "image":
            if not item.image_data:
                ToastNotification("이미지 데이터를 찾을 수 없습니다", icon="🤖")
                return
            try:
                image_png = _image_data_to_png_bytes(item.image_data)
            except Exception as e:
                ToastNotification(f"이미지 변환 실패 — {e}", icon="🤖")
                return
            dialog = AiQueryDialog("", self.panel, context_image=image_png)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            question = dialog.get_question()
            if not question:
                return
            self._start_ai_worker(question, "", image_png=image_png)
            return

        context_text = item.text_content or item.preview_text or ""
        dialog = AiQueryDialog(context_text, self.panel)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        question = dialog.get_question()
        if not question:
            return
        self._start_ai_worker(question, context_text)

    def _on_ai_done(self, question: str, answer: str):
        """AI 답변 → 임시 텍스트 미리보기 창으로 표시(읽기+복사 전용, 수정 메뉴 없음)."""
        from pasteflow.ui.toast import ToastNotification

        self._stop_ai_progress()  # 진행 토스트 종료

        if not answer.strip():
            ToastNotification("답변을 받지 못했습니다", icon="🤖")
            return

        # 마크다운 렌더링용 본문 — 질문은 굵게, 구분선 후 답변.
        # 클립보드 복사는 이 원문(마크다운)을 유지해 다른 곳에 붙일 때 서식 보존.
        body = f"**Q.** {question}\n\n---\n\n{answer}"
        item = ClipboardItem(
            content_type="text",
            text_content=body,
            preview_text=answer[:200],
        )
        # DB에 없는 임시 항목(id 없음) → editable=False로 수정 메뉴 숨김.
        # TextPreviewPopup._instances가 창을 닫을 때까지 참조를 유지하므로 별도 보관 불필요.
        # 앵커: 질문 제출 시점 커서(_ai_anchor) — 다른 모니터에서 질문해도 그 자리에 답변.
        # markdown=True → QTextEdit+setMarkdown으로 서식 렌더링(일반 미리보기는 평문 유지).
        anchor = getattr(self, "_ai_anchor", None) or self.panel.geometry()
        popup = TextPreviewPopup.open_new(item, anchor, editable=False, markdown=True)
        popup.copy_requested.connect(self._on_copy_item)

    def _on_ai_error(self, msg: str):
        from pasteflow.ui.toast import ToastNotification

        self._stop_ai_progress()  # 진행 토스트 종료

        # API 키 미설정 → 토스트 후 설정 다이얼로그 자동 열기 (OCR 에러 처리와 동일 패턴)
        if "API 키" in msg:
            ToastNotification("API 키를 설정해 주세요", icon="🤖")
            QTimer.singleShot(300, self._open_settings)
            return
        ToastNotification(f"AI 질의 실패 — {msg}", icon="🤖")

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

    def _get_secret(self, key: str, default: str = "") -> str:
        """시크릿 키(DPAPI 암호화 저장) 복호화 읽기 헬퍼."""
        from pasteflow.crypto import unprotect
        return unprotect(self.db.get_setting(key, default) or default)

    def _apply_settings_from_db(self):
        """DB에서 설정 로드 → UI/동작에 적용."""
        # Gemini 키/모델 분리 마이그레이션 (옛 단일 키 → backend별 분리 키). 1회성·idempotent.
        _migrate_split_gemini_keys(self.db)

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
        current = {
            "hotkey_panel_toggle": self.db.get_setting("hotkey_panel_toggle", "ctrl+space"),
            "history_max": self.db.get_setting("history_max", "50"),
            "auto_start": self.db.get_setting("auto_start", "0"),
            "hotkey_ocr_trigger": self.db.get_setting("hotkey_ocr_trigger", "ctrl+shift+s"),
            "hotkey_image_to_path": self.db.get_setting("hotkey_image_to_path", "ctrl+shift+p"),
            "hotkey_pin_image": self.db.get_setting("hotkey_pin_image", "alt+f3"),
            "hotkey_capture": self.db.get_setting("hotkey_capture", "alt+f2"),
            "capture_save_folder": self.db.get_setting("capture_save_folder", "") or _default_capture_folder(),
            "ocr_language": self.db.get_setting("ocr_language", "ko"),
            "ocr_engine": self.db.get_setting("ocr_engine", "winrt"),
            "ocr_gemini_backend": self.db.get_setting("ocr_gemini_backend", ""),
            "ocr_gemini_base_url": self.db.get_setting("ocr_gemini_base_url", ""),
            "ocr_gemini_api_key_official": self._get_secret("ocr_gemini_api_key_official"),
            "ocr_gemini_api_key_gateway": self._get_secret("ocr_gemini_api_key_gateway"),
            "ocr_gemini_model_official": self.db.get_setting("ocr_gemini_model_official", ""),
            "ocr_gemini_model_gateway": self.db.get_setting("ocr_gemini_model_gateway", ""),
            "ocr_gemini_model_cache_official": self.db.get_setting("ocr_gemini_model_cache_official", ""),
            "ocr_gemini_model_cache_gateway": self.db.get_setting("ocr_gemini_model_cache_gateway", ""),
            "notify_on_copy": self.db.get_setting("notify_on_copy", "1"),
            "queue_idle_reset_sec": self.db.get_setting("queue_idle_reset_sec", "10"),
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
        old_img2path_hotkey = self.db.get_setting("hotkey_image_to_path", "ctrl+shift+p")
        old_pin_hotkey = self.db.get_setting("hotkey_pin_image", "alt+f3")
        old_capture_hotkey = self.db.get_setting("hotkey_capture", "alt+f2")

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

        # 화면에 핀 단축키 재설정
        new_pin_hotkey = new_settings.get("hotkey_pin_image", "alt+f3")
        if old_pin_hotkey != new_pin_hotkey:
            self.interceptor.set_pin_hotkey(new_pin_hotkey)

        # 영역 캡처 단축키 재설정
        new_capture_hotkey = new_settings.get("hotkey_capture", "alt+f2")
        if old_capture_hotkey != new_capture_hotkey:
            self.interceptor.set_capture_hotkey(new_capture_hotkey)

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
