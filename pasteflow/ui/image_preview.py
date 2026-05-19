"""이미지 확대 미리보기 팝업 — 드래그 이동·휠 줌·다중 창 지원"""
import io

from PyQt6.QtWidgets import (
    QWidget, QLabel, QVBoxLayout, QApplication, QMenu,
)
from PyQt6.QtCore import Qt, QPoint, QRect, QSize, QEvent
from PyQt6.QtGui import QPixmap

from pasteflow.ui.theme import (
    BASE as _BG, SURFACE0 as _SURFACE0, SURFACE1 as _BORDER,
    SURFACE2 as _SURFACE2, TEXT as _TEXT, PEACH as _PEACH, BLUE as _BLUE,
)

PREVIEW_MAX_W = 640
PREVIEW_MAX_H = 480
_PREVIEW_MARGIN = 8
_CASCADE_STEP = 24


def compute_preview_pos(
    panel_geom: QRect,
    popup_size: QSize,
    screen,
    cascade_offset: int = 0,
) -> QPoint:
    """미리보기 팝업 위치 결정 — 패널 우측 우선, 부족하면 좌측, 그래도 안 되면 화면 내 fit.

    cascade_offset: 같은 위치에 연속해서 띄울 때 (x, y) 양쪽으로 어긋나는 양(px).
    """
    avail = screen.availableGeometry()
    w, h = popup_size.width(), popup_size.height()

    def _clamp_y(y: int) -> int:
        return max(avail.top(), min(y, avail.bottom() - h))

    right_x = panel_geom.right() + _PREVIEW_MARGIN + cascade_offset
    if right_x + w <= avail.right():
        return QPoint(right_x, _clamp_y(panel_geom.top() + cascade_offset))

    left_x = panel_geom.left() - _PREVIEW_MARGIN - w - cascade_offset
    if left_x >= avail.left():
        return QPoint(left_x, _clamp_y(panel_geom.top() + cascade_offset))

    x = max(avail.left(), min(panel_geom.right() + _PREVIEW_MARGIN, avail.right() - w))
    return QPoint(x, _clamp_y(panel_geom.top()))


def _dark_menu_style() -> str:
    return f"""
        QMenu {{
            background-color: {_SURFACE0};
            color: {_TEXT};
            border: 1px solid {_BORDER};
            border-radius: 6px;
            padding: 4px;
        }}
        QMenu::item {{
            padding: 6px 16px;
            border-radius: 4px;
        }}
        QMenu::item:selected {{
            background-color: {_SURFACE2};
        }}
    """


class ImagePreviewPopup(QWidget):
    """이미지 확대 미리보기 — 다중 창 동시 표시 지원"""

    _instances: list["ImagePreviewPopup"] = []

    # ------------------------------------------------------------------
    # 클래스 메서드
    # ------------------------------------------------------------------

    @classmethod
    def open_new(cls, image_data: bytes, panel_geom: QRect) -> "ImagePreviewPopup":
        """새 미리보기 창을 열고 인스턴스 목록에 등록한다.

        panel_geom: 패널의 글로벌 geometry — 미리보기를 패널 옆에 배치하기 위한 기준.
        """
        cascade_offset = len(cls._instances) * _CASCADE_STEP
        popup = cls()
        cls._instances.append(popup)
        popup.show_preview(image_data, panel_geom, cascade_offset)
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
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        # close() 시 즉시 destroy → destroyed 시그널 즉시 발동 → main dict 정리 즉시
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self._drag_pos: QPoint | None = None
        self._original_pixmap: QPixmap | None = None
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
            QWidget#img_wrapper {{
                background: transparent;
                border: none;
            }}
            QLabel#image_label {{
                background: transparent;
                border: 2px solid {_PEACH};
            }}
        """)

        self.setObjectName("popup_root")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        img_wrapper = QWidget()
        img_wrapper.setObjectName("img_wrapper")
        img_layout = QVBoxLayout(img_wrapper)
        img_layout.setContentsMargins(6, 6, 6, 6)
        img_layout.setSpacing(0)

        self._image_label = QLabel()
        self._image_label.setObjectName("image_label")
        self._image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        img_layout.addWidget(self._image_label)

        root.addWidget(img_wrapper)

        self.hide()

    # ------------------------------------------------------------------
    # 미리보기 표시
    # ------------------------------------------------------------------

    def show_preview(self, image_data: bytes, panel_geom: QRect, cascade_offset: int = 0):
        """이미지 데이터(DIB 또는 PNG)로 미리보기 표시 — 패널 옆에 배치"""
        png_data = self._to_png(image_data)
        if not png_data:
            return

        pixmap = QPixmap()
        if not pixmap.loadFromData(png_data):
            return

        self._original_pixmap = pixmap
        self._scale_factor = 1.0
        self._apply_scale()

        screen = QApplication.screenAt(panel_geom.center()) or QApplication.primaryScreen()
        if screen:
            self.move(compute_preview_pos(panel_geom, self.size(), screen, cascade_offset))

        self.show()
        self.raise_()

    # ------------------------------------------------------------------
    # 휠 줌
    # ------------------------------------------------------------------

    def wheelEvent(self, event):
        if self._original_pixmap is None:
            return
        delta = event.angleDelta().y()
        if delta == 0:
            return
        factor = 1.1 if delta > 0 else (1 / 1.1)
        self._scale_factor *= factor
        self._apply_scale()

    def _apply_scale(self):
        """현재 _scale_factor를 _original_pixmap에 적용하고 창 크기를 갱신."""
        if self._original_pixmap is None:
            return

        base = self._original_pixmap.scaled(
            PREVIEW_MAX_W, PREVIEW_MAX_H,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        target_w = max(1, round(base.width() * self._scale_factor))
        target_h = max(1, round(base.height() * self._scale_factor))

        scaled = self._original_pixmap.scaled(
            target_w, target_h,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._image_label.setPixmap(scaled)
        self._image_label.setFixedSize(scaled.size())
        self.setMinimumSize(0, 0)
        self.resize(scaled.width() + 12, scaled.height() + 12)

    # ------------------------------------------------------------------
    # 드래그 이동 (본문 영역 클릭 드래그)
    # ------------------------------------------------------------------

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_pos and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.close()
        else:
            super().mouseDoubleClickEvent(event)

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
    # 유틸
    # ------------------------------------------------------------------

    @staticmethod
    def _to_png(data: bytes) -> bytes | None:
        """DIB 또는 기타 이미지 데이터를 PNG로 변환"""
        try:
            from PIL import Image
            img = Image.open(io.BytesIO(data))
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue()
        except Exception:
            return None
