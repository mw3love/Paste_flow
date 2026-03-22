"""PasteFlow 진입점 — 모듈 오케스트레이션

클립보드 모니터 → DB → 큐 → UI 간 이벤트 흐름 관리.
"""
import sys
import os
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QTimer

from pasteflow.database import Database
from pasteflow.models import ClipboardItem
from pasteflow.paste_queue import PasteQueue
from pasteflow.clipboard_monitor import ClipboardMonitor
from pasteflow.paste_interceptor import PasteInterceptor
from pasteflow.hotkey_manager import HotkeyManager
from pasteflow.ui.mini_window import MiniWindow
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

        # UI
        self.mini_window = MiniWindow()
        self.panel = ClipboardPanel()
        self.tray = TrayIcon()

        # 시그널 연결
        self._connect_signals()

        # 큐에는 DB 히스토리를 넣지 않음 — 큐는 현재 세션 복사분만 관리
        # DB 히스토리는 패널 UI에서만 사용

    def _connect_signals(self):
        """모든 시그널 연결"""
        # 트레이
        self.tray.quit_requested.connect(self._quit)
        self.tray.panel_toggle_requested.connect(self._toggle_panel)

        # 미니 창 → 패널
        self.mini_window.open_panel_requested.connect(self._toggle_panel)

        # 패널 시그널
        self.panel.paste_item_requested.connect(self._on_panel_paste)
        self.panel.copy_item_requested.connect(self._on_copy_item)
        self.panel.pin_item_requested.connect(self._on_pin_item)
        self.panel.unpin_item_requested.connect(self._on_unpin_item)
        self.panel.delete_item_requested.connect(self._on_delete_item)
        self.panel.pin_reorder_requested.connect(self._on_pin_reorder)

        # 글로벌 단축키: Alt+V → 패널 토글
        self.hotkey_manager.register("alt+v", self._toggle_panel)

        # Alt+1~9 → 히스토리 N번째 항목 즉시 붙여넣기 (F5-1)
        for n in range(1, 10):
            self.hotkey_manager.register(f"alt+{n}", lambda idx=n: self._on_direct_paste(idx))

    def _on_new_clipboard_item(self, item: ClipboardItem):
        """클립보드 모니터 콜백 — 새 항목 감지"""
        # DB 저장
        saved = self.db.save_item(item)

        # 큐에 추가
        self.queue.add_item(saved)

        # 미니 창 표시
        recent = self.queue.get_items()
        pointer, total = self.queue.get_status()
        self.mini_window.show_and_refresh(recent, pointer, total)

        # 패널 갱신 (열려 있으면)
        if self.panel.isVisible():
            self._refresh_panel()

    def _on_paste_from_hook(self, item: ClipboardItem):
        """붙여넣기 콜백 — 훅 스레드에서 호출됨 → 메인 스레드로 전달"""
        QTimer.singleShot(0, self._update_paste_ui)

    def _update_paste_ui(self):
        """메인 스레드에서 붙여넣기 UI 업데이트"""
        pointer, total = self.queue.get_status()
        self.mini_window.update_status(pointer, total)

        # 패널도 갱신
        if self.panel.isVisible():
            self.panel.update_queue_status(pointer, total)

    def _toggle_panel(self):
        """패널 토글"""
        if self.panel.isVisible():
            self.panel.hide()
        else:
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
        """Alt+N 직접 붙여넣기 (F5-1) — 히스토리 N번째 항목"""
        history = self.db.get_recent_items()
        if n > len(history):
            return
        item = history[n - 1]
        # 별도 스레드에서 실행 (SendInput 대기 방지)
        import threading
        threading.Thread(
            target=self.interceptor.direct_paste, args=(item,), daemon=True
        ).start()

    def _on_panel_paste(self, item: ClipboardItem):
        """패널에서 붙여넣기 요청 — 클립보드 설정 후 SendInput (F5-2, F5-4)"""
        import threading
        threading.Thread(
            target=self.interceptor.direct_paste, args=(item,), daemon=True
        ).start()

    def _on_copy_item(self, item: ClipboardItem):
        """고정 항목 클릭 → 클립보드 복사 + 큐 추가 (F7-6)"""
        self.interceptor._set_clipboard(item)
        self.queue.add_item(item)

        # 미니 창 업데이트
        recent = self.queue.get_items()
        pointer, total = self.queue.get_status()
        self.mini_window.show_and_refresh(recent, pointer, total)

    def _on_pin_item(self, item_id: int):
        """항목 고정"""
        self.db.pin_item(item_id)
        self._refresh_panel()

    def _on_unpin_item(self, item_id: int):
        """항목 고정 해제"""
        self.db.unpin_item(item_id)
        self._refresh_panel()

    def _on_delete_item(self, item_id: int):
        """항목 삭제"""
        self.db.delete_item(item_id)
        self._refresh_panel()

    def _on_pin_reorder(self, id_order_list: list):
        """고정 항목 순서 변경 (F7-5)"""
        self.db.update_pin_orders(id_order_list)
        self._refresh_panel()

    def _quit(self):
        """앱 종료"""
        self.interceptor.stop()
        self.monitor.stop()
        self.hotkey_manager.destroy()
        self.tray.hide()
        self.db.close()
        self.app.quit()

    def run(self):
        """앱 시작"""
        # 모니터 시작
        self.monitor.start()

        # Ctrl+V 인터셉터 시작
        self.interceptor.start()

        # 시스템 트레이 표시
        self.tray.show()

        # Windows 메시지 루프 폴링 (클립보드 모니터용)
        self._msg_timer = QTimer()
        self._msg_timer.timeout.connect(self._pump_messages)
        self._msg_timer.start(50)  # 50ms 간격

        return self.app.exec()

    def _pump_messages(self):
        """Windows 메시지 루프 처리 (WM_CLIPBOARDUPDATE + WM_HOTKEY 수신용)"""
        import ctypes
        import ctypes.wintypes
        msg = ctypes.wintypes.MSG()
        while ctypes.windll.user32.PeekMessageW(
            ctypes.byref(msg), None, 0, 0, 1  # PM_REMOVE
        ):
            ctypes.windll.user32.TranslateMessage(ctypes.byref(msg))
            ctypes.windll.user32.DispatchMessageW(ctypes.byref(msg))


def main():
    app = PasteFlowApp()
    sys.exit(app.run())


if __name__ == "__main__":
    main()
