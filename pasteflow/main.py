"""PasteFlow 진입점 — 모듈 오케스트레이션

클립보드 모니터 → DB → 큐 → UI 간 이벤트 흐름 관리.
"""
import sys
import os
import ctypes
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
from pasteflow.ui.tray import TrayIcon
from pasteflow.ui.settings_dialog import SettingsDialog


class _SignalBridge(QObject):
    """훅 스레드 → 메인 스레드 시그널 전달"""
    paste_happened = pyqtSignal()
    new_item_saved = pyqtSignal(object)  # ClipboardItem — 클립보드 모니터 스레드 → 메인 스레드


class PasteFlowApp:
    """PasteFlow 앱 오케스트레이션"""

    def __init__(self):
        self.app = QApplication(sys.argv)
        self.app.setQuitOnLastWindowClosed(False)

        # 스레드 안전 시그널 브릿지
        self._bridge = _SignalBridge()
        self._bridge.paste_happened.connect(self._update_paste_ui)
        self._bridge.new_item_saved.connect(self._on_new_item_ui)

        # 코어 모듈
        db_path = os.path.join(os.path.dirname(__file__), "..", "pasteflow.db")
        self.db = Database(db_path)
        self.queue = PasteQueue()
        self.monitor = ClipboardMonitor(on_new_item=self._on_new_clipboard_item)
        self.interceptor = PasteInterceptor(
            paste_queue=self.queue,
            clipboard_monitor=self.monitor,
            on_paste=self._on_paste_from_hook,
        )
        self.hotkey_manager = HotkeyManager()

        # UI (패널이 기본 UI — 미니창 없음)
        self.panel = ClipboardPanel()
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
        self.panel.edit_item_requested.connect(self._on_edit_item)

        self.hotkey_manager.register("alt+v", self._toggle_panel)

        for n in range(1, 10):
            self.hotkey_manager.register(f"alt+{n}", lambda idx=n: self._on_direct_paste(idx))

    def _on_new_clipboard_item(self, item: ClipboardItem):
        """클립보드 모니터 콜백 — 백그라운드 스레드에서 호출됨.
        DB/큐 작업만 수행하고 UI 갱신은 시그널로 메인 스레드에 위임."""
        saved = self.db.save_item(item)
        self.queue.add_item(saved)
        self._bridge.new_item_saved.emit(saved)

    def _on_new_item_ui(self, saved: ClipboardItem):
        """메인 스레드에서 UI 갱신 — new_item_saved 시그널 수신"""
        # 패널이 아직 안 보이면 현재 포커스 윈도우 기억
        if not self.panel.isVisible():
            self._prev_foreground_hwnd = ctypes.windll.user32.GetForegroundWindow()

        self._refresh_panel()
        if not self.panel.isVisible():
            # 포커스를 빼앗지 않고 표시 — WA_ShowWithoutActivating + SW_SHOWNOACTIVATE
            hwnd = int(self.panel.winId())
            ctypes.windll.user32.ShowWindow(hwnd, 4)  # SW_SHOWNOACTIVATE
            self.panel.setVisible(True)

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

        # 클립보드 설정
        self.interceptor._set_clipboard(full_item)

        def _do_paste():
            try:
                if target_hwnd:
                    ctypes.windll.user32.SetForegroundWindow(target_hwnd)
                    time.sleep(0.05)
                self.interceptor.send_ctrl_v_to(None)
                time.sleep(0.1)
            except Exception as e:
                print(f"[PanelPaste] Error: {e}")
            finally:
                QTimer.singleShot(0, self._reactivate_panel)

        threading.Thread(target=_do_paste, daemon=True).start()

    def _reactivate_panel(self):
        """붙여넣기 완료 → 패널 다시 활성화"""
        try:
            self.panel._paste_in_progress = False
            if self.panel.isVisible():
                self.panel.raise_()
                self.panel.activateWindow()
        except Exception:
            pass

    def _on_copy_item(self, item: ClipboardItem):
        """고정 항목 클릭 → 클립보드 복사 + 큐 추가"""
        full_item = self.db.get_item(item.id) or item
        self.interceptor._set_clipboard(full_item)
        self.queue.add_item(full_item)
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

    def _on_edit_item(self, item_id: int, new_text: str):
        self.db.update_item_text(item_id, new_text)
        self._refresh_panel()

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
            "hotkey_panel_toggle": self.db.get_setting("hotkey_panel_toggle", "alt+v"),
            "history_max": self.db.get_setting("history_max", "50"),
            "panel_auto_close": self.db.get_setting("panel_auto_close", "1"),
            "auto_start": self.db.get_setting("auto_start", "0"),
        }
        dlg = SettingsDialog(current)
        dlg.settings_changed.connect(self._on_settings_changed)
        dlg.exec()

    def _on_settings_changed(self, new_settings: dict):
        """설정 변경 적용"""
        # 단축키 비교는 DB 저장 전에 이전 값을 먼저 읽어야 함
        old_hotkey = self.db.get_setting("hotkey_panel_toggle", "alt+v")

        for key, value in new_settings.items():
            self.db.set_setting(key, value)

        # 패널 자동 닫기
        self.panel._auto_close = new_settings.get("panel_auto_close", "1") == "1"

        # 단축키 재등록
        new_hotkey = new_settings.get("hotkey_panel_toggle", "alt+v")
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
        self._msg_timer.start(50)

        return self.app.exec()

    def _pump_messages(self):
        import ctypes
        import ctypes.wintypes
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
