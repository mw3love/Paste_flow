"""순차 붙여넣기 진행 HUD — 우하단에 큐 목록과 포인터를 실시간 표시.

포커스를 빼앗지 않는 비활성 창(토스트와 동일 플래그)이라 붙여넣기에 간섭하지 않는다.
첫 Ctrl+Shift+V에 표시되고 매 붙여넣기마다 갱신, 큐 소진/중단 후 잠시 뒤 fade-out.
"""
from PyQt6.QtWidgets import QWidget, QLabel, QVBoxLayout, QHBoxLayout, QPushButton
from PyQt6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve, pyqtSignal
from PyQt6.QtGui import QGuiApplication, QPainter, QColor, QPen

from pasteflow.ui.theme import COLORS
from pasteflow.ui.toast import reserve_bottom

# 화면 가장자리 여백 / HUD와 토스트 스택 사이 간격 (px)
_SCREEN_MARGIN = 20
_HUD_GAP = 10
# 큐가 길 때 표시할 최대 행 수 — 초과분은 "외 N개"로 축약
_MAX_ROWS = 10
# 큐 소진/중단 후 HUD가 머무는 시간(ms)
_FINISH_LINGER_MS = 1200


def _elide(text: str, limit: int = 36) -> str:
    text = " ".join((text or "").split())
    if len(text) > limit:
        return text[:limit] + "…"
    return text or "(빈 항목)"


class PasteHud(QWidget):
    """순차 붙여넣기 진행 상황 HUD (단일 인스턴스 재사용)."""

    # ✕ 버튼 클릭 — 남은 붙여넣기 취소(큐 비우기 + HUD 닫기)를 main이 처리
    cancel_requested = pyqtSignal()

    def __init__(self):
        super().__init__(None, Qt.WindowType.FramelessWindowHint |
                         Qt.WindowType.WindowStaysOnTopHint |
                         Qt.WindowType.Tool |
                         Qt.WindowType.BypassWindowManagerHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(4)
        self._layout = layout

        # 헤더 행: 제목 + (여백) + ✕ 취소 버튼
        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(8)
        self._header = QLabel()
        self._header.setStyleSheet(
            f"color: {COLORS['peach']}; font-size: 15px; font-weight: 700;"
            f" background: transparent;")
        header_row.addWidget(self._header)
        header_row.addStretch(1)

        self._cancel_btn = QPushButton("✕")
        self._cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._cancel_btn.setToolTip("남은 붙여넣기 취소")
        self._cancel_btn.setFixedSize(20, 20)
        self._cancel_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._cancel_btn.setStyleSheet(
            f"QPushButton {{ color: {COLORS['subtext0']}; background: transparent;"
            f" border: none; font-size: 14px; font-weight: 700; padding: 0; }}"
            f" QPushButton:hover {{ color: {COLORS['peach']}; }}")
        self._cancel_btn.clicked.connect(self.cancel_requested.emit)
        header_row.addWidget(self._cancel_btn)
        layout.addLayout(header_row)

        self._rows: list[QLabel] = []

        self._finish_timer = QTimer(self)
        self._finish_timer.setSingleShot(True)
        self._finish_timer.timeout.connect(self._start_fade_out)

        self._anim = QPropertyAnimation(self, b"windowOpacity")
        self._anim.setDuration(400)
        self._anim.setEasingCurve(QEasingCurve.Type.InCubic)
        self._anim.finished.connect(self._on_fade_done)
        self._fading = False

    def show_progress(self, items: list, pointer: int):
        """큐 목록 + 현재 포인터로 HUD를 표시/갱신."""
        self._finish_timer.stop()
        self._anim.stop()
        self._fading = False
        self.setWindowOpacity(1.0)
        self._render(items, pointer)
        self._reposition()
        if not self.isVisible():
            self.show()

    def finish(self):
        """큐 소진/중단 — 잠시 머문 뒤 fade-out. 표시 중이 아니면 무시."""
        if not self.isVisible() or self._fading:
            return
        self._finish_timer.start(_FINISH_LINGER_MS)

    def dismiss(self):
        """사용자 명시 취소(✕) — linger 없이 즉시 fade-out. 표시 중이 아니면 무시."""
        if not self.isVisible() or self._fading:
            return
        self._finish_timer.stop()
        self._start_fade_out()

    def _render(self, items: list, pointer: int):
        total = len(items)
        done = min(pointer, total)
        self._header.setText(f"순차 붙여넣기  {done}/{total}")

        # 표시할 줄 목록 구성 ((텍스트, 색, 굵기))
        lines: list[tuple[str, str, str]] = []
        for i, item in enumerate(items):
            if len(lines) >= _MAX_ROWS and total > _MAX_ROWS:
                lines.append((f"     외 {total - _MAX_ROWS}개",
                               COLORS['overlay0'], "400"))
                break
            preview = _elide(getattr(item, "preview_text", "") or "")
            if i < pointer:
                mark, color, weight = "✓", COLORS['overlay0'], "400"
            elif i == pointer:
                mark, color, weight = "▶", COLORS['text'], "700"
            else:
                mark, color, weight = "·", COLORS['subtext0'], "400"
            lines.append((f"{mark}  {i + 1}. {preview}", color, weight))

        # 행 위젯 동기화 — 부족하면 생성, 남으면 숨김
        while len(self._rows) < len(lines):
            lbl = QLabel()
            self._layout.addWidget(lbl)
            self._rows.append(lbl)
        for idx, lbl in enumerate(self._rows):
            if idx < len(lines):
                text, color, weight = lines[idx]
                lbl.setText(text)
                lbl.setStyleSheet(
                    f"color: {color}; font-size: 13px; font-weight: {weight};"
                    f" background: transparent;")
                lbl.show()
            else:
                lbl.hide()

    def _reposition(self):
        """우하단 코너에 배치하고 토스트 스택이 HUD 위로 쌓이도록 여백 확보."""
        self.adjustSize()
        screen = QGuiApplication.primaryScreen().availableGeometry()
        x = screen.right() - _SCREEN_MARGIN - self.width()
        y = screen.bottom() - _SCREEN_MARGIN - self.height()
        self.move(x, y)
        reserve_bottom(self.height() + _HUD_GAP)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(1, 1, -1, -1)
        painter.setBrush(QColor(COLORS['base']))
        pen = QPen(QColor(COLORS['peach']))
        pen.setWidth(2)
        painter.setPen(pen)
        painter.drawRoundedRect(rect, 12, 12)

    def _start_fade_out(self):
        self._fading = True
        self._anim.stop()
        self._anim.setStartValue(self.windowOpacity())
        self._anim.setEndValue(0.0)
        self._anim.start()

    def _on_fade_done(self):
        if not self._fading:
            return
        self._fading = False
        self.hide()
        self.setWindowOpacity(1.0)
        reserve_bottom(0)
