"""텍스트 원본 미리보기 팝업 — 다중 창 동시 표시 지원

UX 정책:
- 창 전체가 드래그 이동 영역. 텍스트 부분 선택은 지원하지 않으며 우클릭 메뉴의
  `수정`을 통해 편집 다이얼로그에서 자연스럽게 선택·편집한다.
- 표시 위젯은 QPlainTextEdit이다. 공백 없는 긴 URL/해시/코드도 `WrapAtWordBoundaryOrAnywhere`
  모드로 문자 단위 줄바꿈되어 양옆 잘림이 없다. QLabel+QScrollArea 조합은 word-boundary가
  없는 토큰을 절대 잘라주지 않아 폐기했다.
"""
import math

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QApplication, QMenu, QPlainTextEdit, QFrame,
)
from PyQt6.QtCore import Qt, QPoint, QRect, QEvent, pyqtSignal
from PyQt6.QtGui import QTextOption, QFont, QTextDocument

from pasteflow.ui.theme import BASE as _BG, SURFACE2 as _SURFACE2, TEXT as _TEXT, PEACH as _PEACH
from pasteflow.ui.image_preview import (
    compute_preview_pos, _CASCADE_STEP, _dark_menu_style,
)
from pasteflow.models import ClipboardItem

# 초기 표시 시점 폭/높이 상한 (사용자가 zoom하면 화면 한계까지 확장)
PREVIEW_INITIAL_MAX_W = 360
PREVIEW_INITIAL_MAX_H = 300
_BASE_FONT_SIZE = 12
_SCALE_STEP = 1.1  # Ctrl+휠 한 칸당 ~10% (1.3=30%는 너무 급격해 촘촘하게 낮춤)
_SCREEN_MARGIN = 40  # 화면 한계 cap 시 가장자리 여유

# 창 chrome(외곽) 폭/높이 — 컨테이너 테두리(2+2) + 에디터 viewport 마진(8+8)
_CHROME_W = 4 + 16
_CHROME_H = 4 + 16

# 본문 폰트 패밀리 — 맑은 고딕(작은 크기에서 힌팅이 강해 또렷하다).
_FONT_FAMILY = "맑은 고딕"


class TextPreviewPopup(QWidget):
    """텍스트 전체 미리보기 — 다중 창 동시 표시 지원"""

    _instances: list["TextPreviewPopup"] = []

    # 우클릭 메뉴 → main 핸들러로 전달
    copy_requested = pyqtSignal(object)   # ClipboardItem
    edit_requested = pyqtSignal(int)      # item_id

    # ------------------------------------------------------------------
    # 클래스 메서드
    # ------------------------------------------------------------------

    @classmethod
    def open_new(cls, item: ClipboardItem, panel_geom: QRect,
                 editable: bool = True) -> "TextPreviewPopup":
        """새 미리보기 창을 열고 인스턴스 목록에 등록한다.

        editable=False면 우클릭 "수정" 메뉴를 숨긴다(DB에 없는 임시 항목 등 — id가
        없어 수정·저장 경로가 무력하므로 메뉴 자체를 제거).
        """
        cascade_offset = len(cls._instances) * _CASCADE_STEP
        popup = cls(item, editable=editable)
        cls._instances.append(popup)
        popup.show_preview(panel_geom, cascade_offset)
        return popup

    @classmethod
    def close_all(cls):
        """열려 있는 모든 미리보기 창을 닫는다."""
        for popup in list(cls._instances):
            popup.close()

    # ------------------------------------------------------------------
    # 인스턴스 초기화
    # ------------------------------------------------------------------

    def __init__(self, item: ClipboardItem, editable: bool = True):
        super().__init__(None)
        self._item = item
        self._editable = editable
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
        self._drag_pos: QPoint | None = None
        self.setCursor(Qt.CursorShape.SizeAllCursor)

        # 최상위 창은 배경만 담당 — 테두리는 내부 컨테이너가 담당한다.
        self.setStyleSheet(f"background-color: {_BG};")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._container = QWidget()
        self._container.setObjectName("popup_container")
        root.addWidget(self._container)

        container_layout = QVBoxLayout(self._container)
        container_layout.setContentsMargins(2, 2, 2, 2)
        container_layout.setSpacing(0)

        self._editor = QPlainTextEdit()
        self._editor.setReadOnly(True)
        self._editor.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self._editor.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        # 양쪽 스크롤바 모두 영구 차단 — "미리보기는 한 번에 다 보여야 한다"는 정책.
        # popup이 화면 한계에 부딪힐 때만 가장자리 1줄 잘림 발생.
        self._editor.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._editor.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # editor가 focus를 못 받게 해 키보드 스크롤(PageUp/Down/Space) 차단.
        # 스크롤바가 안 보여도 키보드로 viewport가 움직이면 빈 공간이 노출돼
        # "아랫줄이 따로 있는 것처럼" 보이는 혼란을 막는다.
        self._editor.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._editor.setFrameShape(QFrame.Shape.NoFrame)
        # QPlainTextEdit 기본 우클릭 메뉴 차단 → popup의 contextMenuEvent로 버블링
        self._editor.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        # 균일한 상하좌우 여백 — viewport margin + document margin=0 조합
        self._editor.setViewportMargins(8, 8, 8, 8)
        self._editor.document().setDocumentMargin(0)
        self._editor.setCursor(Qt.CursorShape.SizeAllCursor)
        self._editor.viewport().setCursor(Qt.CursorShape.SizeAllCursor)
        self._editor.setStyleSheet(f"""
            QPlainTextEdit {{
                background: {_BG};
                color: {_TEXT};
                border: none;
            }}
        """)
        container_layout.addWidget(self._editor, 1)

        # viewport 클릭/휠 → popup이 처리 (전체 창 드래그·휠 줌)
        self._editor.viewport().installEventFilter(self)

        self._apply_active_style(False)
        self._apply_scale()
        self.hide()

    # ------------------------------------------------------------------
    # 이벤트 필터 — viewport에서 발생한 휠·마우스 일괄 처리
    # ------------------------------------------------------------------

    def eventFilter(self, obj, event):
        if obj is self._editor.viewport():
            et = event.type()
            if et == QEvent.Type.Wheel:
                delta = event.angleDelta().y()
                if delta != 0:
                    factor = _SCALE_STEP if delta > 0 else (1 / _SCALE_STEP)
                    self._scale_factor *= factor
                    self._apply_scale()
                    self._resize_to_content()
                return True
            # 창 이동: 좌클릭·휠(가운데)클릭 모두 이동(텍스트 선택은 지원하지 않음).
            if et == QEvent.Type.MouseButtonPress:
                if self._is_move_button(event.button()):
                    self.activateWindow()
                    self._drag_pos = (
                        event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                    )
                    return True
            elif et == QEvent.Type.MouseMove:
                if self._drag_pos is not None and event.buttons():
                    self.move(event.globalPosition().toPoint() - self._drag_pos)
                    return True
            elif et == QEvent.Type.MouseButtonRelease:
                if self._drag_pos is not None and event.button() in (
                        Qt.MouseButton.LeftButton, Qt.MouseButton.MiddleButton):
                    self._drag_pos = None
                    return True
        return super().eventFilter(obj, event)

    def _is_move_button(self, btn) -> bool:
        """창 이동 트리거 버튼 — 좌클릭(전체 창 드래그)·휠(가운데)클릭 모두 이동으로 취급."""
        return btn in (Qt.MouseButton.LeftButton, Qt.MouseButton.MiddleButton)

    # ------------------------------------------------------------------
    # 본체에서 직접 발생한 마우스 이벤트 (자식이 안 잡은 영역: 컨테이너 테두리 등)
    # ------------------------------------------------------------------

    def mousePressEvent(self, event):
        if self._is_move_button(event.button()):
            self.activateWindow()
            self._drag_pos = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_pos is not None and event.buttons():
            self.move(event.globalPosition().toPoint() - self._drag_pos)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        super().mouseReleaseEvent(event)

    # ------------------------------------------------------------------
    # 표시
    # ------------------------------------------------------------------

    def show_preview(self, panel_geom: QRect, cascade_offset: int = 0):
        text = self._item.text_content or self._item.preview_text or ""
        self._editor.setPlainText(text)
        self._resize_to_content()
        screen = QApplication.screenAt(panel_geom.center()) or QApplication.primaryScreen()
        if screen:
            self.move(compute_preview_pos(panel_geom, self.size(), screen, cascade_offset))
        self.show()
        self.raise_()

    # ------------------------------------------------------------------
    # 우클릭 메뉴 — 전체 복사 / 수정 / 닫기 (패널 메뉴와 동일 명칭·순서)
    # ------------------------------------------------------------------

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        menu.setStyleSheet(_dark_menu_style())

        copy_action = menu.addAction("전체 복사")
        copy_action.triggered.connect(self._emit_copy)

        if self._editable and self._item.content_type != "image":
            edit_action = menu.addAction("수정")
            edit_action.triggered.connect(lambda: self.edit_requested.emit(self._item.id))

        menu.addSeparator()
        close_action = menu.addAction("닫기")
        close_action.triggered.connect(self.close)

        menu.exec(event.globalPos())

    def _emit_copy(self):
        self.copy_requested.emit(self._item)

    # ------------------------------------------------------------------
    # 키보드 (ESC)
    # ------------------------------------------------------------------

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)

    # ------------------------------------------------------------------
    # 활성화 외곽선 — 비활성 시 회색(상시), 활성 시 코랄
    # ------------------------------------------------------------------

    def _apply_active_style(self, active: bool):
        """활성(보고 있는 창) = 코랄(주인공), 비활성 = 중립 회색(존재만 표시, 안 튐).
        컨테이너에 적용해야 자식 위젯에 안 가려짐."""
        border = _PEACH if active else _SURFACE2
        self._container.setStyleSheet(f"""
            QWidget#popup_container {{
                background-color: {_BG};
                border: 2px solid {border};
            }}
        """)

    def changeEvent(self, event):
        if event.type() == QEvent.Type.ActivationChange and hasattr(self, "_container"):
            self._apply_active_style(self.isActiveWindow())
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
        """현재 배율에 맞춰 에디터 폰트 크기 갱신 — PreferFullHinting으로 픽셀 그리드에
        스냅해 작은 크기에서 획을 또렷하게 한다(Qt 기본 CJK 힌팅은 약해 흐려짐)."""
        size = max(1, round(_BASE_FONT_SIZE * self._scale_factor))
        font = QFont(self._editor.font())
        font.setPixelSize(size)
        font.setFamily(_FONT_FAMILY)
        font.setWeight(QFont.Weight.Normal)
        font.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
        self._editor.setFont(font)
        # 폰트 변경 시 document margin도 재설정 (Qt가 setFont에서 reset 하는 경우 대비)
        self._editor.document().setDocumentMargin(0)

    def _set_line_wrap(self, wrap: bool):
        mode = (QPlainTextEdit.LineWrapMode.WidgetWidth if wrap
                else QPlainTextEdit.LineWrapMode.NoWrap)
        self._editor.setLineWrapMode(mode)

    def _resize_to_content(self):
        """텍스트 전체가 한 번에 보이도록 popup 크기 결정.

        설계:
        - 자연 너비가 화면 안에 들어오면 NoWrap 모드로 강제 — QPlainTextEdit
          viewport에 우리가 모르는 내부 padding이 있어 textWidth를 정확히
          맞춰도 sub-pixel 차이로 wrap이 새는 경우가 있다(휠 줌 시 줄 수가
          깜빡이는 증상). LineWrapMode.NoWrap로 wrap 자체를 차단해야 안정.
        - 자연 너비가 화면을 초과해야만 WidgetWidth wrap 적용.
        - 높이도 항상 모든 줄을 표시 — 스크롤바는 화면 한계에 부딪힐 때만.

        한계는 화면 크기. PREVIEW_INITIAL_MAX_*는 첫 표시 시점에만 의미가
        있고, 사용자가 zoom-in 하면 화면 한계까지 자유롭게 확장된다.
        """
        screen = (self.screen()
                  or QApplication.screenAt(self.geometry().center())
                  or QApplication.primaryScreen())
        avail = screen.availableGeometry()
        screen_max_w = avail.width() - _SCREEN_MARGIN
        screen_max_h = avail.height() - _SCREEN_MARGIN

        # width는 zoom과 함께 비례 확장 (짧은 텍스트는 줌해도 wrap 없이 1줄 유지),
        # 단 화면 너비를 초과하진 않음. height는 컨텐츠가 다 보이도록 화면 한계까지 확장.
        max_w = min(screen_max_w, round(PREVIEW_INITIAL_MAX_W * self._scale_factor))
        max_h = screen_max_h

        min_text_w = max(1, 60 - _CHROME_W)

        # 자연 너비 측정 (독립 QTextDocument — editor.document()는 lazy).
        tmp = QTextDocument()
        tmp.setDefaultFont(self._editor.font())
        opt = tmp.defaultTextOption()
        opt.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        tmp.setDefaultTextOption(opt)
        tmp.setDocumentMargin(0)
        tmp.setPlainText(self._editor.toPlainText())
        tmp.setTextWidth(-1)
        natural_w = tmp.idealWidth()

        max_text_w = max_w - _CHROME_W
        if math.ceil(natural_w) <= max_text_w:
            # 자연 너비가 화면 안에 들어옴 → wrap 차단해 1줄 보장
            self._set_line_wrap(False)
            text_w = max(min_text_w, math.ceil(natural_w))
        else:
            # 자연 너비가 화면 초과 → wrap on
            self._set_line_wrap(True)
            text_w = max_text_w

        # 결정된 너비로 layout 후 높이 측정 (모든 줄 합한 높이)
        tmp.setTextWidth(text_w)
        text_h = tmp.size().height()

        w = text_w + _CHROME_W
        h = min(max_h, round(text_h) + _CHROME_H)
        self.resize(w, h)
        self._clamp_to_screen(avail)

    def _clamp_to_screen(self, avail):
        """resize 후 popup이 화면 밖으로 나갔으면 안쪽으로 끌어들임."""
        geo = self.geometry()
        x, y = geo.x(), geo.y()
        if geo.right() > avail.right():
            x = max(avail.left(), avail.right() - geo.width())
        if geo.bottom() > avail.bottom():
            y = max(avail.top(), avail.bottom() - geo.height())
        if x < avail.left():
            x = avail.left()
        if y < avail.top():
            y = avail.top()
        if (x, y) != (geo.x(), geo.y()):
            self.move(x, y)
