"""이미지 확대 미리보기 팝업 — 드래그 이동·휠 줌·닫기 버튼 지원"""
import io

from PyQt6.QtWidgets import (
    QWidget, QLabel, QToolButton,
    QVBoxLayout, QHBoxLayout, QApplication,
)
from PyQt6.QtCore import Qt, QPoint
from PyQt6.QtGui import QPixmap


# Catppuccin Mocha
_BG = "#1e1e2e"
_BORDER = "#45475a"
_TEXT = "#cdd6f4"
_SURFACE1 = "#313244"

PREVIEW_MAX_W = 640
PREVIEW_MAX_H = 480


class ImagePreviewPopup(QWidget):
    """이미지 확대 미리보기 — 싱글톤처럼 사용"""

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
        # 패널 포커스 보호 — 팝업이 열려도 패널이 비활성화되지 않음
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self._drag_pos: QPoint | None = None
        self._original_pixmap: QPixmap | None = None
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
                font-size: 14px;
                font-weight: bold;
                border-radius: 3px;
            }}
            QToolButton#close_btn:hover {{
                background-color: {_SURFACE1};
            }}
            QLabel#image_label {{
                background: transparent;
                border: none;
            }}
        """)

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 4, 6, 6)
        root.setSpacing(2)

        # 상단 바: × 닫기 버튼
        top_bar = QHBoxLayout()
        top_bar.setContentsMargins(0, 0, 0, 0)
        top_bar.addStretch()

        close_btn = QToolButton()
        close_btn.setObjectName("close_btn")
        close_btn.setText("×")
        close_btn.setFixedSize(18, 18)
        close_btn.clicked.connect(self.close)
        top_bar.addWidget(close_btn)
        root.addLayout(top_bar)

        # 이미지 표시 레이블
        self._image_label = QLabel()
        self._image_label.setObjectName("image_label")
        self._image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self._image_label)

        self.hide()

    # ------------------------------------------------------------------
    # 미리보기 표시
    # ------------------------------------------------------------------

    def show_preview(self, image_data: bytes, global_pos: QPoint):
        """이미지 데이터(DIB 또는 PNG)로 미리보기 표시"""
        png_data = self._to_png(image_data)
        if not png_data:
            return

        pixmap = QPixmap()
        if not pixmap.loadFromData(png_data):
            return

        # 원본 픽스맵 보존 (휠 줌 기준점)
        self._original_pixmap = pixmap
        self._scale_factor = 1.0

        self._apply_scale()

        # 커서 우측 하단에 배치, 화면 밖으로 나가지 않도록 조정
        screen = QApplication.primaryScreen()
        if screen:
            geom = screen.availableGeometry()
            x = global_pos.x() + 16
            y = global_pos.y() + 16
            if x + self.width() > geom.right():
                x = global_pos.x() - self.width() - 8
            if y + self.height() > geom.bottom():
                y = global_pos.y() - self.height() - 8
            self.move(x, y)

        self.show()
        self.raise_()

    def toggle_preview(self, image_data: bytes, global_pos: QPoint):
        """클릭 시 토글 — 보이면 숨기고, 숨겨져 있으면 표시"""
        if self.isVisible():
            self.hide()
        else:
            self.show_preview(image_data, global_pos)

    def hide_preview(self):
        self.hide()

    # ------------------------------------------------------------------
    # 휠 줌
    # ------------------------------------------------------------------

    def wheelEvent(self, event):
        if self._original_pixmap is None:
            return
        delta = event.angleDelta().y()
        factor = 1.1 if delta > 0 else (1 / 1.1)
        self._scale_factor = max(0.1, min(8.0, self._scale_factor * factor))
        self._apply_scale()

    def _apply_scale(self):
        """현재 _scale_factor를 _original_pixmap에 적용하고 창 크기·위치를 갱신."""
        if self._original_pixmap is None:
            return

        # 초기 표시 크기: PREVIEW_MAX 상한 이내로 맞춘 후 scale_factor 적용
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
        self._image_label.adjustSize()
        self.adjustSize()

        # 화면 경계 클램핑 (드래그·줌 후 창이 화면 밖으로 벗어나지 않도록)
        screen = QApplication.primaryScreen()
        if screen:
            geom = screen.availableGeometry()
            pos = self.pos()
            x = max(geom.left(), min(pos.x(), geom.right() - self.width()))
            y = max(geom.top(), min(pos.y(), geom.bottom() - self.height()))
            if (x, y) != (pos.x(), pos.y()):
                self.move(x, y)

    # ------------------------------------------------------------------
    # 드래그 이동
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

    # ------------------------------------------------------------------
    # 키보드 (ESC — 창 클릭 후 포커스 획득 시 동작)
    # ------------------------------------------------------------------

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)

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
