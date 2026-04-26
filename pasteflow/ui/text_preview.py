"""텍스트 원본 미리보기 팝업 — 3줄 초과 텍스트 클릭 시 전체 내용 표시"""

from PyQt6.QtWidgets import (
    QLabel, QScrollArea, QVBoxLayout, QHBoxLayout, QWidget,
    QApplication, QToolButton,
)
from PyQt6.QtCore import Qt, QPoint, QEvent

from pasteflow.ui.theme import (
    BASE as _BG, CRUST as _CRUST, SURFACE0 as _SURFACE0,
    SURFACE1 as _BORDER, TEXT as _TEXT, RED as _RED,
)

PREVIEW_MAX_W = 360
PREVIEW_MAX_H = 300
_BASE_FONT_SIZE = 12
_SCALE_STEP = 1.3


class _DragHeader(QWidget):
    """드래그로 부모 창을 이동시키는 헤더 바"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._drag_pos: QPoint | None = None

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = (
                event.globalPosition().toPoint() - self.window().frameGeometry().topLeft()
            )
            self.window().activateWindow()
            self.window().setFocus(Qt.FocusReason.MouseFocusReason)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_pos and event.buttons() & Qt.MouseButton.LeftButton:
            self.window().move(event.globalPosition().toPoint() - self._drag_pos)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        super().mouseReleaseEvent(event)


class TextPreviewPopup(QWidget):
    """텍스트 전체 미리보기 — 싱글톤"""

    _instance = None

    @classmethod
    def instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        self._scale_factor: float = 1.0

        self.setStyleSheet(f"""
            QWidget {{
                background-color: {_BG};
                border: 1px solid {_BORDER};
                border-radius: 6px;
            }}
            QToolButton#close_btn {{
                color: {_TEXT};
                background: transparent;
                border: none;
                font-size: 16px;
                font-weight: bold;
                border-radius: 3px;
            }}
            QToolButton#close_btn:hover {{
                background-color: {_RED};
                color: {_CRUST};
            }}
        """)

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 0, 0, 6)
        root.setSpacing(2)

        # 헤더: 드래그 이동 + 줌 레벨 + × 닫기
        header = _DragHeader(self)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(6, 0, 0, 0)
        header_layout.setSpacing(0)

        self._zoom_label = QLabel("100%")
        self._zoom_label.setStyleSheet(
            f"color: {_TEXT}; font-size: 13px; background: transparent; border: none;"
        )
        header_layout.addWidget(self._zoom_label)
        header_layout.addStretch()

        close_btn = QToolButton()
        close_btn.setObjectName("close_btn")
        close_btn.setText("×")
        close_btn.setFixedSize(32, 32)
        close_btn.clicked.connect(self.hide)
        header_layout.addWidget(close_btn)

        root.addWidget(header)

        # 스크롤 영역
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet(f"""
            QScrollArea {{
                border: none;
                background: transparent;
            }}
            QScrollBar:vertical {{
                background: transparent;
                width: 4px;
            }}
            QScrollBar::handle:vertical {{
                background: {_BORDER};
                border-radius: 2px;
                min-height: 20px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0;
            }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
                background: transparent;
            }}
        """)

        self._label = QLabel()
        self._label.setWordWrap(True)
        self._label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._scroll.setWidget(self._label)
        root.addWidget(self._scroll)

        # viewport 이벤트 필터: 휠줌 전용
        self._scroll.viewport().installEventFilter(self)

        self._apply_scale()
        self.hide()

    # ------------------------------------------------------------------
    # 이벤트 필터 — viewport 휠을 줌으로 변환
    # ------------------------------------------------------------------

    def eventFilter(self, obj, event):
        if obj is self._scroll.viewport() and event.type() == QEvent.Type.Wheel:
            delta = event.angleDelta().y()
            if delta != 0:
                factor = _SCALE_STEP if delta > 0 else (1 / _SCALE_STEP)
                self._scale_factor *= factor
                self._apply_scale()
                self._label.adjustSize()
                self._resize_to_content()
            return True  # 스크롤 소비 차단
        return super().eventFilter(obj, event)

    # ------------------------------------------------------------------
    # 표시 / 토글
    # ------------------------------------------------------------------

    def show_preview(self, text: str, global_pos: QPoint):
        self._label.setText(text)
        self._label.adjustSize()
        self._resize_to_content()
        self._place_near(global_pos)
        self.show()
        self.raise_()

    def toggle_preview(self, text: str, global_pos: QPoint):
        if self.isVisible():
            self.hide()
        else:
            self.show_preview(text, global_pos)

    def hide_preview(self):
        self.hide()

    # ------------------------------------------------------------------
    # 키보드 (헤더 클릭 후 포커스 획득 시 ESC 동작)
    # ------------------------------------------------------------------

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
        else:
            super().keyPressEvent(event)

    # ------------------------------------------------------------------
    # 내부 유틸
    # ------------------------------------------------------------------

    def _apply_scale(self):
        size = round(_BASE_FONT_SIZE * self._scale_factor)
        self._label.setStyleSheet(f"""
            color: {_TEXT};
            font-size: {size}px;
            background-color: {_BG};
            padding: 0;
        """)
        self._zoom_label.setText(f"{round(self._scale_factor * 100)}%")

    def _resize_to_content(self):
        max_w = round(PREVIEW_MAX_W * self._scale_factor)
        max_h = round(PREVIEW_MAX_H * self._scale_factor)
        w = min(max_w, self._label.sizeHint().width() + 28)
        h = min(max_h, self._label.sizeHint().height() + 60)
        self.resize(w, h)

    def _place_near(self, global_pos: QPoint):
        screen = QApplication.screenAt(global_pos) or QApplication.primaryScreen()
        if not screen:
            return
        geom = screen.availableGeometry()
        x = global_pos.x() + 16
        y = global_pos.y() + 16
        if x + self.width() > geom.right():
            x = global_pos.x() - self.width() - 8
        if y + self.height() > geom.bottom():
            y = global_pos.y() - self.height() - 8
        self.move(x, y)
