"""음성 입력(STT) 녹음 중 표시 — 커서 근처에 뜨는 음량 반응 이퀄라이저 pill.

Wispr Flow의 녹음 표시를 참고해 추가(2026-08-02, 사용자 실사용 비교 피드백). 녹음 중에만
떴다가 사라지는 형태로 범위를 좁혔다 — 마우스를 상시 따라다니는 플로팅 pill(Wispr의 다른
쪽 기능)은 항상 켜진 오버레이라 설계 부담이 커 별도 라운드로 미뤘다(사용자 확인,
2026-08-02).

toast.py의 커서 앵커 배치(`_place_anchored`)와 동일한 위치 규칙을 쓰되, 클릭으로
녹음을 멈출 수 있어야 하므로 `WindowTransparentForInput`(클릭 통과)은 쓰지 않는다 —
그래서 커서 앵커 토스트가 아니라 별도의 작은 위젯으로 분리했다.
"""
import math
import time

from PyQt6.QtCore import Qt, QPoint, pyqtSignal
from PyQt6.QtGui import QGuiApplication, QPainter, QColor
from PyQt6.QtWidgets import QWidget

from pasteflow.ui.theme import COLORS

_N_BARS = 5
_BAR_W = 6
_BAR_GAP = 6
_PILL_W = 96
_PILL_H = 40


class SttIndicator(QWidget):
    """녹음 중 커서 근처에 뜨는 음량 반응 이퀄라이저 pill. 클릭하면 녹음 종료."""

    stop_clicked = pyqtSignal()

    def __init__(self, anchor: QPoint):
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.BypassWindowManagerHint,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)  # 대상 앱 포커스를 뺏지 않음
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("클릭하면 녹음을 종료합니다")
        self.setFixedSize(_PILL_W, _PILL_H)
        self._level = 0.0
        self._bar_levels = [0.0] * _N_BARS
        self._place_near(anchor)
        self.show()

    def _place_near(self, anchor: QPoint):
        """앵커(커서) 옆 +16px, 화면 경계에서 반전 — toast.py `_place_anchored`(center=False)와 동일 규칙."""
        screen = QGuiApplication.screenAt(anchor) or QGuiApplication.primaryScreen()
        avail = screen.availableGeometry()
        w, h = self.width(), self.height()
        x = anchor.x() + 16
        y = anchor.y() + 16
        if x + w > avail.right():
            x = anchor.x() - w - 16
        if y + h > avail.bottom():
            y = anchor.y() - h - 16
        x = max(avail.left(), min(x, avail.right() - w))
        y = max(avail.top(), min(y, avail.bottom() - h))
        self.move(x, y)

    def set_level(self, level: float):
        """0.0~1.0 음량(RMS)을 받아 막대 높이를 갱신한다.

        막대마다 위상이 다른 사인파로 살짝 흔들어(jitter) 실제 이퀄라이저처럼 보이게 하고,
        이전 값과 50:50으로 섞어 매 틱(50ms) 급격히 튀지 않게 스무딩한다.
        """
        self._level = max(0.0, min(1.0, level))
        t = time.monotonic()
        for i in range(_N_BARS):
            jitter = 0.7 + 0.3 * math.sin(t * 7 + i * 1.3)
            target = self._level * jitter
            self._bar_levels[i] = self._bar_levels[i] * 0.5 + target * 0.5
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect()

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(COLORS['mantle']))
        painter.drawRoundedRect(rect, 18, 18)

        total_w = _N_BARS * _BAR_W + (_N_BARS - 1) * _BAR_GAP
        x0 = (rect.width() - total_w) // 2
        max_h = rect.height() - 12
        painter.setBrush(QColor(COLORS['peach']))
        for i, lv in enumerate(self._bar_levels):
            # RMS는 보통 낮은 값(정상 발화 0.02~0.15대)이라 3배 증폭해야 막대가 눈에 띈다.
            h = max(4, int(max_h * min(1.0, lv * 3)))
            x = x0 + i * (_BAR_W + _BAR_GAP)
            y = rect.height() // 2 - h // 2
            painter.drawRoundedRect(x, y, _BAR_W, h, 3, 3)
        painter.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.stop_clicked.emit()

    def dismiss(self):
        """즉시 닫기(idempotent) — Qt 안전 삭제(`deleteLater`)로 시그널 처리 중 재진입 방지."""
        self.hide()
        self.deleteLater()
