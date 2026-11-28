"""「판서 중」 작은 아이콘 — 판서가 앱 쓰기 상태일 때 모니터 오른쪽 위에 뜨는 둥근 연필 버튼.

계획: docs/plan/20261125-2226-판서-조작-개선.md. 누르면 clicked를 보내고, app/live_draw.py가
판서 창을 그리기 상태로 돌린다(툴바가 나오고 이 아이콘은 닫힌다). 나타날 때 포커스를 뺏지 않고,
판서 창(TOPMOST)보다 위에 오게 claim_topmost를 건다.
"""
from PyQt6.QtCore import QPoint, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QWidget

from pasteflow.ui.menu_icons import menu_icon
from pasteflow.ui.theme import PEACH
from pasteflow.winapi import click_through

_SIZE = 36        # 지름(논리 px)
_ICON = 20        # 연필 그림 크기
_MARGIN = 12      # 모니터 사용 가능 영역 오른쪽·위에서 띄우는 거리
_DIM = 0.45       # 평소 불투명도 — 화면을 덜 가리게, 마우스를 올리면 1.0


class LiveDrawBadge(QWidget):
    clicked = pyqtSignal()

    def __init__(self, screen, tooltip: str):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedSize(_SIZE, _SIZE)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tooltip)
        self._icon = menu_icon("pencil-simple", PEACH, PEACH).pixmap(QSize(_ICON, _ICON))
        a = screen.availableGeometry()
        self.move(QPoint(a.right() - _MARGIN - _SIZE + 1, a.top() + _MARGIN))
        self.setWindowOpacity(_DIM)

    def show_on_top(self):
        self.show()
        click_through.claim_topmost(int(self.winId()))

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(18, 18, 18, 220))
        p.drawEllipse(QRectF(0.5, 0.5, _SIZE - 1, _SIZE - 1))
        off = (_SIZE - _ICON) // 2
        p.drawPixmap(off, off, self._icon)

    def enterEvent(self, e):
        self.setWindowOpacity(1.0)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.setWindowOpacity(_DIM)
        super().leaveEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()
