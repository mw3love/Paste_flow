"""이미지 확대 미리보기 팝업 — 마우스 오버 시 최대 320×240px"""
import io

from PyQt6.QtWidgets import QLabel, QApplication
from PyQt6.QtCore import Qt, QPoint
from PyQt6.QtGui import QPixmap
from PIL import Image


# Catppuccin Mocha
_BG = "#1e1e2e"
_BORDER = "#45475a"

PREVIEW_MAX_W = 320
PREVIEW_MAX_H = 240


class ImagePreviewPopup(QLabel):
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
            Qt.WindowType.ToolTip
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setStyleSheet(f"""
            background-color: {_BG};
            border: 1px solid {_BORDER};
            border-radius: 6px;
            padding: 4px;
        """)
        self.hide()

    def show_preview(self, image_data: bytes, global_pos: QPoint):
        """이미지 데이터(DIB 또는 PNG)로 미리보기 표시"""
        png_data = self._to_png(image_data)
        if not png_data:
            return

        pixmap = QPixmap()
        if not pixmap.loadFromData(png_data):
            return

        scaled = pixmap.scaled(
            PREVIEW_MAX_W, PREVIEW_MAX_H,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.setPixmap(scaled)
        self.adjustSize()

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

    @staticmethod
    def _to_png(data: bytes) -> bytes | None:
        """DIB 또는 기타 이미지 데이터를 PNG로 변환"""
        try:
            img = Image.open(io.BytesIO(data))
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue()
        except Exception:
            return None
