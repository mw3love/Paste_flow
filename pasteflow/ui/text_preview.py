"""텍스트 원본 미리보기 팝업 — 다중 창 동시 표시 지원"""

from PyQt6.QtWidgets import (
    QLabel, QScrollArea, QVBoxLayout, QWidget,
    QApplication, QMenu,
)
from PyQt6.QtCore import Qt, QPoint, QRect, QEvent

from pasteflow.ui.theme import (
    BASE as _BG, SURFACE0 as _SURFACE0, SURFACE1 as _BORDER,
    SURFACE2 as _SURFACE2, TEXT as _TEXT, BLUE as _BLUE,
)
from pasteflow.ui.image_preview import (
    compute_preview_pos, _CASCADE_STEP, _dark_menu_style,
)

PREVIEW_MAX_W = 360
PREVIEW_MAX_H = 300
_BASE_FONT_SIZE = 12
_SCALE_STEP = 1.3
_GRIP_HEIGHT = 6


class _DragGrip(QWidget):
    """6px 얇은 드래그 그립 — 윈도우 이동용. 닫기 버튼/배율 라벨 대체."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(_GRIP_HEIGHT)
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.setStyleSheet(f"background-color: {_SURFACE0}; border: none;")
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
    """텍스트 전체 미리보기 — 다중 창 동시 표시 지원"""

    _instances: list["TextPreviewPopup"] = []

    # ------------------------------------------------------------------
    # 클래스 메서드
    # ------------------------------------------------------------------

    @classmethod
    def open_new(cls, text: str, panel_geom: QRect) -> "TextPreviewPopup":
        """새 미리보기 창을 열고 인스턴스 목록에 등록한다."""
        cascade_offset = len(cls._instances) * _CASCADE_STEP
        popup = cls()
        cls._instances.append(popup)
        popup.show_preview(text, panel_geom, cascade_offset)
        return popup

    @classmethod
    def close_all(cls):
        """열려 있는 모든 미리보기 창을 닫는다."""
        for popup in list(cls._instances):
            popup.close()

    # ------------------------------------------------------------------
    # 인스턴스 초기화
    # ------------------------------------------------------------------

    def __init__(self):
        super().__init__(None)
        self.setObjectName("popup_root")
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        # close() 시 즉시 destroy → destroyed 시그널 즉시 발동 → main dict 정리 즉시
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

        self._scale_factor: float = 1.0

        self.setStyleSheet(f"""
            QWidget#popup_root {{
                background-color: {_BG};
                border: 1px solid {_BORDER};
                border-radius: 6px;
            }}
            QWidget#popup_root[active="true"] {{
                border: 2px solid {_BLUE};
            }}
        """)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 상단 6px 드래그 그립 (닫기/배율 라벨 대신)
        self._grip = _DragGrip(self)
        root.addWidget(self._grip)

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
        self._label.setContentsMargins(8, 4, 8, 8)
        self._scroll.setWidget(self._label)
        root.addWidget(self._scroll)

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
            return True
        return super().eventFilter(obj, event)

    # ------------------------------------------------------------------
    # 표시
    # ------------------------------------------------------------------

    def show_preview(self, text: str, panel_geom: QRect, cascade_offset: int = 0):
        self._label.setText(text)
        self._label.adjustSize()
        self._resize_to_content()
        screen = QApplication.screenAt(panel_geom.center()) or QApplication.primaryScreen()
        if screen:
            self.move(compute_preview_pos(panel_geom, self.size(), screen, cascade_offset))
        self.show()
        self.raise_()

    # ------------------------------------------------------------------
    # 우클릭 메뉴 — 닫기
    # ------------------------------------------------------------------

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        menu.setStyleSheet(_dark_menu_style())
        close_action = menu.addAction("닫기")
        close_action.triggered.connect(self.close)
        menu.exec(event.globalPos())

    # ------------------------------------------------------------------
    # 키보드 (ESC)
    # ------------------------------------------------------------------

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)

    # ------------------------------------------------------------------
    # 활성화 외곽선 — 클릭으로 활성될 때 외곽 테두리 강조
    # ------------------------------------------------------------------

    def changeEvent(self, event):
        if event.type() == QEvent.Type.ActivationChange:
            self.setProperty("active", self.isActiveWindow())
            self.style().unpolish(self)
            self.style().polish(self)
        super().changeEvent(event)

    # ------------------------------------------------------------------
    # 생명주기 — _instances 목록 정리
    # ------------------------------------------------------------------

    def closeEvent(self, event):
        type(self)._instances = [p for p in type(self)._instances if p is not self]
        super().closeEvent(event)

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

    def _resize_to_content(self):
        max_w = round(PREVIEW_MAX_W * self._scale_factor)
        max_h = round(PREVIEW_MAX_H * self._scale_factor)
        w = min(max_w, self._label.sizeHint().width() + 28)
        h = min(max_h, self._label.sizeHint().height() + _GRIP_HEIGHT + 20)
        self.resize(w, h)
