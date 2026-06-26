"""텍스트 원본 미리보기 팝업 — 다중 창 동시 표시 지원

UX 정책:
- 창 전체가 드래그 이동 영역. 텍스트 부분 선택은 지원하지 않으며 우클릭 메뉴의
  `수정`을 통해 편집 다이얼로그에서 자연스럽게 선택·편집한다.
- 표시 위젯은 QPlainTextEdit이다. 공백 없는 긴 URL/해시/코드도 `WrapAtWordBoundaryOrAnywhere`
  모드로 문자 단위 줄바꿈되어 양옆 잘림이 없다. QLabel+QScrollArea 조합은 word-boundary가
  없는 토큰을 절대 잘라주지 않아 폐기했다.
"""
import math
import re

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QApplication, QMenu, QPlainTextEdit, QTextEdit, QFrame,
)
from PyQt6.QtCore import Qt, QPoint, QRect, QEvent, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QTextOption, QFont, QTextDocument, QTextCursor, QTextCharFormat, QColor,
    QTextListFormat, QTextBlockFormat,
)

from pasteflow.ui.theme import (
    BASE as _BG, SURFACE0 as _SURFACE0, SURFACE1 as _BORDER, SURFACE2 as _SURFACE2,
    TEXT as _TEXT, BLUE as _BLUE, PEACH as _PEACH, RED as _RED, GREEN as _GREEN,
)
from pasteflow.ui.image_preview import (
    compute_preview_pos, _CASCADE_STEP, _dark_menu_style,
)
from pasteflow.models import ClipboardItem

# 초기 표시 시점 폭/높이 상한 (사용자가 zoom하면 화면 한계까지 확장)
PREVIEW_INITIAL_MAX_W = 360
PREVIEW_INITIAL_MAX_H = 300
_BASE_FONT_SIZE = 12
_SCALE_STEP = 1.3
_SCREEN_MARGIN = 40  # 화면 한계 cap 시 가장자리 여유

# AI 답변(마크다운) 전용 — 줌 없이 바로 읽기 편한 고정값. 휠은 줌이 아니라 스크롤로
# 동작하므로(Ctrl+휠=줌) 글자 크기가 항상 일정하다. 길면 세로 스크롤로 본다.
_MD_FONT_SIZE = 16        # 답변 기본 글자 크기(고정)
_MD_INITIAL_MAX_W = 600   # 답변 초기 최대 폭(prose는 넓을수록 가독성↑)
_MD_MAX_H_FRAC = 0.8      # 답변 창 높이 상한(화면 비율) — 초과분은 스크롤

# 창 chrome(외곽) 폭/높이 — 컨테이너 테두리(2+2) + 에디터 viewport 마진(8+8)
_CHROME_W = 4 + 16
_CHROME_H = 4 + 16

# 형광펜 — 코랄 배경 + 채도 높은 빨강 글자(사용자 지정). 모델 서식 색은 _apply_marks가
# 매번 초기화 후 재적용하므로 형광펜 글자색이 그 위를 덮는다.
_HL_BG = QColor(255, 130, 95, 38)   # 코랄(아주 연하게) — 빨강 글자가 강조를 담당
_HL_FG = QColor(255, 70, 70)        # 선명한 빨강 글자
# 세로 스크롤바 폭 — 마크다운 답변이 넘칠 때 텍스트를 가리지 않게 폭 보정.
_SCROLLBAR_W = 12

# AI 답변 문단 줄간격/여백 — "줄이 따닥따닥" 방지(가독성).
_MD_LINE_HEIGHT = 135   # ProportionalHeight(%)
_MD_BLOCK_MARGIN = 7.0  # 문단 사이 여백(px)

# CommonMark: 따옴표로 감싼 볼드(**'X'**) 뒤에 공백 없이 글자가 오면 닫는 **가
# 닫힘 구분자로 인정되지 않아(flanking 규칙) 볼드가 풀린다. 따옴표를 볼드 바깥으로
# 옮겨('**X**') 어디서든 정상 렌더되게 한다. 직선/곡선 따옴표 모두 처리.
_QUOTED_BOLD_RE = re.compile(r"""\*\*(['"‘’“”])(.+?)\1\*\*""")


def _fix_markdown_emphasis(text: str) -> str:
    return _QUOTED_BOLD_RE.sub(lambda m: f"{m.group(1)}**{m.group(2)}**{m.group(1)}", text)


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
    def open_new(cls, item: ClipboardItem, panel_geom: QRect, editable: bool = True,
                 markdown: bool = False, center: bool = False) -> "TextPreviewPopup":
        """새 미리보기 창을 열고 인스턴스 목록에 등록한다.

        editable=False면 우클릭 "수정" 메뉴를 숨긴다(AI 답변 등 DB에 없는 임시 항목 —
        id가 없어 수정·저장 경로가 무력하므로 메뉴 자체를 제거).
        markdown=True면 QTextEdit+setMarkdown으로 서식을 렌더링한다(AI 답변 전용 —
        일반 텍스트 미리보기는 원문 확인 용도라 평문 유지).
        center=True면 panel_geom 옆이 아니라 panel_geom이 속한 모니터 정중앙에 띄운다
        (AI 답변 전용 — _ai_anchor가 가리키는 커서 모니터 한복판).
        """
        cascade_offset = len(cls._instances) * _CASCADE_STEP
        popup = cls(item, editable=editable, markdown=markdown)
        cls._instances.append(popup)
        popup.show_preview(panel_geom, cascade_offset, center=center)
        return popup

    @classmethod
    def close_all(cls):
        """열려 있는 모든 미리보기 창을 닫는다."""
        for popup in list(cls._instances):
            popup.close()

    # ------------------------------------------------------------------
    # 인스턴스 초기화
    # ------------------------------------------------------------------

    def __init__(self, item: ClipboardItem, editable: bool = True, markdown: bool = False):
        super().__init__(None)
        self._item = item
        self._editable = editable
        self._markdown = markdown
        self._raw_text = ""  # 마크다운 측정용 원문 (show_preview에서 채움)
        self._marks: list[tuple[int, int]] = []  # 형광펜 범위(문서 position 좌표)
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

        # markdown=True → QTextEdit(서식 렌더링), 아니면 QPlainTextEdit(평문, 기존 정책).
        self._editor = QTextEdit() if markdown else QPlainTextEdit()
        self._editor.setReadOnly(True)
        # 마크다운(AI 답변)은 형광펜용 텍스트 선택 허용(좌드래그=선택→형광펜),
        # 일반 미리보기는 선택 불가(창 전체 드래그=이동) 기존 정책 유지.
        self._editor.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse if markdown
            else Qt.TextInteractionFlag.NoTextInteraction)
        self._editor.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        # 양쪽 스크롤바 모두 영구 차단 — "미리보기는 한 번에 다 보여야 한다"는 정책.
        # popup이 화면 한계에 부딪힐 때만 가장자리 1줄 잘림 발생.
        self._editor.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # 마크다운(AI 답변)은 길면 세로 스크롤 허용(휠=스크롤). 일반 미리보기는 기존
        # "한 번에 다 보임" 정책대로 스크롤바 차단.
        self._editor.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded if markdown
            else Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
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
        # 마크다운=형광펜 선택용 I빔, 일반=창 이동 SizeAll
        _ecur = Qt.CursorShape.IBeamCursor if markdown else Qt.CursorShape.SizeAllCursor
        self._editor.setCursor(_ecur)
        self._editor.viewport().setCursor(_ecur)
        _editor_sel = "QTextEdit" if markdown else "QPlainTextEdit"
        self._editor.setStyleSheet(f"""
            {_editor_sel} {{
                background: {_BG};
                color: {_TEXT};
                border: none;
            }}
            QScrollBar:vertical {{
                background: {_SURFACE0};
                width: {_SCROLLBAR_W}px;
                border-radius: {_SCROLLBAR_W // 2}px;
            }}
            QScrollBar::handle:vertical {{
                background: {_SURFACE2};
                border-radius: {_SCROLLBAR_W // 2}px;
                min-height: 28px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: {_BLUE};
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0;
            }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
                background: transparent;
            }}
        """)
        container_layout.addWidget(self._editor)

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
                ctrl = event.modifiers() & Qt.KeyboardModifier.ControlModifier
                # 마크다운(AI 답변)은 휠=스크롤(글자 크기 고정), Ctrl+휠=줌.
                # 일반 미리보기는 기존대로 휠=줌.
                if self._markdown and not ctrl:
                    return False  # QTextEdit가 스크롤 처리
                delta = event.angleDelta().y()
                if delta != 0:
                    factor = _SCALE_STEP if delta > 0 else (1 / _SCALE_STEP)
                    self._scale_factor *= factor
                    self._apply_scale()
                    self._resize_to_content()
                return True
            # 창 이동 버튼: 마크다운=가운데클릭(좌클릭은 형광펜 선택용), 일반=좌클릭.
            move_btn = (Qt.MouseButton.MiddleButton if self._markdown
                        else Qt.MouseButton.LeftButton)
            if et == QEvent.Type.MouseButtonPress:
                if event.button() == move_btn:
                    self.activateWindow()
                    self._drag_pos = (
                        event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                    )
                    return True
                if self._markdown and event.button() == Qt.MouseButton.LeftButton:
                    self.activateWindow()
                    return False  # QTextEdit가 텍스트 선택 처리하도록 통과
            elif et == QEvent.Type.MouseMove:
                if self._drag_pos is not None and event.buttons() & move_btn:
                    self.move(event.globalPosition().toPoint() - self._drag_pos)
                    return True
            elif et == QEvent.Type.MouseButtonRelease:
                if event.button() == move_btn and self._drag_pos is not None:
                    self._drag_pos = None
                    return True
                if self._markdown and event.button() == Qt.MouseButton.LeftButton:
                    # 선택 확정 후(QTextEdit release 처리 뒤) 형광펜 토글·해제 적용
                    QTimer.singleShot(0, self._on_left_select_release)
                    return False
        return super().eventFilter(obj, event)

    # ------------------------------------------------------------------
    # 본체에서 직접 발생한 마우스 이벤트 (자식이 안 잡은 영역: 컨테이너 테두리 등)
    # ------------------------------------------------------------------

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.activateWindow()
            self._drag_pos = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        super().mouseReleaseEvent(event)

    # ------------------------------------------------------------------
    # 표시
    # ------------------------------------------------------------------

    def show_preview(self, panel_geom: QRect, cascade_offset: int = 0, center: bool = False):
        text = self._item.text_content or self._item.preview_text or ""
        if self._markdown:
            text = _fix_markdown_emphasis(text)  # 따옴표 볼드 정상화
            self._raw_text = text
            self._editor.setMarkdown(text)
            self._apply_block_spacing(self._editor.document())  # 줄간격·문단 여백
            self._set_list_bullets(self._editor.document())     # ○/ㅇ → •
            self._apply_marks()  # 모델 색 + 형광펜
        else:
            self._raw_text = text
            self._editor.setPlainText(text)
        self._resize_to_content()
        screen = QApplication.screenAt(panel_geom.center()) or QApplication.primaryScreen()
        if screen:
            if center:
                # panel_geom이 속한 모니터 정중앙 (연속 표시 시 cascade로 살짝 어긋나게).
                avail = screen.availableGeometry()
                w, h = self.width(), self.height()
                x = avail.center().x() - w // 2 + cascade_offset
                y = avail.center().y() - h // 2 + cascade_offset
                x = max(avail.left(), min(x, avail.right() - w))
                y = max(avail.top(), min(y, avail.bottom() - h))
                self.move(x, y)
            else:
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

        if self._markdown and self._marks:
            clear_action = menu.addAction("형광펜 지우기")
            clear_action.triggered.connect(self._clear_marks)

        menu.addSeparator()
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
    # 활성화 외곽선 — 비활성 시 주황(상시), 활성 시 파랑
    # ------------------------------------------------------------------

    def _apply_active_style(self, active: bool):
        """비활성 시 주황, 활성(클릭) 시 파랑. 컨테이너에 적용해야 자식 위젯에 안 가려짐."""
        border = _BLUE if active else _PEACH
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
        """현재 배율에 맞춰 에디터 폰트 크기 갱신."""
        base = _MD_FONT_SIZE if self._markdown else _BASE_FONT_SIZE
        size = max(1, round(base * self._scale_factor))
        font = QFont(self._editor.font())
        font.setPixelSize(size)
        self._editor.setFont(font)
        # 폰트 변경 시 document margin도 재설정 (Qt가 setFont에서 reset 하는 경우 대비)
        self._editor.document().setDocumentMargin(0)

    def _colorize_markdown(self):
        """setMarkdown이 남긴 서식(제목/굵게/코드/기울임)에 색을 입혀 시인성을 높인다.

        전경색(글자색)만 바꾼다 — 배경색은 사용자 형광펜 전용 채널로 비워둔다.
        포맷 변경이 fragment 재분할을 유발할 수 있어 먼저 (위치,길이,색)을 모두
        수집한 뒤 일괄 적용한다.
        """
        doc = self._editor.document()
        spans = []  # (position, length, color)
        blk = doc.begin()
        while blk.isValid():
            heading = blk.blockFormat().headingLevel()
            it = blk.begin()
            while not it.atEnd():
                frag = it.fragment()
                if frag.isValid():
                    cf = frag.charFormat()
                    if heading > 0:
                        color = _BLUE
                    elif cf.fontFixedPitch():
                        color = _RED
                    elif cf.fontWeight() >= 700:
                        color = _PEACH
                    elif cf.fontItalic():
                        color = _GREEN
                    else:
                        color = None
                    if color:
                        spans.append((frag.position(), frag.length(), color))
                it += 1
            blk = blk.next()

        for pos, length, color in spans:
            cur = QTextCursor(doc)
            cur.setPosition(pos)
            cur.setPosition(pos + length, QTextCursor.MoveMode.KeepAnchor)
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(color))
            cur.mergeCharFormat(fmt)

    # ------------------------------------------------------------------
    # 형광펜(하이라이트) — 좌드래그 선택 → 배경색 토글. 클릭=해당 마크 해제.
    # 데이터는 문서 position 범위로 보관, 복사 시 백틱으로 직렬화(prior art 패턴).
    # ------------------------------------------------------------------

    def _on_left_select_release(self):
        """좌클릭 릴리스: 선택이 있으면 그 범위 형광펜 토글, 없으면(클릭) 마크 해제."""
        cur = self._editor.textCursor()
        if cur.hasSelection():
            s, e = cur.selectionStart(), cur.selectionEnd()
            cur.clearSelection()
            self._editor.setTextCursor(cur)
            self._toggle_mark(s, e)
        else:
            self._remove_mark_at(cur.position())

    @staticmethod
    def _merge_marks(marks):
        out = []
        for a, b in sorted(m for m in marks if m[1] > m[0]):
            if out and a <= out[-1][1]:
                out[-1] = (out[-1][0], max(out[-1][1], b))
            else:
                out.append((a, b))
        return out

    def _toggle_mark(self, start: int, end: int):
        """선택이 기존 마크 안에 완전히 들면 그 마크 제거, 아니면 추가+병합."""
        covering = next((m for m in self._marks if m[0] <= start and end <= m[1]), None)
        if covering:
            self._marks.remove(covering)
        else:
            self._marks = self._merge_marks(self._marks + [(start, end)])
        self._apply_marks()

    def _remove_mark_at(self, pos: int):
        covering = next((m for m in self._marks if m[0] <= pos < m[1]), None)
        if covering:
            self._marks.remove(covering)
            self._apply_marks()

    def _apply_marks(self):
        """문자 서식을 처음부터 재구성: 초기화 → 모델 색 → 형광펜(연빨강 배경+빨강 글자).

        형광펜이 글자색까지 바꾸므로 토글/해제 시 원래 색 복원이 필요한데, 전체를
        초기화 후 colorize+marks를 다시 입히면 깔끔하다(블록 서식=줄간격/여백은
        문자 서식이라 건드리지 않으므로 유지된다)."""
        doc = self._editor.document()
        whole = QTextCursor(doc)
        whole.select(QTextCursor.SelectionType.Document)
        base = QTextCharFormat()
        base.setForeground(QColor(_TEXT))
        base.setBackground(QColor(0, 0, 0, 0))
        whole.mergeCharFormat(base)

        self._colorize_markdown()

        for s, e in self._marks:
            c = QTextCursor(doc)
            c.setPosition(s)
            c.setPosition(e, QTextCursor.MoveMode.KeepAnchor)
            hf = QTextCharFormat()
            hf.setBackground(_HL_BG)
            hf.setForeground(_HL_FG)
            c.mergeCharFormat(hf)

    @staticmethod
    def _apply_block_spacing(doc):
        """모든 블록에 줄간격·문단 여백 적용(AI 답변 가독성). 다른 블록 속성은 보존."""
        blk = doc.begin()
        while blk.isValid():
            bf = blk.blockFormat()
            bf.setLineHeight(
                _MD_LINE_HEIGHT,
                QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
            bf.setBottomMargin(_MD_BLOCK_MARGIN)
            c = QTextCursor(doc)
            c.setPosition(blk.position())
            c.setBlockFormat(bf)
            blk = blk.next()

    @staticmethod
    def _set_list_bullets(doc):
        """모든 리스트(중첩 포함)를 • (ListDisc)로 통일 — 기본 ○/ㅇ 대신."""
        seen = set()
        blk = doc.begin()
        while blk.isValid():
            tl = blk.textList()
            if tl is not None and id(tl) not in seen:
                seen.add(id(tl))
                f = tl.format()
                f.setStyle(QTextListFormat.Style.ListDisc)
                tl.setFormat(f)
            blk = blk.next()

    def _marked_markdown(self) -> str:
        """형광펜 범위를 백틱으로 감싼 마크다운 — 복사용.

        Qt의 toMarkdown()은 굵게·제목조차 보존하지 못하므로(라운드트립 불가),
        **원본 소스에 백틱만 삽입**해 모델의 마크다운을 100% 보존한다.
        렌더 텍스트 좌표(마크)를 소스 좌표로 두 포인터 정렬(마크다운 구문 문자는
        소스에만 있으므로 건너뜀)로 변환한 뒤 오른쪽부터 백틱을 끼운다.
        한계: 형광펜이 리스트 불릿·수평선 등 '소스엔 있으나 렌더 텍스트엔 없는'
        구조를 가로지르면 그 경계는 정확히 매핑되지 않을 수 있다(드문 경우).
        """
        if not self._marks:
            return self._raw_text
        src = self._raw_text
        rendered = self._editor.toPlainText()
        # rendered[k] → src 인덱스 매핑
        mp = []
        si = 0
        for rc in rendered:
            while si < len(src) and src[si] != rc:
                si += 1
            mp.append(si if si < len(src) else len(src))
            if si < len(src):
                si += 1
        n = len(rendered)

        spans = []
        for s, e in self._marks:
            s = max(0, min(s, n))
            e = max(0, min(e, n))
            if e <= s:
                continue
            src_s = mp[s] if s < n else len(src)
            src_e = (mp[e - 1] + 1) if (e - 1) < n else len(src)
            if src_e > src_s:
                spans.append((src_s, src_e))

        out = src
        for a, b in sorted(spans, reverse=True):
            out = out[:a] + "`" + out[a:b] + "`" + out[b:]
        return out

    def _emit_copy(self):
        """복사 — 마크다운 모드면 형광펜을 백틱으로 직렬화한 본문으로 갱신 후 emit."""
        if self._markdown:
            self._item.text_content = self._marked_markdown()
        self.copy_requested.emit(self._item)

    def _clear_marks(self):
        """형광펜 전체 해제 (모델 서식 색은 그대로)."""
        if self._marks:
            self._marks = []
            self._apply_marks()

    def _set_line_wrap(self, wrap: bool):
        """줄바꿈 on/off — QTextEdit/QPlainTextEdit의 LineWrapMode enum이 달라 분기."""
        if self._markdown:
            mode = (QTextEdit.LineWrapMode.WidgetWidth if wrap
                    else QTextEdit.LineWrapMode.NoWrap)
        else:
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
        # 단 화면 너비를 초과하진 않음.
        # height는 컨텐츠가 다 보이도록 화면 한계까지 자유롭게 확장.
        init_max_w = _MD_INITIAL_MAX_W if self._markdown else PREVIEW_INITIAL_MAX_W
        max_w = min(screen_max_w, round(init_max_w * self._scale_factor))
        # 마크다운(AI 답변)은 길면 화면 비율로 높이를 제한하고 세로 스크롤로 본다.
        max_h = (min(screen_max_h, round(avail.height() * _MD_MAX_H_FRAC))
                 if self._markdown else screen_max_h)

        min_text_w = max(1, 60 - _CHROME_W)

        # 자연 너비 측정 (독립 QTextDocument — editor.document()는 lazy).
        # 마크다운 모드는 원문을 setMarkdown으로 렌더한 문서 기준으로 측정한다
        # (editor.toPlainText()는 서식이 벗겨진 평문이라 측정에 못 씀).
        tmp = QTextDocument()
        tmp.setDefaultFont(self._editor.font())
        opt = tmp.defaultTextOption()
        opt.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        tmp.setDefaultTextOption(opt)
        tmp.setDocumentMargin(0)
        if self._markdown:
            tmp.setMarkdown(self._raw_text)
            self._apply_block_spacing(tmp)  # 줄간격·여백 반영해 높이 정확히 측정
        else:
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
        full_h = round(text_h) + _CHROME_H
        h = min(max_h, full_h)
        # 마크다운 답변이 화면 상한을 넘어 세로 스크롤이 생기면, 스크롤바가 텍스트
        # 오른쪽을 가리지 않도록 그만큼 폭을 더한다.
        if self._markdown and full_h > max_h:
            w += _SCROLLBAR_W
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
