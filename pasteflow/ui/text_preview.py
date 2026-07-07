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
import webbrowser

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QApplication, QMenu, QPlainTextEdit, QTextEdit,
    QFrame, QPushButton, QLineEdit,
)
from PyQt6.QtCore import Qt, QPoint, QRect, QRectF, QEvent, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QTextOption, QFont, QFontMetrics, QTextDocument, QTextCursor, QTextCharFormat, QColor,
    QTextListFormat, QTextBlockFormat,
    QImage, QPainter, QPixmap, QPalette, QAbstractTextDocumentLayout,
)

from pasteflow.ui.theme import (
    BASE as _BG, SURFACE0 as _SURFACE0, SURFACE1 as _BORDER, SURFACE2 as _SURFACE2,
    TEXT as _TEXT, BLUE as _BLUE, PEACH as _PEACH,
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

# 형광펜 — 어두운 적갈색 칩(불투명) + 선명한 빨강 글자. 예전 연코랄 배경은 빨강 글자와
# 같은 계열이라 대비가 죽어 뿌옇게 보였다(빨강 위 빨강). 어두운 배경으로 뒤집어 글자를
# 또렷하게 한다. 배경은 패널 중립 회색(#1e1e1e)과 구분되도록 살짝 붉은기를 둬, '그림자'가
# 아니라 '의도된 강조'로 읽히게 한다. 모델 서식 색은 _apply_marks가 매번 초기화 후
# 재적용하므로 형광펜 글자색이 그 위를 덮는다.
_HL_BG = QColor(40, 20, 20)         # 어두운 적갈색 칩(#281414, 불투명) — 패널 회색과 구분
_HL_FG = QColor(255, 90, 90)        # 선명한 빨강 글자(#ff5a5a) — 어두운 칩 위 고대비

# 마크다운(AI 답변) 요소별 글자색 — 앱 전역 액센트(theme)와 분리해 답변 가독성에 맞춰
# 독립 튜닝한다(여기를 바꿔도 버튼·테두리 색은 안 변함). 백틱/코드는 사용자 형광펜(빨강)과
# 확실히 구분되도록 청록(teal)을 쓰고(핑크·빨강 회피), 볼드 코랄은 채도를 조금 올렸다.
_MD_HEADING = "#89b4fa"   # 제목 — 파랑
_MD_CODE    = "#38bdf8"   # 백틱/코드 — 파랑(형광펜 빨강과 대비). 폰트는 본문으로 통일(아래)
_MD_BOLD    = "#ff9e5e"   # 볼드 — 코랄(채도↑, dual subtitle 느낌)
_MD_ITALIC  = "#a6e3a1"   # 기울임 — 초록
_MD_LINK    = "#60a5fa"   # 하이퍼링크 — 밝은 파랑 + 밑줄(클릭 가능 표시, 제목 파랑과 톤 구분)

# 본문 폰트 패밀리. 코드(백틱) 구간은 Qt가 모노스페이스로 그려 본문과 폰트가 튀므로,
# 색·밑줄만 강조로 남기고 폰트는 이 본문 폰트로 되돌려 다른 텍스트와 통일한다.
# 일반 텍스트 미리보기(12px)는 맑은 고딕 — 작은 크기에서 힌팅이 강해 또렷하다.
_FONT_FAMILY = "맑은 고딕"
# AI 답변(마크다운, 16px) 전용 본문 폰트 — Noto Sans KR(구글 본문 가독성용). 획 굵기가
# 균일하고 자간에 여유가 있어 긴 답변에서 눈이 덜 피로하다(맑은 고딕은 획이 가늘어 촘촘).
# 읽기 크기(16px)라 맑은 고딕 대비 이득이 크다. 미설치 PC면 sans-serif로 폴백.
_MD_FONT_FAMILY = "Noto Sans KR"
# 세로 스크롤바 폭 — 마크다운 답변이 넘칠 때 텍스트를 가리지 않게 폭 보정.
_SCROLLBAR_W = 12

# AI 답변 문단 줄간격/여백 — "줄이 따닥따닥" 방지(가독성).
_MD_LINE_HEIGHT = 135   # ProportionalHeight(%)
_MD_BLOCK_MARGIN = 7.0  # 문단 사이 여백(px)

# CommonMark: 따옴표로 감싼 볼드(**'X'**) 뒤에 공백 없이 글자가 오면 닫는 **가
# 닫힘 구분자로 인정되지 않아(flanking 규칙) 볼드가 풀린다. 따옴표를 볼드 바깥으로
# 옮겨('**X**') 어디서든 정상 렌더되게 한다. 직선/곡선 따옴표 모두 처리.
_QUOTED_BOLD_RE = re.compile(r"""\*\*(['"‘’“”])(.+?)\1\*\*""")

# Qt 마크다운은 볼드와 인라인코드(백틱)의 '중첩'을 렌더하지 못해 ** 가 글자로 노출된다
# (실측 확인: `**X**`·**`X`** 둘 다 별표 잔존). 중첩을 풀어 순수 코드(`X`)로 만들고,
# 코드 스팬 자체를 볼드로 그려(_apply_marks) '볼드+백틱' 효과를 포맷으로 살린다.
_CODE_BOLD_RE = re.compile(r"`\*\*(.+?)\*\*`")     # `**X**` → `X`
_BOLD_CODE_RE = re.compile(r"\*\*(`[^`]+`)\*\*")   # **`X`** → `X`


def _fix_markdown_emphasis(text: str) -> str:
    text = _CODE_BOLD_RE.sub(r"`\1`", text)
    text = _BOLD_CODE_RE.sub(r"\1", text)
    return _QUOTED_BOLD_RE.sub(lambda m: f"{m.group(1)}**{m.group(2)}**{m.group(1)}", text)


# F 스냅 프리셋(방향별 크기). AI 답변창은 답변마다 새 인스턴스로 생겼다 닫히는 짧은 수명이라
# 인스턴스에 기억시키면 다음 답변창에 안 넘어간다 → 모듈 레벨에 보관하고, DB 영속화(재시작 후
# 유지)는 main이 주입한 콜백으로 처리한다(UI 위젯이 DB를 직접 만지지 않게 분리).
_SNAP_PRESETS: dict[str, tuple[int, int]] = {}      # {"landscape": (w,h), "portrait": (w,h)}
_SNAP_PERSIST = None                                  # callable(orientation:str, w:int, h:int)

# 펜딩 탭 답변 자리 sentinel — 후속 질문 엔터 직후 답이 아직 없는 턴을 표시(is 비교로 식별).
_PENDING = object()


def configure_snap_presets(presets: dict, persist_cb) -> None:
    """main이 시작 시 1회 호출 — DB에서 읽은 방향별 F 프리셋과 저장 콜백을 주입한다."""
    global _SNAP_PERSIST
    _SNAP_PRESETS.clear()
    if presets:
        _SNAP_PRESETS.update(presets)
    _SNAP_PERSIST = persist_cb


class _ResizeGrip(QWidget):
    """우하단 코너 그립 — 드래그로 부모 팝업을 자유 리사이즈(AI 답변창 전용).

    프레임리스 창이라 OS 리사이즈 핸들이 없어 직접 구현한다. 가운데클릭=창 이동과
    충돌하지 않도록 좌클릭 드래그만 처리하며, 드래그 시작 시 부모의 수동 크기 모드를
    켠다(이후 줌이 자동 크기로 되돌리지 않음)."""

    _SIZE = 24  # 잡기 편하도록 넉넉히 (꼭짓점에 flush 배치)

    def __init__(self, popup: "TextPreviewPopup"):
        super().__init__(popup)
        self._popup = popup
        self.setFixedSize(self._SIZE, self._SIZE)
        self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        self._press = None

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._press = (event.globalPosition().toPoint(), self._popup.size())
            self._moved = False
            event.accept()

    def mouseMoveEvent(self, event):
        if self._press is not None:
            start, sz = self._press
            d = event.globalPosition().toPoint() - start
            self._moved = True
            self._popup._apply_manual_resize(sz.width() + d.x(), sz.height() + d.y())
            event.accept()

    def mouseReleaseEvent(self, event):
        # 실제 드래그로 크기를 바꿨을 때만 그 크기를 방향별 F 프리셋으로 기억(메모리+DB).
        if self._press is not None and self._moved:
            self._popup._commit_snap_preset()
        self._press = None
        event.accept()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QColor(_SURFACE2))
        s = self._SIZE
        # 우하단 꼭짓점 기준 대각선 3줄(크기조절 핸들 관용 표기).
        for off in (4, 10, 16):
            p.drawLine(s - off, s - 3, s - 3, s - off)
        p.end()


class TextPreviewPopup(QWidget):
    """텍스트 전체 미리보기 — 다중 창 동시 표시 지원"""

    _instances: list["TextPreviewPopup"] = []

    # 우클릭 메뉴 → main 핸들러로 전달
    copy_requested = pyqtSignal(object)   # ClipboardItem
    edit_requested = pyqtSignal(int)      # item_id
    copy_as_image_requested = pyqtSignal(object)  # QPixmap (AI 답변 전용 — 답변 전체를 이미지로)
    copy_text_requested = pyqtSignal(str)  # 선택 텍스트 (AI 답변 전용 — 선택→복사 모드)
    followup_requested = pyqtSignal(str)  # 이어서 질문 (AI 답변 전용 — 하단 입력칸 Enter)

    # ------------------------------------------------------------------
    # 클래스 메서드
    # ------------------------------------------------------------------

    @classmethod
    def open_new(cls, item: ClipboardItem, panel_geom: QRect, editable: bool = True,
                 markdown: bool = False, center: bool = False,
                 initial_turn: tuple[str, str] | None = None) -> "TextPreviewPopup":
        """새 미리보기 창을 열고 인스턴스 목록에 등록한다.

        editable=False면 우클릭 "수정" 메뉴를 숨긴다(AI 답변 등 DB에 없는 임시 항목 —
        id가 없어 수정·저장 경로가 무력하므로 메뉴 자체를 제거).
        markdown=True면 QTextEdit+setMarkdown으로 서식을 렌더링한다(AI 답변 전용 —
        일반 텍스트 미리보기는 원문 확인 용도라 평문 유지).
        center=True면 panel_geom 옆이 아니라 panel_geom이 속한 모니터 정중앙에 띄운다
        (AI 답변 전용 — _ai_anchor가 가리키는 커서 모니터 한복판).
        initial_turn=(질문, 답변)이면 첫 대화 턴으로 설정한 뒤 표시한다(AI 답변 전용 —
        show 전에 턴을 넣어 빈 화면 깜빡임·재사이즈를 피한다).
        """
        cascade_offset = len(cls._instances) * _CASCADE_STEP
        popup = cls(item, editable=editable, markdown=markdown)
        cls._instances.append(popup)
        if initial_turn is not None:
            popup._append_turn_data(initial_turn[0], initial_turn[1])
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
        # 형광펜(True) ↔ 선택→복사(False, 기본) 모드. 우상단 버튼·Shift+백틱으로 토글.
        # 답변이 처음 열릴 땐 선택→복사 모드 — 부분 발췌가 흔하고, 형광펜은 필요할 때만 켠다.
        self._highlight_mode = False
        self._manual_size = False  # 코너 그립으로 수동 리사이즈하면 자동 크기 산정 중단
        self._snapped = False      # F 키로 모니터 방향 영역에 스냅된 상태인지(토글용)
        self._pressed_anchor = ""  # 좌클릭 누른 위치의 링크 URL(드래그 없이 떼면 브라우저로 염)
        # 마크다운 요소(제목/볼드/코드/기울임) 스팬 — setMarkdown 직후 1회만 수집(아래).
        self._syntax_spans: list[tuple[int, int, str, bool]] = []
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

        # AI 답변(마크다운) 전용 — 상단 "턴 탭" 바(Q1/Q2/… 답변별 페이지). 데이터는 add_turn이
        # 채운다. 한 턴일 땐 숨기고(첫 답변은 예전 모습 그대로), 두 번째 답변부터 나타난다.
        # 목적: 대화가 이어져도 한 화면에 한 문답만 보여 스크롤이 무한정 길어지지 않게(사용자
        # 요청). 모델은 여전히 전체 대화를 인지하며, 탭은 표시만 나눈다.
        self._turns: list[tuple[str, str]] = []   # [(질문 원문, 답변 or _PENDING), ...]
        self._current_tab: int = 0
        self._think_timer: QTimer | None = None   # 펜딩 탭 "생각 중" 경과시간 갱신 타이머
        self._tab_btns: list[QPushButton] = []
        self._tabbar: QWidget | None = None
        self._tab_layout: QHBoxLayout | None = None
        if markdown:
            self._tabbar = QWidget()
            self._tabbar.setFixedHeight(34)
            self._tab_layout = QHBoxLayout(self._tabbar)
            # 우측 여백 68px = 우상단 형광펜 토글(_hl_btn) + 닫기(_close_btn) 두 버튼 자리 확보
            # (탭이 그 밑으로 안 감). 6+26+6+26+4 ≈ 68.
            self._tab_layout.setContentsMargins(4, 4, 68, 2)
            self._tab_layout.setSpacing(4)
            self._tabbar.setVisible(False)
            container_layout.addWidget(self._tabbar)

        # markdown=True → QTextEdit(서식 렌더링), 아니면 QPlainTextEdit(평문, 기존 정책).
        self._editor = QTextEdit() if markdown else QPlainTextEdit()
        self._editor.setReadOnly(True)
        # 마크다운(AI 답변)은 형광펜용 텍스트 선택 허용(좌드래그=선택→형광펜) + 링크 호버
        # 인식(LinksAccessibleByMouse — 링크 위 손가락 커서). 실제 링크 열기는 QTextEdit가
        # 자동으로 안 하므로(QTextBrowser와 달리) 클릭 위치의 anchor를 잡아 직접 연다.
        # 일반 미리보기는 선택 불가(창 전체 드래그=이동) 기존 정책 유지.
        self._editor.setTextInteractionFlags(
            (Qt.TextInteractionFlag.TextSelectableByMouse
             | Qt.TextInteractionFlag.LinksAccessibleByMouse) if markdown
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
        # 에디터가 남는 세로 공간을 차지하고 하단 입력칸은 고정 높이를 유지하도록 stretch=1.
        # (일반 미리보기는 입력칸이 없어 stretch가 무의미하므로 무해.)
        container_layout.addWidget(self._editor, 1)

        # AI 답변(마크다운) 전용 — 하단 "이어서 질문" 입력칸(웹 챗봇식 후속 대화).
        # 답변을 인지한 상태로 추가 질문을 받는다(main이 대화 히스토리를 팝업에 보관).
        self._input: QLineEdit | None = None
        if markdown:
            self._input = QLineEdit()
            self._input.setPlaceholderText("이어서 질문…  (Enter 전송)")
            # 폰트를 위젯에 명시(맑은 고딕 13px) 후 그 폰트 메트릭으로 높이를 잡는다 —
            # 스타일시트 font-size는 sizeHint에 안 반영돼, 예전엔 기본 폰트 기준 낮은 높이로
            # 고정돼 한글 디센더(ㅁ/ㅇ/질 아랫부분)가 잘렸다. +14는 상하 패딩·디센더 여유.
            _ifont = self._input.font()
            _ifont.setPixelSize(13)
            _ifont.setFamily(_MD_FONT_FAMILY)
            self._input.setFont(_ifont)
            self._input.setFixedHeight(max(32, QFontMetrics(_ifont).height() + 14))
            self._input.setStyleSheet(f"""
                QLineEdit {{
                    background: {_SURFACE0};
                    color: {_TEXT};
                    border: none;
                    border-top: 1px solid {_SURFACE2};
                    padding: 4px 8px;
                    font-size: 13px;
                }}
                QLineEdit:focus {{ border-top: 1px solid {_PEACH}; }}
                QLineEdit:disabled {{ color: {_SURFACE2}; }}
            """)
            self._input.returnPressed.connect(self._submit_followup)
            container_layout.addWidget(self._input)

        # viewport 클릭/휠 → popup이 처리 (전체 창 드래그·휠 줌)
        self._editor.viewport().installEventFilter(self)

        # AI 답변(마크다운) 전용 오버레이: 우상단 형광펜/선택 토글 버튼 + 우하단 리사이즈 그립.
        self._hl_btn: QPushButton | None = None
        self._close_btn: QPushButton | None = None
        self._grip: _ResizeGrip | None = None
        if markdown:
            self._hl_btn = QPushButton(self)
            self._hl_btn.setFixedSize(26, 26)
            self._hl_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self._hl_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self._hl_btn.setStyleSheet(f"""
                QPushButton {{
                    background: {_SURFACE0};
                    border: 1px solid {_SURFACE2};
                    border-radius: 6px;
                    font-size: 14px;
                }}
                QPushButton:hover {{ background: {_SURFACE2}; }}
            """)
            self._hl_btn.clicked.connect(self._toggle_highlight_mode)
            self._update_hl_btn()
            # 우상단 닫기(✕) 버튼 — ESC와 동일(self.close). hover 시 코랄로 '닫힘'을 강조.
            # 형광펜 토글 오른쪽(창 모서리)에 두어 통상적인 닫기 버튼 위치를 따른다.
            self._close_btn = QPushButton("✕", self)
            self._close_btn.setFixedSize(26, 26)
            self._close_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self._close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self._close_btn.setToolTip("닫기 (Esc)")
            self._close_btn.setStyleSheet(f"""
                QPushButton {{
                    background: {_SURFACE0};
                    border: 1px solid {_SURFACE2};
                    border-radius: 6px;
                    font-size: 13px;
                    color: {_TEXT};
                }}
                QPushButton:hover {{ background: {_PEACH}; border-color: {_PEACH}; color: #1a1a1a; }}
            """)
            self._close_btn.clicked.connect(self.close)
            self._grip = _ResizeGrip(self)

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
            # 창 이동: 휠(가운데)클릭은 어느 모드에서나 이동. 좌클릭은 일반 미리보기에서만
            # 이동(마크다운=AI 답변은 좌클릭=텍스트 선택). (_is_move_button 참조)
            if et == QEvent.Type.MouseButtonPress:
                if self._is_move_button(event.button()):
                    self.activateWindow()
                    self._drag_pos = (
                        event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                    )
                    return True
                if self._markdown and event.button() == Qt.MouseButton.LeftButton:
                    self.activateWindow()
                    # 누른 위치에 링크가 있으면 기억 — 드래그 없이 떼면 release에서 연다.
                    self._pressed_anchor = self._editor.anchorAt(event.position().toPoint())
                    return False  # QTextEdit가 텍스트 선택 처리하도록 통과
            elif et == QEvent.Type.MouseMove:
                if self._drag_pos is not None and event.buttons():
                    self.move(event.globalPosition().toPoint() - self._drag_pos)
                    return True
                # 링크 위에선 손모양, 그 외엔 I빔 — 뷰포트 커서를 IBeam으로 고정해 둬서
                # Qt의 자동 링크 호버 커서가 안 떠, 마우스 이동마다 직접 토글한다(드래그 중 제외).
                if self._markdown:
                    href = self._editor.anchorAt(event.position().toPoint())
                    self._editor.viewport().setCursor(
                        Qt.CursorShape.PointingHandCursor if href
                        else Qt.CursorShape.IBeamCursor)
            elif et == QEvent.Type.MouseButtonRelease:
                if self._drag_pos is not None and self._is_move_button(event.button()):
                    self._drag_pos = None
                    return True
                if self._markdown and event.button() == Qt.MouseButton.LeftButton:
                    # 선택 확정 후(QTextEdit release 처리 뒤) 형광펜 토글·해제 적용
                    QTimer.singleShot(0, self._on_left_select_release)
                    return False
        return super().eventFilter(obj, event)

    def _is_move_button(self, btn) -> bool:
        """창 이동 트리거 버튼인지 — 휠(가운데)클릭은 항상, 좌클릭은 일반 미리보기에서만."""
        if btn == Qt.MouseButton.MiddleButton:
            return True
        return btn == Qt.MouseButton.LeftButton and not self._markdown

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

    def show_preview(self, panel_geom: QRect, cascade_offset: int = 0, center: bool = False):
        if self._markdown:
            if self._turns:
                self._render_current_turn()  # 현재 턴만 렌더(탭)
            else:
                self._render_markdown(self._item.text_content or self._item.preview_text or "")
        else:
            text = self._item.text_content or self._item.preview_text or ""
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

    def _render_markdown(self, text: str):
        """마크다운 본문을 에디터에 렌더(따옴표 볼드 정상화 → setMarkdown → 줄간격/불릿 →
        요소색 스팬 수집 → 형광펜). show_preview와 후속 대화 갱신(update_answer)이 공유한다.

        코드 색칠이 fontFixedPitch를 끄므로(폰트 통일) 재탐지가 깨진다 → 변형 전에
        1회만 수집해 두고 _apply_marks가 그 위치로 재적용한다(형광펜 시 색 증발 방지).
        """
        text = _fix_markdown_emphasis(text)  # 따옴표 볼드 정상화
        self._raw_text = text
        self._editor.setMarkdown(text)
        self._apply_block_spacing(self._editor.document())  # 줄간격·문단 여백
        self._set_list_bullets(self._editor.document())     # ○/ㅇ → •
        self._syntax_spans = self._collect_syntax_spans()
        self._apply_marks()  # 모델 색 + 형광펜

    # ------------------------------------------------------------------
    # 이어서 질문(AI 답변 전용) — 하단 입력칸 + 대화 갱신
    # ------------------------------------------------------------------

    def _submit_followup(self):
        """하단 입력칸 Enter — 새 펜딩 탭을 즉시 띄우고(질문+생각중 애니메이션) 질문을 main에
        전달한다.

        엔터 즉시 새 탭(Q_n+1)을 만들어 그 자리에 "🤔 AI가 생각하고 있어요…"를 보여줘,
        답이 올 때까지 '멈춘 게 아니라 일하는 중'임을 확실히 알린다(사용자 요청). 실제 대화
        히스토리·워커 호출은 main이 처리한다(followup_requested → 재질의 → resolve_pending).
        """
        if self._input is None:
            return
        text = self._input.text().strip()
        if not text:
            return
        self._input.clear()
        self.begin_followup(text)
        self.followup_requested.emit(text)

    def set_thinking(self, thinking: bool):
        """후속 질의 진행 표시 — 입력칸을 비활성화하고 placeholder를 전환한다."""
        if self._input is None:
            return
        self._input.setEnabled(not thinking)
        self._input.setPlaceholderText(
            "AI 생각 중…" if thinking else "이어서 질문…  (Enter 전송)")

    # ------------------------------------------------------------------
    # 펜딩 탭 — 후속 질문 엔터 즉시 "생각 중" 표시 (답 도착 시 실제 답변으로 교체)
    # ------------------------------------------------------------------

    def begin_followup(self, question: str):
        """새 펜딩 탭을 추가해 "생각 중" 애니메이션을 시작한다(답변은 아직 없음).

        답변 자리를 sentinel(_PENDING)로 둔 턴을 하나 넣고 최신 탭으로 전환한다. 창 크기는
        재산정하지 않아(이전 답변 크기 유지) 답 도착 전까지 깜빡임이 없다.
        """
        import time
        self._append_turn_data(question, _PENDING)
        self.set_thinking(True)
        self._pending_start = time.monotonic()
        self._render_current_turn()  # 크기 재산정 없이 본문만 "생각 중"으로
        self._editor.verticalScrollBar().setValue(0)
        if self._think_timer is None:
            self._think_timer = QTimer(self)
            self._think_timer.setInterval(500)
            self._think_timer.timeout.connect(self._tick_pending)
        self._think_timer.start()

    def _tick_pending(self):
        """펜딩 탭 본문의 경과시간·점 애니메이션을 0.5초마다 갱신(가벼운 setMarkdown)."""
        if not (self._turns and self._turns[self._current_tab][1] is _PENDING):
            return
        self._render_current_turn()  # 경과시간(초) 갱신만 — 점 애니메이션은 제거됨

    def _stop_think_timer(self):
        if self._think_timer is not None:
            self._think_timer.stop()

    def resolve_pending(self, answer: str):
        """펜딩 탭을 실제 답변으로 교체하고 렌더·크기 재산정한다(add_turn의 완료 절반).

        후속 질의가 정상 종료되면 main이 호출. 형광펜은 턴이 바뀌었으므로 초기화한다.
        """
        self._stop_think_timer()
        if self._turns and self._turns[self._current_tab][1] is _PENDING:
            q = self._turns[self._current_tab][0]
            self._turns[self._current_tab] = (q, answer)
        self._marks = []
        self._render_current_turn()
        self.set_thinking(False)
        if not self._manual_size:
            self._resize_to_content()
        self._editor.verticalScrollBar().setValue(0)  # 새 답변은 상단부터

    def cancel_pending(self):
        """펜딩 탭을 제거하고 직전 탭으로 복귀한다(후속 질의 에러 시 main이 호출)."""
        self._stop_think_timer()
        if not (self._turns and self._turns[self._current_tab][1] is _PENDING):
            self.set_thinking(False)
            return
        self._turns.pop()
        self._current_tab = max(0, len(self._turns) - 1)
        self._rebuild_tabs()
        self._marks = []
        self._render_current_turn()
        self.set_thinking(False)
        if not self._manual_size:
            self._resize_to_content()

    def _append_turn_data(self, question: str, answer: str):
        """대화 턴 데이터 추가 + 탭 바 갱신(현재 탭=최신). 렌더는 호출자가 한다."""
        self._turns.append((question, answer))
        self._current_tab = len(self._turns) - 1
        self._rebuild_tabs()

    def add_turn(self, question: str, answer: str):
        """새 대화 턴(후속 질문 응답) 추가 — 최신 탭으로 전환해 렌더하고 상단부터 보여준다.

        각 탭은 그 턴의 문답 한 쌍만 표시하므로 스크롤이 무한정 길어지지 않는다(사용자 요청).
        형광펜(_marks)은 문서 position이 바뀌므로 초기화한다.
        """
        self._append_turn_data(question, answer)
        self._marks = []
        self._render_current_turn()
        self.set_thinking(False)
        if not self._manual_size:
            self._resize_to_content()
        self._editor.verticalScrollBar().setValue(0)  # 새 답변은 상단부터

    def _render_current_turn(self):
        """현재 탭의 문답을 마크다운으로 렌더. 우클릭 복사·이미지화가 이 턴을 담도록
        _item.text_content도 현재 턴으로 맞춘다(보고 있는 것 = 복사되는 것).

        펜딩 턴(답변 미도착)이면 "생각 중" 애니메이션을 렌더하고 _item은 건드리지 않는다
        (복사 시 '생각 중' 문구가 딸려가지 않게)."""
        import time
        q, a = self._turns[self._current_tab]
        if a is _PENDING:
            elapsed = int(time.monotonic() - getattr(self, "_pending_start", time.monotonic()))
            m, s = divmod(elapsed, 60)
            # 점(●··) 애니메이션 제거 — 폭 변화 노이즈 없이 경과시간만으로 "생각 중"을 표시.
            text = (f"**Q.** {q}\n\n---\n\n"
                    f"🤔 AI가 생각하고 있어요…  ({m}:{s:02d})")
            self._render_markdown(text)
            return
        text = f"**Q.** {q}\n\n---\n\n{a}"
        self._item.text_content = text
        self._render_markdown(text)

    def _select_tab(self, idx: int):
        """탭 클릭 → 해당 턴으로 전환(상단부터 표시). 형광펜은 턴마다 초기화."""
        if idx == self._current_tab or not (0 <= idx < len(self._turns)):
            return
        self._current_tab = idx
        self._marks = []
        self._render_current_turn()
        self._update_tab_styles()
        # 펜딩 탭(생각 중)은 본문이 짧아 재산정하면 창이 확 줄어든다 → 이전 크기 유지
        # (begin_followup과 동일 정책). 완성된 탭으로 갈 때만 그 내용에 맞춰 재산정.
        if not self._manual_size and self._turns[idx][1] is not _PENDING:
            self._resize_to_content()
        self._editor.verticalScrollBar().setValue(0)

    def _rebuild_tabs(self):
        """턴 수에 맞춰 탭 버튼(Q1/Q2/…)을 다시 만든다. 한 턴이면 탭 바를 숨긴다."""
        if self._tabbar is None:
            return
        lay = self._tab_layout
        while lay.count():
            it = lay.takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None)
        self._tab_btns = []
        for i in range(len(self._turns)):
            b = QPushButton(f"Q{i + 1}")
            b.setFixedHeight(24)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.clicked.connect(lambda _=False, idx=i: self._select_tab(idx))
            lay.addWidget(b)
            self._tab_btns.append(b)
        lay.addStretch(1)
        self._update_tab_styles()
        # 한 턴이면 숨김(첫 답변은 예전 모습). 두 번째부터 탭 노출.
        self._tabbar.setVisible(len(self._turns) > 1)

    def _update_tab_styles(self):
        """현재 탭=코랄 강조, 나머지=중립. (앱 2톤 체계와 동일)"""
        for i, b in enumerate(self._tab_btns):
            if i == self._current_tab:
                b.setStyleSheet(
                    f"QPushButton{{background:{_PEACH};color:{_BG};border:none;"
                    f"border-radius:5px;padding:2px 10px;font-size:12px;font-weight:bold;}}")
            else:
                b.setStyleSheet(
                    f"QPushButton{{background:{_SURFACE0};color:{_TEXT};"
                    f"border:1px solid {_SURFACE2};border-radius:5px;padding:2px 10px;"
                    f"font-size:12px;}}QPushButton:hover{{background:{_SURFACE2};}}")

    def _top_reserve(self) -> int:
        """상단 턴 탭 바가 차지하는 높이(두 턴 이상일 때만 — 자동 크기 산정에서 예약)."""
        tb = getattr(self, "_tabbar", None)
        if tb is not None and len(self._turns) > 1:
            return tb.height()
        return 0

    def _bottom_reserve(self) -> int:
        """하단 입력칸이 차지하는 높이(자동 크기 산정·그립 배치에서 예약)."""
        inp = getattr(self, "_input", None)
        return inp.height() if inp is not None else 0

    # ------------------------------------------------------------------
    # 우클릭 메뉴 — 전체 복사 / 수정 / 닫기 (패널 메뉴와 동일 명칭·순서)
    # ------------------------------------------------------------------

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        menu.setStyleSheet(_dark_menu_style())

        copy_action = menu.addAction("전체 복사")
        copy_action.triggered.connect(self._emit_copy)

        # AI 답변(마크다운)만 — 답변 전체(스크롤 포함)를 이미지로 복사. 일반 미리보기는
        # 원문 확인 용도라 이미지화 수요가 없어 노출하지 않는다.
        if self._markdown:
            img_action = menu.addAction("이미지로 복사")
            img_action.triggered.connect(self._emit_copy_as_image)

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
            return
        # Shift+백틱 → 형광펜/선택복사 모드 토글 (마크다운=AI 답변 전용).
        # Shift+` 는 키보드 레이아웃에 따라 ~ (AsciiTilde) 또는 ` (QuoteLeft)로 들어온다.
        if (self._markdown
                and (event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
                and event.key() in (Qt.Key.Key_QuoteLeft, Qt.Key.Key_AsciiTilde)):
            self._toggle_highlight_mode()
            return
        # Ctrl+C → 현재 선택 텍스트 복사(선택→복사 모드에서 드래그로 선택한 부분 발췌).
        # 에디터는 NoFocus라 키 이벤트가 팝업으로 오므로 여기서 직접 처리한다.
        if ((event.modifiers() & Qt.KeyboardModifier.ControlModifier)
                and event.key() == Qt.Key.Key_C):
            cur = self._editor.textCursor()
            if cur.hasSelection():
                txt = cur.selectedText().replace(chr(0x2029), chr(10))
                if txt.strip():
                    self.copy_text_requested.emit(txt)
            return
        # F → 현재 모니터 방향에 맞춘 중앙 영역으로 스냅 ↔ 내용맞춤 크기 복귀 토글(AI 답변 전용).
        if (self._markdown
                and not (event.modifiers() & Qt.KeyboardModifier.ControlModifier)
                and event.key() == Qt.Key.Key_F):
            self._toggle_snap_zone()
            return
        super().keyPressEvent(event)

    # ------------------------------------------------------------------
    # 활성화 외곽선 — 비활성 시 주황(상시), 활성 시 파랑
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

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._position_overlays()

    def _position_overlays(self):
        """우상단 토글 버튼·우하단 리사이즈 그립을 현재 창 크기에 맞춰 재배치(마크다운 전용)."""
        m = 6  # 컨테이너 테두리(2px) 바깥쪽 여백
        gap = 6  # 두 버튼 사이 간격
        btn = getattr(self, "_hl_btn", None)  # __init__ 중 이른 resize 대비 가드
        close = getattr(self, "_close_btn", None)
        grip = getattr(self, "_grip", None)
        # 닫기(✕)는 창 모서리(맨 오른쪽), 형광펜 토글은 그 왼쪽에 배치.
        if close is not None:
            close.move(self.width() - close.width() - m, m)
            close.raise_()
        if btn is not None:
            close_w = close.width() + gap if close is not None else 0
            btn.move(self.width() - btn.width() - m - close_w, m)
            btn.raise_()
        if grip is not None:
            # 하단 "이어서 질문" 입력칸이 있으면 그 위에, 없으면 우하단 꼭짓점에 flush.
            grip_y = self.height() - grip.height() - self._bottom_reserve()
            grip.move(self.width() - grip.width(), grip_y)
            grip.raise_()

    def _toggle_snap_zone(self):
        """F 키 — 현재 창이 올라가 있는 모니터의 방향(가로/세로)에 맞춘 중앙 영역으로
        스냅한다. 다시 누르면 내용맞춤(자동) 크기로 복귀(FancyZones식 토글).

        크기: 사용자가 그립으로 맞춰 저장한 방향별 프리셋(_SNAP_PRESETS)이 있으면 그 크기,
        없으면 기본 비율(가로 = 폭 80%×높이 90% / 세로 = 폭 90%×높이 40%).
        스냅 시 wrap을 강제 on해 새 폭에 맞춰 내용이 재배치되고, 길면 세로 스크롤로 본다.
        """
        if self._snapped:
            # 복귀: 자동 크기 산정 재개 → 내용맞춤 크기로
            self._snapped = False
            self._manual_size = False
            self._resize_to_content()
            self._clamp_to_screen(self._current_avail())
            return
        avail = self._current_avail()
        orient = self._orientation_of(avail)
        preset = _SNAP_PRESETS.get(orient)
        if preset:  # 그립으로 저장해 둔 사용자 크기(현재 모니터를 못 넘게 clamp)
            w = min(preset[0], avail.width())
            h = min(preset[1], avail.height())
        elif orient == "landscape":
            w = round(avail.width() * 0.80)
            h = round(avail.height() * 0.90)
        else:
            w = round(avail.width() * 0.90)
            h = round(avail.height() * 0.40)
        x = avail.left() + (avail.width() - w) // 2
        y = avail.top() + (avail.height() - h) // 2
        self._manual_size = True   # 스냅 크기를 줌·재표시가 덮어쓰지 않게
        self._snapped = True
        self._set_line_wrap(True)  # 새 폭에 맞춰 내용 재배치(가로 잘림 방지)
        self.resize(w, h)
        self.move(x, y)

    def _current_avail(self):
        """현재 창이 속한 모니터의 작업 영역(availableGeometry)."""
        screen = (self.screen()
                  or QApplication.screenAt(self.geometry().center())
                  or QApplication.primaryScreen())
        return screen.availableGeometry()

    @staticmethod
    def _orientation_of(avail) -> str:
        return "landscape" if avail.width() >= avail.height() else "portrait"

    def _commit_snap_preset(self):
        """그립 리사이즈 완료 시 — 현재 크기를 현재 모니터 방향의 F 프리셋으로 저장(메모리+DB).

        이후 그 방향 모니터에서 F를 누르면 기본 비율 대신 이 크기로 스냅된다.
        영속화 콜백은 main이 configure_snap_presets로 주입(없으면 메모리에만 — 세션 한정)."""
        avail = self._current_avail()
        orient = self._orientation_of(avail)
        w, h = self.width(), self.height()
        _SNAP_PRESETS[orient] = (w, h)
        if _SNAP_PERSIST is not None:
            try:
                _SNAP_PERSIST(orient, w, h)
            except Exception:
                pass

    def _apply_manual_resize(self, w: int, h: int):
        """코너 그립 드래그 → 자유 리사이즈. 이후 자동 크기 산정은 중단하고, 폭에 맞춰
        내용이 재배치되도록 wrap을 강제 on(좁히면 가로 잘림 방지)."""
        self._manual_size = True
        self._snapped = False  # 수동 조절하면 스냅 토글 상태 해제(다음 F는 스냅으로)
        self._set_line_wrap(True)
        screen = (self.screen()
                  or QApplication.screenAt(self.geometry().center())
                  or QApplication.primaryScreen())
        avail = screen.availableGeometry()
        w = max(220, min(w, avail.width()))
        h = max(140, min(h, avail.height()))
        self.resize(w, h)

    # ------------------------------------------------------------------
    # 생명주기 — _instances 목록 정리
    # ------------------------------------------------------------------

    def closeEvent(self, event):
        self._stop_think_timer()  # 펜딩 애니메이션 타이머 정리(누수 방지)
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
        # 폰트 패밀리·굵기·힌팅을 경로별로 나눈다.
        # · 일반 미리보기(맑은 고딕, 12px): PreferFullHinting으로 픽셀 그리드에 스냅 →
        #   작은 크기에서 획을 또렷하게(Qt 기본 CJK 힌팅은 약해 흐려짐).
        # · AI 답변(Noto Sans KR, 16px): 본문을 Medium(500)으로 살짝 도톰하게 하고(레귤러
        #   400은 Gemini 대비 얇게 보임), 하드 힌팅을 해제(PreferNoHinting)해 획이 픽셀에
        #   스냅되며 얇아지는 것을 막는다 → 브라우저처럼 획이 제 굵기를 유지해 부드럽고 도톰.
        #   볼드(제목·**굵게**)는 setMarkdown이 700을 주므로 500 본문과 대비가 유지된다.
        if self._markdown:
            font.setFamily(_MD_FONT_FAMILY)
            font.setWeight(QFont.Weight.Medium)
            font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
        else:
            font.setFamily(_FONT_FAMILY)
            font.setWeight(QFont.Weight.Normal)
            font.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
        self._editor.setFont(font)
        # 폰트 변경 시 document margin도 재설정 (Qt가 setFont에서 reset 하는 경우 대비)
        self._editor.document().setDocumentMargin(0)

    def _collect_syntax_spans(self) -> list[tuple[int, int, str, bool]]:
        """setMarkdown이 남긴 서식(제목/굵게/코드/기울임) 스팬을 수집한다.

        반드시 **서식 변형 전**(setMarkdown 직후)에 1회 호출해야 한다. 코드 색칠 시
        fontFixedPitch를 끄기 때문에, 매번 재탐지하면 두 번째부터 코드를 못 찾는다.
        수집 결과(위치·길이·색·밑줄여부)는 _syntax_spans에 저장돼 _apply_marks가
        형광펜 토글마다 같은 위치로 재적용한다(텍스트 불변이라 position 안정).
        """
        doc = self._editor.document()
        spans: list[tuple[int, int, str, bool]] = []
        blk = doc.begin()
        while blk.isValid():
            heading = blk.blockFormat().headingLevel()
            it = blk.begin()
            while not it.atEnd():
                frag = it.fragment()
                if frag.isValid():
                    cf = frag.charFormat()
                    underline = False
                    if heading > 0:
                        color = _MD_HEADING
                    elif cf.isAnchor() and cf.anchorHref():
                        color = _MD_LINK
                        underline = True  # 하이퍼링크 = 파랑 + 밑줄
                    elif cf.fontFixedPitch():
                        color = _MD_CODE
                        underline = True  # 백틱/코드 = 밑줄 + 볼드(_apply_marks에서)
                    elif cf.fontWeight() >= 700:
                        color = _MD_BOLD
                    elif cf.fontItalic():
                        color = _MD_ITALIC
                    else:
                        color = None
                    if color:
                        spans.append((frag.position(), frag.length(), color, underline))
                it += 1
            blk = blk.next()
        return spans

    # ------------------------------------------------------------------
    # 형광펜(하이라이트) — 좌드래그 선택 → 배경색 토글. 클릭=해당 마크 해제.
    # 데이터는 문서 position 범위로 보관, 복사 시 백틱으로 직렬화(prior art 패턴).
    # ------------------------------------------------------------------

    def _on_left_select_release(self):
        """좌클릭 릴리스 — 모드에 따라 갈린다.

        형광펜 모드: 선택이 있으면 그 범위 형광펜 토글, 없으면(클릭) 마크 해제.
        선택→복사 모드: 선택 텍스트를 클립보드로 복사(선택 표시는 유지해 무엇을 복사했는지
        시각 확인). 형광펜은 건드리지 않는다.
        """
        # 링크 클릭: 누른 곳에 링크가 있고 드래그(선택)가 없으면 기본 브라우저로 연다.
        # 두 모드(형광펜/선택→복사) 공통으로 먼저 처리한다. 드래그로 선택했으면 링크로
        # 안 보고 모드별 로직(선택·형광펜)으로 넘긴다.
        anchor = self._pressed_anchor
        self._pressed_anchor = ""
        if anchor and not self._editor.textCursor().hasSelection():
            webbrowser.open(anchor)
            return

        # 선택→복사 모드: 드래그는 일반 텍스트 선택만 한다(자동복사 없음). 선택 후
        # Ctrl+C로 복사(keyPressEvent에서 처리). 형광펜은 건드리지 않는다.
        if not self._highlight_mode:
            return
        cur = self._editor.textCursor()
        if cur.hasSelection():
            s, e = cur.selectionStart(), cur.selectionEnd()
            cur.clearSelection()
            self._editor.setTextCursor(cur)
            self._toggle_mark(s, e)
        else:
            self._remove_mark_at(cur.position())

    def _toggle_highlight_mode(self):
        """형광펜 ↔ 선택→복사 모드 전환 (우상단 버튼·Shift+백틱).

        선택→복사 모드에서 텍스트를 먼저 드래그 선택해 둔 뒤 형광펜 모드로 진입하면,
        그 선택 범위에 즉시 형광펜을 적용한다(드래그→모드전환 순서도 자연스럽게 동작).
        버튼·Shift+백틱 모두 에디터 선택을 건드리지 않으므로 선택이 그대로 살아 있다.
        """
        self._highlight_mode = not self._highlight_mode
        self._update_hl_btn()
        if self._highlight_mode:
            cur = self._editor.textCursor()
            if cur.hasSelection():
                s, e = cur.selectionStart(), cur.selectionEnd()
                cur.clearSelection()
                self._editor.setTextCursor(cur)
                self._toggle_mark(s, e)

    def _update_hl_btn(self):
        if self._hl_btn is None:
            return
        if self._highlight_mode:
            self._hl_btn.setText("🖍")
            self._hl_btn.setToolTip("형광펜 모드 — 드래그로 강조 (클릭/Shift+` 로 선택·복사 모드 전환)")
        else:
            self._hl_btn.setText("✂")
            self._hl_btn.setToolTip("선택→복사 모드 — 드래그로 선택하면 바로 복사 (클릭/Shift+` 로 형광펜 모드 전환)")

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
        base.setFontUnderline(False)  # 이전 밑줄 제거 — 마크다운 색/형광펜이 다시 입힘
        whole.mergeCharFormat(base)

        # 마크다운 요소 색 재적용 (수집은 setMarkdown 직후 1회 — _syntax_spans).
        for pos, length, color, underline in self._syntax_spans:
            cur = QTextCursor(doc)
            cur.setPosition(pos)
            cur.setPosition(pos + length, QTextCursor.MoveMode.KeepAnchor)
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(color))
            if underline:  # 코드(백틱)·링크 공통 = 색 + 밑줄
                fmt.setFontUnderline(True)
                fmt.setUnderlineColor(QColor(color))
                if color == _MD_CODE:  # 코드만 볼드 + 본문폰트(모노스페이스 튐 방지). 링크는 비볼드.
                    fmt.setFontFixedPitch(False)
                    fmt.setFontFamily(_MD_FONT_FAMILY)
                    fmt.setFontWeight(QFont.Weight.Bold)
            cur.mergeCharFormat(fmt)

        for s, e in self._marks:
            c = QTextCursor(doc)
            c.setPosition(s)
            c.setPosition(e, QTextCursor.MoveMode.KeepAnchor)
            hf = QTextCharFormat()
            hf.setBackground(_HL_BG)
            hf.setForeground(_HL_FG)
            hf.setFontUnderline(True)        # 사용자 형광펜 = 빨강 밑줄
            hf.setUnderlineColor(_HL_FG)
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

    def _emit_copy_as_image(self):
        """답변 전체를 한 장의 이미지로 렌더 → main이 클립보드(DIB)+히스토리에 저장."""
        try:
            pix = self._render_answer_pixmap()
        except Exception:
            return
        if pix is not None and not pix.isNull():
            self.copy_as_image_requested.emit(pix)

    def _render_answer_pixmap(self) -> QPixmap:
        """현재 답변 문서 전체(스크롤로 가려진 부분 포함)를 한 장의 픽맵으로 렌더.

        살아있는 에디터 문서를 clone해 현재 표시 폭으로 줄바꿈을 고정한 뒤 단독 렌더한다
        (clone은 형광펜·요소색 등 char 포맷을 모두 보존). 본문 기본 글자색은 QSS로만
        지정돼 있어 단독 렌더 시 검게 나오므로, PaintContext의 palette Text 색에 본문색
        (_TEXT)을 주입한다 — 명시 char 포맷(제목·볼드·코드·형광펜)은 그대로 우선 적용된다.
        배경(_BG)+여백을 입혀 '팝업 스샷'처럼 보이게 한다. HiDPI는 devicePixelRatio로 보정.
        """
        src = self._editor.document()
        doc = src.clone(self)
        text_w = src.textWidth()
        if text_w <= 0:
            text_w = float(self._editor.viewport().width())
        doc.setTextWidth(text_w)
        doc.setDocumentMargin(0)

        size = doc.size()  # QSizeF
        pad = 18
        dpr = self.devicePixelRatioF() or 1.0
        w = int(math.ceil(size.width() + pad * 2))
        h = int(math.ceil(size.height() + pad * 2))
        img = QImage(int(w * dpr), int(h * dpr), QImage.Format.Format_ARGB32_Premultiplied)
        img.setDevicePixelRatio(dpr)
        img.fill(QColor(_BG))

        painter = QPainter(img)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
            painter.translate(pad, pad)
            ctx = QAbstractTextDocumentLayout.PaintContext()
            pal = ctx.palette
            pal.setColor(QPalette.ColorRole.Text, QColor(_TEXT))
            ctx.palette = pal
            ctx.clip = QRectF(0, 0, size.width(), size.height())
            doc.documentLayout().draw(painter, ctx)
        finally:
            painter.end()
        return QPixmap.fromImage(img)

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

        단, 코너 그립으로 수동 리사이즈한 뒤에는(_manual_size) 자동 산정을 건너뛴다 —
        사용자가 정한 크기를 줌·재표시가 덮어쓰지 않게 한다(내용은 wrap+스크롤로 흡수).
        """
        if self._manual_size:
            return
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
        # 상단 턴 탭 바 + 하단 "이어서 질문" 입력칸(마크다운=AI 답변 전용) 높이도 예약해
        # 에디터(본문)가 눌리지 않게 한다.
        full_h = round(text_h) + _CHROME_H + self._top_reserve() + self._bottom_reserve()
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
