"""전체 클립보드 패널 — F4 요구사항 구현

고정 섹션 + 히스토리 섹션, 검색, 우클릭 컨텍스트 메뉴, 다중 선택.
Alt+V / 트레이 / 미니창 ▲ 버튼으로 토글.
"""
from datetime import datetime
from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QScrollArea, QMenu, QApplication, QGraphicsOpacityEffect,
)
from PyQt6.QtCore import Qt, pyqtSignal, QPoint, QTimer, QEvent, QMimeData
from PyQt6.QtGui import QPixmap, QDrag, QCursor

from pasteflow.models import ClipboardItem
from pasteflow.ui.image_preview import ImagePreviewPopup

# Catppuccin Mocha 색상
COLORS = {
    "base": "#1e1e2e",
    "mantle": "#181825",
    "crust": "#11111b",
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

PANEL_WIDTH = 360
PANEL_HEIGHT = 520
PANEL_MIN_WIDTH = 280
PANEL_MIN_HEIGHT = 300


class PanelItemWidget(QWidget):
    """패널 내 개별 항목 위젯"""

    clicked = pyqtSignal(int, object)          # (item_id, QMouseEvent)
    double_clicked = pyqtSignal(int)           # item_id
    context_menu_requested = pyqtSignal(int, object)  # (item_id, QPoint)

    def __init__(
        self,
        item: ClipboardItem,
        number: int,
        is_current: bool = False,
        is_done: bool = False,
        is_pinned: bool = False,
        is_selected: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self.item = item
        self.item_id = item.id
        self._is_pinned = is_pinned
        self._is_selected = is_selected
        self._is_hovered = False
        self._drag_start_pos = None
        self._did_drag = False

        self._setup_ui(item, number, is_current, is_done, is_pinned)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        # 고정 항목은 드래그 재정렬 가능
        if is_pinned:
            self.setAcceptDrops(True)

    def _setup_ui(
        self, item: ClipboardItem, number: int,
        is_current: bool, is_done: bool, is_pinned: bool,
    ):
        self.setMinimumHeight(48)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(8)

        # 좌측 강조 바
        bar = QWidget()
        bar.setFixedSize(3, 48)
        if is_pinned:
            bar.setStyleSheet(f"background-color: {COLORS['peach']}; border-radius: 2px;")
        elif is_current:
            bar.setStyleSheet(f"background-color: {COLORS['teal']}; border-radius: 2px;")
        elif is_done:
            bar.setStyleSheet(f"background-color: {COLORS['overlay0']}; border-radius: 2px;")
        else:
            bar.setStyleSheet("background: transparent;")
        layout.addWidget(bar)

        # 번호 뱃지
        text_color = COLORS['subtext0'] if is_done else COLORS['text']
        badge = QLabel(str(number))
        badge.setFixedSize(20, 20)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setStyleSheet(f"""
            background-color: {COLORS['surface0']};
            color: {text_color};
            border-radius: 10px;
            font-size: 10px;
        """)
        layout.addWidget(badge)

        # 유형 아이콘
        if is_pinned:
            type_text = "\U0001f4cc"  # 📌
        elif is_done:
            type_text = "\u2713"  # ✓
        elif item.content_type == "image":
            type_text = "\U0001f5bc"  # 🖼
        else:
            type_text = "T"
        icon_label = QLabel(type_text)
        icon_label.setFixedSize(16, 16)
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_label.setStyleSheet(f"color: {COLORS['subtext0']}; font-size: 11px;")
        layout.addWidget(icon_label)

        # 미리보기 (최대 2줄)
        content_layout = QVBoxLayout()
        content_layout.setContentsMargins(0, 4, 0, 4)
        content_layout.setSpacing(0)

        if item.content_type == "image" and item.thumbnail:
            # 이미지 썸네일 (80x60)
            thumb_label = QLabel()
            pixmap = QPixmap()
            pixmap.loadFromData(item.thumbnail)
            scaled = pixmap.scaled(80, 60, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            thumb_label.setPixmap(scaled)
            thumb_label.setFixedSize(80, 60)
            content_layout.addWidget(thumb_label)
            self.setMinimumHeight(72)
        else:
            preview = item.preview_text or ""
            lines = preview.split("\n")[:2]
            display_text = "\n".join(lines)
            if len(display_text) > 80:
                display_text = display_text[:80] + "..."

            text_label = QLabel(display_text)
            text_label.setWordWrap(True)
            text_label.setMaximumHeight(32)
            text_label.setStyleSheet(f"color: {text_color}; font-size: 12px;")
            content_layout.addWidget(text_label)

        layout.addLayout(content_layout, 1)

        # 시간 표시
        time_str = self._format_time(item.created_at)
        time_label = QLabel(time_str)
        time_label.setStyleSheet(f"color: {COLORS['subtext0']}; font-size: 10px;")
        time_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(time_label)

        self._apply_bg_style()

    def _format_time(self, created_at) -> str:
        """상대 시간 포맷"""
        if isinstance(created_at, str):
            try:
                dt = datetime.fromisoformat(created_at)
            except Exception:
                return created_at
        elif isinstance(created_at, datetime):
            dt = created_at
        else:
            return ""

        now = datetime.now()
        diff = now - dt
        seconds = diff.total_seconds()

        if seconds < 60:
            return "방금"
        elif seconds < 3600:
            return f"{int(seconds // 60)}분전"
        elif seconds < 86400:
            return f"{int(seconds // 3600)}시간전"
        else:
            return f"{int(seconds // 86400)}일전"

    def _apply_bg_style(self):
        if self._is_selected:
            self.setStyleSheet(f"background-color: {COLORS['surface2']}; border-radius: 6px;")
        elif self._is_hovered:
            self.setStyleSheet(f"background-color: {COLORS['surface1']}; border-radius: 6px;")
        else:
            self.setStyleSheet(f"background-color: {COLORS['surface0']}; border-radius: 6px;")

    @property
    def is_selected(self):
        return self._is_selected

    @is_selected.setter
    def is_selected(self, value: bool):
        self._is_selected = value
        self._apply_bg_style()

    def enterEvent(self, event):
        self._is_hovered = True
        self._apply_bg_style()
        # F8-5: 이미지 항목 호버 시 확대 미리보기
        if self.item.content_type == "image" and self.item.image_data:
            ImagePreviewPopup.instance().show_preview(
                self.item.image_data, QCursor.pos()
            )
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._is_hovered = False
        self._apply_bg_style()
        if self.item.content_type == "image" and self.item.image_data:
            ImagePreviewPopup.instance().hide_preview()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_pos = event.pos()
            self._did_drag = False
            # 고정 항목은 release에서 클릭 처리 (드래그와 충돌 방지)
            if not self._is_pinned:
                self.clicked.emit(self.item_id, event)
            event.accept()  # 부모(패널)로 전파 방지
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        """고정 항목 드래그 시작 (F7-5)"""
        if (
            self._is_pinned
            and self._drag_start_pos
            and event.buttons() & Qt.MouseButton.LeftButton
        ):
            distance = (event.pos() - self._drag_start_pos).manhattanLength()
            if distance > 10:
                self._did_drag = True
                drag = QDrag(self)
                mime = QMimeData()
                mime.setData("application/x-pasteflow-pin-id", str(self.item_id).encode())
                drag.setMimeData(mime)
                drag.exec(Qt.DropAction.MoveAction)
                self._drag_start_pos = None
                return
        event.accept()

    def mouseReleaseEvent(self, event):
        # 고정 항목: 드래그 안 했으면 클릭으로 처리
        if self._is_pinned and not self._did_drag and event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.item_id, event)
        self._drag_start_pos = None
        self._did_drag = False
        event.accept()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.double_clicked.emit(self.item_id)
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event):
        self.context_menu_requested.emit(self.item_id, event.globalPos())

    # ── 드롭 수신 (고정 항목끼리 순서 교환) ──

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat("application/x-pasteflow-pin-id"):
            event.acceptProposedAction()
            self.setStyleSheet(f"background-color: {COLORS['surface2']}; border-radius: 6px; border: 1px dashed {COLORS['teal']};")

    def dragLeaveEvent(self, event):
        self._apply_bg_style()

    def dropEvent(self, event):
        """드롭 시 순서 교환 시그널 발신"""
        source_id = int(event.mimeData().data("application/x-pasteflow-pin-id").data().decode())
        target_id = self.item_id
        if source_id != target_id:
            # 부모(ClipboardPanel)에서 처리
            panel = self.parent()
            while panel and not isinstance(panel, ClipboardPanel):
                panel = panel.parent()
            if panel:
                panel._on_pin_reorder(source_id, target_id)
        self._apply_bg_style()
        event.acceptProposedAction()


class ClipboardPanel(QWidget):
    """전체 클립보드 패널

    시그널:
        paste_item_requested(ClipboardItem) — 항목 붙여넣기 요청
        pin_item_requested(int) — 항목 고정 요청
        unpin_item_requested(int) — 항목 고정 해제 요청
        delete_item_requested(int) — 항목 삭제 요청
    """

    paste_item_requested = pyqtSignal(object)    # ClipboardItem
    copy_item_requested = pyqtSignal(object)     # ClipboardItem (F7-6: 클립보드 복사+큐 추가)
    pin_item_requested = pyqtSignal(int)         # item_id
    unpin_item_requested = pyqtSignal(int)       # item_id
    delete_item_requested = pyqtSignal(int)      # item_id
    pin_reorder_requested = pyqtSignal(list)     # [(item_id, new_order), ...]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pinned_items: list[ClipboardItem] = []
        self._history_items: list[ClipboardItem] = []
        self._pointer: int = 0
        self._total: int = 0
        self._selected_ids: set[int] = set()
        self._last_clicked_id: Optional[int] = None
        self._search_text: str = ""
        self._drag_pos = None
        self._auto_close = True

        self._setup_window()
        self._setup_ui()

    def _setup_window(self):
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.resize(PANEL_WIDTH, PANEL_HEIGHT)
        self.setMinimumSize(PANEL_MIN_WIDTH, PANEL_MIN_HEIGHT)

        # 우하단 위치
        screen = QApplication.primaryScreen()
        if screen:
            geom = screen.availableGeometry()
            x = geom.right() - PANEL_WIDTH - 10
            y = geom.bottom() - PANEL_HEIGHT - 10
            self.move(x, y)

    def _setup_ui(self):
        # 메인 컨테이너 (라운딩, 배경)
        self._container = QWidget(self)
        self._container.setObjectName("panelContainer")
        self._container.setStyleSheet(f"""
            #panelContainer {{
                background-color: {COLORS['base']};
                border: 1px solid {COLORS['surface1']};
                border-radius: 12px;
            }}
        """)

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.addWidget(self._container)

        main_layout = QVBoxLayout(self._container)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(8)

        # ── 검색 바 + 닫기 버튼 ──
        search_row = QHBoxLayout()
        search_row.setSpacing(6)

        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("\U0001f50d 검색...")
        self._search_input.setFixedHeight(36)
        self._search_input.setStyleSheet(f"""
            QLineEdit {{
                background-color: {COLORS['surface0']};
                color: {COLORS['text']};
                border: 2px solid transparent;
                border-radius: 8px;
                padding: 0 12px;
                font-size: 13px;
            }}
            QLineEdit:focus {{
                border-color: {COLORS['blue']};
            }}
        """)
        self._search_input.textChanged.connect(self._on_search_changed)
        search_row.addWidget(self._search_input, 1)

        close_btn = QPushButton("\u2715")
        close_btn.setFixedSize(28, 28)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {COLORS['subtext0']};
                border: none;
                font-size: 14px;
            }}
            QPushButton:hover {{
                background: {COLORS['red']};
                color: {COLORS['base']};
                border-radius: 6px;
            }}
        """)
        close_btn.clicked.connect(self.hide)
        search_row.addWidget(close_btn)

        main_layout.addLayout(search_row)

        # ── 스크롤 영역 ──
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet(f"""
            QScrollArea {{
                border: none;
                background: transparent;
            }}
            QScrollBar:vertical {{
                background: {COLORS['surface0']};
                width: 6px;
                border-radius: 3px;
            }}
            QScrollBar::handle:vertical {{
                background: {COLORS['overlay0']};
                border-radius: 3px;
                min-height: 20px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0;
            }}
        """)

        self._scroll_content = QWidget()
        self._scroll_content.setStyleSheet("background: transparent;")
        self._items_layout = QVBoxLayout(self._scroll_content)
        self._items_layout.setContentsMargins(0, 0, 0, 0)
        self._items_layout.setSpacing(4)
        self._items_layout.addStretch()

        self._scroll.setWidget(self._scroll_content)
        main_layout.addWidget(self._scroll, 1)

    # ── Public API ──

    def refresh(
        self,
        pinned: list[ClipboardItem],
        history: list[ClipboardItem],
        pointer: int,
        total: int,
    ):
        """항목 목록 갱신"""
        self._pinned_items = pinned
        self._history_items = history
        self._pointer = pointer
        self._total = total
        self._rebuild()

    def update_queue_status(self, pointer: int, total: int):
        """큐 상태만 업데이트 (붙여넣기 시)"""
        self._pointer = pointer
        self._total = total
        self._rebuild()

    def toggle(self):
        """표시/숨기기 토글"""
        if self.isVisible():
            self.hide()
        else:
            self.show()
            self.raise_()
            self.activateWindow()

    # ── Internal ──

    def _rebuild(self):
        """항목 위젯 전체 재구성"""
        # 기존 위젯 제거 (stretch 포함)
        while self._items_layout.count():
            child = self._items_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        search = self._search_text.lower().strip()

        # ── 고정 섹션 ──
        filtered_pinned = self._filter_items(self._pinned_items, search)
        if filtered_pinned:
            header = self._create_section_header(
                f"\U0001f4cc 고정 항목 ({len(filtered_pinned)})"
            )
            self._items_layout.addWidget(header)

            for i, item in enumerate(filtered_pinned, 1):
                widget = PanelItemWidget(
                    item, i,
                    is_pinned=True,
                    is_selected=item.id in self._selected_ids,
                )
                self._connect_item_signals(widget)
                self._items_layout.addWidget(widget)

            # 구분선
            sep = QWidget()
            sep.setFixedHeight(1)
            sep.setStyleSheet(f"background-color: {COLORS['surface1']};")
            self._items_layout.addWidget(sep)

        # ── 히스토리 섹션 ──
        filtered_history = self._filter_items(self._history_items, search)
        status_text = ""
        if self._total > 0:
            if self._pointer >= self._total:
                status_text = "완료 \u2713"
            else:
                status_text = f"붙여넣기 {self._pointer + 1}/{self._total}"

        header_text = f"\U0001f4cb 히스토리 ({len(filtered_history)})"
        if status_text:
            header_text += f"          {status_text}"
        header = self._create_section_header(header_text)
        self._items_layout.addWidget(header)

        for i, item in enumerate(filtered_history, 1):
            # 순차 상태 계산: history는 최신순(DESC)이므로
            # 큐 내 인덱스와 매핑 필요 — 큐는 별도이므로 ID 기반 매칭
            is_current = False
            is_done = False
            widget = PanelItemWidget(
                item, i,
                is_current=is_current,
                is_done=is_done,
                is_selected=item.id in self._selected_ids,
            )
            self._connect_item_signals(widget)
            self._items_layout.addWidget(widget)

        self._items_layout.addStretch()

    def _filter_items(self, items: list[ClipboardItem], search: str) -> list[ClipboardItem]:
        """검색 필터"""
        if not search:
            return items
        return [
            item for item in items
            if item.preview_text and search in item.preview_text.lower()
        ]

    def _create_section_header(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setFixedHeight(28)
        label.setStyleSheet(f"""
            color: {COLORS['overlay0']};
            font-size: 11px;
            font-weight: 600;
            padding-left: 4px;
        """)
        return label

    def _connect_item_signals(self, widget: PanelItemWidget):
        widget.clicked.connect(self._on_item_clicked)
        widget.double_clicked.connect(self._on_item_double_clicked)
        widget.context_menu_requested.connect(self._on_item_context_menu)

    # ── 이벤트 핸들러 ──

    def _on_search_changed(self, text: str):
        self._search_text = text
        self._rebuild()

    def _on_item_clicked(self, item_id: int, event):
        """단일/다중 선택 처리 + 고정 항목 클릭 복사 (F7-6)"""
        modifiers = QApplication.keyboardModifiers()

        if modifiers & Qt.KeyboardModifier.ControlModifier:
            # Ctrl+클릭: 토글 선택 (F4-6)
            if item_id in self._selected_ids:
                self._selected_ids.discard(item_id)
            else:
                self._selected_ids.add(item_id)
        elif modifiers & Qt.KeyboardModifier.ShiftModifier:
            # Shift+클릭: 범위 선택 (F4-7)
            if self._last_clicked_id is not None:
                self._select_range(self._last_clicked_id, item_id)
        else:
            # 일반 클릭
            self._selected_ids.clear()
            self._selected_ids.add(item_id)

            # F7-6: 고정 항목 클릭 시 클립보드에 복사
            item = self._find_item(item_id)
            if item and item.is_pinned:
                self.copy_item_requested.emit(item)

        self._last_clicked_id = item_id
        self._rebuild()

    def _select_range(self, from_id: int, to_id: int):
        """Shift+클릭 범위 선택"""
        all_items = self._pinned_items + self._history_items
        ids = [item.id for item in all_items]

        try:
            from_idx = ids.index(from_id)
            to_idx = ids.index(to_id)
        except ValueError:
            return

        start, end = min(from_idx, to_idx), max(from_idx, to_idx)
        for idx in range(start, end + 1):
            self._selected_ids.add(ids[idx])

    def _on_item_double_clicked(self, item_id: int):
        """더블클릭 → 붙여넣기 후 패널 닫기 (F5-2)"""
        item = self._find_item(item_id)
        if item:
            self.paste_item_requested.emit(item)
            self.hide()

    def _on_item_context_menu(self, item_id: int, pos: QPoint):
        """우클릭 컨텍스트 메뉴 (F4-8)"""
        item = self._find_item(item_id)
        if not item:
            return

        menu = QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{
                background-color: {COLORS['surface0']};
                color: {COLORS['text']};
                border: 1px solid {COLORS['surface1']};
                border-radius: 8px;
                padding: 4px;
            }}
            QMenu::item {{
                padding: 6px 16px;
                border-radius: 4px;
            }}
            QMenu::item:selected {{
                background-color: {COLORS['surface1']};
            }}
            QMenu::separator {{
                height: 1px;
                background: {COLORS['surface1']};
                margin: 4px 8px;
            }}
        """)

        paste_action = menu.addAction("\U0001f4cb 붙여넣기")
        paste_action.triggered.connect(lambda: self._do_paste(item))

        if item.is_pinned:
            unpin_action = menu.addAction("\U0001f4cc 고정 해제")
            unpin_action.triggered.connect(lambda: self.unpin_item_requested.emit(item_id))
        else:
            pin_action = menu.addAction("\U0001f4cc 고정")
            pin_action.triggered.connect(lambda: self.pin_item_requested.emit(item_id))

        copy_action = menu.addAction("\U0001f4cb 복사")
        copy_action.triggered.connect(lambda: self._do_copy(item))

        menu.addSeparator()

        delete_action = menu.addAction("\U0001f5d1 삭제")
        delete_action.triggered.connect(lambda: self.delete_item_requested.emit(item_id))

        menu.exec(pos)

    def _do_paste(self, item: ClipboardItem):
        """붙여넣기 실행"""
        self.paste_item_requested.emit(item)
        self.hide()

    def _do_copy(self, item: ClipboardItem):
        """클립보드에 복사 (붙여넣기 없이)"""
        clipboard = QApplication.clipboard()
        if item.text_content:
            clipboard.setText(item.text_content)

    def _find_item(self, item_id: int) -> Optional[ClipboardItem]:
        """ID로 항목 찾기"""
        for item in self._pinned_items + self._history_items:
            if item.id == item_id:
                return item
        return None

    def _on_pin_reorder(self, source_id: int, target_id: int):
        """고정 항목 드래그 재정렬 (F7-5)"""
        ids = [item.id for item in self._pinned_items]
        if source_id not in ids or target_id not in ids:
            return

        source_idx = ids.index(source_id)
        target_idx = ids.index(target_id)

        ids.remove(source_id)
        new_target_idx = ids.index(target_id)

        if source_idx < target_idx:
            # 아래로 드래그 → 타겟 뒤에 삽입
            ids.insert(new_target_idx + 1, source_id)
        else:
            # 위로 드래그 → 타겟 앞에 삽입
            ids.insert(new_target_idx, source_id)

        new_orders = [(item_id, i) for i, item_id in enumerate(ids)]
        self.pin_reorder_requested.emit(new_orders)

    # ── 외부 클릭 감지 (F4-9) ──

    def showEvent(self, event):
        """표시 시 앱 상태 감시 시작"""
        super().showEvent(event)
        QApplication.instance().applicationStateChanged.connect(
            self._on_app_state_changed
        )

    def hideEvent(self, event):
        """숨김 시 앱 상태 감시 해제"""
        try:
            QApplication.instance().applicationStateChanged.disconnect(
                self._on_app_state_changed
            )
        except (TypeError, RuntimeError):
            pass
        super().hideEvent(event)

    def _on_app_state_changed(self, state):
        """앱이 비활성화(다른 앱 클릭)되면 패널 닫기"""
        from PyQt6.QtCore import Qt as QtCore_Qt
        if (
            self._auto_close
            and self.isVisible()
            and state != QtCore_Qt.ApplicationState.ApplicationActive
        ):
            self.hide()

    # ── 드래그 이동 ──

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_pos and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        super().mouseReleaseEvent(event)
