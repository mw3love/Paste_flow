"""PasteFlow 진입점 — 모듈 오케스트레이션

클립보드 모니터 → DB → 큐 → UI 간 이벤트 흐름 관리.
"""
import sys
import os
import ctypes
import ctypes.wintypes
import threading
import time
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QTimer, QObject, pyqtSignal

from pasteflow.database import Database
from pasteflow.models import ClipboardItem
from pasteflow.paste_queue import PasteQueue
from pasteflow.clipboard_monitor import ClipboardMonitor
from pasteflow.paste_interceptor import PasteInterceptor
from pasteflow.hotkey_manager import HotkeyManager
from pasteflow.ui.panel import ClipboardPanel
from pasteflow.ui.image_preview import ImagePreviewPopup
from pasteflow.ui.tray import TrayIcon
from pasteflow.ui.settings_dialog import SettingsDialog

# ── 드래그 붙여넣기 헬퍼 ──────────────────────────────────────────────────────

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
    from pasteflow.paste_interceptor import _make_key_input, _send_inputs, VK_V, KEYEVENTF_KEYUP
    VK_CONTROL = 0x11

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
        _send_inputs([
            _make_key_input(VK_CONTROL),
            _make_key_input(VK_V),
            _make_key_input(VK_V, KEYEVENTF_KEYUP),
            _make_key_input(VK_CONTROL, KEYEVENTF_KEYUP),
        ])

    QTimer.singleShot(80, _send)


def _get_explorer_folder(hwnd: int):
    """CabinetWClass HWND → 현재 폴더 경로 반환. 실패 시 None.
    Qt 메인 스레드 전용 (COM이 MTA로 이미 초기화된 환경에서 호출).
    """
    try:
        import win32com.client
        shell = win32com.client.Dispatch("Shell.Application")
        for window in shell.Windows():
            try:
                if int(window.HWND) == hwnd:
                    return window.Document.Folder.Self.Path
            except Exception:
                continue
    except Exception:
        pass
    return None


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


# ─────────────────────────────────────────────────────────────────────────────


class _SignalBridge(QObject):
    """훅 스레드 → 메인 스레드 시그널 전달"""
    paste_happened = pyqtSignal()
    new_item_saved = pyqtSignal(object)  # 모든 복사 경로: DB + 큐 추가
    queue_cleared = pyqtSignal()         # Ctrl+Shift+X: 큐 초기화
    undo_happened = pyqtSignal()         # Ctrl+Shift+Z: 실수 복구


class PasteFlowApp:
    """PasteFlow 앱 오케스트레이션"""

    def __init__(self):
        self.app = QApplication(sys.argv)
        self.app.setQuitOnLastWindowClosed(False)

        # 스레드 안전 시그널 브릿지
        self._bridge = _SignalBridge()
        self._bridge.paste_happened.connect(self._update_paste_ui)
        self._bridge.new_item_saved.connect(self._on_new_item_ui)
        self._bridge.queue_cleared.connect(self._on_queue_clear_ui)
        self._bridge.undo_happened.connect(self._update_paste_ui)

        # 코어 모듈
        db_path = os.path.join(os.path.dirname(__file__), "..", "pasteflow.db")
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
            on_queue_clear=self._on_queue_clear_from_hook,
            on_undo=self._on_undo_from_hook,
        )
        self.hotkey_manager = HotkeyManager()

        # UI (패널이 기본 UI — 미니창 없음)
        self.panel = ClipboardPanel()
        # 첫 표시 지연 제거: 네이티브 윈도우 핸들을 미리 생성해 둠
        self.panel.show()
        self.panel.hide()
        self.tray = TrayIcon()

        # 패널 열기 전 포커스된 윈도우 추적
        self._prev_foreground_hwnd = None

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
        self.panel.open_settings_requested.connect(self._open_settings)
        self.panel.quit_requested.connect(self._quit)
        self.panel.clear_history_requested.connect(self._on_clear_history)
        self.panel.drag_to_app_requested.connect(self._on_drag_to_app)
        self.panel.queue_select_requested.connect(self._on_queue_select)

        panel_hotkey = self.db.get_setting("hotkey_panel_toggle", "ctrl+space")
        self.hotkey_manager.register(panel_hotkey, self._toggle_panel)

        for n in range(1, 10):
            self.hotkey_manager.register(f"alt+{n}", lambda idx=n: self._on_direct_paste(idx))

    def _on_new_clipboard_item(self, item: ClipboardItem):
        """Ctrl+Shift+C 경로: DB 저장 + 큐 추가 — 백그라운드 스레드에서 호출됨."""
        saved = self.db.save_item(item)
        self.queue.add_item(saved)
        self._bridge.new_item_saved.emit(saved)

    def _on_duplicate_clipboard_item(self):
        """중복 클립보드 콜백 — 무시 (중복은 큐/DB에 추가하지 않음)."""
        pass

    def _on_queue_clear_from_hook(self):
        """Ctrl+Shift+X 콜백 — 훅 스레드에서 호출됨."""
        self._bridge.queue_cleared.emit()

    def _on_undo_from_hook(self, item: ClipboardItem):
        """Ctrl+Shift+Z 콜백 — 훅 스레드에서 호출됨."""
        self._bridge.undo_happened.emit()

    def _on_new_item_ui(self, saved: ClipboardItem):
        """메인 스레드에서 UI 갱신 — 패널이 열려 있을 때만 갱신"""
        if self.panel.isVisible():
            self._refresh_panel()

    def _on_queue_clear_ui(self):
        """메인 스레드: Ctrl+Shift+X — 큐 초기화 후 패널 갱신"""
        if self.panel.isVisible():
            self._refresh_panel()

    def _on_paste_from_hook(self, item: ClipboardItem):
        """붙여넣기 콜백 — 훅 스레드에서 호출됨 → 시그널로 메인 스레드 전달"""
        self._bridge.paste_happened.emit()

    def _update_paste_ui(self):
        """메인 스레드에서 붙여넣기 UI 업데이트"""
        pointer, total = self.queue.get_status()
        if self.panel.isVisible():
            self.panel.update_queue_status(pointer, total)

    def _toggle_panel(self):
        """패널 토글"""
        if self.panel.isVisible():
            self.panel.hide()
        else:
            self._prev_foreground_hwnd = ctypes.windll.user32.GetForegroundWindow()
            self._refresh_panel()
            self.panel._user_activated = True
            self.panel.show()
            self.panel.raise_()
            self.panel.activateWindow()

    def _refresh_panel(self):
        """패널 데이터 갱신"""
        pinned = self.db.get_pinned_items_summary()
        history = self.db.get_recent_items_summary()
        pointer, total = self.queue.get_status()
        queue_item_ids = [item.id for item in self.queue.get_items()]
        self.panel.refresh(pinned, history, pointer, total, queue_item_ids)

    def _on_direct_paste(self, n: int):
        """Alt+N 직접 붙여넣기"""
        history = self.db.get_recent_items()
        if n > len(history):
            return
        item = history[n - 1]
        threading.Thread(
            target=self.interceptor.direct_paste, args=(item,), daemon=True
        ).start()

    def _on_panel_paste(self, item: ClipboardItem):
        """패널에서 더블클릭 붙여넣기 — 패널 유지, 대상 앱에 붙여넣기"""
        # 패널 표시용 항목은 image_data/extra_formats 없음 → DB에서 전체 로드
        full_item = self.db.get_item(item.id) or item
        target_hwnd = self._prev_foreground_hwnd

        # 붙여넣기 중 포커스 이동으로 패널이 자동닫기되지 않도록 플래그 설정
        self.panel._paste_in_progress = True

        def _do_paste():
            try:
                self.interceptor.direct_paste(full_item, target_hwnd)
            except Exception as e:
                print(f"[PanelPaste] Error: {e}")
            finally:
                QTimer.singleShot(0, self._reactivate_panel)

        threading.Thread(target=_do_paste, daemon=True).start()

    def _reactivate_panel(self):
        """붙여넣기 완료 → 플래그 해제 (포커스는 대상 앱 유지)"""
        try:
            self.panel._paste_in_progress = False
        except Exception:
            pass

    def _on_copy_item(self, item: ClipboardItem):
        """고정 항목 클릭 → 클립보드 복사 + 큐 추가"""
        full_item = self.db.get_item(item.id) or item
        self.interceptor._set_clipboard(full_item)
        self.queue.add_item(full_item)
        self._refresh_panel()

    def _on_queue_select(self, item_id: int):
        """패널 히스토리 항목 클릭 → 해당 항목부터 최신까지 큐로 설정"""
        history = self.db.get_recent_items()  # newest-first (id DESC)
        ids = [item.id for item in history]
        if item_id not in ids:
            return
        i = ids.index(item_id)
        # history[0:i+1] = [newest, ..., selected] → reverse → [selected, ..., newest]
        queue_items = list(reversed(history[0 : i + 1]))
        self.queue.set_queue(queue_items)
        self._refresh_panel()

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

    def _on_preview_image(self, item_id: int, pos):
        item = self.db.get_item(item_id)
        if item and item.image_data:
            ImagePreviewPopup.instance().toggle_preview(item.image_data, pos)

    def _on_clear_history(self):
        self.db.clear_history()
        self.queue.clear()
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
        print(f"[DBG] content_type={full_item.content_type!r} has_image={bool(full_item.image_data)} root_class={root_class!r} hwnd={root_hwnd}")
        if full_item.image_data and full_item.content_type == "image":
            folder = None
            if root_class in _EXPLORER_CLASSES:
                folder = _get_explorer_folder(root_hwnd)
                print(f"[DBG] Explorer folder={folder!r}")
            elif root_class in _DESKTOP_CLASSES:
                folder = _get_desktop_path()
                print(f"[DBG] Desktop folder={folder!r}")
            else:
                print(f"[DBG] root_class not in Explorer/Desktop sets")
            if folder:
                try:
                    path = _save_image_to_folder(full_item.image_data, folder)
                    print(f"[DBG] 저장 완료: {path}")
                except Exception as e:
                    print(f"[DBG] _save_image_to_folder 실패: {e}")
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

        if _is_chromium_window(class_name):
            # Electron/Chromium: 포그라운드 활성화 후 SendInput(Ctrl+V)
            top_hwnd = win32gui.GetAncestor(target, win32con.GA_ROOT)
            _activate_and_send_ctrl_v(top_hwnd)
        else:
            # Win32 / WinUI3: WM_PASTE 직접 전송
            win32gui.SendMessage(target, win32con.WM_PASTE, 0, 0)

    # ── 설정 ──

    def _apply_settings_from_db(self):
        """DB에서 설정 로드 → UI/동작에 적용"""
        auto_close = self.db.get_setting("panel_auto_close", "1")
        self.panel._auto_close = auto_close == "1"

        # 패널 위치/크기 복원
        import json
        geo_json = self.db.get_setting("panel_geometry")
        if geo_json:
            try:
                self.panel.restore_geometry_dict(json.loads(geo_json))
            except Exception:
                pass

    def _open_settings(self):
        """설정 다이얼로그 열기"""
        current = {
            "hotkey_panel_toggle": self.db.get_setting("hotkey_panel_toggle", "ctrl+space"),
            "history_max": self.db.get_setting("history_max", "50"),
            "panel_auto_close": self.db.get_setting("panel_auto_close", "1"),
            "auto_start": self.db.get_setting("auto_start", "0"),
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

        for key, value in new_settings.items():
            self.db.set_setting(key, value)

        # 패널 자동 닫기
        self.panel._auto_close = new_settings.get("panel_auto_close", "1") == "1"

        # 단축키 재등록
        new_hotkey = new_settings.get("hotkey_panel_toggle", "ctrl+space")
        if old_hotkey != new_hotkey:
            self.hotkey_manager.unregister_all()
            self.hotkey_manager.register(new_hotkey, self._toggle_panel)
            for n in range(1, 10):
                self.hotkey_manager.register(
                    f"alt+{n}", lambda idx=n: self._on_direct_paste(idx)
                )

        # 자동 시작
        auto_start = new_settings.get("auto_start", "0") == "1"
        self._set_auto_start(auto_start)

    def _set_auto_start(self, enable: bool):
        """Windows 시작 시 자동 실행 레지스트리 설정"""
        import winreg
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        try:
            reg_key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE
            )
            if enable:
                exe_path = os.path.abspath(sys.argv[0])
                winreg.SetValueEx(reg_key, "PasteFlow", 0, winreg.REG_SZ, exe_path)
            else:
                try:
                    winreg.DeleteValue(reg_key, "PasteFlow")
                except FileNotFoundError:
                    pass
            winreg.CloseKey(reg_key)
        except Exception as e:
            print(f"[Settings] 자동 시작 설정 실패: {e}")

    def _quit(self):
        # 패널 위치/크기 저장
        import json
        geo = self.panel.get_geometry_dict()
        self.db.set_setting("panel_geometry", json.dumps(geo))

        self.interceptor.stop()
        self.monitor.stop()
        self.hotkey_manager.destroy()
        self.tray.hide()
        self.db.close()
        self.app.quit()

    def run(self):
        self.monitor.start()
        self.interceptor.start()
        self.tray.show()

        self._msg_timer = QTimer()
        self._msg_timer.timeout.connect(self._pump_messages)
        self._msg_timer.start(1)

        return self.app.exec()

    def _pump_messages(self):
        msg = ctypes.wintypes.MSG()
        while ctypes.windll.user32.PeekMessageW(
            ctypes.byref(msg), None, 0, 0, 1
        ):
            ctypes.windll.user32.TranslateMessage(ctypes.byref(msg))
            ctypes.windll.user32.DispatchMessageW(ctypes.byref(msg))


def main():
    app = PasteFlowApp()
    sys.exit(app.run())


if __name__ == "__main__":
    main()
