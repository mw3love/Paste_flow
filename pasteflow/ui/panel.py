"""전체 클립보드 패널

고정 섹션 + 히스토리 섹션, 검색, 우클릭 컨텍스트 메뉴, 다중 선택.
Alt+V / 트레이로 토글.
"""
from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QScrollArea, QMenu, QApplication, QGraphicsOpacityEffect,
    QSizePolicy, QDialog, QPlainTextEdit, QDialogButtonBox,
)
import ctypes
import ctypes.wintypes

from PyQt6.QtCore import Qt, pyqtSignal, QPoint, QTimer, QEvent, QMimeData, QRect
from PyQt6.QtGui import QPixmap, QCursor, QFontMetrics, QFont

_HWND_TOPMOST = ctypes.wintypes.HWND(-1)
_HWND_NOTOPMOST = ctypes.wintypes.HWND(-2)
_SWP_NOMOVE = 0x0002
_SWP_NOSIZE = 0x0001
_SWP_NOACTIVATE = 0x0010

from pasteflow.models import ClipboardItem
from pasteflow.ui.image_preview import ImagePreviewPopup
from pasteflow.ui.text_preview import TextPreviewPopup

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

PANEL_WIDTH = 280
PANEL_HEIGHT = 350
PANEL_MIN_WIDTH = 220
PANEL_MIN_HEIGHT = 325
RESIZE_MARGIN = 6

# 드래그 MIME 타입
MIME_ITEM_TO_PIN = "application/x-pasteflow-item-id"


class PanelItemWidget(QWidget):
    """패널 내 개별 항목 위젯"""

    clicked = pyqtSignal(int, object)
    double_clicked = pyqtSignal(int)
    context_menu_requested = pyqtSignal(int, object)
    delete_clicked = pyqtSignal(int)
    external_drag_paste = pyqtSignal(int, QPoint)

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
        self._ext_drag_active = False
        self._text_label: Optional[QLabel] = None
        self._first_resize = True

        self._setup_ui(item, number, is_current, is_done, is_pinned)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)


    def _setup_ui(
        self, item: ClipboardItem, number: int,
        is_current: bool, is_done: bool, is_pinned: bool,
    ):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(6)

        # 좌측 번호 레이블 — 현재 큐 항목=청록, 완료=회색, 고정=주황, 일반=주황
        if is_current:
            accent_color = COLORS['teal']
        elif is_done:
            accent_color = COLORS['surface2']
        else:
            accent_color = COLORS['peach']
        self._accent_color = accent_color

        num_label = QLabel(str(number))
        num_label.setFixedWidth(22)
        num_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        num_label.setStyleSheet("color: #ffffff; font-size: 13px; background: transparent; border: none;")
        layout.addWidget(num_label)

        # 미리보기
        text_color = COLORS['subtext0'] if is_done else "#ffffff"
        if item.content_type == "image" and item.thumbnail:
            thumb_label = QLabel()
            thumb_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            pixmap = QPixmap()
            pixmap.loadFromData(item.thumbnail)
            scaled = pixmap.scaled(80, 60, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            thumb_label.setPixmap(scaled)
            thumb_label.setFixedSize(80, 60)
            layout.addWidget(thumb_label)
            layout.addStretch(1)
            self.setFixedHeight(72)
        else:
            preview = item.text_content or item.preview_text or ""
            all_lines = preview.strip().split("\n")
            lines = all_lines[:3]
            display_text = "\n".join(line[:80] for line in lines)
            line_count = len(lines)

            text_label = QLabel(display_text)
            text_label.setWordWrap(True)
            text_label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
            text_label.setMinimumWidth(0)
            text_label.setMaximumHeight(45)  # 최대 3줄
            text_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            text_label.setStyleSheet(f"color: {text_color}; font-size: 12px;")
            # AlignVCenter 명시: label이 행 내 상단/하단 쏠림 방지
            layout.addWidget(text_label, 1, Qt.AlignmentFlag.AlignVCenter)
            self._text_label = text_label

            # 생성 시 높이 미리 계산 → 첫 resizeEvent 점프(지직) 방지
            # 실제 레이블 너비 ≈ PANEL_WIDTH - 패널마진(20) - 아이템내부(lmargin4+num22+spacing6+del22+rmargin4) = PANEL_WIDTH - 78
            _f = QFont(); _f.setPixelSize(12)
            _fm = QFontMetrics(_f)
            _rect = _fm.boundingRect(
                QRect(0, 0, max(1, PANEL_WIDTH - 78), 10000),
                Qt.TextFlag.TextWordWrap | int(Qt.AlignmentFlag.AlignLeft),
                display_text,
            )
            _vl = max(1, (_rect.height() + _fm.height() - 1) // _fm.height())
            _init_h = 26 if _vl == 1 else (38 if _vl == 2 else 50)
            self.setFixedHeight(_init_h)

        # 바는 레이아웃이 행 높이에 맞춰 자동 조정 (타이머 불필요)

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

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._first_resize:
            self._first_resize = False
            return
        self._adjust_text_height()

    def _adjust_text_height(self):
        if self._text_label is None:
            return
        avail_w = self.width() - 58  # lmargin(4) + num(22) + spacing(6) + del_btn(22) + rmargin(4)
        if avail_w <= 0:
            return
        fm = self._text_label.fontMetrics()
        rect = fm.boundingRect(
            QRect(0, 0, avail_w, 10000),
            Qt.TextFlag.TextWordWrap | int(Qt.AlignmentFlag.AlignLeft),
            self._text_label.text(),
        )
        actual_lines = max(1, (rect.height() + fm.height() - 1) // fm.height())
        visual_lines = min(3, actual_lines)
        new_h = 26 if visual_lines == 1 else (38 if visual_lines == 2 else 50)
        # 4줄 이상: AlignTop(1·2·3번째 줄 표시, 나머지 하단 클립)
        # 1~3줄: AlignVCenter(균등 여백)
        align = (Qt.AlignmentFlag.AlignTop if actual_lines > 3
                 else Qt.AlignmentFlag.AlignVCenter) | Qt.AlignmentFlag.AlignLeft
        self._text_label.setAlignment(align)
        if self.height() != new_h:
            self.setFixedHeight(new_h)

    def _apply_bg_style(self):
        border_color = self._accent_color
        if self._is_selected:
            self.setStyleSheet(f"background-color: {COLORS['surface2']}; border-radius: 6px; border: 1px solid {border_color};")
        elif self._is_hovered:
            self.setStyleSheet(f"background-color: {COLORS['surface1']}; border-radius: 6px; border: 1px solid {border_color};")
        else:
            self.setStyleSheet(f"background-color: {COLORS['surface0']}; border-radius: 6px; border: 1px solid {border_color};")

    def set_queue_state(self, is_current: bool, is_done: bool, in_queue: bool = True):
        """위젯 재생성 없이 큐 상태(색상)만 업데이트.
        in_queue=False → 큐에서 제거됨, 기본 색상으로 복원.
        """
        if is_current:
            self._accent_color = COLORS['teal']
        elif is_done:
            self._accent_color = COLORS['surface2']
        else:
            self._accent_color = COLORS['peach']
        if self._text_label:
            text_color = COLORS['subtext0'] if is_done else "#ffffff"
            self._text_label.setStyleSheet(f"color: {text_color}; font-size: 12px;")
        self._apply_bg_style()

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
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        """드래그 시작"""
        if self._drag_start_pos and event.buttons() & Qt.MouseButton.LeftButton:
            distance = (event.pos() - self._drag_start_pos).manhattanLength()
            if distance > 10:
                self._did_drag = True
                ImagePreviewPopup.close_all()
                TextPreviewPopup.instance().hide_preview()

                if not self._is_pinned:
                    # 비고정 항목 → 패널 안: 재정렬 / 패널 밖: 외부 드래그
                    panel = self.parent()
                    while panel and not isinstance(panel, ClipboardPanel):
                        panel = panel.parent()
                    cursor_pos = QCursor.pos()
                    inside = self.window().geometry().contains(cursor_pos)
                    new_shape = (Qt.CursorShape.ClosedHandCursor if inside
                                 else Qt.CursorShape.DragCopyCursor)
                    if not self._ext_drag_active:
                        # 드래그 시작: 커서 스택에 1회만 push
                        self._ext_drag_active = True
                        self._apply_drag_source_style()
                        QApplication.setOverrideCursor(new_shape)
                        if panel:
                            panel._ext_drag_active = True
                            panel._hist_drag_source_id = self.item_id
                    else:
                        # 이미 드래그 중: 스택 깊이 유지, 모양만 교체
                        oc = QApplication.overrideCursor()
                        if oc and oc.shape() != new_shape:
                            QApplication.changeOverrideCursor(QCursor(new_shape))
                    if inside and panel:
                        panel._update_hist_hover(cursor_pos)
                    elif not inside and panel:
                        panel._clear_hist_drag_highlight()
                        panel._hist_drag_target_id = None
                    return

                # 고정 항목 → 비고정 항목과 동일한 fake drag 방식
                panel = self.parent()
                while panel and not isinstance(panel, ClipboardPanel):
                    panel = panel.parent()
                cursor_pos = QCursor.pos()
                inside = self.window().geometry().contains(cursor_pos)
                new_shape = (Qt.CursorShape.ClosedHandCursor if inside
                             else Qt.CursorShape.DragCopyCursor)
                if not self._ext_drag_active:
                    self._ext_drag_active = True
                    self._apply_drag_source_style()
                    QApplication.setOverrideCursor(new_shape)
                    if panel:
                        panel._ext_drag_active = True
                        panel._pin_drag_source_id = self.item_id
                else:
                    oc = QApplication.overrideCursor()
                    if oc and oc.shape() != new_shape:
                        QApplication.changeOverrideCursor(QCursor(new_shape))
                if inside and panel:
                    panel._update_pin_hover(cursor_pos)
                elif not inside and panel:
                    panel._clear_pin_drag_highlight()
                    panel._pin_drag_target_id = None
                return
        event.accept()

    def _apply_drag_source_style(self):
        """드래그 소스 위젯 강조 스타일"""
        self.setStyleSheet(
            f"background-color: {COLORS['surface2']}; border-radius: 6px;"
            f"border: 1px solid {COLORS['teal']};"
        )

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if self._ext_drag_active:
                self._ext_drag_active = False
                QApplication.restoreOverrideCursor()
                panel = self.parent()
                while panel and not isinstance(panel, ClipboardPanel):
                    panel = panel.parent()
                cursor_pos = QCursor.pos()
                if panel:
                    if not self.window().geometry().contains(cursor_pos):
                        # 붙여넣기 완료 후 OS 활성화 이벤트가 changeEvent를 발동하기 전까지 guard 유지
                        QTimer.singleShot(300, lambda: setattr(panel, '_ext_drag_active', False))
                    else:
                        panel._ext_drag_active = False
                self._apply_bg_style()  # 드래그 소스 강조 해제
                if not self.window().geometry().contains(cursor_pos):
                    self.external_drag_paste.emit(self.item_id, cursor_pos)
                elif self._is_pinned and panel:
                    panel._do_pin_reorder(self.item_id, panel._pin_drag_target_id)
                    panel._clear_pin_drag_highlight()
                    panel._emit_current_pin_order()
                    panel._pin_drag_source_id = None
                    panel._pin_drag_target_id = None
                elif not self._is_pinned and panel:
                    panel._do_hist_reorder(self.item_id, panel._hist_drag_target_id)
                    panel._clear_hist_drag_highlight()
                    panel._emit_current_hist_order()
                    panel._hist_drag_source_id = None
                    panel._hist_drag_target_id = None
            elif not self._did_drag:
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



class PinDropZone(QWidget):
    """고정 섹션 — 히스토리 항목 드롭 수신"""

    item_dropped = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setFixedHeight(4)
        self._default_style = "background: transparent;"
        self.setStyleSheet(self._default_style)

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat(MIME_ITEM_TO_PIN):
            event.acceptProposedAction()
            self.setFixedHeight(28)
            self.setStyleSheet(
                f"background-color: {COLORS['surface1']};"
                f"border: 1px dashed {COLORS['peach']};"
                f"border-radius: 6px;"
            )

    def dragLeaveEvent(self, event):
        self.setFixedHeight(4)
        self.setStyleSheet(self._default_style)

    def dropEvent(self, event):
        if event.mimeData().hasFormat(MIME_ITEM_TO_PIN):
            item_id = int(event.mimeData().data(MIME_ITEM_TO_PIN).data().decode())
            self.item_dropped.emit(item_id)
        self.setFixedHeight(4)
        self.setStyleSheet(self._default_style)
        event.acceptProposedAction()


class EditItemDialog(QDialog):
    """고정 항목 텍스트 수정 다이얼로그"""

    def __init__(self, current_text: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("항목 수정")
        self.setMinimumSize(360, 200)
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {COLORS['base']};
                color: {COLORS['text']};
            }}
            QLabel {{
                color: {COLORS['subtext0']};
                font-size: 11px;
            }}
            QPlainTextEdit {{
                background-color: {COLORS['surface0']};
                color: {COLORS['text']};
                border: 1px solid {COLORS['surface2']};
                border-radius: 6px;
                padding: 6px;
                font-size: 13px;
            }}
            QPlainTextEdit:focus {{
                border: 1px solid {COLORS['teal']};
            }}
            QPushButton {{
                background-color: {COLORS['surface1']};
                color: {COLORS['text']};
                border: none;
                border-radius: 6px;
                padding: 6px 16px;
                font-size: 12px;
            }}
            QPushButton:hover {{
                background-color: {COLORS['surface2']};
            }}
            QPushButton[text="저장"] {{
                background-color: {COLORS['teal']};
                color: {COLORS['base']};
            }}
            QPushButton[text="저장"]:hover {{
                background-color: #7ed5c8;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(12, 12, 12, 12)

        layout.addWidget(QLabel("내용을 수정하세요. 저장 시 원본 서식(HTML/RTF)은 제거됩니다."))

        self._editor = QPlainTextEdit()
        self._editor.setPlainText(current_text)
        self._editor.setFocus()
        layout.addWidget(self._editor, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("취소")
        cancel_btn.clicked.connect(self.reject)
        save_btn = QPushButton("저장")
        save_btn.setProperty("text", "저장")
        save_btn.clicked.connect(self.accept)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(save_btn)
        layout.addLayout(btn_row)

    def get_text(self) -> str:
        return self._editor.toPlainText()


class ClipboardPanel(QWidget):
    """전체 클립보드 패널"""

    paste_item_requested = pyqtSignal(object)
    copy_item_requested = pyqtSignal(object)
    combine_copy_requested = pyqtSignal(object)  # F6: 다중 선택 결합 복사
    pin_item_requested = pyqtSignal(int)
    unpin_item_requested = pyqtSignal(int)
    delete_item_requested = pyqtSignal(int)
    pin_reorder_requested = pyqtSignal(list)
    history_reorder_requested = pyqtSignal(list)
    edit_item_requested = pyqtSignal(int, str)  # (item_id, new_text)
    preview_image_requested = pyqtSignal(int, QPoint)  # (item_id, global_pos)
    open_settings_requested = pyqtSignal()
    quit_requested = pyqtSignal()
    clear_history_requested = pyqtSignal()
    drag_to_app_requested = pyqtSignal(int, QPoint)
    queue_select_requested = pyqtSignal(int)  # item_id — 해당 항목부터 최신까지 큐 설정
    panel_hidden = pyqtSignal()  # 패널 숨겨질 때 emit
    always_on_top_changed = pyqtSignal(bool)  # 항상 위에 상태 변경 → main이 DB 저장

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pinned_items: list[ClipboardItem] = []
        self._history_items: list[ClipboardItem] = []
        self._pointer: int = 0
        self._total: int = 0
        self._selected_ids: set[int] = set()
        self._last_clicked_id: Optional[int] = None
        self._search_text: str = ""
        self._search_expanded: bool = False
        self._queue_item_ids: list[int] = []
        self._status_label: Optional[QLabel] = None
        self._drag_pos = None
        self._drag_source_id = None       # 드래그 중인 고정 항목 ID (QDrag 잔재, 미사용)
        self._pin_drag_source_id = None   # 드래그 중인 고정 항목 ID (fake drag)
        self._pin_drag_target_id = None   # 드래그 중 하이라이트된 고정 타겟 ID
        self._hist_drag_source_id = None  # 드래그 중인 히스토리 항목 ID
        self._hist_drag_target_id = None  # 드래그 중 하이라이트된 타겟 항목 ID
        self._resize_edges: set = set()
        self._resize_start_pos = None
        self._resize_start_geometry = None
        self._pinned_collapsed = True
        self._auto_close = True  # F4-9: 외부 클릭 시 자동 닫기
        self._user_activated = False  # 사용자가 직접 열었는지 (vs 복사 팝업)
        self._paste_in_progress = False  # 패널 붙여넣기 중 자동닫기 방지
        self._ext_drag_active = False    # 외부 드래그 중 자동닫기 방지
        self._kbd_focus_id: Optional[int] = None  # 키보드 포커스된 항목 ID
        self._always_on_top = True       # 항상 위에 토글 상태
        self._pin_btn: Optional[QPushButton] = None

        self._setup_window()
        self._setup_ui()

    def _setup_window(self):
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        # WS_EX_TOOLWINDOW: 작업표시줄 제외, QWindowToolSaveBits 창 생성 없이
        hwnd = int(self.winId())
        import ctypes
        GWL_EXSTYLE = -20
        WS_EX_TOOLWINDOW = 0x00000080
        WS_EX_APPWINDOW  = 0x00040000
        ex = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        ex = (ex | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW
        ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex)
        # 투명 배경 대신 solid 배경 — 리사이즈 가장자리에서 마우스 이벤트 수신 가능
        self.setStyleSheet(f"background-color: {COLORS['base']};")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.resize(PANEL_MIN_WIDTH, PANEL_MIN_HEIGHT)
        self.setMinimumSize(PANEL_MIN_WIDTH, PANEL_MIN_HEIGHT)

        self._cursor_timer = QTimer(self)
        self._cursor_timer.setInterval(50)
        self._cursor_timer.timeout.connect(self._sync_resize_cursor)

        screen = QApplication.primaryScreen()
        if screen:
            geom = screen.availableGeometry()
            x = geom.right() - PANEL_MIN_WIDTH - 10
            y = geom.bottom() - PANEL_MIN_HEIGHT - 10
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
        outer_layout.setContentsMargins(
            RESIZE_MARGIN, RESIZE_MARGIN, RESIZE_MARGIN, RESIZE_MARGIN
        )
        outer_layout.addWidget(self._container)

        main_layout = QVBoxLayout(self._container)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(8)

        # ── 검색 토글 버튼 + 검색 입력창 + 닫기 버튼 ──
        # 레이아웃: [🔍 or search_input(stretch)] [spacer(stretch)] [×]
        # 닫기(×)는 항상 우측 고정. 돋보기는 항상 좌측.
        search_row = QHBoxLayout()
        search_row.setSpacing(6)

        # 돋보기 아이콘 버튼 (축소 상태 — 좌측 고정)
        self._search_btn = QPushButton("\U0001f50d")
        self._search_btn.setFixedSize(28, 28)
        self._search_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._search_btn.setToolTip("검색 (클릭하여 열기)")
        self._search_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {COLORS['subtext0']};
                border: none;
                font-size: 14px;
                border-radius: 6px;
                padding: 0;
            }}
            QPushButton:hover {{
                background: {COLORS['surface0']};
                color: {COLORS['text']};
            }}
        """)
        self._search_btn.clicked.connect(self._toggle_search)
        search_row.addWidget(self._search_btn)

        # 검색 입력창 (펼침 상태 — stretch로 우측까지 채움)
        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("검색...")
        self._search_input.setFixedHeight(28)
        self._search_input.setStyleSheet(f"""
            QLineEdit {{
                background-color: {COLORS['surface0']};
                color: {COLORS['text']};
                border: 2px solid transparent;
                border-radius: 6px;
                padding: 0 8px;
                font-size: 12px;
            }}
            QLineEdit:focus {{
                border-color: {COLORS['blue']};
            }}
        """)
        self._search_input.textChanged.connect(self._on_search_changed)
        self._search_input.installEventFilter(self)
        self._search_input.hide()
        search_row.addWidget(self._search_input, 1)

        # 스페이서 — 축소 상태에서 돋보기와 닫기 사이 공간 채움
        self._header_spacer = QWidget()
        self._header_spacer.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        search_row.addWidget(self._header_spacer, 1)

        # 항상 위에 토글 버튼 — 닫기 버튼 왼쪽
        self._pin_btn = QPushButton("📌")
        self._pin_btn.setFixedSize(24, 24)
        self._pin_btn.setCheckable(True)
        self._pin_btn.setChecked(True)
        self._pin_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._pin_btn.setToolTip("항상 위에 고정")
        self._pin_btn.setStyleSheet(self._pin_btn_style(active=True))
        self._pin_btn.clicked.connect(self._toggle_always_on_top)
        search_row.addWidget(self._pin_btn)

        # 닫기 버튼 — 항상 빨간 원, 우측 고정
        close_btn = QPushButton("\u00d7")
        close_btn.setFixedSize(24, 24)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(f"""
            QPushButton {{
                background: {COLORS['red']};
                color: {COLORS['crust']};
                border: none;
                font-size: 16px;
                font-weight: 400;
                font-family: 'Segoe UI', sans-serif;
                padding: 0;
                padding-bottom: 1px;
                border-radius: 12px;
            }}
            QPushButton:hover {{
                background: #e06c75;
            }}
        """)
        close_btn.clicked.connect(self.hide)
        search_row.addWidget(close_btn)

        main_layout.addLayout(search_row)

        # ── 스크롤 영역 ──
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet(f"""
            QScrollArea {{
                border: none;
                background: transparent;
            }}
            QScrollBar:vertical {{
                background: transparent;
                width: 5px;
                margin: 2px 0;
            }}
            QScrollBar::handle:vertical {{
                background: {COLORS['surface2']};
                border-radius: 2px;
                min-height: 30px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: {COLORS['overlay0']};
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0;
            }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
                background: transparent;
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

        self._scroll_content.installEventFilter(self)

    # ── Public API ──

    def refresh(
        self,
        pinned: list[ClipboardItem],
        history: list[ClipboardItem],
        pointer: int,
        total: int,
        queue_item_ids: list[int] = None,
    ):
        self._pinned_items = pinned
        self._history_items = history
        self._pointer = pointer
        self._total = total
        self._queue_item_ids = queue_item_ids or []
        self._rebuild()

    @property
    def history_items(self) -> list:
        return self._history_items

    @property
    def pinned_items(self) -> list:
        return self._pinned_items

    def update_queue_status(self, pointer: int, total: int):
        self._pointer = pointer
        self._total = total
        self._rebuild()

    def update_queue_highlight(self, pointer: int, total: int, queue_item_ids: list):
        """큐 상태 시각 업데이트 — 위젯 재생성 없이 색상만 변경 (빠름)"""
        self._pointer = pointer
        self._total = total
        self._queue_item_ids = queue_item_ids

        if self._status_label:
            if total > 0:
                self._status_label.setText(f"붙여넣기 {pointer}/{total}")
                self._status_label.show()
            else:
                self._status_label.hide()

        q_index = {qid: idx for idx, qid in enumerate(queue_item_ids)}
        for i in range(self._items_layout.count()):
            widget = self._items_layout.itemAt(i).widget()
            if not isinstance(widget, PanelItemWidget):
                continue
            q_idx = q_index.get(widget.item_id)
            if q_idx is not None and q_idx < pointer:
                widget.set_queue_state(is_current=False, is_done=True)
            elif q_idx is not None and q_idx == pointer:
                widget.set_queue_state(is_current=True, is_done=False)
            elif q_idx is not None:
                widget.set_queue_state(is_current=False, is_done=False, in_queue=True)
            else:
                widget.set_queue_state(is_current=False, is_done=False, in_queue=False)

    def show_near_cursor(self):
        """마우스 커서 근처(우하단 +16px)에 패널 표시. 화면 경계 초과 시 반전."""
        cursor_pos = QCursor.pos()
        screen = QApplication.screenAt(cursor_pos) or QApplication.primaryScreen()
        avail = screen.availableGeometry()

        w = self.width()
        h = self.height()
        offset = 16

        x = cursor_pos.x() + offset
        y = cursor_pos.y() + offset

        if x + w > avail.right():
            x = cursor_pos.x() - w - offset
        if y + h > avail.bottom():
            y = cursor_pos.y() - h - offset

        x = max(avail.left(), x)
        y = max(avail.top(), y)

        # SetWindowPos로 이동+표시를 한 번에 — move()→show() 두 단계 사이 깜빡임 방지
        hwnd = ctypes.wintypes.HWND(int(self.winId()))
        SWP_SHOWWINDOW = 0x0040
        ctypes.windll.user32.SetWindowPos(
            hwnd, _HWND_TOPMOST, x, y, 0, 0,
            _SWP_NOSIZE | SWP_SHOWWINDOW,
        )
        self.raise_()
        self.activateWindow()

    def toggle(self):
        if self.isVisible():
            self.hide()
        else:
            self.show_near_cursor()

    # ── Internal ──

    def _rebuild(self):
        sc = self._scroll_content
        sc.setUpdatesEnabled(False)
        try:
            while self._items_layout.count():
                child = self._items_layout.takeAt(0)
                w = child.widget()
                if w:
                    w.hide()
                    w.setParent(None)
                    del w

            search = self._search_text.lower().strip()

            # ── 고정 섹션 ──
            filtered_pinned = self._filter_items(self._pinned_items, search)

            arrow = "\u25BC" if not self._pinned_collapsed else "\u25B6"
            pin_header_text = f"{arrow} 고정메모"

            pin_header_row = QHBoxLayout()
            pin_header_row.setContentsMargins(4, 0, 0, 0)
            pin_header_row.setSpacing(0)

            pin_header_btn = QPushButton(pin_header_text)
            pin_header_btn.setFixedHeight(24)
            fm = QFontMetrics(pin_header_btn.font())
            text_width = fm.horizontalAdvance(pin_header_text) + 16
            pin_header_btn.setFixedWidth(text_width)
            pin_header_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            pin_header_btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent;
                    color: {COLORS['peach']};
                    border: none;
                    font-size: 11px;
                    font-weight: 600;
                    text-align: left;
                    padding: 0 4px;
                }}
                QPushButton:hover {{
                    color: {COLORS['peach']};
                }}
            """)
            pin_header_btn.clicked.connect(self._toggle_pinned)
            pin_header_row.addWidget(pin_header_btn)
            pin_header_row.addStretch()

            self._status_label = QLabel()
            self._status_label.setFixedHeight(20)
            self._status_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._status_label.setStyleSheet(
                f"color: {COLORS['peach']}; font-size: 11px; font-weight: 600; "
                f"background: transparent; padding-right: 4px;"
            )
            if self._total > 0:
                self._status_label.setText(f"붙여넣기 {self._pointer}/{self._total}")
                self._status_label.show()
            else:
                self._status_label.hide()
            pin_header_row.addWidget(self._status_label)

            pin_header_wrapper = QWidget(sc)
            pin_header_wrapper.setLayout(pin_header_row)
            pin_header_wrapper.setStyleSheet("background: transparent;")
            self._items_layout.addWidget(pin_header_wrapper)

            drop_zone = PinDropZone(sc)
            drop_zone.item_dropped.connect(lambda item_id: self.pin_item_requested.emit(item_id))
            self._items_layout.addWidget(drop_zone)

            if not self._pinned_collapsed and filtered_pinned:
                for i, item in enumerate(filtered_pinned, 1):
                    is_current_pin = False
                    is_done_pin = False
                    if item.id in self._queue_item_ids:
                        q_idx = self._queue_item_ids.index(item.id)
                        if q_idx < self._pointer:
                            is_done_pin = True
                        elif q_idx == self._pointer:
                            is_current_pin = True
                    widget = PanelItemWidget(
                        item, i,
                        is_pinned=True,
                        is_current=is_current_pin,
                        is_done=is_done_pin,
                        is_selected=item.id in self._selected_ids,
                        parent=sc,
                    )
                    self._connect_item_signals(widget)
                    self._items_layout.addWidget(widget)

            # 구분선
            sep = QWidget(sc)
            sep.setFixedHeight(1)
            sep.setStyleSheet(f"background-color: {COLORS['surface1']};")
            self._items_layout.addWidget(sep)

            # ── 히스토리 섹션 헤더 ──
            filtered_history = self._filter_items(self._history_items, search)

            hist_header_row = QHBoxLayout()
            hist_header_row.setContentsMargins(4, 0, 0, 0)
            hist_header_row.setSpacing(0)

            hist_title = QLabel("\U0001f4cb 히스토리")
            hist_title.setFixedHeight(20)
            hist_title.setStyleSheet(
                f"color: {COLORS['peach']}; font-size: 11px; font-weight: 600; "
                f"background: transparent; padding-left: 2px;"
            )
            hist_header_row.addWidget(hist_title)
            hist_header_row.addStretch()

            hist_header_wrapper = QWidget(sc)
            hist_header_wrapper.setLayout(hist_header_row)
            hist_header_wrapper.setStyleSheet("background: transparent;")
            self._items_layout.addWidget(hist_header_wrapper)

            for i, item in enumerate(filtered_history, 1):
                # 큐 상태 계산: 큐에 있는 항목이면 포인터 기준으로 current/done 판단
                is_current = False
                is_done = False
                if item.id in self._queue_item_ids:
                    q_idx = self._queue_item_ids.index(item.id)
                    if q_idx < self._pointer:
                        is_done = True
                    elif q_idx == self._pointer:
                        is_current = True

                widget = PanelItemWidget(
                    item, i,
                    is_current=is_current,
                    is_done=is_done,
                    is_selected=item.id in self._selected_ids,
                    parent=sc,
                )
                self._connect_item_signals(widget)
                self._items_layout.addWidget(widget)

            self._items_layout.addStretch()
        finally:
            sc.setUpdatesEnabled(True)

    def _toggle_pinned(self):
        self._pinned_collapsed = not self._pinned_collapsed
        self._rebuild()

    def _pin_btn_style(self, active: bool) -> str:
        if active:
            return f"""
                QPushButton {{
                    background: {COLORS['surface1']};
                    color: {COLORS['peach']};
                    border: none;
                    font-size: 13px;
                    border-radius: 6px;
                    padding: 0;
                }}
                QPushButton:hover {{
                    background: {COLORS['surface2']};
                }}
            """
        else:
            return f"""
                QPushButton {{
                    background: transparent;
                    color: {COLORS['overlay0']};
                    border: none;
                    font-size: 13px;
                    border-radius: 6px;
                    padding: 0;
                }}
                QPushButton:hover {{
                    background: {COLORS['surface0']};
                }}
            """

    def _apply_always_on_top(self, value: bool):
        """SetWindowPos로 TOPMOST 플래그만 변경 — 창 재생성 없이 깜빡임 방지"""
        hwnd = ctypes.wintypes.HWND(int(self.winId()))
        insert_after = _HWND_TOPMOST if value else _HWND_NOTOPMOST
        ctypes.windll.user32.SetWindowPos(
            hwnd, insert_after, 0, 0, 0, 0,
            _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOACTIVATE,
        )

    def _toggle_always_on_top(self, checked: bool):
        self._always_on_top = checked
        if self._pin_btn:
            self._pin_btn.setStyleSheet(self._pin_btn_style(active=checked))
        self._apply_always_on_top(checked)
        self.always_on_top_changed.emit(checked)

    def set_always_on_top(self, value: bool):
        """외부(main.py)에서 DB 설정값 적용 시 호출. 창 가시성은 변경하지 않음."""
        self._always_on_top = value
        if self._pin_btn:
            self._pin_btn.setChecked(value)
            self._pin_btn.setStyleSheet(self._pin_btn_style(active=value))
        self._apply_always_on_top(value)

    def _filter_items(self, items: list[ClipboardItem], search: str) -> list[ClipboardItem]:
        if not search:
            return items
        return [
            item for item in items
            if search in (item.preview_text or "").lower()
            or search in (item.text_content or "").lower()
        ]

    def _connect_item_signals(self, widget: PanelItemWidget):
        widget.clicked.connect(self._on_item_clicked)
        widget.double_clicked.connect(self._on_item_double_clicked)
        widget.context_menu_requested.connect(self._on_item_context_menu)
        widget.delete_clicked.connect(self._on_item_delete)
        widget.external_drag_paste.connect(self._on_item_external_drag_paste)

    def _on_item_external_drag_paste(self, item_id: int, cursor_pos: QPoint):
        self.drag_to_app_requested.emit(item_id, cursor_pos)

    # ── 이벤트 핸들러 ──

    def _on_search_changed(self, text: str):
        self._search_text = text
        self._rebuild()

    def _toggle_search(self):
        if self._search_expanded:
            self._collapse_search()
        else:
            self._expand_search()

    def _expand_search(self):
        self._search_expanded = True
        self._header_spacer.hide()
        self._search_input.show()
        self._search_input.setFocus()

    def _collapse_search(self):
        self._search_expanded = False
        self._search_input.clear()
        self._search_input.hide()
        self._header_spacer.show()

    def eventFilter(self, obj, event):
        if (hasattr(self, '_scroll_content') and obj is self._scroll_content):
            if (event.type() == QEvent.Type.MouseButtonDblClick
                    and event.button() == Qt.MouseButton.LeftButton):
                self._reset_to_min_size()
                return True
        if obj is self._search_input:
            if event.type() == QEvent.Type.KeyPress:
                if event.key() == Qt.Key.Key_Escape:
                    self._collapse_search()
                    return True
            elif event.type() == QEvent.Type.FocusOut:
                pass  # 포커스 아웃으로는 검색창을 닫지 않음
        return super().eventFilter(obj, event)

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
            if item:
                self.queue_select_requested.emit(item_id)

        self._last_clicked_id = item_id
        self._kbd_focus_id = item_id
        self._update_selection_visuals()

    def _update_selection_visuals(self):
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
        """더블클릭 — 이미지: 미리보기 팝업 / 텍스트: 텍스트 미리보기 팝업 토글"""
        item = self._find_item(item_id)
        if not item:
            return
        if item.content_type == "image":
            self.preview_image_requested.emit(item_id, QCursor.pos())
        else:
            text = item.text_content or item.preview_text or ""
            TextPreviewPopup.instance().toggle_preview(text, QCursor.pos())

    def _on_item_delete(self, item_id: int):
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
            unpin_action = menu.addAction("고정메모 해제")
            unpin_action.triggered.connect(lambda: self.unpin_item_requested.emit(item_id))
        else:
            pin_action = menu.addAction("고정메모")
            pin_action.triggered.connect(lambda: self.pin_item_requested.emit(item_id))

        copy_action = menu.addAction("\U0001f4cb 복사")
        copy_action.triggered.connect(lambda: self._do_copy(item))

        if item.content_type != "image":
            edit_action = menu.addAction("\u270f\ufe0f 수정")
            edit_action.triggered.connect(lambda: self._on_edit_item(item))
        else:
            preview_action = menu.addAction("\U0001f50d 미리보기")
            preview_action.triggered.connect(
                lambda: self.preview_image_requested.emit(item_id, pos)
            )

        menu.addSeparator()

        delete_action = menu.addAction("\U0001f5d1 삭제")
        delete_action.triggered.connect(lambda: self.delete_item_requested.emit(item_id))

        menu.exec(pos)

    def _on_edit_item(self, item: ClipboardItem):
        dialog = EditItemDialog(item.text_content or "", self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            new_text = dialog.get_text()
            if new_text != (item.text_content or ""):
                self.edit_item_requested.emit(item.id, new_text)

    def _do_paste(self, item: ClipboardItem):
        self.paste_item_requested.emit(item)

    def _do_copy(self, item: ClipboardItem):
        """우클릭 복사 — copy_item_requested로 전체 포맷 복사 + self_triggered 처리"""
        self.copy_item_requested.emit(item)

    def _find_item(self, item_id: int) -> Optional[ClipboardItem]:
        for item in self._pinned_items + self._history_items:
            if item.id == item_id:
                return item
        return None

    def _combine_selected_items(self) -> Optional[ClipboardItem]:
        """선택된 항목들을 순서대로 결합하여 새 ClipboardItem 생성"""
        all_items = self._pinned_items + self._history_items
        selected = [item for item in all_items if item.id in self._selected_ids]
        if not selected:
            return None

        texts = []
        first_image = None
        for item in selected:
            if item.text_content:
                texts.append(item.text_content)
            elif item.content_type == "image":
                texts.append("[이미지]")
                if first_image is None:
                    first_image = item

        combined_text = "\n".join(texts)

        # 전체가 이미지면 첫 이미지 반환
        if all(item.content_type == "image" for item in selected) and first_image:
            return first_image

        return ClipboardItem(
            content_type="text",
            text_content=combined_text,
        )

    def _do_pin_reorder(self, source_id: int, target_id: int):
        """마우스 릴리즈 시 고정 항목 타겟 위치로 이동"""
        source_w = target_w = None
        source_idx = target_idx = -1
        for i in range(self._items_layout.count()):
            w = self._items_layout.itemAt(i).widget()
            if isinstance(w, PanelItemWidget) and w._is_pinned:
                if w.item_id == source_id:
                    source_w = w
                    source_idx = i
                elif w.item_id == target_id:
                    target_w = w
                    target_idx = i

        if not source_w or not target_w:
            return

        moving_down = source_idx < target_idx
        self._items_layout.removeWidget(source_w)
        for i in range(self._items_layout.count()):
            if self._items_layout.itemAt(i).widget() is target_w:
                insert_idx = i + 1 if moving_down else i
                self._items_layout.insertWidget(insert_idx, source_w)
                break
        source_w._apply_bg_style()

    def _emit_current_pin_order(self):
        """현재 레이아웃의 고정 항목 순서를 시그널로 전달"""
        ids = []
        for i in range(self._items_layout.count()):
            w = self._items_layout.itemAt(i).widget()
            if isinstance(w, PanelItemWidget) and w._is_pinned:
                ids.append(w.item_id)
        new_orders = [(item_id, i) for i, item_id in enumerate(ids)]
        self.pin_reorder_requested.emit(new_orders)

    def _update_pin_hover(self, cursor_pos: QPoint):
        """고정 항목 드래그 중 커서 아래 고정 항목 탐지 → 타겟 하이라이트"""
        widget_under = QApplication.widgetAt(cursor_pos)
        target_w = widget_under
        while target_w and not isinstance(target_w, PanelItemWidget):
            target_w = target_w.parent()

        if not target_w or not target_w._is_pinned:
            self._clear_pin_drag_highlight()
            self._pin_drag_target_id = None
            return
        if target_w.item_id == self._pin_drag_source_id:
            return
        if target_w.item_id == self._pin_drag_target_id:
            return  # 같은 타겟, 변화 없음

        self._clear_pin_drag_highlight()
        self._pin_drag_target_id = target_w.item_id
        target_w.setStyleSheet(
            f"background-color: {COLORS['surface1']}; border-radius: 6px;"
            f"border: 1px dashed {COLORS['peach']};"
        )

    def _clear_pin_drag_highlight(self):
        """고정 드래그 타겟 하이라이트 해제"""
        if self._pin_drag_target_id is None:
            return
        for i in range(self._items_layout.count()):
            w = self._items_layout.itemAt(i).widget()
            if isinstance(w, PanelItemWidget) and w.item_id == self._pin_drag_target_id:
                w._apply_bg_style()
                break

    def _update_hist_hover(self, cursor_pos: QPoint):
        """히스토리 드래그 중 커서 아래 항목 탐지 → 타겟 하이라이트"""
        widget_under = QApplication.widgetAt(cursor_pos)
        target_w = widget_under
        while target_w and not isinstance(target_w, PanelItemWidget):
            target_w = target_w.parent()

        if not target_w or target_w._is_pinned:
            self._clear_hist_drag_highlight()
            self._hist_drag_target_id = None
            return
        if target_w.item_id == self._hist_drag_source_id:
            return
        if target_w.item_id == self._hist_drag_target_id:
            return  # 같은 타겟, 변화 없음

        self._clear_hist_drag_highlight()
        self._hist_drag_target_id = target_w.item_id
        target_w.setStyleSheet(
            f"background-color: {COLORS['surface1']}; border-radius: 6px;"
            f"border: 1px dashed {COLORS['teal']};"
        )

    def _clear_hist_drag_highlight(self):
        """히스토리 드래그 타겟 하이라이트 해제"""
        if self._hist_drag_target_id is None:
            return
        for i in range(self._items_layout.count()):
            w = self._items_layout.itemAt(i).widget()
            if isinstance(w, PanelItemWidget) and w.item_id == self._hist_drag_target_id:
                w._apply_bg_style()
                break

    def _do_hist_reorder(self, source_id: int, target_id):
        """마우스 릴리즈 시 히스토리 항목 타겟 위치로 이동"""
        if target_id is None or source_id == target_id:
            return
        source_w = target_w = None
        source_idx = target_idx = -1
        for i in range(self._items_layout.count()):
            w = self._items_layout.itemAt(i).widget()
            if isinstance(w, PanelItemWidget) and not w._is_pinned:
                if w.item_id == source_id:
                    source_w = w
                    source_idx = i
                elif w.item_id == target_id:
                    target_w = w
                    target_idx = i

        if not source_w or not target_w:
            return

        moving_down = source_idx < target_idx
        self._items_layout.removeWidget(source_w)
        for i in range(self._items_layout.count()):
            if self._items_layout.itemAt(i).widget() is target_w:
                insert_idx = i + 1 if moving_down else i
                self._items_layout.insertWidget(insert_idx, source_w)
                break
        source_w._apply_bg_style()

    def _emit_current_hist_order(self):
        """현재 레이아웃의 히스토리 항목 순서를 시그널로 전달 + 인메모리 갱신"""
        ids = []
        for i in range(self._items_layout.count()):
            w = self._items_layout.itemAt(i).widget()
            if isinstance(w, PanelItemWidget) and not w._is_pinned:
                ids.append(w.item_id)
        new_orders = [(item_id, i) for i, item_id in enumerate(ids)]
        self.history_reorder_requested.emit(new_orders)
        # _rebuild() 시 새 순서가 유지되도록 인메모리 목록도 갱신
        id_to_item = {item.id: item for item in self._history_items}
        self._history_items = [id_to_item[item_id] for item_id in ids if item_id in id_to_item]

    # ── Qt 방식 리사이즈 + 드래그 이동 ──

    def _get_resize_edges(self, pos) -> set:
        """마우스 위치가 어느 가장자리에 있는지 반환"""
        edges = set()
        m = RESIZE_MARGIN
        r = self.rect()
        if pos.y() <= m:
            edges.add("top")
        if pos.y() >= r.height() - m:
            edges.add("bottom")
        if pos.x() <= m:
            edges.add("left")
        if pos.x() >= r.width() - m:
            edges.add("right")
        return edges

    def _cursor_for_edges(self, edges: set):
        if ("top" in edges and "left" in edges) or ("bottom" in edges and "right" in edges):
            return Qt.CursorShape.SizeFDiagCursor
        if ("top" in edges and "right" in edges) or ("bottom" in edges and "left" in edges):
            return Qt.CursorShape.SizeBDiagCursor
        if "left" in edges or "right" in edges:
            return Qt.CursorShape.SizeHorCursor
        if "top" in edges or "bottom" in edges:
            return Qt.CursorShape.SizeVerCursor
        return Qt.CursorShape.ArrowCursor

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            edges = self._get_resize_edges(event.pos())
            if edges:
                self._resize_edges = edges
                self._resize_start_pos = event.globalPosition().toPoint()
                self._resize_start_geometry = self.geometry()
                self._drag_pos = None
            else:
                self._resize_edges = set()
                self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._resize_edges and self._resize_start_pos and event.buttons() & Qt.MouseButton.LeftButton:
            delta = event.globalPosition().toPoint() - self._resize_start_pos
            g = QRect(self._resize_start_geometry)
            if "right" in self._resize_edges:
                g.setWidth(max(PANEL_MIN_WIDTH, g.width() + delta.x()))
            if "left" in self._resize_edges:
                new_left = min(g.left() + delta.x(), g.right() - PANEL_MIN_WIDTH)
                g.setLeft(new_left)
            if "bottom" in self._resize_edges:
                g.setHeight(max(PANEL_MIN_HEIGHT, g.height() + delta.y()))
            if "top" in self._resize_edges:
                new_top = min(g.top() + delta.y(), g.bottom() - PANEL_MIN_HEIGHT)
                g.setTop(new_top)
            self.setGeometry(g)
        elif self._drag_pos and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        self._resize_edges = set()
        self._resize_start_pos = None
        self._resize_start_geometry = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._reset_to_min_size()
        super().mouseDoubleClickEvent(event)

    def _reset_to_min_size(self):
        """빈공간 더블클릭 시 최소 크기로 복원"""
        self.resize(PANEL_MIN_WIDTH, PANEL_MIN_HEIGHT)

    # ── F10-4: 위치/크기 저장 복원 ──

    def get_geometry_dict(self) -> dict:
        """현재 위치/크기를 dict로 반환"""
        g = self.geometry()
        return {"x": g.x(), "y": g.y(), "w": g.width(), "h": g.height()}

    def restore_geometry_dict(self, d: dict):
        """dict에서 위치/크기 복원 — 화면 밖이면 우하단 기본 위치로 clamp"""
        from PyQt6.QtWidgets import QApplication
        try:
            x, y, w, h = int(d["x"]), int(d["y"]), int(d["w"]), int(d["h"])
            screen = QApplication.screenAt(
                self.geometry().center()
            ) or QApplication.primaryScreen()
            avail = screen.availableGeometry()
            # 패널이 완전히 화면 밖이면 우하단으로 이동
            if x >= avail.right() or y >= avail.bottom() or x + w <= avail.left() or y + h <= avail.top():
                x = avail.right() - w - 20
                y = avail.bottom() - h - 20
            self.setGeometry(x, y, w, h)
        except (KeyError, ValueError):
            pass

    # ── F4-9: 외부 클릭 시 자동 닫기 ──

    def changeEvent(self, event):
        """창 비활성화 시 자동 닫기 (사용자가 직접 연 경우만, 항상 위에 활성 시 닫지 않음)"""
        if (event.type() == QEvent.Type.ActivationChange
                and self._auto_close
                and not self._always_on_top
                and self._user_activated
                and not self._paste_in_progress
                and not self._ext_drag_active
                and not self.isActiveWindow()):
            self._user_activated = False
            self.hide()
        super().changeEvent(event)

    def showEvent(self, event):
        self._cursor_timer.start()
        super().showEvent(event)
        QTimer.singleShot(0, self.setFocus)

    def hideEvent(self, event):
        self._cursor_timer.stop()
        self.unsetCursor()
        if self._search_expanded:
            self._collapse_search()
        ImagePreviewPopup.close_all()
        super().hideEvent(event)
        self.panel_hidden.emit()

    def _sync_resize_cursor(self):
        """마우스 위치에 따라 리사이즈 커서를 동기화 (자식 위젯 위에서도 정확히 동작)"""
        if QApplication.mouseButtons() & Qt.MouseButton.LeftButton:
            return
        local_pos = self.mapFromGlobal(QCursor.pos())
        if self.rect().contains(local_pos):
            edges = self._get_resize_edges(local_pos)
            if edges:
                self.setCursor(self._cursor_for_edges(edges))
            else:
                self.unsetCursor()
        else:
            self.unsetCursor()

    # ── F6: 다중 선택 Ctrl+C 결합 복사 ──

    def keyPressEvent(self, event):
        key = event.key()
        mods = event.modifiers()

        # ── 방향키: 항목 이동 ──
        if key == Qt.Key.Key_Up:
            self._kbd_move(-1)
            event.accept()
            return
        if key == Qt.Key.Key_Down:
            self._kbd_move(1)
            event.accept()
            return

        # ── Enter: 더블클릭 동작 (미리보기 / 붙여넣기) ──
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._kbd_activate()
            event.accept()
            return

        # ── Delete: 항목 삭제 ──
        if key == Qt.Key.Key_Delete:
            self._kbd_delete()
            event.accept()
            return

        # ── Escape: 패널 닫기 ──
        if key == Qt.Key.Key_Escape:
            self.hide()
            event.accept()
            return

        # ── Ctrl+C: 다중 선택 결합 복사 ──
        if (key == Qt.Key.Key_C
                and mods & Qt.KeyboardModifier.ControlModifier
                and len(self._selected_ids) > 1):
            combined = self._combine_selected_items()
            if combined:
                self.combine_copy_requested.emit(combined)
            event.accept()
            return

        super().keyPressEvent(event)

    def _kbd_get_ordered_items(self) -> list:
        """레이아웃 순서대로 표시된 PanelItemWidget 목록 반환 [(item_id, widget), ...]"""
        result = []
        for i in range(self._items_layout.count()):
            w = self._items_layout.itemAt(i).widget()
            if isinstance(w, PanelItemWidget) and w.isVisible():
                result.append((w.item_id, w))
        return result

    def _kbd_move(self, delta: int):
        """키보드 포커스를 delta 방향으로 이동 (단일 선택 + 큐 선택)"""
        items = self._kbd_get_ordered_items()
        if not items:
            return
        ids = [item_id for item_id, _ in items]

        if self._kbd_focus_id is None or self._kbd_focus_id not in ids:
            new_id = ids[0] if delta > 0 else ids[-1]
        else:
            cur_idx = ids.index(self._kbd_focus_id)
            new_idx = max(0, min(len(ids) - 1, cur_idx + delta))
            new_id = ids[new_idx]

        self._kbd_focus_id = new_id
        self._selected_ids = {new_id}
        self._last_clicked_id = new_id
        self._update_selection_visuals()

        # 선택 항목이 스크롤 뷰에 보이도록
        for item_id, w in items:
            if item_id == new_id:
                self._scroll.ensureWidgetVisible(w)
                break

        self.queue_select_requested.emit(new_id)

    def _kbd_activate(self):
        """Enter: 포커스 항목에 더블클릭 동작 실행"""
        if self._kbd_focus_id is not None:
            self._on_item_double_clicked(self._kbd_focus_id)

    def _kbd_delete(self):
        """Delete: 포커스 항목 삭제 후 포커스를 다음 항목으로 이동"""
        if self._kbd_focus_id is None:
            return
        del_id = self._kbd_focus_id
        items = self._kbd_get_ordered_items()
        ids = [item_id for item_id, _ in items]
        if del_id in ids:
            idx = ids.index(del_id)
            if idx + 1 < len(ids):
                self._kbd_focus_id = ids[idx + 1]
            elif idx - 1 >= 0:
                self._kbd_focus_id = ids[idx - 1]
            else:
                self._kbd_focus_id = None
        self._on_item_delete(del_id)

    def contextMenuEvent(self, event):
        """패널 빈 곳 우클릭 → 패널 닫기 / 설정 / 종료"""
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

        close_action = menu.addAction("패널 닫기")
        close_action.triggered.connect(self.hide)

        settings_action = menu.addAction("설정")
        settings_action.triggered.connect(self.open_settings_requested.emit)

        menu.addSeparator()

        clear_action = menu.addAction("히스토리 초기화")
        clear_action.triggered.connect(self.clear_history_requested.emit)

        menu.addSeparator()

        quit_action = menu.addAction("종료")
        quit_action.triggered.connect(self.quit_requested.emit)

        menu.exec(event.globalPos())
