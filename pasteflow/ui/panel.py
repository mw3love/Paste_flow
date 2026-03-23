"""전체 클립보드 패널 — F4 요구사항 구현

고정 섹션 + 히스토리 섹션, 검색, 우클릭 컨텍스트 메뉴, 다중 선택.
Alt+V / 트레이 / 미니창 ▲ 버튼으로 토글.
"""
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

# 드래그 MIME 타입
MIME_PIN_REORDER = "application/x-pasteflow-pin-id"
MIME_ITEM_TO_PIN = "application/x-pasteflow-item-id"


class PanelItemWidget(QWidget):
    """패널 내 개별 항목 위젯"""

    clicked = pyqtSignal(int, object)          # (item_id, QMouseEvent)
    double_clicked = pyqtSignal(int)           # item_id
    context_menu_requested = pyqtSignal(int, object)  # (item_id, QPoint)
    delete_clicked = pyqtSignal(int)           # item_id

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
        layout.setContentsMargins(0, 0, 4, 0)
        layout.setSpacing(6)

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

        # 미리보기
        if item.content_type == "image" and item.thumbnail:
            thumb_label = QLabel()
            pixmap = QPixmap()
            pixmap.loadFromData(item.thumbnail)
            scaled = pixmap.scaled(80, 60, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            thumb_label.setPixmap(scaled)
            thumb_label.setFixedSize(80, 60)
            layout.addWidget(thumb_label)
            layout.addStretch(1)
            self.setMinimumHeight(72)
        else:
            preview = item.text_content or item.preview_text or ""
            lines = preview.strip().split("\n")[:3]
            display_text = "\n".join(line[:80] for line in lines)

            text_label = QLabel(display_text)
            text_label.setWordWrap(True)
            text_label.setStyleSheet(f"color: {text_color}; font-size: 12px;")
            layout.addWidget(text_label, 1)
            self.setMinimumHeight(56)

        # 삭제 버튼 (×)
        del_btn = QPushButton("\u2715")
        del_btn.setFixedSize(22, 22)
        del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        del_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {COLORS['overlay0']};
                border: none;
                font-size: 12px;
            }}
            QPushButton:hover {{
                background: {COLORS['red']};
                color: {COLORS['base']};
                border-radius: 11px;
            }}
        """)
        del_btn.clicked.connect(lambda: self.delete_clicked.emit(self.item_id))
        layout.addWidget(del_btn)

        self._apply_bg_style()

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
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._is_hovered = False
        self._apply_bg_style()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_pos = event.pos()
            self._did_drag = False
            # 이미지 항목 클릭 시 확대 미리보기
            if self.item.content_type == "image" and self.item.image_data:
                ImagePreviewPopup.instance().toggle_preview(
                    self.item.image_data, QCursor.pos()
                )
            # 고정 항목은 release에서 클릭 처리 (드래그와 충돌 방지)
            if not self._is_pinned:
                self.clicked.emit(self.item_id, event)
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        """드래그 시작 — 고정 항목은 재정렬, 히스토리 항목은 고정으로 이동"""
        if self._drag_start_pos and event.buttons() & Qt.MouseButton.LeftButton:
            distance = (event.pos() - self._drag_start_pos).manhattanLength()
            if distance > 10:
                self._did_drag = True
                drag = QDrag(self)
                mime = QMimeData()
                if self._is_pinned:
                    mime.setData(MIME_PIN_REORDER, str(self.item_id).encode())
                else:
                    mime.setData(MIME_ITEM_TO_PIN, str(self.item_id).encode())
                drag.setMimeData(mime)
                drag.exec(Qt.DropAction.MoveAction)
                self._drag_start_pos = None
                return
        event.accept()

    def mouseReleaseEvent(self, event):
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
        if event.mimeData().hasFormat(MIME_PIN_REORDER):
            event.acceptProposedAction()
            self.setStyleSheet(f"background-color: {COLORS['surface2']}; border-radius: 6px; border: 1px dashed {COLORS['teal']};")

    def dragLeaveEvent(self, event):
        self._apply_bg_style()

    def dropEvent(self, event):
        """드롭 시 순서 교환 시그널 발신"""
        source_id = int(event.mimeData().data(MIME_PIN_REORDER).data().decode())
        target_id = self.item_id
        if source_id != target_id:
            panel = self.parent()
            while panel and not isinstance(panel, ClipboardPanel):
                panel = panel.parent()
            if panel:
                panel._on_pin_reorder(source_id, target_id)
        self._apply_bg_style()
        event.acceptProposedAction()


class PinDropZone(QWidget):
    """고정 섹션 헤더 — 히스토리 항목 드롭 수신"""

    item_dropped = pyqtSignal(int)  # item_id

    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setFixedHeight(28)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(4)

        self._label = QLabel(text)
        self._label.setStyleSheet(f"""
            color: {COLORS['overlay0']};
            font-size: 11px;
            font-weight: 600;
        """)
        layout.addWidget(self._label)
        layout.addStretch()

        self._default_style = "background: transparent;"
        self.setStyleSheet(self._default_style)

    def update_text(self, text: str):
        self._label.setText(text)

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat(MIME_ITEM_TO_PIN):
            event.acceptProposedAction()
            self.setStyleSheet(f"background-color: {COLORS['surface1']}; border: 1px dashed {COLORS['peach']}; border-radius: 6px;")

    def dragLeaveEvent(self, event):
        self.setStyleSheet(self._default_style)

    def dropEvent(self, event):
        if event.mimeData().hasFormat(MIME_ITEM_TO_PIN):
            item_id = int(event.mimeData().data(MIME_ITEM_TO_PIN).data().decode())
            self.item_dropped.emit(item_id)
        self.setStyleSheet(self._default_style)
        event.acceptProposedAction()


class ClipboardPanel(QWidget):
    """전체 클립보드 패널"""

    paste_item_requested = pyqtSignal(object)    # ClipboardItem
    copy_item_requested = pyqtSignal(object)     # ClipboardItem
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
        self._pinned_collapsed = True  # 고정 섹션 기본 접힘

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

        screen = QApplication.primaryScreen()
        if screen:
            geom = screen.availableGeometry()
            x = geom.right() - PANEL_WIDTH - 10
            y = geom.bottom() - PANEL_HEIGHT - 10
            self.move(x, y)

    def _setup_ui(self):
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
        self._pinned_items = pinned
        self._history_items = history
        self._pointer = pointer
        self._total = total
        self._rebuild()

    def update_queue_status(self, pointer: int, total: int):
        self._pointer = pointer
        self._total = total
        self._rebuild()

    def toggle(self):
        if self.isVisible():
            self.hide()
        else:
            self.show()
            self.raise_()
            self.activateWindow()

    # ── Internal ──

    def _rebuild(self):
        """항목 위젯 전체 재구성"""
        while self._items_layout.count():
            child = self._items_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        search = self._search_text.lower().strip()

        # ── 고정 섹션 (접기/펼치기) ──
        filtered_pinned = self._filter_items(self._pinned_items, search)
        pin_count = len(filtered_pinned)

        # 고정 헤더 (토글 + 드롭 존)
        arrow = "\u25BC" if not self._pinned_collapsed else "\u25B6"
        pin_header_text = f"{arrow} \U0001f4cc 고정 ({pin_count})"

        pin_header_btn = QPushButton(pin_header_text)
        pin_header_btn.setFixedHeight(28)
        pin_header_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        pin_header_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {COLORS['overlay0']};
                border: none;
                font-size: 11px;
                font-weight: 600;
                text-align: left;
                padding-left: 4px;
            }}
            QPushButton:hover {{
                color: {COLORS['text']};
            }}
        """)
        pin_header_btn.clicked.connect(self._toggle_pinned)
        self._items_layout.addWidget(pin_header_btn)

        # 드롭 존 (히스토리 → 고정 드래그용)
        drop_zone = PinDropZone("  \u2191 여기에 드롭하여 고정")
        drop_zone.item_dropped.connect(lambda item_id: self.pin_item_requested.emit(item_id))
        drop_zone.setVisible(not self._pinned_collapsed or pin_count == 0)
        self._items_layout.addWidget(drop_zone)

        if not self._pinned_collapsed and filtered_pinned:
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
                status_text = "\uc644\ub8cc \u2713"
            else:
                status_text = f"\ubd99\uc5ec\ub123\uae30 {self._pointer + 1}/{self._total}"

        header_text = f"\U0001f4cb \ud788\uc2a4\ud1a0\ub9ac ({len(filtered_history)})"
        if status_text:
            header_text += f"          {status_text}"
        header = self._create_section_header(header_text)
        self._items_layout.addWidget(header)

        for i, item in enumerate(filtered_history, 1):
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

    def _toggle_pinned(self):
        """고정 섹션 토글"""
        self._pinned_collapsed = not self._pinned_collapsed
        self._rebuild()

    def _filter_items(self, items: list[ClipboardItem], search: str) -> list[ClipboardItem]:
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
        widget.delete_clicked.connect(self._on_item_delete)

    # ── 이벤트 핸들러 ──

    def _on_search_changed(self, text: str):
        self._search_text = text
        self._rebuild()

    def _on_item_clicked(self, item_id: int, event):
        modifiers = QApplication.keyboardModifiers()

        if modifiers & Qt.KeyboardModifier.ControlModifier:
            if item_id in self._selected_ids:
                self._selected_ids.discard(item_id)
            else:
                self._selected_ids.add(item_id)
        elif modifiers & Qt.KeyboardModifier.ShiftModifier:
            if self._last_clicked_id is not None:
                self._select_range(self._last_clicked_id, item_id)
        else:
            self._selected_ids.clear()
            self._selected_ids.add(item_id)

            item = self._find_item(item_id)
            if item and item.is_pinned:
                self.copy_item_requested.emit(item)

        self._last_clicked_id = item_id
        self._update_selection_visuals()

    def _update_selection_visuals(self):
        """선택 상태만 시각적으로 업데이트 — rebuild 없이"""
        for i in range(self._items_layout.count()):
            widget = self._items_layout.itemAt(i).widget()
            if isinstance(widget, PanelItemWidget):
                widget.is_selected = widget.item_id in self._selected_ids

    def _select_range(self, from_id: int, to_id: int):
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
        """더블클릭 → 붙여넣기 (패널은 열린 상태 유지)"""
        item = self._find_item(item_id)
        if item:
            self.paste_item_requested.emit(item)

    def _on_item_delete(self, item_id: int):
        """삭제 버튼 클릭 — 위젯 즉시 숨겨서 빠른 피드백"""
        for i in range(self._items_layout.count()):
            widget = self._items_layout.itemAt(i).widget()
            if isinstance(widget, PanelItemWidget) and widget.item_id == item_id:
                widget.hide()
                break
        self.delete_item_requested.emit(item_id)

    def _on_item_context_menu(self, item_id: int, pos: QPoint):
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
        self.paste_item_requested.emit(item)

    def _do_copy(self, item: ClipboardItem):
        clipboard = QApplication.clipboard()
        if item.text_content:
            clipboard.setText(item.text_content)

    def _find_item(self, item_id: int) -> Optional[ClipboardItem]:
        for item in self._pinned_items + self._history_items:
            if item.id == item_id:
                return item
        return None

    def _on_pin_reorder(self, source_id: int, target_id: int):
        ids = [item.id for item in self._pinned_items]
        if source_id not in ids or target_id not in ids:
            return

        source_idx = ids.index(source_id)
        target_idx = ids.index(target_id)

        ids.remove(source_id)
        new_target_idx = ids.index(target_id)

        if source_idx < target_idx:
            ids.insert(new_target_idx + 1, source_id)
        else:
            ids.insert(new_target_idx, source_id)

        new_orders = [(item_id, i) for i, item_id in enumerate(ids)]
        self.pin_reorder_requested.emit(new_orders)

    # ── 외부 클릭 감지 ──

    def showEvent(self, event):
        super().showEvent(event)
        QApplication.instance().applicationStateChanged.connect(
            self._on_app_state_changed
        )

    def hideEvent(self, event):
        try:
            QApplication.instance().applicationStateChanged.disconnect(
                self._on_app_state_changed
            )
        except (TypeError, RuntimeError):
            pass
        super().hideEvent(event)

    def _on_app_state_changed(self, state):
        from PyQt6.QtCore import Qt as QtCore_Qt
        if (
            self._auto_close
            and self.isVisible()
            and state != QtCore_Qt.ApplicationState.ApplicationActive
        ):
            QTimer.singleShot(100, self._check_and_hide)

    def _check_and_hide(self):
        """100ms 후 재확인 — 내부 rebuild 중 오판 방지"""
        from PyQt6.QtCore import Qt as QtCore_Qt
        if (
            self._auto_close
            and self.isVisible()
            and QApplication.instance().applicationState()
                != QtCore_Qt.ApplicationState.ApplicationActive
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
