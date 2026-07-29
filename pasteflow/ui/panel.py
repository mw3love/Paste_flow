"""전체 클립보드 패널

고정 섹션 + 히스토리 섹션, 검색, 우클릭 컨텍스트 메뉴, 다중 선택.
Alt+V / 트레이로 토글.
"""
from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QMenu, QApplication, QGraphicsOpacityEffect,
    QSizePolicy, QDialog, QPlainTextEdit,
)
import ctypes
import ctypes.wintypes

from PyQt6.QtCore import Qt, pyqtSignal, QPoint, QTimer, QEvent, QRect, QPropertyAnimation, QEasingCurve
from PyQt6.QtGui import QPixmap, QCursor, QFontMetrics, QFont

_HWND_TOPMOST = ctypes.wintypes.HWND(-1)
_SWP_NOMOVE = 0x0002
_SWP_NOSIZE = 0x0001
_SWP_NOACTIVATE = 0x0010

from pasteflow.models import ClipboardItem
from pasteflow.ui.ai_query import AiQueryDialog
from pasteflow.ui.image_preview import ImagePreviewPopup
from pasteflow.ui.text_preview import TextPreviewPopup
from pasteflow.ui.theme import COLORS, PEACH_HOVER

PANEL_WIDTH = 320
PANEL_HEIGHT = 420
PANEL_MIN_WIDTH = 260
PANEL_MIN_HEIGHT = 390
RESIZE_MARGIN = 6

# 드래그 MIME 타입
MIME_ITEM_TO_PIN = "application/x-pasteflow-item-id"


class PanelItemWidget(QWidget):
    """패널 내 개별 항목 위젯"""

    clicked = pyqtSignal(int, object)
    context_menu_requested = pyqtSignal(int, object)
    external_drag_paste = pyqtSignal(int, QPoint, bool)  # (item_id, cursor_pos, alt_held)

    def __init__(
        self,
        item: ClipboardItem,
        is_current: bool = False,
        is_done: bool = False,
        is_pinned: bool = False,
        is_selected: bool = False,
        in_queue: bool = False,
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

        self._setup_ui(item, is_current, is_done, is_pinned, in_queue)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)


    def _setup_ui(
        self, item: ClipboardItem,
        is_current: bool, is_done: bool, is_pinned: bool,
        in_queue: bool = False,
    ):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(0)

        if is_done:
            accent_color = COLORS['surface2']
        elif in_queue:
            accent_color = COLORS['peach']
        else:
            accent_color = COLORS['surface2']
        self._accent_color = accent_color

        # 미리보기
        text_color = COLORS['subtext0'] if is_done else "#ffffff"
        if item.content_type == "image" and item.thumbnail:
            thumb_label = QLabel()
            thumb_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            pixmap = QPixmap()
            pixmap.loadFromData(item.thumbnail)
            scaled = pixmap.scaled(96, 72, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            thumb_label.setPixmap(scaled)
            thumb_label.setFixedSize(96, 72)
            layout.addWidget(thumb_label)
            layout.addStretch(1)
            self.setFixedHeight(86)
        else:
            preview = item.text_content or item.preview_text or ""
            all_lines = preview.strip().split("\n")
            lines = all_lines[:5]
            display_text = "\n".join(line[:80] for line in lines)

            text_label = QLabel(display_text)
            text_label.setWordWrap(True)
            text_label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
            text_label.setMinimumWidth(0)
            text_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            text_label.setStyleSheet(f"color: {text_color}; font-size: 12px;")
            layout.addWidget(text_label, 1)
            self._text_label = text_label

            # 실제 레이블 너비 ≈ PANEL_WIDTH - 패널마진(20) - 아이템내부(lmargin8+rmargin8) = PANEL_WIDTH - 36
            _f = QFont(); _f.setPixelSize(12)
            _fm = QFontMetrics(_f)
            _rect = _fm.boundingRect(
                QRect(0, 0, max(1, PANEL_WIDTH - 36), 10000),
                Qt.TextFlag.TextWordWrap | int(Qt.AlignmentFlag.AlignLeft),
                display_text,
            )
            _vl = max(1, min(5, (_rect.height() + _fm.lineSpacing() - 1) // _fm.lineSpacing()))
            _label_h = _vl * _fm.lineSpacing() + 8
            text_label.setFixedHeight(_label_h)
            self.setFixedHeight(_label_h + 12)  # 12 = 상하 패딩(6+6)

        # 바는 레이아웃이 행 높이에 맞춰 자동 조정 (타이머 불필요)

        self._apply_bg_style()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._adjust_text_height()

    def _adjust_text_height(self):
        if self._text_label is None:
            return
        avail_w = self.width() - 16  # lmargin(8) + rmargin(8)
        if avail_w <= 0:
            return
        fm = self._text_label.fontMetrics()
        rect = fm.boundingRect(
            QRect(0, 0, avail_w, 10000),
            Qt.TextFlag.TextWordWrap | int(Qt.AlignmentFlag.AlignLeft),
            self._text_label.text(),
        )
        actual_lines = max(1, (rect.height() + fm.lineSpacing() - 1) // fm.lineSpacing())
        visual_lines = min(5, actual_lines)
        label_h = visual_lines * fm.lineSpacing() + 8
        new_h = label_h + 12  # 12 = 상하 패딩(6+6)
        align = (Qt.AlignmentFlag.AlignTop if actual_lines > 5
                 else Qt.AlignmentFlag.AlignVCenter) | Qt.AlignmentFlag.AlignLeft
        self._text_label.setAlignment(align)
        if self._text_label.height() != label_h:
            self._text_label.setFixedHeight(label_h)
        if self.height() != new_h:
            self.setFixedHeight(new_h)

    def _apply_bg_style(self):
        border_color = self._accent_color
        if self._is_selected:
            # 선택 = 코랄 강조(테마 규칙: 코랄=선택·주목). 회색 배경만 살짝 바뀌던 낮은
            # 시인성을 개선 — 따뜻한 코랄 틴트 배경 + 코랄 테두리로 확실히 구분.
            self.setStyleSheet(f"background-color: #4d3320; border-radius: 6px; border: 1px solid {COLORS['peach']};")
        elif self._is_hovered:
            self.setStyleSheet(f"background-color: {COLORS['surface1']}; border-radius: 6px; border: 1px solid {border_color};")
        else:
            self.setStyleSheet(f"background-color: {COLORS['surface0']}; border-radius: 6px; border: 1px solid {border_color};")

    def set_queue_state(self, is_current: bool, is_done: bool, in_queue: bool = True):
        """위젯 재생성 없이 큐 상태(색상)만 업데이트.
        in_queue=False → 큐에서 제거됨, 기본 색상으로 복원.
        """
        if is_done:
            self._accent_color = COLORS['surface2']
        elif in_queue:
            self._accent_color = COLORS['peach']
        else:
            self._accent_color = COLORS['surface2']
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
        # hover된 항목을 키보드 포커스 타겟으로 — Space로 즉시 이 항목 미리보기가 열리도록
        panel = self.parent()
        while panel and not isinstance(panel, ClipboardPanel):
            panel = panel.parent()
        if panel is not None:
            panel._kbd_focus_id = self.item_id
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
                TextPreviewPopup.close_all()

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
            f"border: 1px solid {COLORS['peach']};"
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
                    # event.modifiers()가 이 release 이벤트의 권위 있는 수정키 상태.
                    # QApplication.keyboardModifiers()는 마지막 처리 이벤트 기준이라 lag 가능.
                    alt_held = bool(
                        event.modifiers() & Qt.KeyboardModifier.AltModifier
                    )
                    self.external_drag_paste.emit(self.item_id, cursor_pos, alt_held)
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

    def contextMenuEvent(self, event):
        if self.childAt(event.pos()) is None:
            event.ignore()
            return
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
                border: 1px solid {COLORS['peach']};
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
                background-color: {COLORS['peach']};
                color: {COLORS['base']};
            }}
            QPushButton[text="저장"]:hover {{
                background-color: {PEACH_HOVER};
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
    preview_image_requested = pyqtSignal(int)  # item_id — 위치는 main이 panel.geometry()로 계산
    preview_text_requested = pyqtSignal(int)   # item_id — 동상
    ocr_item_requested = pyqtSignal(int)       # item_id — 이미지 항목에 OCR 적용
    copy_image_as_path_requested = pyqtSignal(int)  # item_id — 이미지를 임시 PNG로 저장 후 경로를 클립보드에 텍스트로 복사
    open_settings_requested = pyqtSignal()
    quit_requested = pyqtSignal()
    clear_history_requested = pyqtSignal()
    drag_to_app_requested = pyqtSignal(int, QPoint, bool)  # (item_id, cursor_pos, alt_held)
    queue_select_requested = pyqtSignal(int)    # item_id — 해당 항목부터 최신까지 큐 설정
    queue_deselect_requested = pyqtSignal(int)  # item_id — 큐 해제
    panel_hidden = pyqtSignal()  # 패널 숨겨질 때 emit
    auto_close_changed = pyqtSignal(bool)  # 자동닫기 상태 변경 → main이 DB 저장

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pinned_items: list[ClipboardItem] = []
        self._history_items: list[ClipboardItem] = []
        self._pointer: int = 0
        self._total: int = 0
        self._selected_ids: set[int] = set()
        self._last_clicked_id: Optional[int] = None
        self._queue_item_ids: list[int] = []
        self._status_label: Optional[QLabel] = None
        self._drag_pos = None
        self._pin_drag_source_id = None   # 드래그 중인 고정 항목 ID (fake drag)
        self._pin_drag_target_id = None   # 드래그 중 하이라이트된 고정 타겟 ID
        self._hist_drag_source_id = None  # 드래그 중인 히스토리 항목 ID
        self._hist_drag_target_id = None  # 드래그 중 하이라이트된 타겟 항목 ID
        self._resize_edges: set = set()
        self._resize_start_pos = None
        self._resize_start_geometry = None
        self._pinned_collapsed = True
        self._history_collapsed = False
        self._auto_close = True  # 핀 비활성 시 외부 클릭으로 자동 닫기
        self._user_activated = False  # 사용자가 직접 열었는지 (vs 복사 팝업)
        self._paste_in_progress = False  # 패널 붙여넣기 중 자동닫기 방지
        self._ext_drag_active = False    # 외부 드래그 중 자동닫기 방지
        self._kbd_focus_id: Optional[int] = None  # 키보드 포커스된 항목 ID
        self._pin_btn: Optional[QPushButton] = None
        self._fade_anim: Optional[QPropertyAnimation] = None

        self._setup_window()
        self._setup_ui()
        self._setup_opacity()

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

        # 화면 밖에 대기 — show() 전에 위치가 확정되지 않은 채 렌더링되는 깜빡임 방지
        self.move(-10000, -10000)

    def _setup_ui(self):
        self._container = QWidget(self)
        self._container.setObjectName("panelContainer")
        self._container.setStyleSheet(f"""
            #panelContainer {{
                background-color: {COLORS['base']};
                border: 1.5px solid {COLORS['overlay0']};
                border-radius: 10px;
            }}
        """)

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.addWidget(self._container)

        main_layout = QVBoxLayout(self._container)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(8)

        # ── 헤더: 상태 레이블 + 📌 + ✕ ──
        header_row = QHBoxLayout()
        header_row.setSpacing(4)

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        header_row.addWidget(spacer)

        # 붙여넣기 상태 레이블
        self._status_label = QLabel()
        self._status_label.setFixedHeight(24)
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._status_label.setStyleSheet(
            f"color: {COLORS['peach']}; font-size: 11px; font-weight: 600; "
            f"background: transparent; padding-right: 2px;"
        )
        self._status_label.hide()
        header_row.addWidget(self._status_label)

        # 항상 위에 토글 버튼 (Segoe MDL2 Assets: =PinnedFill, =Pin)
        self._pin_btn = QPushButton()
        self._pin_btn.setFixedSize(24, 24)
        self._pin_btn.setCheckable(True)
        self._pin_btn.setChecked(False)
        self._pin_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._pin_btn.setToolTip("항상 위에 고정")
        self._apply_pin_btn_state(active=False)  # 기본: 핀 비활성 = 자동닫기 ON
        self._pin_btn.clicked.connect(self._toggle_auto_close)
        header_row.addWidget(self._pin_btn)

        # 숨기기(최소화) 버튼
        close_btn = QPushButton("−")
        close_btn.setFixedSize(24, 24)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {COLORS['subtext0']};
                border: none;
                font-size: 13px;
                border-radius: 6px;
                padding: 0;
            }}
            QPushButton:hover {{
                background: {COLORS['red']};
                color: {COLORS['crust']};
            }}
        """)
        close_btn.clicked.connect(self.hide)
        header_row.addWidget(close_btn)

        main_layout.addLayout(header_row)

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

    def clear_selection(self):
        """클릭 선택 상태 초기화 — 큐 소진/클리어 시 선택 코랄 테두리가 큐 잔상처럼 남는 것 방지."""
        self._selected_ids.clear()
        self._update_selection_visuals()

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
                widget.set_queue_state(is_current=True, is_done=False, in_queue=True)
            elif q_idx is not None:
                widget.set_queue_state(is_current=False, is_done=False, in_queue=True)
            else:
                widget.set_queue_state(is_current=False, is_done=False, in_queue=False)

    def _setup_opacity(self):
        self._opacity_effect = QGraphicsOpacityEffect(self)
        self._opacity_effect.setOpacity(1.0)
        self.setGraphicsEffect(self._opacity_effect)

    def _fade_in(self):
        if self._fade_anim and self._fade_anim.state() == QPropertyAnimation.State.Running:
            self._fade_anim.stop()
        anim = QPropertyAnimation(self._opacity_effect, b"opacity", self)
        anim.setDuration(180)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade_anim = anim
        anim.start()

    def _fade_out_and_hide(self):
        if self._fade_anim and self._fade_anim.state() == QPropertyAnimation.State.Running:
            self._fade_anim.stop()
        anim = QPropertyAnimation(self._opacity_effect, b"opacity", self)
        anim.setDuration(140)
        anim.setStartValue(self._opacity_effect.opacity())
        anim.setEndValue(0.0)
        anim.setEasingCurve(QEasingCurve.Type.InCubic)
        anim.finished.connect(self._do_hide)
        self._fade_anim = anim
        anim.start()

    def _do_hide(self):
        self._opacity_effect.setOpacity(1.0)
        super().hide()

    def hide(self):
        if not self.isVisible():
            return
        self._fade_out_and_hide()

    def hide_immediate(self):
        """애니메이션 없이 즉시 숨긴다 — 붙여넣기 직전처럼 타이밍이 중요한 경우에 사용."""
        if self._fade_anim and self._fade_anim.state() == QPropertyAnimation.State.Running:
            self._fade_anim.stop()
        self._do_hide()

    def show_near_cursor(self):
        """마우스 커서 근처(우하단 +16px)에 패널 표시. 화면 경계 초과 시 반전."""
        self.resize(PANEL_MIN_WIDTH, PANEL_MIN_HEIGHT)
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

        self._opacity_effect.setOpacity(0.0)
        self.move(x, y)
        self.show()
        self.raise_()
        self.activateWindow()
        self._fade_in()

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

            # ── 고정 섹션 ──
            filtered_pinned = self._pinned_items

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
                    in_queue_pin = item.id in self._queue_item_ids
                    if in_queue_pin:
                        q_idx = self._queue_item_ids.index(item.id)
                        if q_idx < self._pointer:
                            is_done_pin = True
                        elif q_idx == self._pointer:
                            is_current_pin = True
                    widget = PanelItemWidget(
                        item,
                        is_pinned=True,
                        is_current=is_current_pin,
                        is_done=is_done_pin,
                        is_selected=item.id in self._selected_ids,
                        in_queue=in_queue_pin,
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
            filtered_history = self._history_items

            hist_arrow = "▼" if not self._history_collapsed else "▶"
            hist_header_text = f"{hist_arrow} 히스토리"

            hist_header_row = QHBoxLayout()
            hist_header_row.setContentsMargins(4, 0, 0, 0)
            hist_header_row.setSpacing(0)

            hist_header_btn = QPushButton(hist_header_text)
            hist_header_btn.setFixedHeight(24)
            fm = QFontMetrics(hist_header_btn.font())
            hist_header_btn.setFixedWidth(fm.horizontalAdvance(hist_header_text) + 16)
            hist_header_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            hist_header_btn.setStyleSheet(f"""
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
            hist_header_btn.clicked.connect(self._toggle_history)
            hist_header_row.addWidget(hist_header_btn)
            hist_header_row.addStretch()

            hist_header_wrapper = QWidget(sc)
            hist_header_wrapper.setLayout(hist_header_row)
            hist_header_wrapper.setStyleSheet("background: transparent;")
            self._items_layout.addWidget(hist_header_wrapper)

            if not self._history_collapsed:
                for i, item in enumerate(filtered_history, 1):
                    # 큐 상태 계산: 큐에 있는 항목이면 포인터 기준으로 current/done 판단
                    is_current = False
                    is_done = False
                    in_queue = item.id in self._queue_item_ids
                    if in_queue:
                        q_idx = self._queue_item_ids.index(item.id)
                        if q_idx < self._pointer:
                            is_done = True
                        elif q_idx == self._pointer:
                            is_current = True

                    widget = PanelItemWidget(
                        item,
                        is_current=is_current,
                        is_done=is_done,
                        is_selected=item.id in self._selected_ids,
                        in_queue=in_queue,
                        parent=sc,
                    )
                    self._connect_item_signals(widget)
                    self._items_layout.addWidget(widget)

            self._items_layout.addStretch()

            # 헤더 상태 레이블 동기화
            if self._status_label:
                if self._total > 0:
                    self._status_label.setText(f"붙여넣기 {self._pointer}/{self._total}")
                    self._status_label.show()
                else:
                    self._status_label.hide()
        finally:
            sc.setUpdatesEnabled(True)

    def _toggle_pinned(self):
        self._pinned_collapsed = not self._pinned_collapsed
        self._rebuild()
        self._scroll.verticalScrollBar().setValue(0)

    def _toggle_history(self):
        self._history_collapsed = not self._history_collapsed
        self._rebuild()

    def _apply_pin_btn_state(self, active: bool):
        """핀 버튼 글리프·색상 동시 업데이트 — Segoe MDL2 Assets"""
        if not self._pin_btn:
            return
        #  = PinnedFill (활성),   = Pin (비활성)
        self._pin_btn.setText("" if active else "")
        color = COLORS["peach"] if active else COLORS["subtext0"]
        self._pin_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {color};
                border: none;
                font-size: 13px;
                font-family: 'Segoe MDL2 Assets';
                border-radius: 6px;
                padding: 0;
            }}
            QPushButton:hover {{
                background: {COLORS["surface1"]};
            }}
        """)


    def _set_always_on_top(self):
        """항상 최상단 고정 — 창 생성 후 1회 호출"""
        hwnd = ctypes.wintypes.HWND(int(self.winId()))
        ctypes.windll.user32.SetWindowPos(
            hwnd, _HWND_TOPMOST, 0, 0, 0, 0,
            _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOACTIVATE,
        )

    def _toggle_auto_close(self, checked: bool):
        # 핀 활성(checked=True) = 자동닫기 OFF
        self._auto_close = not checked
        self._apply_pin_btn_state(active=checked)
        self.auto_close_changed.emit(self._auto_close)

    def set_auto_close(self, value: bool):
        """외부(main.py)에서 DB 설정값 적용 시 호출"""
        self._auto_close = value
        if self._pin_btn:
            pin_active = not value
            self._pin_btn.setChecked(pin_active)
            self._apply_pin_btn_state(active=pin_active)

    def _connect_item_signals(self, widget: PanelItemWidget):
        widget.clicked.connect(self._on_item_clicked)
        widget.context_menu_requested.connect(self._on_item_context_menu)
        widget.external_drag_paste.connect(self._on_item_external_drag_paste)

    def _on_item_external_drag_paste(self, item_id: int, cursor_pos: QPoint, alt_held: bool):
        self.drag_to_app_requested.emit(item_id, cursor_pos, alt_held)

    # ── 이벤트 핸들러 ──

    def eventFilter(self, obj, event):
        if (hasattr(self, '_scroll_content') and obj is self._scroll_content):
            if (event.type() == QEvent.Type.MouseButtonDblClick
                    and event.button() == Qt.MouseButton.LeftButton):
                self._reset_to_min_size()
                return True
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
        """)

        if item.content_type == "image":
            preview_action = menu.addAction("미리보기\tSpace")
            preview_action.triggered.connect(
                lambda: self.preview_image_requested.emit(item_id)
            )
        else:
            preview_action = menu.addAction("미리보기\tSpace")
            preview_action.triggered.connect(
                lambda: self.preview_text_requested.emit(item_id)
            )

        if item_id in self._queue_item_ids:
            queue_action = menu.addAction("큐 해제\tC")
            queue_action.triggered.connect(lambda: self.queue_deselect_requested.emit(item_id))
        else:
            queue_action = menu.addAction("큐에 추가\tC")
            queue_action.triggered.connect(lambda: self.queue_select_requested.emit(item_id))

        menu.addSeparator()

        copy_action = menu.addAction("복사\tCtrl+C")
        copy_action.triggered.connect(lambda: self._do_copy(item))

        if item.content_type == "image":
            ocr_action = menu.addAction("텍스트 추출(OCR)")
            ocr_action.triggered.connect(lambda: self.ocr_item_requested.emit(item_id))
            path_action = menu.addAction("파일로 저장 후 경로 복사")
            path_action.triggered.connect(lambda: self.copy_image_as_path_requested.emit(item_id))
        else:
            edit_action = menu.addAction("수정")
            edit_action.triggered.connect(lambda: self._on_edit_item(item))

        if item.is_pinned:
            unpin_action = menu.addAction("고정 해제\tP")
            unpin_action.triggered.connect(lambda: self.unpin_item_requested.emit(item_id))
        else:
            pin_action = menu.addAction("고정추가\tP")
            pin_action.triggered.connect(lambda: self.pin_item_requested.emit(item_id))

        menu.addSeparator()

        delete_action = menu.addAction("삭제\tDel")
        delete_action.triggered.connect(lambda: self.delete_item_requested.emit(item_id))

        menu.exec(pos)

    def _on_edit_item(self, item: ClipboardItem):
        dialog = EditItemDialog(item.text_content or "", self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            new_text = dialog.get_text()
            if new_text != (item.text_content or ""):
                self.edit_item_requested.emit(item.id, new_text)

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
            f"border: 1px dashed {COLORS['peach']};"
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
        """창 비활성화 시 자동 닫기 (핀 비활성 상태이고 사용자가 직접 연 경우만).

        새 활성 윈도우가 미리보기 팝업·AI 질문창이면 패널을 닫지 않는다 — 미리보기
        드래그·스크롤·클릭 중이거나 AI에게 질문을 작성하는 중에 패널이 사라지면 사용성이
        망가진다. `AiQueryDialog`는 v1.49.4부터 비모달이라(패널을 잠그지 않음) 이 예외가
        없으면 질문창에 포커스가 넘어가는 순간 패널이 곧장 자동으로 숨어 버린다.
        """
        if (event.type() == QEvent.Type.ActivationChange
                and self._auto_close
                and self._user_activated
                and not self._paste_in_progress
                and not self._ext_drag_active
                and not self.isActiveWindow()):
            active = QApplication.activeWindow()
            if not isinstance(active, (ImagePreviewPopup, TextPreviewPopup, AiQueryDialog)):
                self._user_activated = False
                self.hide()
        super().changeEvent(event)

    def showEvent(self, event):
        self._cursor_timer.start()
        super().showEvent(event)
        self._set_always_on_top()
        QTimer.singleShot(0, self.setFocus)

    def hideEvent(self, event):
        # 패널이 숨겨져도 미리보기 팝업은 독립 창이라 그대로 둔다 — 패널만 최소화하고
        # 이미지를 계속 보고 싶은 경우(자체 ✕/ESC로 닫는 것과 별개) 대비.
        self._cursor_timer.stop()
        self.unsetCursor()
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

        # ── Enter: 포커스 항목 붙여넣기 ──
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._kbd_activate()
            event.accept()
            return

        # ── Delete: 항목 삭제 ──
        if key == Qt.Key.Key_Delete:
            self._kbd_delete()
            event.accept()
            return

        # ── Escape / Ctrl+W: 패널 닫기 ──
        if key == Qt.Key.Key_Escape or (
                key == Qt.Key.Key_W
                and mods & Qt.KeyboardModifier.ControlModifier):
            self.hide()
            event.accept()
            return

        # ── F10: 설정 열기 ──
        if key == Qt.Key.Key_F10:
            self.open_settings_requested.emit()
            event.accept()
            return

        # ── Space: 포커스 항목 미리보기 ──
        if key == Qt.Key.Key_Space:
            self._kbd_preview()
            event.accept()
            return

        # ── 복사: Ctrl+C (단일 복사 / 다중 결합 복사) ──
        if key == Qt.Key.Key_C and mods & Qt.KeyboardModifier.ControlModifier:
            self._kbd_copy()
            event.accept()
            return

        # ── c: 포커스 항목 큐에 추가/해제 토글 (수정자 없는 단일키) ──
        if key == Qt.Key.Key_C and not mods & (
                Qt.KeyboardModifier.ControlModifier
                | Qt.KeyboardModifier.AltModifier):
            self._kbd_queue_toggle()
            event.accept()
            return

        # ── P: 포커스 항목 고정/해제 토글 ──
        if key == Qt.Key.Key_P and not mods & Qt.KeyboardModifier.ControlModifier:
            self._kbd_pin_toggle()
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

    def _kbd_activate(self):
        """Enter: 포커스 항목 붙여넣기"""
        if self._kbd_focus_id is None:
            return
        item = self._find_item(self._kbd_focus_id)
        if item:
            self.paste_item_requested.emit(item)

    def _kbd_preview(self):
        """Space: 포커스 항목 미리보기"""
        if self._kbd_focus_id is None:
            return
        item = self._find_item(self._kbd_focus_id)
        if not item:
            return
        if item.content_type == "image":
            self.preview_image_requested.emit(item.id)
        else:
            self.preview_text_requested.emit(item.id)

    def _kbd_copy(self):
        """Ctrl+C: 단일 복사, 다중 선택이면 결합 복사"""
        if len(self._selected_ids) > 1:
            combined = self._combine_selected_items()
            if combined:
                self.combine_copy_requested.emit(combined)
        else:
            item = self._find_item(self._kbd_focus_id)
            if item:
                self._do_copy(item)

    def _kbd_queue_toggle(self):
        """c: 포커스 항목 큐 추가/해제 토글 — 앵커 항목이면 해제, 아니면 교체"""
        if self._kbd_focus_id is None:
            return
        if self._queue_item_ids and self._kbd_focus_id == self._queue_item_ids[0]:
            self.queue_deselect_requested.emit(self._kbd_focus_id)
        else:
            self.queue_select_requested.emit(self._kbd_focus_id)

    def _kbd_pin_toggle(self):
        """P: 포커스 항목 고정/해제 토글"""
        if self._kbd_focus_id is None:
            return
        item = self._find_item(self._kbd_focus_id)
        if not item:
            return
        if item.is_pinned:
            self.unpin_item_requested.emit(self._kbd_focus_id)
        else:
            self.pin_item_requested.emit(self._kbd_focus_id)

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

        close_action = menu.addAction("×  패널 숨기기")
        close_action.triggered.connect(self.hide)

        menu.addSeparator()

        clear_action = menu.addAction("🗑  히스토리 초기화")
        clear_action.triggered.connect(self.clear_history_requested.emit)

        menu.addSeparator()

        settings_action = menu.addAction("⚙  설정")
        settings_action.triggered.connect(self.open_settings_requested.emit)

        quit_action = menu.addAction("⏻  PasteFlow 종료")
        quit_action.triggered.connect(self.quit_requested.emit)

        menu.exec(event.globalPos())
