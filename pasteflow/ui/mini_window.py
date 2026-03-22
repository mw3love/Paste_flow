"""미니 클립보드 창 — 우하단 플로팅

평소 숨김 → 복사 시 fade-in → 5초 후 fade-out 자동 사라짐.
마우스 오버 시 사라지지 않음.
"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QGraphicsOpacityEffect, QSizeGrip,
)
from PyQt6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve, pyqtSignal, QPoint, QEvent
from PyQt6.QtGui import QFont, QPixmap, QColor, QCursor

from pasteflow.models import ClipboardItem
from pasteflow.ui.image_preview import ImagePreviewPopup

# Catppuccin Mocha 색상
COLORS = {
    "base": "#1e1e2e",
    "surface0": "#313244",
    "surface1": "#45475a",
    "surface2": "#585b70",
    "teal": "#94e2d5",
    "peach": "#fab387",
    "text": "#cdd6f4",
    "subtext0": "#a6adc8",
    "overlay0": "#6c7086",
    "blue": "#89b4fa",
    "red": "#f38ba8",
    "green": "#a6e3a1",
}

MAX_DISPLAY_ITEMS = 5
AUTO_DISMISS_MS = 5000
FADE_IN_MS = 200
FADE_OUT_MS = 300


class MiniWindow(QWidget):
    """미니 클립보드 창"""

    open_panel_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._items: list[ClipboardItem] = []
        self._pointer: int = 0
        self._total: int = 0
        self._mouse_over = False
        self._drag_pos = None

        self._setup_window()
        self._setup_ui()
        self._setup_animations()
        self._setup_timer()

    def _setup_window(self):
        """윈도우 속성 설정"""
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedWidth(280)
        self.setMinimumHeight(100)

        # 우하단 위치 계산
        from PyQt6.QtWidgets import QApplication
        screen = QApplication.primaryScreen()
        if screen:
            geom = screen.availableGeometry()
            x = geom.right() - 290
            y = geom.bottom() - 300
            self.move(x, y)

    def _setup_ui(self):
        """UI 구성"""
        # 메인 컨테이너
        self._container = QWidget(self)
        self._container.setObjectName("container")
        self._container.setStyleSheet(f"""
            #container {{
                background-color: {COLORS['base']};
                border: 1px solid {COLORS['surface1']};
                border-radius: 10px;
            }}
        """)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(self._container)

        container_layout = QVBoxLayout(self._container)
        container_layout.setContentsMargins(8, 6, 8, 6)
        container_layout.setSpacing(0)

        # 상태 바
        status_bar = QHBoxLayout()
        self._status_label = QLabel("대기 중")
        self._status_label.setStyleSheet(
            f"color: {COLORS['overlay0']}; font-size: 11px; font-weight: 500;"
        )
        status_bar.addWidget(self._status_label)
        status_bar.addStretch()

        # ▲ 전체 패널 열기 버튼
        self._expand_btn = QPushButton("▲")
        self._expand_btn.setFixedSize(24, 24)
        self._expand_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._expand_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {COLORS['subtext0']};
                border: none;
                font-size: 12px;
            }}
            QPushButton:hover {{
                background: {COLORS['surface1']};
                border-radius: 4px;
            }}
        """)
        self._expand_btn.clicked.connect(self.open_panel_requested.emit)
        status_bar.addWidget(self._expand_btn)

        # × 닫기 버튼
        self._close_btn = QPushButton("×")
        self._close_btn.setFixedSize(24, 24)
        self._close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._close_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {COLORS['subtext0']};
                border: none;
                font-size: 14px;
            }}
            QPushButton:hover {{
                background: {COLORS['red']};
                color: {COLORS['base']};
                border-radius: 4px;
            }}
        """)
        self._close_btn.clicked.connect(self._dismiss_now)
        status_bar.addWidget(self._close_btn)

        container_layout.addLayout(status_bar)

        # 구분선
        sep = QWidget()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background-color: {COLORS['surface1']};")
        container_layout.addWidget(sep)

        # 항목 리스트 영역
        self._items_layout = QVBoxLayout()
        self._items_layout.setContentsMargins(0, 4, 0, 4)
        self._items_layout.setSpacing(2)
        container_layout.addLayout(self._items_layout)

    def _setup_animations(self):
        """fade-in/out 애니메이션"""
        self._opacity_effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._opacity_effect)
        self._opacity_effect.setOpacity(0.0)

        self._fade_anim = QPropertyAnimation(self._opacity_effect, b"opacity")

    def _setup_timer(self):
        """자동 사라짐 타이머"""
        self._dismiss_timer = QTimer(self)
        self._dismiss_timer.setSingleShot(True)
        self._dismiss_timer.timeout.connect(self._fade_out)

    # --- Public API ---

    def show_and_refresh(self, items: list[ClipboardItem], pointer: int, total: int):
        """복사 발생 시 호출 — 항목 갱신 후 표시"""
        self._items = items[-MAX_DISPLAY_ITEMS:]
        self._pointer = pointer
        self._total = total
        self._rebuild_items()
        self._update_status()
        self._fade_in()
        self._restart_dismiss_timer()

    def update_status(self, pointer: int, total: int):
        """붙여넣기 시 상태만 업데이트"""
        self._pointer = pointer
        self._total = total
        self._update_status()
        self._rebuild_items()

    # --- Internal ---

    def _update_status(self):
        """상태 라벨 업데이트"""
        if self._total == 0:
            self._status_label.setText("대기 중")
            self._status_label.setStyleSheet(
                f"color: {COLORS['overlay0']}; font-size: 11px; font-weight: 500;"
            )
        elif self._pointer >= self._total:
            self._status_label.setText("완료 ✓")
            self._status_label.setStyleSheet(
                f"color: {COLORS['green']}; font-size: 11px; font-weight: 500;"
            )
        else:
            self._status_label.setText(f"붙여넣기 {self._pointer + 1}/{self._total}")
            self._status_label.setStyleSheet(
                f"color: {COLORS['teal']}; font-size: 11px; font-weight: 500;"
            )

    def _rebuild_items(self):
        """항목 위젯 재구성"""
        # 기존 위젯 제거
        while self._items_layout.count():
            child = self._items_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        for i, item in enumerate(self._items):
            # 전체 큐에서의 실제 인덱스 계산
            actual_index = (self._total - len(self._items)) + i
            is_current = actual_index == self._pointer
            is_done = actual_index < self._pointer

            row = self._create_item_row(item, actual_index + 1, is_current, is_done)
            self._items_layout.addWidget(row)

        self.adjustSize()

    def _create_item_row(
        self, item: ClipboardItem, number: int, is_current: bool, is_done: bool
    ) -> QWidget:
        """항목 행 위젯 생성"""
        row = QWidget()
        row.setObjectName("itemRow")
        row.setFixedHeight(36)
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 8, 0)
        row_layout.setSpacing(8)

        # 좌측 강조 바
        bar = QWidget()
        bar.setFixedSize(3, 36)
        if is_current:
            bar.setStyleSheet(f"background-color: {COLORS['teal']}; border-radius: 2px;")
        elif is_done:
            bar.setStyleSheet(f"background-color: {COLORS['overlay0']}; border-radius: 2px;")
        else:
            bar.setStyleSheet("background: transparent;")
        row_layout.addWidget(bar)

        # 번호 뱃지
        badge = QLabel(str(number))
        badge.setFixedSize(20, 20)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        text_color = COLORS['subtext0'] if is_done else COLORS['text']
        badge.setStyleSheet(f"""
            background-color: {COLORS['surface0']};
            color: {text_color};
            border-radius: 10px;
            font-size: 10px;
        """)
        row_layout.addWidget(badge)

        # 이미지 항목: 썸네일 40×30 표시
        if item.content_type == "image" and item.thumbnail:
            thumb_label = QLabel()
            thumb_label.setFixedSize(40, 30)
            pixmap = QPixmap()
            pixmap.loadFromData(item.thumbnail)
            scaled = pixmap.scaled(
                40, 30,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            thumb_label.setPixmap(scaled)
            thumb_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            thumb_label.setStyleSheet(
                f"background-color: {COLORS['surface1']}; border-radius: 3px;"
            )
            row_layout.addWidget(thumb_label)

            img_text = QLabel("[이미지]")
            img_text.setStyleSheet(f"color: {text_color}; font-size: 12px;")
            row_layout.addWidget(img_text, 1)

            # 호버 시 확대 미리보기 (F8-5)
            row.setMouseTracking(True)
            row._image_data = item.image_data
            row.installEventFilter(self)
        else:
            # 텍스트 항목: 유형 아이콘 + 미리보기
            type_icon = "🖼" if item.content_type == "image" else "T"
            icon_label = QLabel(type_icon)
            icon_label.setFixedSize(16, 16)
            icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            icon_label.setStyleSheet(f"color: {COLORS['subtext0']}; font-size: 11px;")
            row_layout.addWidget(icon_label)

            preview = item.preview_text or ""
            if len(preview) > 30:
                preview = preview[:30] + "..."
            text_label = QLabel(preview)
            text_label.setStyleSheet(f"color: {text_color}; font-size: 12px;")
            row_layout.addWidget(text_label, 1)

        row.setStyleSheet(f"#itemRow {{ background-color: {COLORS['surface0']}; border-radius: 4px; }}")
        return row

    # --- Image preview (F8-5) ---

    def eventFilter(self, obj, event):
        """이미지 항목 호버 시 확대 미리보기"""
        if hasattr(obj, '_image_data') and obj._image_data:
            if event.type() == QEvent.Type.Enter:
                ImagePreviewPopup.instance().show_preview(
                    obj._image_data, QCursor.pos()
                )
            elif event.type() == QEvent.Type.Leave:
                ImagePreviewPopup.instance().hide_preview()
        return super().eventFilter(obj, event)

    # --- Fade animations ---

    def _fade_in(self):
        """fade-in 표시"""
        self._fade_anim.stop()
        self.show()
        self._fade_anim.setDuration(FADE_IN_MS)
        self._fade_anim.setStartValue(self._opacity_effect.opacity())
        self._fade_anim.setEndValue(1.0)
        self._fade_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade_anim.start()

    def _fade_out(self):
        """fade-out 사라짐"""
        self._fade_anim.stop()
        self._fade_anim.setDuration(FADE_OUT_MS)
        self._fade_anim.setStartValue(self._opacity_effect.opacity())
        self._fade_anim.setEndValue(0.0)
        self._fade_anim.setEasingCurve(QEasingCurve.Type.InCubic)
        self._fade_anim.finished.connect(self._on_fade_out_done)
        self._fade_anim.start()

    def _on_fade_out_done(self):
        """fade-out 완료 후 숨기기"""
        self._fade_anim.finished.disconnect(self._on_fade_out_done)
        if self._opacity_effect.opacity() < 0.1:
            self.hide()

    def _dismiss_now(self):
        """즉시 숨기기"""
        self._dismiss_timer.stop()
        self._fade_anim.stop()
        self._opacity_effect.setOpacity(0.0)
        self.hide()

    def _restart_dismiss_timer(self):
        """자동 사라짐 타이머 (재)시작"""
        self._dismiss_timer.stop()
        self._dismiss_timer.start(AUTO_DISMISS_MS)

    # --- Mouse events ---

    def enterEvent(self, event):
        """마우스 오버 → 타이머 일시정지"""
        self._mouse_over = True
        self._dismiss_timer.stop()
        super().enterEvent(event)

    def leaveEvent(self, event):
        """마우스 벗어남 → 타이머 재시작"""
        self._mouse_over = False
        self._restart_dismiss_timer()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        """드래그 이동 시작"""
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        """드래그 이동"""
        if self._drag_pos and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        """드래그 종료"""
        self._drag_pos = None
        super().mouseReleaseEvent(event)
