"""우하단 알림 토스트 — 스택 방식으로 쌓이고 fade-out.

_ToastStack 싱글턴이 활성 토스트를 우하단 코너 기준으로 위로 쌓아 관리한다.
가장 새 토스트가 코너(맨 아래), 이전 것들이 위로 밀린다. 토스트가 닫히면
남은 토스트가 코너 쪽으로 다시 정렬된다.
"""
from PyQt6.QtWidgets import QWidget, QLabel, QHBoxLayout
from PyQt6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve, QPoint
from PyQt6.QtGui import QGuiApplication, QPainter, QColor, QPen

from pasteflow.ui.theme import COLORS

# 토스트 간 세로 간격 / 화면 가장자리 여백 (px)
_TOAST_GAP = 10
_SCREEN_MARGIN = 20
# 동시에 보일 수 있는 최대 토스트 수 — 초과 시 가장 오래된 것 즉시 제거
_MAX_STACK = 5

# 복사 알림 토스트 지속 시간 (복사가 잦으므로 기본 3초보다 짧게)
COPY_TOAST_DURATION_MS = 2000


class _ToastStack:
    """활성 토스트를 우하단 코너 기준으로 위로 쌓아 배치하는 매니저 (싱글턴)."""

    def __init__(self):
        self._toasts: list["ToastNotification"] = []
        # 우하단의 다른 위젯(붙여넣기 HUD 등)을 위해 비워둘 하단 여백 (px)
        self._bottom_reserved = 0

    def set_bottom_reserved(self, px: int):
        """하단 여백을 설정 — 토스트 스택이 그 위로 쌓이도록 한다."""
        px = max(0, int(px))
        if px != self._bottom_reserved:
            self._bottom_reserved = px
            self._relayout()

    def add(self, toast: "ToastNotification"):
        self._toasts.append(toast)
        # 최대 개수 초과 → 가장 오래된 것을 동기 제거 후 즉시 닫음
        while len(self._toasts) > _MAX_STACK:
            oldest = self._toasts.pop(0)
            oldest.dismiss_now()
        self._relayout()

    def remove(self, toast: "ToastNotification"):
        if toast in self._toasts:
            self._toasts.remove(toast)
            self._relayout()

    def _relayout(self):
        """우하단 코너 기준으로 모든 토스트를 다시 배치 (최신 = 맨 아래)."""
        screen = QGuiApplication.primaryScreen().availableGeometry()
        x_right = screen.right() - _SCREEN_MARGIN
        y = screen.bottom() - _SCREEN_MARGIN - self._bottom_reserved
        # 리스트 끝(최신)이 코너에 오도록 역순으로 아래→위 배치
        for toast in reversed(self._toasts):
            top = y - toast.height()
            toast.move_to(QPoint(x_right - toast.width(), top))
            y = top - _TOAST_GAP


_stack = _ToastStack()


def reserve_bottom(px: int):
    """우하단 위젯(붙여넣기 HUD 등)을 위해 토스트 스택 하단 여백을 확보."""
    _stack.set_bottom_reserved(px)


class ToastNotification(QWidget):
    """우하단 스택에 쌓이는 알림 토스트."""

    def __init__(self, message: str, duration_ms: int = 3000,
                 icon: str = "✓", badge: str = None,
                 badge_position: str = "trailing"):
        """
        badge_position: "leading"(아이콘과 본문 사이) | "trailing"(본문 뒤, 기본)
        """
        super().__init__(None, Qt.WindowType.FramelessWindowHint |
                         Qt.WindowType.WindowStaysOnTopHint |
                         Qt.WindowType.Tool |
                         Qt.WindowType.BypassWindowManagerHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        self._closing = False
        self._pos_anim = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(24, 18, 24, 18)
        layout.setSpacing(12)

        icon_lbl = QLabel(icon)
        icon_lbl.setStyleSheet(
            f"color: {COLORS['peach']}; font-size: 22px; background: transparent;")
        layout.addWidget(icon_lbl)

        # 큐 개수 등 강조 배지 (선택) — 본문 앞/뒤 배치 선택 가능
        badge_lbl = None
        if badge:
            badge_lbl = QLabel(badge)
            badge_lbl.setStyleSheet(
                f"color: {COLORS['peach']}; font-size: 16px; font-weight: 600;"
                f" background: transparent;")

        if badge_lbl is not None and badge_position == "leading":
            layout.addWidget(badge_lbl)

        label = QLabel(message)
        label.setStyleSheet(
            f"color: {COLORS['text']}; font-size: 18px; background: transparent;")
        layout.addWidget(label)

        if badge_lbl is not None and badge_position == "trailing":
            layout.addWidget(badge_lbl)

        self.setStyleSheet("background: transparent;")
        self.setWindowOpacity(0.0)
        self.adjustSize()

        # 스택에 등록 → 위치 결정 (show 전이라 본인은 직접 배치, 기존 토스트는 위로 슬라이드)
        _stack.add(self)
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

    def move_to(self, pos: QPoint):
        """스택 매니저가 호출 — 표시 전이면 즉시 이동, 표시 중이면 슬라이드."""
        if not self.isVisible():
            self.move(pos)
            return
        if self._pos_anim is not None:
            self._pos_anim.stop()
        anim = QPropertyAnimation(self, b"pos")
        anim.setDuration(160)
        anim.setStartValue(self.pos())
        anim.setEndValue(pos)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.start()
        self._pos_anim = anim

    def dismiss_now(self):
        """스택 한도 초과 — 즉시 제거."""
        if not self._closing:
            self._closing = True
            self.close()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(1, 1, -1, -1)
        painter.setBrush(QColor(COLORS['base']))
        pen = QPen(QColor(COLORS['peach']))
        pen.setWidth(2)
        painter.setPen(pen)
        painter.drawRoundedRect(rect, 12, 12)

    def closeEvent(self, event):
        _stack.remove(self)
        super().closeEvent(event)

    def _start_fade_out(self):
        if self._closing:
            return
        self._closing = True
        self._anim_out.start()


def _elide(text: str, limit: int = 30) -> str:
    """공백·줄바꿈을 단일 공백으로 합치고 limit 길이로 말줄임."""
    text = " ".join(text.split())
    if len(text) > limit:
        return text[:limit] + "…"
    return text


def show_copy_toast(item, queue_count: int) -> ToastNotification:
    """복사 알림 토스트 — 누적 큐 카운트(Q{n})를 본문 앞에 배치한 미리보기."""
    preview = _elide(getattr(item, "preview_text", None) or "클립보드 항목")
    return ToastNotification(
        preview,
        duration_ms=COPY_TOAST_DURATION_MS,
        icon="📋",
        badge=f"Q{queue_count}",
        badge_position="leading",
    )
