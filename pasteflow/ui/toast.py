"""우하단 알림 토스트 — 스택 방식으로 쌓이고 fade-out.

_ToastStack 싱글턴이 활성 토스트를 우하단 코너 기준으로 위로 쌓아 관리한다.
가장 새 토스트가 코너(맨 아래), 이전 것들이 위로 밀린다. 토스트가 닫히면
남은 토스트가 코너 쪽으로 다시 정렬된다.
"""
from PyQt6.QtWidgets import QWidget, QLabel, QHBoxLayout
from PyQt6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve, QPoint
from PyQt6.QtGui import QGuiApplication, QPainter, QColor, QPen, QPixmap

from pasteflow.ui.theme import COLORS

# 토스트 간 세로 간격 / 화면 가장자리 여백 (px)
_TOAST_GAP = 10
_SCREEN_MARGIN = 20
# 토스트 썸네일 한 변 최대 크기 (px) — 이미지→경로 붙여넣기 시각 확인용
_THUMB_MAX = 96
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
        # 스택이 떠 있는 동안 고정할 모니터 (첫 토스트 등록 시 커서 모니터로 잡음)
        self._screen = None

    def set_bottom_reserved(self, px: int):
        """하단 여백을 설정 — 토스트 스택이 그 위로 쌓이도록 한다."""
        px = max(0, int(px))
        if px != self._bottom_reserved:
            self._bottom_reserved = px
            self._relayout()

    def add(self, toast: "ToastNotification"):
        # 스택 토스트(시작 알림·복사 알림 등 수동적 알림)는 항상 주모니터 우하단에 고정한다.
        # 예측 가능한 위치 — 커서를 따라 모니터를 옮겨다니지 않는다. (능동적으로 기다리는
        # AI·OCR 진행/결과는 스택이 아니라 anchor/center 모드로 활성 모니터 중앙에 뜬다.)
        if not self._toasts:
            self._screen = QGuiApplication.primaryScreen()
        self._toasts.append(toast)
        # 최대 개수 초과 → 가장 오래된 것을 동기 제거 후 즉시 닫음
        while len(self._toasts) > _MAX_STACK:
            oldest = self._toasts.pop(0)
            oldest.dismiss_now()
        self._relayout()

    def remove(self, toast: "ToastNotification"):
        if toast in self._toasts:
            self._toasts.remove(toast)
            if not self._toasts:
                self._screen = None
            self._relayout()

    def _relayout(self):
        """주모니터(고정) 우하단 기준으로 모든 토스트를 다시 배치 (최신 = 맨 아래)."""
        scr = self._screen or QGuiApplication.primaryScreen()
        screen = scr.availableGeometry()
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
                 badge_position: str = "trailing",
                 image_path: str = None, image_bytes: bytes = None,
                 anchor: QPoint = None, center: bool = False):
        """
        badge_position: "leading"(아이콘과 본문 사이) | "trailing"(본문 뒤, 기본)
        image_path: 주어지면 아이콘과 본문 사이에 그 이미지의 썸네일을 표시
                    (이미지→경로 붙여넣기 시 "의도한 이미지 맞나" 시각 확인용).
                    로드 실패 시 조용히 생략.
        image_bytes: image_path 대신 메모리 바이트(PNG 썸네일 등)에서 썸네일을 그린다
                    — 복사 알림처럼 디스크 파일 경로가 없는 이미지 항목용. 로드 실패 시 생략.
        anchor: 주어지면 우하단 스택 대신 그 지점을 기준으로 배치하고 클릭을 아래 앱으로
                통과시킨다(작업 방해 0). 멀티모니터에서 시선이 닿는 모니터에 진행/결과를
                표시하기 위한 모드(OCR·AI 진행 칩).
        center: anchor와 함께 쓰며 True면 그 지점(커서) 옆이 아니라 **그 지점이 속한
                모니터 정중앙**에 배치한다(예측 가능·가장자리 잘림 없음). False면 +16px 옆.
        """
        flags = (Qt.WindowType.FramelessWindowHint |
                 Qt.WindowType.WindowStaysOnTopHint |
                 Qt.WindowType.Tool |
                 Qt.WindowType.BypassWindowManagerHint)
        # 커서 앵커 모드: 칩 아래 앱으로 클릭을 통과시켜 작업을 방해하지 않는다.
        if anchor is not None:
            flags |= Qt.WindowType.WindowTransparentForInput
        super().__init__(None, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        self._closing = False
        self._pos_anim = None
        self._anchor = anchor  # None=우하단 스택, QPoint=앵커 기준 배치
        self._center = center  # True면 앵커 모니터 정중앙, False면 앵커 옆 +16px

        layout = QHBoxLayout(self)
        layout.setContentsMargins(24, 18, 24, 18)
        layout.setSpacing(12)

        # icon 빈 문자열이면 아이콘 라벨 생략 (썸네일이 카테고리를 대신할 때)
        if icon:
            icon_lbl = QLabel(icon)
            icon_lbl.setStyleSheet(
                f"color: {COLORS['peach']}; font-size: 22px; background: transparent;")
            layout.addWidget(icon_lbl)

        # 썸네일 (선택) — 아이콘과 본문 사이. 디스크 경로(image_path) 또는
        # 메모리 바이트(image_bytes, 복사 알림의 항목 썸네일)에서 로드한다.
        pix = None
        if image_path:
            pix = QPixmap(image_path)
        elif image_bytes:
            _p = QPixmap()
            if _p.loadFromData(image_bytes):
                pix = _p
        if pix is not None:
            if not pix.isNull():
                thumb = QLabel()
                thumb.setPixmap(pix.scaled(
                    _THUMB_MAX, _THUMB_MAX,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation))
                thumb.setStyleSheet("background: transparent;")
                layout.addWidget(thumb)

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
        self._label = label  # set_message()로 본문 갱신 (지속형 진행 토스트용)

        if badge_lbl is not None and badge_position == "trailing":
            layout.addWidget(badge_lbl)

        self.setStyleSheet("background: transparent;")
        self.setWindowOpacity(0.0)
        self.adjustSize()

        # 위치 결정 — 앵커 모드는 직접 배치(스택 독립), 아니면 우하단 스택 등록.
        if anchor is not None:
            self._place_anchored()
        else:
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

        # duration_ms=0 → 지속형: 자동 fade-out 없음. 호출자가 dismiss()로 닫는다
        # (AI 답변처럼 끝나는 시점을 미리 알 수 없는 작업의 진행 표시용).
        if duration_ms > 0:
            QTimer.singleShot(duration_ms, self._start_fade_out)

    def set_message(self, text: str):
        """본문 텍스트 갱신 + 폭 변화 반영 재배치 (지속형 진행 토스트)."""
        if getattr(self, "_label", None) is not None and not self._closing:
            self._label.setText(text)
            self.adjustSize()
            if self._anchor is not None:
                self._place_anchored()
            else:
                _stack._relayout()

    def _place_anchored(self):
        """앵커 기준 배치 — center=True면 앵커 모니터 정중앙, 아니면 앵커 옆 +16px(경계 반전)."""
        screen = QGuiApplication.screenAt(self._anchor) or QGuiApplication.primaryScreen()
        avail = screen.availableGeometry()
        w, h = self.width(), self.height()
        if self._center:
            x = avail.center().x() - w // 2
            y = avail.center().y() - h // 2
        else:
            x = self._anchor.x() + 16
            y = self._anchor.y() + 16
            if x + w > avail.right():
                x = self._anchor.x() - w - 16
            if y + h > avail.bottom():
                y = self._anchor.y() - h - 16
        x = max(avail.left(), min(x, avail.right() - w))
        y = max(avail.top(), min(y, avail.bottom() - h))
        self.move(x, y)

    def dismiss(self):
        """지속형 토스트를 fade-out으로 닫는다 (idempotent)."""
        self._start_fade_out()

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
    """복사 알림 토스트 — 누적 큐 카운트(Q{n})를 본문 앞에 배치한 미리보기.

    이미지 항목이면 preview_text(`[이미지]`) 대신 항목 썸네일을 함께 띄운다
    (패널·경로 붙여넣기 피드백과 시각적으로 일관되게).
    """
    preview = _elide(getattr(item, "preview_text", None) or "클립보드 항목")
    # 이미지면 항목 썸네일을 렌더. **원본(image_data)을 우선** — 토스트가 96px로 줄여
    # 그리므로 원본을 축소하면 선명하다. 미리 만든 thumbnail은 80×60이라 96px로 늘리면
    # 뭉개진다(캡처 토스트가 image_path 원본으로 선명한 것과 같은 이유). 원본이 raw CF_DIB면
    # QPixmap이 못 여므로 BMP 헤더를 조립해 로드 가능하게 만들고(핀 항목은 thumbnail이
    # 없어 이 변환이 없으면 아이콘만 떴다), 그래도 안 되면 thumbnail로 폴백.
    thumb_bytes = None
    if getattr(item, "content_type", None) == "image":
        data = getattr(item, "image_data", None)
        if data:
            _probe = QPixmap()
            if _probe.loadFromData(data):
                thumb_bytes = data
            else:
                # raw CF_DIB(QPixmap 미지원) → BMP 파일 헤더 조립 후 재시도
                from pasteflow.clipboard_monitor import is_encoded_image, _dib_to_bmp
                if not is_encoded_image(data):
                    try:
                        bmp = _dib_to_bmp(data)
                        if QPixmap().loadFromData(bmp):
                            thumb_bytes = bmp
                    except Exception:
                        pass
        if thumb_bytes is None:
            thumb_bytes = getattr(item, "thumbnail", None) or data
    # 아이콘은 생략한다 — Q{n} 배지가 이미 "복사됨" 카테고리를 나타내므로 중복.
    # 이미지는 썸네일이 그 자리를 대신하고, 텍스트는 배지+본문만으로 충분하다.
    return ToastNotification(
        preview,
        duration_ms=COPY_TOAST_DURATION_MS,
        icon="",
        badge=f"Q{queue_count}",
        badge_position="leading",
        image_bytes=thumb_bytes,
    )
