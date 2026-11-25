"""라이브 화면 판서 — 단축키 슬롯과 「판서 중」 아이콘. 계획: docs/plan/done/20261125-2040-라이브-화면-판서.md,
docs/plan/20261125-2226-판서-조작-개선.md.

단축키 한 번 = 꺼져 있으면 커서 모니터에 켜기(그리기), 켜져 있으면 그리기↔앱 쓰기 전환.
그리기 상태의 Esc는 판서 창을 숨긴다(hidden — 아래). 판서 창의 Ctrl+C는
영역 캡처와 같은 저장 경로(_on_capture_region)로 간다. 닫을 때 쓰던 도구를 DB 설정
live_draw_last_tool에 적어 두고 다음에 그 도구로 시작한다(숨길 때도).

Esc는 판서 창을 숨기기만 한다(docs/plan/20261125-2301-판서-복원과-새-칠판.md). 창은 모니터 이름별로
_live_draw_boards에 남아 있다가, 보이는 판서 창이 없을 때 단축키를 누르면 커서 모니터의 것을 다시 연다.
앱 종료 때 모두 닫는다.
"""
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import QApplication

# 판서에서 고를 수 있는 도구 이름 — 주석 편집기 _TOOLS에서 도형 버튼(shape)을 실제 모양 rect·ellipse로 푼 것
_LIVE_DRAW_TOOLS = ("select", "marker", "pen", "rect", "ellipse", "arrow", "text", "badge", "fade")
_LAST_TOOL_KEY = "live_draw_last_tool"


def pick_start_tool(saved) -> str:
    """저장된 마지막 도구 → 시작 도구. 없거나·도구 없음이었거나·모르는 이름이면 펜."""
    return saved if saved in _LIVE_DRAW_TOOLS else "pen"


def board_action(stored, cur) -> str:
    """숨겨 둔 칠판의 모니터 geometry(없으면 None)와 지금 geometry → "new"·"reuse"·"replace".
    해상도·배치가 바뀌었으면 그림이 화면과 어긋나므로 바꿔 만든다."""
    if stored is None:
        return "new"
    return "reuse" if tuple(stored) == tuple(cur) else "replace"


def _geom_tuple(screen) -> tuple:
    g = screen.geometry()
    return (g.x(), g.y(), g.width(), g.height())


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
        key, cur = screen.name(), _geom_tuple(screen)
        old = self._live_draw_boards.get(key)
        act = board_action(None if old is None else old._board_geom, cur)
        if act == "reuse":
            self._live_draw = old
            old.reopen()
            return
        if act == "replace":
            self._live_draw_boards.pop(key, None)
            old.close()
        ov = LiveDrawOverlay(screen)
        ov._board_geom = cur
        ov.screen_copy_requested.connect(self._on_capture_region)
        ov.mode_changed.connect(self._on_live_draw_mode)
        ov.hidden.connect(lambda o=ov: self._on_live_draw_hidden(o))
        ov.closed.connect(lambda o=ov: self._on_live_draw_closed(o))
        self._live_draw_boards[key] = ov
        self._live_draw = ov
        ov.start(pick_start_tool(self.db.get_setting(_LAST_TOOL_KEY, "")))

    def _on_live_draw_mode(self, drawing: bool):
        """앱 쓰기 상태에서만 모니터 오른쪽 위에 「판서 중」 작은 아이콘(누르면 그리기로)."""
        self._dismiss_live_draw_chip()
        if drawing or self._live_draw is None:
            return
        from pasteflow.ui.live_draw_badge import LiveDrawBadge
        hotkey = _pretty_hotkey(self.db.get_setting("hotkey_live_draw", "ctrl+shift+d"))
        badge = LiveDrawBadge(self._live_draw._screen, f"판서 중 — 누르면 그리기 ({hotkey})")
        badge.clicked.connect(self._on_live_draw_badge_clicked)
        self._live_draw_chip = badge
        badge.show_on_top()

    def _on_live_draw_badge_clicked(self):
        ov = self._live_draw
        if ov is not None and not ov.is_edit_mode():
            ov.toggle_mode()   # 그리기로 — mode_changed(True)가 아이콘을 닫는다

    def _dismiss_live_draw_chip(self):
        chip = self._live_draw_chip
        self._live_draw_chip = None
        if chip is not None:
            chip.close()
            chip.deleteLater()

    def _on_live_draw_hidden(self, ov):
        """Esc로 숨김 — 창은 _live_draw_boards에 남겨 다시 열 수 있게 둔다."""
        if self._live_draw is not ov:
            return
        self._dismiss_live_draw_chip()
        self.db.set_setting(_LAST_TOOL_KEY, ov.current_tool or "")
        self._live_draw = None

    def _on_live_draw_closed(self, ov):
        for k, v in list(self._live_draw_boards.items()):
            if v is ov:
                del self._live_draw_boards[k]
        if self._live_draw is ov:
            self._dismiss_live_draw_chip()
            self.db.set_setting(_LAST_TOOL_KEY, ov.current_tool or "")
            self._live_draw = None

    def _close_live_draw(self):
        """앱 종료 때 — 숨긴 것까지 모든 판서 창을 닫는다(그림은 버린다)."""
        for ov in list(self._live_draw_boards.values()):
            ov.close()
