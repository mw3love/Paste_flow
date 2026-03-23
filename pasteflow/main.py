"""PasteFlow 진입점 — 모듈 오케스트레이션

클립보드 모니터 → DB → 큐 → UI 간 이벤트 흐름 관리.
"""
import sys
import os
import ctypes
import threading
import time
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QTimer

from pasteflow.database import Database
from pasteflow.models import ClipboardItem
from pasteflow.paste_queue import PasteQueue
from pasteflow.clipboard_monitor import ClipboardMonitor
from pasteflow.paste_interceptor import PasteInterceptor
from pasteflow.hotkey_manager import HotkeyManager
from pasteflow.ui.panel import ClipboardPanel
from pasteflow.ui.tray import TrayIcon


class PasteFlowApp:
    """PasteFlow 앱 오케스트레이션"""

    def __init__(self):
        self.app = QApplication(sys.argv)
        self.app.setQuitOnLastWindowClosed(False)

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

        # 시그널 연결
        self._connect_signals()

    def _connect_signals(self):
        """모든 시그널 연결"""
        self.tray.quit_requested.connect(self._quit)
        self.tray.panel_toggle_requested.connect(self._toggle_panel)

        self.panel.paste_item_requested.connect(self._on_panel_paste)
        self.panel.copy_item_requested.connect(self._on_copy_item)
        self.panel.pin_item_requested.connect(self._on_pin_item)
        self.panel.unpin_item_requested.connect(self._on_unpin_item)
        self.panel.delete_item_requested.connect(self._on_delete_item)
        self.panel.pin_reorder_requested.connect(self._on_pin_reorder)

        self.hotkey_manager.register("alt+v", self._toggle_panel)

        for n in range(1, 10):
            self.hotkey_manager.register(f"alt+{n}", lambda idx=n: self._on_direct_paste(idx))

    def _on_new_clipboard_item(self, item: ClipboardItem):
        """클립보드 모니터 콜백 — 새 항목 감지"""
        saved = self.db.save_item(item)
        self.queue.add_item(saved)

        # 패널이 아직 안 보이면 현재 포커스 윈도우 기억
        if not self.panel.isVisible():
            self._prev_foreground_hwnd = ctypes.windll.user32.GetForegroundWindow()

        self._refresh_panel()
        if not self.panel.isVisible():
            # 포커스를 빼앗지 않고 표시 (SW_SHOWNOACTIVATE)
            self.panel.show()
            hwnd = int(self.panel.winId())
            ctypes.windll.user32.ShowWindow(hwnd, 4)  # SW_SHOWNOACTIVATE

    def _on_paste_from_hook(self, item: ClipboardItem):
        """붙여넣기 콜백 — 훅 스레드에서 호출됨 → 메인 스레드로 전달"""
        QTimer.singleShot(0, self._update_paste_ui)

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
            self.panel.show()
            self.panel.raise_()
            self.panel.activateWindow()

    def _refresh_panel(self):
        """패널 데이터 갱신"""
        pinned = self.db.get_pinned_items()
        history = self.db.get_recent_items()
        pointer, total = self.queue.get_status()
        self.panel.refresh(pinned, history, pointer, total)

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
        target_hwnd = self._prev_foreground_hwnd

        # 클립보드 설정
        self.interceptor._set_clipboard(item)

        def _do_paste():
            try:
                time.sleep(0.05)
                if target_hwnd:
                    ctypes.windll.user32.SetForegroundWindow(target_hwnd)
                    time.sleep(0.1)
                self.interceptor.send_ctrl_v_to(None)
                time.sleep(0.2)
                QTimer.singleShot(0, self._reactivate_panel)
            except Exception as e:
                print(f"[PanelPaste] Error: {e}")

        threading.Thread(target=_do_paste, daemon=True).start()

    def _reactivate_panel(self):
        """붙여넣기 완료 → 패널 다시 활성화"""
        try:
            if self.panel.isVisible():
                self.panel.raise_()
                self.panel.activateWindow()
        except Exception:
            pass

    def _on_copy_item(self, item: ClipboardItem):
        """고정 항목 클릭 → 클립보드 복사 + 큐 추가"""
        self.interceptor._set_clipboard(item)
        self.queue.add_item(item)
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

    def _quit(self):
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
