"""라이브 화면 판서 — 단축키 슬롯과 「판서 중」 칩. 계획: docs/plan/done/20261125-2040-라이브-화면-판서.md.

단축키 한 번 = 꺼져 있으면 커서 모니터에 켜기(그리기), 켜져 있으면 그리기↔앱 쓰기 전환.
끝내기는 그리기 상태의 Esc(판서 창이 스스로 닫고 closed를 보낸다). 판서 창의 Ctrl+C는
영역 캡처와 같은 저장 경로(_on_capture_region)로 간다.
"""
from PyQt6.QtCore import QPoint
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import QApplication


def _pretty_hotkey(s: str) -> str:
    return "+".join(p.capitalize() if len(p) > 1 else p.upper() for p in s.split("+") if p)


class LiveDrawMixin:
    def _on_live_draw_hotkey(self):
        ov = self._live_draw
        if ov is not None:
            ov.toggle_mode()
            return
        from pasteflow.ui.live_draw import LiveDrawOverlay
        screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        ov = LiveDrawOverlay(screen)
        ov.screen_copy_requested.connect(self._on_capture_region)
        ov.mode_changed.connect(self._on_live_draw_mode)
        ov.closed.connect(self._on_live_draw_closed)
        self._live_draw = ov
        ov.start()

    def _on_live_draw_mode(self, drawing: bool):
        """앱 쓰기 상태에서만 모니터 오른쪽 위에 「판서 중」 칩(클릭 통과)."""
        self._dismiss_live_draw_chip()
        if drawing or self._live_draw is None:
            return
        from pasteflow.ui.toast import ToastNotification
        hotkey = _pretty_hotkey(self.db.get_setting("hotkey_live_draw", "ctrl+shift+d"))
        avail = self._live_draw._screen.availableGeometry()
        self._live_draw_chip = ToastNotification(
            f"판서 중 · {hotkey}", duration_ms=0, icon="✏",
            anchor=QPoint(avail.right(), avail.top()))

    def _dismiss_live_draw_chip(self):
        chip = self._live_draw_chip
        self._live_draw_chip = None
        if chip is not None:
            chip.dismiss()

    def _on_live_draw_closed(self):
        self._dismiss_live_draw_chip()
        self._live_draw = None

    def _close_live_draw(self):
        """앱 종료 때 — 그림은 버린다(Esc와 같음)."""
        if self._live_draw is not None:
            self._live_draw.close()
