"""시작 알림 토스트 창 — 우하단에 잠깐 표시 후 fade-out."""
from PyQt6.QtWidgets import QWidget, QLabel, QHBoxLayout
from PyQt6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve, QRect
from PyQt6.QtGui import QGuiApplication, QPainter, QColor, QPen

COLORS = {
    "base": "#1e1e2e",
    "peach": "#fab387",
    "text": "#cdd6f4",
}


class ToastNotification(QWidget):
    def __init__(self, message: str, duration_ms: int = 3000):
        super().__init__(None, Qt.WindowType.FramelessWindowHint |
                         Qt.WindowType.WindowStaysOnTopHint |
                         Qt.WindowType.Tool |
                         Qt.WindowType.BypassWindowManagerHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(8)

        icon = QLabel("✓")
        icon.setStyleSheet(f"color: {COLORS['peach']}; font-size: 14px; background: transparent;")
        layout.addWidget(icon)

        label = QLabel(message)
        label.setStyleSheet(f"color: {COLORS['text']}; font-size: 13px; background: transparent;")
        layout.addWidget(label)

        self.setStyleSheet("background: transparent;")

        self.setWindowOpacity(0.0)
        self.adjustSize()
        self._position_bottom_right()
        self.show()

        # fade-in
        self._anim_in = QPropertyAnimation(self, b"windowOpacity")
        self._anim_in.setDuration(300)
        self._anim_in.setStartValue(0.0)
        self._anim_in.setEndValue(1.0)
        self._anim_in.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim_in.start()

        # fade-out
        self._anim_out = QPropertyAnimation(self, b"windowOpacity")
        self._anim_out.setDuration(400)
        self._anim_out.setStartValue(1.0)
        self._anim_out.setEndValue(0.0)
        self._anim_out.setEasingCurve(QEasingCurve.Type.InCubic)
        self._anim_out.finished.connect(self.close)

        QTimer.singleShot(duration_ms, self._start_fade_out)

    def _position_bottom_right(self):
        screen = QGuiApplication.primaryScreen().availableGeometry()
        margin = 20
        x = screen.right() - self.width() - margin
        y = screen.bottom() - self.height() - margin
        self.move(x, y)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(1, 1, -1, -1)
        painter.setBrush(QColor(COLORS['base']))
        pen = QPen(QColor(COLORS['peach']))
        pen.setWidth(1)
        painter.setPen(pen)
        painter.drawRoundedRect(rect, 8, 8)

    def _start_fade_out(self):
        self._anim_out.start()
