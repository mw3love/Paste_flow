"""텍스트 원본 미리보기 팝업 — 3줄 초과 텍스트 클릭 시 전체 내용 표시"""

from PyQt6.QtWidgets import QLabel, QScrollArea, QVBoxLayout, QWidget, QApplication
from PyQt6.QtCore import Qt, QPoint

# Catppuccin Mocha
_BG = "#1e1e2e"
_BORDER = "#45475a"
_TEXT = "#cdd6f4"

PREVIEW_MAX_W = 360
PREVIEW_MAX_H = 300


class TextPreviewPopup(QWidget):
    """텍스트 전체 미리보기 — 싱글톤처럼 사용"""

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
        self.setMaximumSize(PREVIEW_MAX_W, PREVIEW_MAX_H)
        self.setStyleSheet(f"""
            background-color: {_BG};
            border: 1px solid {_BORDER};
            border-radius: 6px;
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

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
        self._label.setStyleSheet(f"""
            color: {_TEXT};
            font-size: 12px;
            background: transparent;
            padding: 0;
        """)
        self._scroll.setWidget(self._label)

        layout.addWidget(self._scroll)
        self.hide()

    def show_preview(self, text: str, global_pos: QPoint):
        self._label.setText(text)
        self._label.adjustSize()

        # 크기 계산
        w = min(PREVIEW_MAX_W, self._label.sizeHint().width() + 28)
        h = min(PREVIEW_MAX_H, self._label.sizeHint().height() + 28)
        self.resize(w, h)

        # 커서 우측 하단에 배치, 화면 밖 방지
        screen = QApplication.primaryScreen()
        if screen:
            geom = screen.availableGeometry()
            x = global_pos.x() + 16
            y = global_pos.y() + 16
            if x + w > geom.right():
                x = global_pos.x() - w - 8
            if y + h > geom.bottom():
                y = global_pos.y() - h - 8
            self.move(x, y)

        self.show()
        self.raise_()

    def toggle_preview(self, text: str, global_pos: QPoint):
        if self.isVisible():
            self.hide()
        else:
            self.show_preview(text, global_pos)

    def hide_preview(self):
        self.hide()
