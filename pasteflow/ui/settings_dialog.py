"""설정 다이얼로그 — F10

단축키 커스터마이징, 히스토리 제한, 자동 시작, 자동 닫기 설정.
"""
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QSpinBox, QCheckBox, QGroupBox, QFormLayout, QGridLayout, QComboBox, QLineEdit,
    QStyle, QStyledItemDelegate, QFileDialog, QScrollArea, QWidget, QFrame, QApplication,
    QPlainTextEdit, QInputDialog, QTabWidget,
)
from PyQt6.QtCore import Qt, pyqtSignal, QEvent, QSize, QPoint
from PyQt6.QtGui import QColor, QFontMetrics

from pasteflow import ai_palette
from pasteflow.ui.theme import COLORS, PEACH_HOVER, check_icon_url, chevron_icon_url


# 모델 콤보에서 "이 행은 계열 헤더" 표시용 커스텀 role. 들여쓰기 델리게이트가 읽는다.
_HEADER_ROLE = Qt.ItemDataRole.UserRole + 1

# 헤더 아래 모델명을 들여쓸 픽셀. 계열(상위)과 모델(하위)의 상하 관계를 눈에 보이게.
_MODEL_INDENT_PX = 16


class _ModelIndentDelegate(QStyledItemDelegate):
    """모델 행만 오른쪽으로 들여쓴다 (헤더는 제자리).

    **텍스트에 공백·불릿을 넣어 들여쓰면 안 된다** — 이 콤보는 `setEditable(True)`라
    표시 텍스트가 곧 저장되는 모델명이고(`_on_save`가 `currentText()`를 그대로 씀),
    앞에 붙인 공백이 API 모델명을 오염시킨다. 그래서 *그리기*만 옮긴다.

    아이콘(투명 16px)으로 들여쓰는 대안도 있으나, 그러면 **닫힌 콤보의 현재 모델명 앞에도**
    빈 아이콘 자리가 생겨 텍스트가 밀린다(팝업 view에만 거는 델리게이트는 그렇지 않다).
    """

    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        if not index.data(_HEADER_ROLE):
            option.rect.setLeft(option.rect.left() + _MODEL_INDENT_PX)


class _DragHandle(QLabel):
    """행 순서를 바꾸는 손잡이 — 누른 채 위아래로 끌면 소유 행이 드래그 신호를 낸다.

    QDrag(OLE D&D)는 쓰지 않는다(패널 드래그와 같은 이유 회피 — 이 앱은 창 안 재배치에
    항상 수동 마우스 추적 방식을 쓴다, panel.py의 fake drag와 동일 계열). 눌린 동안의
    후속 mouseMove/mouseRelease는 Qt의 암묵적 그랩으로 이 위젯이 계속 받는다.
    """

    def __init__(self, row: "_PaletteSiteRow"):
        super().__init__("⠿")
        self._row = row
        self.setFixedWidth(20)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setStyleSheet(f"color: {_TXT}; font-size: 14px;")
        self.setToolTip("드래그해서 순서 바꾸기")

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self._row.drag_started.emit(self._row)
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.MouseButton.LeftButton:
            self._row.drag_moved.emit(self._row, int(e.globalPosition().y()))
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self._row.drag_ended.emit(self._row)
        super().mouseReleaseEvent(e)


class _PaletteSiteRow(QWidget):
    """AI 팔레트(Alt+` 자유질문창) 타겟 한 줄 — 번호·드래그손잡이·라벨·키워드·종류·URL·삭제.

    순서가 곧 팔레트의 번호(Alt+1~9)이므로 드래그 손잡이로 순서를 바꿀 수 있다(번호는
    그 순서를 즉시 반영해 표시 — `set_number`). 종류가 `url`이 아니면(구글 AI·드라이브·
    내부 API 답변) 그 kind는 main.py의 기존 배관을 그대로 타므로 URL 칸이 의미가 없어
    비활성화한다.
    """

    remove_requested = pyqtSignal(object)   # self
    drag_started = pyqtSignal(object)       # self
    drag_moved = pyqtSignal(object, int)    # self, global_y
    drag_ended = pyqtSignal(object)         # self

    def __init__(self, site: dict, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(2)

        # 위 줄: 손잡이(맨 왼쪽 — 재정렬 그립의 통상 위치)·번호·라벨·키워드·종류·삭제.
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(4)

        self.drag_handle = _DragHandle(self)

        self.number_label = QLabel("")
        self.number_label.setFixedWidth(20)
        self.number_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.number_label.setStyleSheet(f"color: {_TITLE}; font-size: 11px;")
        self.number_label.setToolTip("Alt+숫자로 이 타겟에 바로 보냅니다")

        # ⚠ DIALOG_STYLE(전역)엔 :disabled 규칙이 없다 — Qt 기본 비활성 렌더는 이 다크
        # 테마에서 글자·배경이 거의 같은 톤이 돼(2026-07-29 사용자 보고: "맨 앞 위치조정
        # 버튼 색이 겹쳐서 안 보임") URL 칸이 상시 비활성인 이 위젯에서 특히 두드러진다.
        # 위젯 자체에 :disabled까지 포함한 스타일을 직접 줘야 한다(ai_query.py의
        # `QLabel:disabled`/`QComboBox:disabled` 처리와 같은 이유 — "스타일시트로 색을
        # 명시한 위젯은 Qt 기본 회색화가 안 먹는다").
        self.label_edit = QLineEdit(site.get("label", ""))
        self.label_edit.setPlaceholderText("표시 이름")
        self.label_edit.setFixedWidth(90)

        self.keyword_edit = QLineEdit(site.get("keyword", ""))
        self.keyword_edit.setPlaceholderText("키워드")
        self.keyword_edit.setToolTip(
            "이 글자 뒤에 공백을 붙여 입력하면 자동으로 이 타겟이 선택됩니다\n"
            "(예: \"yt 고양이\" → 유튜브로 \"고양이\" 검색). 비워 두면 접두어 없음.")
        self.keyword_edit.setFixedWidth(48)

        self.kind_combo = QComboBox()
        # 고정폭 — 헤더 라벨과도 정확히 맞아야 하고, 행마다 콤보가 자기 텍스트 길이로만
        # 자라면 행끼리 열이 안 맞아 지그재그로 보인다("PasteFlow 답변(API)"가 가장 길다).
        self.kind_combo.setFixedWidth(150)
        for kind, kind_label in ai_palette.KIND_LABELS.items():
            self.kind_combo.addItem(kind_label, kind)
        idx = self.kind_combo.findData(site.get("kind", ai_palette.KIND_URL))
        self.kind_combo.setCurrentIndex(max(0, idx))
        self.kind_combo.currentIndexChanged.connect(self._on_kind_changed)

        # ✕ 삭제 버튼 — 전역 QPushButton 기본 스타일의 padding(6px 16px = 가로 32px)이
        # setFixedWidth(28)보다 커서 글자가 그려질 내부 폭이 음수가 돼 "✕"가 전혀 안
        # 보였다(2026-07-29 사용자 보고). 이 버튼만 패딩을 좁힌 전용 스타일로 덮는다.
        self.remove_btn = QPushButton("✕")
        self.remove_btn.setFixedWidth(28)
        self.remove_btn.setToolTip("이 타겟 삭제")
        self.remove_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.remove_btn.setStyleSheet(
            f"QPushButton {{ background-color: {_BTN}; color: {_TXT}; border: none; "
            f"border-radius: 6px; padding: 4px 2px; }}"
            f"QPushButton:hover {{ background-color: {_BTN_HOVER}; }}"
        )
        self.remove_btn.clicked.connect(lambda: self.remove_requested.emit(self))

        top.addWidget(self.drag_handle)
        top.addWidget(self.number_label)
        top.addWidget(self.label_edit)
        top.addWidget(self.keyword_edit)
        top.addWidget(self.kind_combo)
        top.addStretch(1)
        top.addWidget(self.remove_btn)
        outer.addLayout(top)

        # 아래 줄: URL — 한 줄에 다 욱여넣으면 너무 잘려서 따로 한 줄 전체 폭을 준다.
        url_row = QHBoxLayout()
        url_row.setContentsMargins(20 + 4 + 20 + 4, 0, 0, 0)  # 손잡이+번호 폭만큼 들여써 위 라벨과 시작점을 맞춤
        url_row.setSpacing(4)
        self.url_edit = QLineEdit(site.get("url", ""))
        self.url_edit.setPlaceholderText("https://example.com/search?q={q}")
        # 같은 이유(:disabled 미정의) — url 아닌 종류(구글 AI·드라이브·API, 기본 6개 중 3개)는
        # 이 칸이 상시 비활성이라 안내 문구("(내장 — main.py가 처리)")가 안 보이면 그 자체로
        # "왜 URL 칸이 비었지"가 되므로 대비를 명시적으로 준다.
        self.url_edit.setStyleSheet(
            f"QLineEdit {{ background-color: {_INSET}; color: {_TXT}; "
            f"border: 1px solid {_LINE}; border-radius: 5px; padding: 5px 8px; }}"
            f"QLineEdit:focus {{ border-color: {COLORS['peach']}; }}"
            f"QLineEdit:disabled {{ background-color: {_PAGE}; color: #6a6a6a; "
            f"border: 1px solid {_LINE}; }}"
        )
        url_row.addWidget(self.url_edit, 1)
        outer.addLayout(url_row)

        self._on_kind_changed()

    def _on_kind_changed(self, *_args):
        is_url = self.kind_combo.currentData() == ai_palette.KIND_URL
        self.url_edit.setEnabled(is_url)
        self.url_edit.setPlaceholderText(
            "https://example.com/search?q={q}" if is_url else "(내장 — main.py가 처리)")

    def set_number(self, n: int):
        """드래그·추가·삭제로 순서가 바뀔 때마다 표시 번호(=Alt+숫자)를 갱신."""
        self.number_label.setText(str(n))

    def to_dict(self) -> dict:
        return {
            "label": self.label_edit.text().strip() or "이름 없음",
            "keyword": self.keyword_edit.text().strip(),
            "kind": self.kind_combo.currentData(),
            "url": self.url_edit.text().strip(),
        }


# 프로브 상태 → 상태 줄에 쓸 (말머리, 색). `retry`(429·503)는 판정 불가라 경고색이며,
# `fail`(빨강)과 절대 같은 색을 쓰지 않는다 — 섞으면 멀쩡한 모델을 나쁜 모델로 오해한다.
_PROBE_STYLE = {
    "ok":    ("✓", COLORS['green']),
    "weak":  ("⚠", COLORS['peach']),
    "fail":  ("✗", COLORS['red']),
    "retry": ("⏳", COLORS['peach']),
    "run":   ("", COLORS['subtext0']),
    "skip":  ("—", COLORS['subtext0']),
}


# ── 옵션창 전용 팔레트 (전역 다크 테마와 분리 — 폼 가독성·정돈 우선) ──────────────
# 깊이: 페이지(가장 어두움) → 카드(그룹박스) → 보조버튼. 입력칸은 카드에 '박힌' 느낌으로
# 페이지색을 써서 카드와 또렷이 구분(흐릿한 동색 회색 3개로 뭉개지지 않게). 강조색은
# coral(peach) 하나로 통일(저장 버튼·체크·포커스) — 앱 전역 단일 액센트와 일치.
# (의도된 분리 — COLORS['base']로 되돌리지 말 것)
_PAGE = "#1a1a1a"       # 다이얼로그 배경
_CARD = "#262626"       # 그룹박스(카드) 면
_INSET = "#1a1a1a"      # 입력칸/체크박스 — 카드에 박힌 듯(페이지색과 동일)
_LINE = "#3a3a3a"       # 테두리·구분선·스크롤바
_BTN = "#303030"        # 보조 버튼(카드보다 살짝 올라옴)
_BTN_HOVER = "#3a3a3a"
_TXT = "#d4d4d4"        # 본문 글자
_TITLE = "#9aa0b0"      # 그룹 제목(차분한 회청)

_CHECK_ICON = check_icon_url()   # 켜진 체크박스에 그릴 코랄 ✓ (아웃라인 방식)
_CHEV_UP = chevron_icon_url("up")     # 스핀박스 상하 버튼 셰브론(중립)
_CHEV_DN = chevron_icon_url("down")

DIALOG_STYLE = f"""
    QDialog {{
        background-color: {_PAGE};
        color: {_TXT};
    }}
    QGroupBox {{
        background-color: {_CARD};
        border: 1px solid {_LINE};
        border-radius: 8px;
        margin-top: 6px;
        padding: 32px 14px 14px 14px;
        font-weight: 600;
    }}
    QGroupBox::title {{
        subcontrol-origin: padding;
        subcontrol-position: top left;
        left: 14px;
        top: 8px;
        padding: 0;
        color: {COLORS['peach']};
        font-size: 13px;
        font-weight: 700;
        background: transparent;
    }}
    QLabel {{
        color: {_TXT};
        background: transparent;
    }}
    QLineEdit, QSpinBox {{
        background-color: {_INSET};
        color: {_TXT};
        border: 1px solid {_LINE};
        border-radius: 5px;
        padding: 5px 8px;
    }}
    QLineEdit:focus, QSpinBox:focus {{
        border-color: {COLORS['peach']};
    }}
    QLineEdit:hover, QSpinBox:hover {{
        border-color: {COLORS['peach']};
    }}
    /* 스핀박스 상하 버튼 — 네이티브 기본(밝은 화살표 + 버튼 위 I빔 커서) 대신
       중립 셰브론. 버튼을 명시적으로 스타일링하면 내부 라인에디트가 버튼 영역을
       덮지 않게 재계산돼 I빔 커서가 버튼 위로 새던 문제도 사라진다(실측). */
    QSpinBox::up-button {{
        subcontrol-origin: border;
        subcontrol-position: top right;
        width: 20px;
        border: none;
        border-left: 1px solid {_LINE};
        border-top-right-radius: 5px;
        background: transparent;
    }}
    QSpinBox::down-button {{
        subcontrol-origin: border;
        subcontrol-position: bottom right;
        width: 20px;
        border: none;
        border-left: 1px solid {_LINE};
        border-bottom-right-radius: 5px;
        background: transparent;
    }}
    QSpinBox::up-arrow {{
        image: url("{_CHEV_UP}");
        width: 9px;
        height: 9px;
    }}
    QSpinBox::down-arrow {{
        image: url("{_CHEV_DN}");
        width: 9px;
        height: 9px;
    }}
    QCheckBox {{
        color: {_TXT};
        spacing: 7px;
        background: transparent;
    }}
    QCheckBox::indicator {{
        width: 16px;
        height: 16px;
        border-radius: 4px;
        border: 1px solid {_LINE};
        background-color: {_INSET};
    }}
    QCheckBox::indicator:hover {{
        border-color: {COLORS['peach']};
    }}
    QCheckBox::indicator:checked {{
        border-color: {COLORS['peach']};
        image: url("{_CHECK_ICON}");
    }}
    QPushButton {{
        background-color: {_BTN};
        color: {_TXT};
        border: none;
        border-radius: 6px;
        padding: 6px 16px;
    }}
    QPushButton:hover {{
        background-color: {_BTN_HOVER};
    }}
    QPushButton#saveBtn {{
        background-color: {COLORS['peach']};
        color: {_PAGE};
        font-weight: 600;
    }}
    QPushButton#saveBtn:hover {{
        background-color: {PEACH_HOVER};
    }}
    QComboBox QAbstractItemView {{
        background-color: {_CARD};
        color: {_TXT};
        selection-background-color: {_BTN};
        selection-color: {_TXT};
        border: 1px solid {_LINE};
        outline: none;
    }}
    QScrollArea {{
        background: transparent;
        border: none;
    }}
    QScrollBar:vertical {{
        background: transparent;
        width: 10px;
        border-radius: 5px;
    }}
    QScrollBar::handle:vertical {{
        background: {_LINE};
        border-radius: 5px;
        min-height: 28px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: #4a4a4a;
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0;
    }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
        background: transparent;
    }}
    /* 탭 — 어두운 탭 바(밝은 네이티브 타이틀바와 분리) + 활성만 코랄 밑줄 강조.
       스타일이 없으면 Qt 기본(밝은 회색)이라 타이틀바와 뭉쳐 시인성이 떨어진다. */
    QTabWidget::pane {{
        border: 1px solid {_LINE};
        border-radius: 8px;
        top: -1px;                 /* 콘텐츠 패널이 탭 바 밑줄과 겹치게 */
        background: {_PAGE};
    }}
    QTabWidget::tab-bar {{
        left: 6px;
    }}
    QTabBar {{
        background: transparent;
        qproperty-drawBase: 0;     /* Qt가 그리는 밝은 base strip 제거 */
    }}
    QTabBar::tab {{
        background: transparent;
        color: {_TITLE};           /* 비활성 = 차분한 회청 */
        padding: 7px 18px;
        margin-right: 2px;
        border: none;
        border-bottom: 2px solid transparent;
        font-size: 12px;
        font-weight: 600;
    }}
    QTabBar::tab:hover {{
        color: {_TXT};
    }}
    QTabBar::tab:selected {{
        color: {COLORS['peach']};              /* 활성 = 코랄 글자 */
        border-bottom: 2px solid {COLORS['peach']};  /* + 코랄 밑줄 */
    }}
"""


def _bullet_checkbox_row(checkbox: QCheckBox) -> QWidget:
    """체크박스를 '•' 불릿과 밀착 배치한 행으로 감싼다. 불릿 있는 다른 옵션 행
    (「•  히스토리 최대 개수」 등)과 같은 열에 정렬돼, 체크박스가 위 항목의
    하위처럼 보이지 않고 독립 항목으로 읽히게 한다."""
    row = QWidget()
    row.setStyleSheet("background: transparent;")   # 컨테이너가 카드보다 어두운 기본 배경을 칠하지 않게
    h = QHBoxLayout(row)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(6)
    bullet = QLabel("•")
    bullet.setStyleSheet(f"color: {_TXT}; background: transparent;")
    h.addWidget(bullet)
    h.addWidget(checkbox)
    h.addStretch()
    return row


class HotkeyEdit(QPushButton):
    """클릭 후 키 조합을 누르면 단축키를 캡처하는 위젯"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._value = ""
        self._listening = False
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clicked.connect(self._start_listening)
        self._apply_style(False)

    def value(self) -> str:
        return self._value

    def set_value(self, v: str):
        self._value = v
        self._listening = False
        self._update_display()

    def _start_listening(self):
        self._listening = True
        self._update_display()
        self.grabKeyboard()

    @staticmethod
    def format_hotkey(value: str) -> str:
        """저장 형식(ctrl+shift+p)을 표시 형식(Ctrl + Shift + P)으로 — 기본 단축키 표기와 통일.
        _value 자체는 파서가 쓰는 소문자 canonical을 유지하고 표시만 바꾼다."""
        if not value:
            return ""
        return " + ".join(tok.capitalize() for tok in value.split("+"))

    def _update_display(self):
        if self._listening:
            self.setText("키를 누르세요...")
            self._apply_style(True)
        else:
            self.setText(self.format_hotkey(self._value) or "클릭하여 설정")
            self._apply_style(False)

    def _apply_style(self, listening: bool):
        if listening:
            self.setStyleSheet(
                f"QPushButton {{ background-color: {_INSET}; "
                f"color: {COLORS['peach']}; border: 1px solid {COLORS['peach']}; "
                f"border-radius: 5px; padding: 5px 8px; text-align: left; }}"
            )
        else:
            self.setStyleSheet(
                f"QPushButton {{ background-color: {_INSET}; "
                f"color: {_TXT}; border: 1px solid {_LINE}; "
                f"border-radius: 5px; padding: 5px 8px; text-align: left; }}"
                f"QPushButton:hover {{ border-color: {COLORS['peach']}; }}"
            )

    def keyPressEvent(self, event):
        if not self._listening:
            super().keyPressEvent(event)
            return

        key = event.key()

        # 순수 modifier 키는 무시하고 계속 대기
        if key in (Qt.Key.Key_Control, Qt.Key.Key_Alt, Qt.Key.Key_Shift, Qt.Key.Key_Meta):
            return

        # Escape → 취소
        if key == Qt.Key.Key_Escape:
            self._listening = False
            self.releaseKeyboard()
            self._update_display()
            return

        modifiers = event.modifiers()
        parts = []
        if modifiers & Qt.KeyboardModifier.ControlModifier:
            parts.append("ctrl")
        if modifiers & Qt.KeyboardModifier.AltModifier:
            parts.append("alt")
        if modifiers & Qt.KeyboardModifier.ShiftModifier:
            parts.append("shift")

        key_name = self._qt_key_to_name(key)
        if key_name:
            parts.append(key_name)
            self._value = "+".join(parts)

        self._listening = False
        self.releaseKeyboard()
        self._update_display()

    def focusOutEvent(self, event):
        if self._listening:
            self._listening = False
            self.releaseKeyboard()
            self._update_display()
        super().focusOutEvent(event)

    def _qt_key_to_name(self, key) -> str:
        _MAP = {
            Qt.Key.Key_Space: "space",
            Qt.Key.Key_Return: "return",
            Qt.Key.Key_Tab: "tab",
            Qt.Key.Key_Backspace: "backspace",
            Qt.Key.Key_Delete: "delete",
            Qt.Key.Key_Home: "home",
            Qt.Key.Key_End: "end",
            Qt.Key.Key_PageUp: "pageup",
            Qt.Key.Key_PageDown: "pagedown",
            Qt.Key.Key_F1: "f1", Qt.Key.Key_F2: "f2", Qt.Key.Key_F3: "f3",
            Qt.Key.Key_F4: "f4", Qt.Key.Key_F5: "f5", Qt.Key.Key_F6: "f6",
            Qt.Key.Key_F7: "f7", Qt.Key.Key_F8: "f8", Qt.Key.Key_F9: "f9",
            Qt.Key.Key_F10: "f10", Qt.Key.Key_F11: "f11", Qt.Key.Key_F12: "f12",
            Qt.Key.Key_QuoteLeft: "`", Qt.Key.Key_AsciiTilde: "`",
            # `[` 는 Shift가 눌리면 Qt가 `{`(BraceLeft)로 주므로 둘 다 "["로 취급
            Qt.Key.Key_BracketLeft: "[", Qt.Key.Key_BraceLeft: "[",
        }
        if key in _MAP:
            return _MAP[key]
        if Qt.Key.Key_A <= key <= Qt.Key.Key_Z:
            return chr(key).lower()
        if Qt.Key.Key_0 <= key <= Qt.Key.Key_9:
            return chr(key)
        return ""


class SettingsDialog(QDialog):
    """설정 다이얼로그"""

    settings_changed = pyqtSignal(dict)  # 변경된 설정 dict

    # 설정 키 상수
    KEY_PANEL_TOGGLE = "hotkey_panel_toggle"
    KEY_HISTORY_MAX = "history_max"
    KEY_AUTO_START = "auto_start"
    KEY_NOTIFY_ON_COPY = "notify_on_copy"
    KEY_OCR_HOTKEY = "hotkey_ocr_trigger"
    KEY_IMAGE_TO_PATH_HOTKEY = "hotkey_image_to_path"
    KEY_SEQ_IMAGE_TO_PATH_HOTKEY = "hotkey_seq_image_to_path"
    KEY_PIN_IMAGE_HOTKEY = "hotkey_pin_image"
    KEY_SEQ_PIN_HOTKEY = "hotkey_seq_pin"
    KEY_CAPTURE_HOTKEY = "hotkey_capture"
    KEY_RECORD_GIF_HOTKEY = "hotkey_record_gif"
    KEY_ASK_AI_HOTKEY = "hotkey_ask_ai"
    # AI 팔레트 타겟 — 자유질문창(Alt+`)의 질문을 보낼 목적지 목록(JSON list).
    # 데이터 모양·기본값·URL 빌더는 pasteflow/ai_palette.py가 소유(main도 이걸 공유).
    KEY_AI_PALETTE_SITES = "ai_palette_sites"
    KEY_CAPTURE_FOLDER = "capture_save_folder"
    KEY_OCR_ENGINE = "ocr_engine"
    # AI 크리덴셜 — Mindlogic 게이트웨이 한 벌(키 + Base URL)뿐이다.
    # v1.50.0: Google AI Studio(공식) 백엔드를 제거하고 backend 개념 자체를 없앴다.
    # 사용 빈도가 낮은 데 비해 backend 분기가 코드 전반(설정·main·엔진)에 퍼져 있었고,
    # Gemini 모델은 게이트웨이에도 그대로 있어 잃는 것이 없다(웹 검색도 nano 심부름꾼이
    # google_search grounding보다 낫다는 실측이 있다 — web_search.py 참조).
    KEY_OCR_GEMINI_BASE_URL = "ocr_gemini_base_url"
    KEY_OCR_GEMINI_API_KEY_GATEWAY = "ocr_gemini_api_key_gateway"
    KEY_OCR_GEMINI_MODEL_GATEWAY = "ocr_gemini_model_gateway"
    KEY_OCR_GEMINI_MODEL_CACHE_GATEWAY = "ocr_gemini_model_cache_gateway"
    # OCR 전용 모델 슬롯 — AI 질의 모델(KEY_OCR_GEMINI_MODEL_*)과 분리(v1.39.0).
    # OCR은 이미지를 보내므로 비전 가능 모델만 고를 수 있고, 저렴한 모델을 따로 둘 수 있다.
    KEY_OCR_MODEL_GATEWAY = "ocr_model_gateway"
    # 여러 모델 비교(선택) — 기본 AI 질의 모델(모델 1)에 더해 동시에 물어볼 추가 모델 2개.
    # 빈 값이면 미사용. 질문창 체크박스가 켜졌을 때만 발동한다.
    KEY_AI_COMPARE_MODEL_A = "ai_compare_model_a"
    KEY_AI_COMPARE_MODEL_B = "ai_compare_model_b"
    # AI 질의 시스템 프롬프트(멘토 페르소나). 빈 값이면 ocr_engine.AI_SYSTEM_PROMPT로 폴백.
    KEY_AI_SYSTEM_PROMPT = "ai_system_prompt"
    # API 프로필 — 이름 붙인 크리덴셜 세트(라벨+base_url+키+모델+캐시)의 목록.
    # 여러 API(구글 직결·게이트웨이 계정 여러 개)를 드롭다운으로 전환하기 위한 것.
    # 엔진이 읽는 "라이브" 키(KEY_OCR_GEMINI_*)는 그대로 두고, 프로필 선택 = 그 값을
    # 라이브 칸에 채우는 것뿐이다(엔진 변경 0). 프로필 묶음은 api_key를 품으므로
    # main._SECRET_KEYS에 등록돼 JSON 통째로 DPAPI 암호화된다.
    KEY_AI_PROFILES = "ai_profiles"           # JSON list, DPAPI 암호화(통째)
    KEY_AI_ACTIVE_PROFILE = "ai_active_profile"  # 마지막 선택 라벨(평문)
    # 구글 AI Studio 직결 프리셋 — base_url이 OpenAI 호환 고정 경로라 매번 외우지 않게
    # 드롭다운에 상시 제공하는 템플릿 프로필. 고르면 URL이 채워지고 키·모델만 넣으면 된다.
    # 사용자가 키를 채워 [+ 저장]하면 그 값이 DB에 남아 유지되고, 삭제하면 다음 실행에
    # 다시 시드된다(템플릿이라 항상 출발점으로 남기는 의도).
    GOOGLE_PRESET_LABEL = "Google AI Studio"
    GOOGLE_PRESET_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"
    GOOGLE_PRESET_MODEL = "gemini-2.5-flash"  # 비전 가능 → AI·OCR 공용 기본
    # 구글 드라이브 OAuth(선택) — 연결하면 AI가 내 드라이브 문서를 검색해 근거로 삼는다.
    # client_secret·refresh_token은 main._SECRET_KEYS에 등록돼 DPAPI로 암호화 저장된다.
    KEY_GDRIVE_CLIENT_ID = "gdrive_client_id"
    KEY_GDRIVE_CLIENT_SECRET = "gdrive_client_secret"
    KEY_GDRIVE_REFRESH_TOKEN = "gdrive_refresh_token"
    KEY_QUEUE_IDLE_RESET = "queue_idle_reset_sec"
    # 비교 콤보의 '미사용' 표시값 — 저장 시 빈 문자열로 환원한다(editable 콤보라 텍스트=값).
    _COMPARE_UNUSED = "(사용 안 함)"

    # 워커 스레드 → UI 안전 통신용 내부 시그널 (models, error_msg)
    _models_fetched = pyqtSignal(list, str)  # (models, error)
    # 드라이브 동의 결과 (refresh_token, error_msg) — 워커 스레드 → UI 안전 통신.
    _gdrive_done = pyqtSignal(str, str)
    # 연결 테스트 단계별 결과 (run_id, slot, status, detail).
    # slot: "conn" | "chat" | "ocr" | "__end__"(버튼 복구 신호)
    # status: ProbeResult.status + "run"(진행 중) / "skip"(앞 단계 실패로 건너뜀)
    # run_id: 이 결과를 만든 테스트 회차. 최신 회차가 아니면 UI가 버린다(아래 _on_probe_done).
    _probe_done = pyqtSignal(int, str, str, str)

    def __init__(self, current_settings: dict, parent=None):
        super().__init__(parent)
        self._settings = dict(current_settings)
        # _setup_ui가 콤보에 거는 currentTextChanged 핸들러가 곧바로 읽으므로 먼저 초기화.
        self._probe_run_id = 0
        # API 프로필 상태. _loading_profiles는 콤보를 프로그램이 채우는 동안 사용자
        # 선택 핸들러(_on_profile_selected)가 오발동하지 않게 막는 가드.
        self._profiles: list[dict] = []
        self._loading_profiles = False
        self._setup_window()
        self._setup_ui()
        self._load_values()
        self._finalize_size()
        self._models_fetched.connect(self._on_models_fetched)
        self._probe_done.connect(self._on_probe_done)
        self._gdrive_done.connect(self._on_gdrive_done)

    def _setup_window(self):
        self.setWindowTitle("PasteFlow 설정")
        # 고정 크기는 _finalize_size()에서 콘텐츠·화면에 맞춰 결정(스크롤 영역과 분리).
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.WindowCloseButtonHint
        )
        self.setStyleSheet(DIALOG_STYLE)

    def showEvent(self, event):
        super().showEvent(event)
        self._strip_min_max_buttons()

    def _strip_min_max_buttons(self):
        """타이틀바에서 최소화·최대화 버튼을 실제로 제거한다.

        창 플래그를 'WindowCloseButtonHint'만 줘도 Windows HWND에는 WS_MINIMIZEBOX/
        WS_MAXIMIZEBOX가 남아 최소화 버튼이 그려진다(Qt→Win32 매핑 특성). 그 버튼을
        누르면 changeEvent가 되돌려 '눌러도 안 되는 버튼'이 된다. WS_SYSMENU는 두되
        (닫기·시스템 메뉴 유지) min/max box 비트를 모두 지우면 Windows가 닫기 버튼만
        그린다(하나만 지우면 남은 쪽이 회색으로 표시됨). 네이티브 창 재생성에도 살아남게
        show마다 재적용한다."""
        try:
            import ctypes
            GWL_STYLE = -16
            WS_MINIMIZEBOX = 0x00020000
            WS_MAXIMIZEBOX = 0x00010000
            user32 = ctypes.windll.user32
            hwnd = int(self.winId())
            style = user32.GetWindowLongW(hwnd, GWL_STYLE)
            new_style = style & ~WS_MINIMIZEBOX & ~WS_MAXIMIZEBOX
            if new_style != style:
                user32.SetWindowLongW(hwnd, GWL_STYLE, new_style)
                SWP_NOSIZE = 0x0001; SWP_NOMOVE = 0x0002
                SWP_NOZORDER = 0x0004; SWP_FRAMECHANGED = 0x0020
                user32.SetWindowPos(
                    hwnd, 0, 0, 0, 0, 0,
                    SWP_NOSIZE | SWP_NOMOVE | SWP_NOZORDER | SWP_FRAMECHANGED,
                )
        except Exception:
            pass

    def changeEvent(self, event):
        """최소화 안전망 — 최소화 버튼은 _strip_min_max_buttons()로 제거하지만,
        그 호출이 어떤 이유로 실패하거나 시스템 메뉴 등 다른 경로로 최소화되면
        즉시 원상 복구한다(작업표시줄 버튼이 없어 좌하단 park되는 것 방지)."""
        if event.type() == QEvent.Type.WindowStateChange and self.isMinimized():
            self.showNormal()
        super().changeEvent(event)

    def _finalize_size(self):
        """창 크기를 콘텐츠에 맞추되 화면을 넘지 않게 cap — 넘으면 스크롤.

        폭: 콘텐츠가 실제로 필요로 하는 폭에 맞춘다(좁게 고정하면 폼·콤보가 뷰포트를
        넘어 오른쪽이 잘렸다). 높이: 고정 높이로 압박하면 word-wrap 라벨의 heightForWidth가
        매 repaint마다 진동해 드래그 시 떨렸다 — 스크롤 영역으로 분리해 해결.
        """
        # 탭 분리 후: 창 크기는 '가장 큰 탭 페이지'에 맞춘다(넘으면 그 탭이 스크롤).
        pages = self._tab_pages
        content_w = max((p.sizeHint().width() for p in pages), default=360)
        content_h = max((p.sizeHint().height() for p in pages), default=420)
        tabbar_h = self._tabs.tabBar().sizeHint().height()
        btn_h = self._btn_bar.sizeHint().height()
        screen = self.screen() or QApplication.primaryScreen()
        ag = screen.availableGeometry() if screen else None
        avail_w = ag.width() if ag else 1200
        avail_h = ag.height() if ag else 1000
        w = min(content_w + 24, avail_w - 80)   # +24: 세로 스크롤바 + 탭 프레임 여유
        h = min(content_h + tabbar_h + btn_h + 8, avail_h - 64)
        self.setFixedSize(max(360, w), max(420, h))

    def _setup_ui(self):
        # 설정이 늘며 단일 스크롤이 화면을 넘어, 탭 2개(일반/AI)로 분리한다.
        # "일반"은 일반 설정 + 단축키를 한 탭에 통합한 것(2026-07-29 요청) — 위에 일반
        # 설정(히스토리·자동시작 등), 그 아래 단축키(기본→기능) 순으로 쌓는다.
        # 창 높이는 _finalize_size가 '가장 큰 탭'에 맞춰 고정하므로 스크롤이 사실상
        # 사라진다. 버튼 바는 탭 밖에 둬 항상 노출.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._tabs = QTabWidget()
        outer.addWidget(self._tabs, 1)

        # 각 탭 페이지(스크롤 안의 콘텐츠 위젯)를 모아 _finalize_size가 가장 큰 것에 맞춘다.
        self._tab_pages: list[QWidget] = []

        def _make_tab(title: str) -> QVBoxLayout:
            page = QWidget()
            pl = QVBoxLayout(page)
            pl.setSpacing(6)
            pl.setContentsMargins(16, 12, 16, 12)
            pl.setAlignment(Qt.AlignmentFlag.AlignTop)  # 짧은 탭은 위로 붙임
            sc = QScrollArea()
            sc.setWidgetResizable(True)
            sc.setFrameShape(QFrame.Shape.NoFrame)
            sc.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            sc.setWidget(page)
            self._tabs.addTab(sc, title)
            self._tab_pages.append(page)
            return pl

        tab_general = _make_tab("일반")
        tab_ai = _make_tab("AI")

        # ── 기능 단축키 그룹 (변경 가능) — 아래 기본 단축키 다음에 배치 ──
        # 인식 편의를 위해 4개 하위 묶음을 얇은 구분선으로 분리(쭉 나열 대신 시각 그룹화):
        #  ① 패널  ② 경로 붙여넣기류  ③ 영역 캡처·핀류  ④ AI(호출·OCR)
        hotkey_group = QGroupBox("기능 단축키 (변경 가능)")
        hotkey_form = QFormLayout(hotkey_group)
        hotkey_form.setVerticalSpacing(4)
        hotkey_form.setContentsMargins(10, 8, 10, 8)

        def _hk_sep():
            line = QFrame()
            line.setFrameShape(QFrame.Shape.HLine)
            line.setFrameShadow(QFrame.Shadow.Plain)
            line.setStyleSheet(f"color:{_LINE}; background-color:{_LINE};")
            line.setFixedHeight(1)
            return line

        # ① 패널
        self._panel_toggle_hotkey = HotkeyEdit()
        hotkey_form.addRow("•  패널 불러오기:", self._panel_toggle_hotkey)
        hotkey_form.addRow(_hk_sep())

        # ② 경로 붙여넣기 / 순차 경로 붙여넣기
        self._image_to_path_hotkey = HotkeyEdit()
        self._image_to_path_hotkey.setToolTip(
            "현재 클립보드 이미지를 임시 PNG로 저장하고 절대경로를 클립보드 텍스트로 교체합니다.\n"
            "이어서 포그라운드 창에 Ctrl+V를 자동 전송합니다.\n"
            "Claude Code CLI 등 '파일 경로 텍스트'를 첨부로 받는 앱에 한 키로 붙여넣기 위한 단축키."
        )
        hotkey_form.addRow("•  경로 붙여넣기:", self._image_to_path_hotkey)

        self._seq_image_to_path_hotkey = HotkeyEdit()
        self._seq_image_to_path_hotkey.setToolTip(
            "순차 붙여넣기(Ctrl+Shift+V)의 '경로 버전'. 순차 큐에서 다음 항목을 꺼내되\n"
            "이미지면 임시 PNG로 저장한 절대경로 텍스트로 붙여넣습니다.\n"
            "예: 영역 캡처(Alt+F2)를 여러 장 찍은 뒤 이 키를 차례로 눌러 경로1·경로2… 순서대로 붙여넣기.\n"
            "이미지가 아닌 항목은 원본 그대로 붙여넣습니다."
        )
        hotkey_form.addRow("•  순차 경로 붙여넣기:", self._seq_image_to_path_hotkey)
        hotkey_form.addRow(_hk_sep())

        # ③ 영역 캡처(Alt+F2) / 영역 캡처 핀(Alt+F3) — 이름을 '영역 캡처' 계열로 통일
        self._capture_hotkey = HotkeyEdit()
        self._capture_hotkey.setToolTip(
            "화면 영역을 드래그로 선택해 캡처합니다(Snipaste의 영역 캡처).\n"
            "캡처 즉시 클립보드에 복사되고 지정 폴더에 PNG로 저장됩니다.\n"
            "ESC 또는 우클릭으로 취소합니다."
        )
        hotkey_form.addRow("•  영역 캡처:", self._capture_hotkey)

        self._pin_image_hotkey = HotkeyEdit()
        self._pin_image_hotkey.setToolTip(
            "현재 클립보드 이미지를 화면 위에 떠 있는 창으로 띄웁니다(Snipaste의 화면 핀).\n"
            "여러 개를 동시에 띄울 수 있고, ESC로 닫습니다.\n"
            "띄운 창에서 Space를 누르면 주석 편집 모드로 들어갑니다."
        )
        hotkey_form.addRow("•  영역 캡처 핀:", self._pin_image_hotkey)

        self._seq_pin_hotkey = HotkeyEdit()
        self._seq_pin_hotkey.setToolTip(
            "화면 핀(영역 캡처 핀)의 '순차 버전'. 순차 붙여넣기(Ctrl+Shift+V)와 같은 큐를\n"
            "공유하며, 큐에서 다음 항목을 꺼내 화면에 핀합니다.\n"
            "예: 영역 캡처(Alt+F2)를 여러 장 찍은 뒤 이 키를 차례로 눌러 캡처1·캡처2… 순서대로 핀.\n"
            "이미지가 아닌 항목은 이미지로 렌더해 핀합니다."
        )
        hotkey_form.addRow("•  순차 핀:", self._seq_pin_hotkey)

        self._record_gif_hotkey = HotkeyEdit()
        self._record_gif_hotkey.setToolTip(
            "화면 영역을 드래그로 선택해 GIF로 녹화합니다.\n"
            "녹화 중 뜨는 ■ 정지 버튼(또는 ESC로 취소)으로 끝내면 GIF로 저장되고\n"
            "파일 경로가 클립보드에 복사됩니다(노션·슬랙 등엔 파일/경로로 넘김).\n"
            "커서는 녹화되지 않으며, 선택이 시작된 단일 모니터만 녹화됩니다(MVP)."
        )
        hotkey_form.addRow("•  GIF 녹화:", self._record_gif_hotkey)
        hotkey_form.addRow(_hk_sep())

        # ④ AI 호출(alt+`) / AI OCR
        self._ask_ai_hotkey = HotkeyEdit()
        self._ask_ai_hotkey.setToolTip(
            "컨텍스트 없이 즉석에서 AI에게 질문하는 입력창을 띄웁니다.\n"
            "클립보드 항목과 무관하게 아무 때나 한 키로 AI를 호출해 자유 질문하고 답변을 받습니다.\n"
            "(우클릭 'AI에게 질문'은 선택한 항목을 컨텍스트로 묻는 방식 — 이건 컨텍스트 없는 자유 질문)"
        )
        hotkey_form.addRow("•  AI 호출:", self._ask_ai_hotkey)

        # AI OCR — 화면 영역을 AI(설정된 API)로 텍스트 인식. 별도 엔진 없음.
        self._ocr_hotkey = HotkeyEdit()
        self._ocr_hotkey.setToolTip(
            "화면 영역을 드래그로 선택해 그 안의 텍스트를 AI(설정된 API)로 인식합니다.\n"
            "결과 텍스트가 클립보드·히스토리에 들어갑니다."
        )
        hotkey_form.addRow("•  AI OCR:", self._ocr_hotkey)

        # ── 기본 단축키 그룹 (고정) — 복사/붙여넣기 등 변경 불가한 핵심 기능. 맨 위 배치 ──
        info_group = QGroupBox("기본 단축키 (고정)")
        info_layout = QGridLayout(info_group)
        info_layout.setSpacing(5)
        info_layout.setContentsMargins(8, 8, 8, 8)
        info_layout.setColumnStretch(0, 1)

        _SHORTCUTS = [
            ("일반 복사",         "Ctrl + C"),
            ("일반 붙여넣기",     "Ctrl + V"),
            ("순서대로 붙여넣기", "Ctrl + Shift + V"),
        ]
        for row, (action, keys) in enumerate(_SHORTCUTS):
            action_lbl = QLabel("•  " + action)
            action_lbl.setStyleSheet(
                f"color: {COLORS['text']}; font-size: 12px;"
            )
            key_lbl = QLabel(keys)
            key_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            key_lbl.setStyleSheet(
                f"color: {COLORS['peach']}; font-size: 12px;"
                f" font-family: 'Consolas', monospace;"
            )
            info_layout.addWidget(action_lbl, row, 0)
            info_layout.addWidget(key_lbl, row, 1)

        # 배치 순서: 기본 단축키(고정) 먼저, 그다음 기능 단축키(변경 가능).
        # 「일반」 탭 안에서는 일반 설정 그룹이 이 둘보다 위에 와야 하므로(아래 general_group
        # 참고) 여기선 일단 append하고, general_group 쪽에서 insertWidget(0, ...)로 맨 위로
        # 끼워 넣는다 — general_group은 파일 순서상 뒤에 만들어지기 때문.
        tab_general.addWidget(info_group)
        tab_general.addWidget(hotkey_group)

        _combo_style = (
            f"QComboBox {{ background-color: {_INSET}; color: {_TXT}; "
            f"border: 1px solid {_LINE}; border-radius: 5px; padding: 5px 8px; }}"
            f"QComboBox:focus {{ border-color: {COLORS['peach']}; }}"
            f"QComboBox:hover {{ border-color: {COLORS['peach']}; }}"
        )

        # ── AI 연동 그룹 (Gemini / Mindlogic API) ──
        # OCR(텍스트 인식)과 AI 답변(우클릭 'AI에게 질문')이 동일 API를 공유한다.
        # OCR은 별도 엔진 선택 없이 이 API로 처리하므로(WinRT 제거) 항상 키가 필요하다.
        ai_group = QGroupBox("AI 연동 (API 프로필)")
        self._ai_form = QFormLayout(ai_group)
        ai_form = self._ai_form
        ai_form.setVerticalSpacing(4)
        ai_form.setContentsMargins(10, 8, 10, 8)

        ai_desc = QLabel(
            "AI 호출·AI OCR에 쓸 API. 여러 API(구글 직결·게이트웨이 계정)를 프로필로 저장해\n"
            "드롭다운으로 전환합니다.")
        ai_desc.setStyleSheet(f"color: {COLORS['subtext0']}; font-size: 11px;")
        ai_desc.setWordWrap(True)
        ai_form.addRow(ai_desc)

        # 섹션 구분선(프로필 ↔ 크리덴셜 ↔ 모델). 프로필 행이 이미 이걸 쓰므로 여기서 정의.
        def _ai_sep() -> QFrame:
            line = QFrame()
            line.setFrameShape(QFrame.Shape.HLine)
            line.setFrameShadow(QFrame.Shadow.Plain)
            line.setStyleSheet(f"color:{_LINE}; background-color:{_LINE};")
            line.setFixedHeight(1)
            return line

        # 프로필 행 — 이름 붙인 크리덴셜 세트를 고르면 아래 키·URL·모델이 한 번에 채워진다.
        self._profile_combo = QComboBox()  # editable 아님(모델 콤보와 달리 라벨 선택기)
        self._profile_combo.setStyleSheet(_combo_style)
        self._profile_combo.setToolTip(
            "저장한 API 프로필. 고르면 API 키·Base URL·모델이 그 프로필 값으로 채워집니다\n"
            "(연결 확인은 [연결 테스트] 버튼을 누르세요).")
        # activated(사용자 선택 전용) — currentIndexChanged와 달리 ⓐ 프로그램이 콤보를
        # 채우는 동안엔 안 터지고(가드 불필요) ⓑ 이미 선택된 항목을 다시 골라도 발화한다.
        # ⓑ가 없으면 활성 프로필과 필드(라이브 크리덴셜)가 어긋난 상태에서 그 프로필을
        # 다시 눌러도 값이 안 채워진다(같은 인덱스라 currentIndexChanged 침묵).
        self._profile_combo.activated.connect(self._on_profile_selected)
        self._profile_save_btn = QPushButton("+ 저장")
        self._profile_save_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._profile_save_btn.setToolTip("지금 입력한 키·URL·모델을 이름 붙여 새 프로필로 저장")
        self._profile_save_btn.clicked.connect(self._on_profile_save)
        self._profile_delete_btn = QPushButton("삭제")
        self._profile_delete_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._profile_delete_btn.setToolTip("선택한 프로필 삭제 ([저장]을 눌러야 최종 반영)")
        self._profile_delete_btn.clicked.connect(self._on_profile_delete)
        prof_row = QHBoxLayout()
        prof_row.setContentsMargins(0, 0, 0, 0)
        prof_row.setSpacing(4)
        prof_row.addWidget(self._profile_combo, 1)
        prof_row.addWidget(self._profile_save_btn)
        prof_row.addWidget(self._profile_delete_btn)
        ai_form.addRow(QLabel("•  API 프로필:"), prof_row)

        ai_form.addRow(_ai_sep())  # 프로필 ↔ 크리덴셜 구분

        # 크리덴셜 — 프로필로 전환하는 (API 키 + Base URL) 한 벌. 게이트웨이든 구글 직결이든
        # OpenAI 호환 경로라 base_url만 바꾸면 된다(구글: .../v1beta/openai).
        self._gateway_key_edit = QLineEdit()
        self._gateway_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._gateway_key_edit.setPlaceholderText("API 키 (구글 또는 게이트웨이)")
        # 키 보기 토글 — Password↔평문. 3개 프로필을 오갈 때 '무슨 키가 들었나' 확인용.
        # ⚠ 이모지(👁)는 Qt 컬러 이모지 폴백으로 버튼에서 깨져 렌더되므로(ai_query.py의
        # 🕘·🔀 제거 전례) 텍스트로 둔다.
        self._key_reveal_btn = QPushButton("보기")
        self._key_reveal_btn.setCheckable(True)
        # 40px는 '보기'/'숨김' 두 글자 + 버튼 패딩에 모자라 글자가 잘렸다 → 56px로.
        self._key_reveal_btn.setFixedWidth(56)
        self._key_reveal_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._key_reveal_btn.setToolTip("API 키 보기/숨기기")
        self._key_reveal_btn.toggled.connect(self._on_key_reveal_toggled)
        self._refresh_btn = QPushButton()
        # Qt 내장 표준 아이콘 — 폰트 의존성 없이 모든 환경에서 보장
        self._refresh_btn.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload))
        self._refresh_btn.setFixedWidth(34)
        self._refresh_btn.setToolTip("사용 가능한 모델 목록 가져오기")
        # NoFocus 필수: 클릭 시 setEnabled(False)로 꺼지는데, StrongFocus면 포커스가
        # editable 모델 콤보로 넘어가 텍스트가 전체 선택돼 조회 중 파랗게 반전돼 보인다.
        self._refresh_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._refresh_btn.clicked.connect(self._on_refresh_models)
        gw_row = QHBoxLayout()
        gw_row.setContentsMargins(0, 0, 0, 0)
        gw_row.setSpacing(4)
        gw_row.addWidget(self._gateway_key_edit, 1)
        gw_row.addWidget(self._key_reveal_btn)
        gw_row.addWidget(self._refresh_btn)
        ai_form.addRow(QLabel("•  API 키:"), gw_row)

        self._base_url_edit = QLineEdit()
        self._base_url_edit.setPlaceholderText(
            "구글: https://generativelanguage.googleapis.com/v1beta/openai"
            "  /  게이트웨이: https://…mindlogic.ai/v1/gateway")
        ai_form.addRow(QLabel("•  Base URL:"), self._base_url_edit)

        ai_form.addRow(_ai_sep())  # 크리덴셜 ↔ 모델 섹션 구분(OCR 모델 위)

        # 모델 콤보 2개 — OCR(이미지 입력 필요)과 AI 질의(전 모델)를 분리한다.
        # 같은 모델을 공유하면 답변용 고가 모델이 OCR에도 쓰이거나(과금), 텍스트 전용
        # 모델을 고르면 OCR이 400으로 깨진다. ↻ 새로고침 1회로 두 콤보를 함께 채운다.
        self._model_label = QLabel("•  AI 모델 1:")
        self._model_combo = QComboBox()
        self._model_combo.setEditable(True)
        self._model_combo.setStyleSheet(_combo_style)
        self._model_combo.setToolTip(
            "AI 질문·답변의 기본 모델. 평소엔 이 모델만 답하고,\n"
            "질문창에서 '여러 모델로 비교'를 켜면 모델 1·2·3으로 동시에 질의합니다.")

        self._ocr_model_label = QLabel("•  OCR 모델:")
        self._ocr_model_combo = QComboBox()
        self._ocr_model_combo.setEditable(True)
        self._ocr_model_combo.setStyleSheet(_combo_style)
        self._ocr_model_combo.setToolTip(
            "이미지에서 텍스트를 추출할 때 쓰는 모델.\n"
            "이미지 입력을 받는 모델이어야 합니다 — [연결 테스트]로 확인하세요."
        )

        # 질의 모델 2·3 (선택) — 질의 모델 1에 더해 '여러 모델로 비교' 시 동시에 물어볼 모델.
        # 질문창에서 '여러 모델로 비교'를 켜면 이 모델들로 함께 질의한다. 미설정(사용 안 함)이
        # 기본이며, 모델 1을 포함해 2개 이상 설정돼야 질문창 체크박스가 나타난다.
        _cmp_tip = "비교 질의에 함께 쓸 모델(선택). '여러 모델로 비교'를 켜면 함께 답합니다."
        self._compare_a_label = QLabel("•  AI 모델 2:")
        self._compare_model_a_combo = QComboBox()
        self._compare_model_a_combo.setEditable(True)
        self._compare_model_a_combo.setStyleSheet(_combo_style)
        self._compare_model_a_combo.setToolTip(_cmp_tip)
        self._compare_b_label = QLabel("•  AI 모델 3:")
        self._compare_model_b_combo = QComboBox()
        self._compare_model_b_combo.setEditable(True)
        self._compare_model_b_combo.setStyleSheet(_combo_style)
        self._compare_model_b_combo.setToolTip(_cmp_tip)

        # 콤보 초기 채우기는 _load_values(_init_model_slots)에서 캐시를 읽어 수행한다.
        # 캐시가 없으면 빈 콤보라, 무엇을 해야 할지 placeholder로 안내한다(빈 값은 엔진
        # 기본 모델로 폴백).
        for combo in (self._model_combo, self._ocr_model_combo):
            le = combo.lineEdit()
            if le is not None:
                le.setPlaceholderText("↻를 눌러 모델 목록을 불러오세요")
            # 계열 헤더 아래 모델명을 들여써 상하위를 구분(팝업 view 한정 — 닫힌 콤보는 불변).
            # 델리게이트는 combo를 부모로 둬야 GC로 사라지지 않는다.
            combo.view().setItemDelegate(_ModelIndentDelegate(combo))
        # 비교 콤보도 같은 들여쓰기 델리게이트를 쓴다(placeholder는 '(사용 안 함)'이 대신).
        for combo in (self._compare_model_a_combo, self._compare_model_b_combo):
            combo.view().setItemDelegate(_ModelIndentDelegate(combo))

        # 모델별 프로브 결과 줄 — 연결 테스트가 각 모델을 실호출해 여기에 개별로 쓴다.
        # 상태 줄 하나에 뭉치면 "뭐가 성공했다는 거지?"가 되므로 콤보마다 따로 둔다.
        self._model_probe_status = self._make_probe_label()
        self._ocr_model_probe_status = self._make_probe_label()
        self._compare_a_probe_status = self._make_probe_label()
        self._compare_b_probe_status = self._make_probe_label()

        # 모델을 바꾸면 직전 결과는 다른 모델 이야기다 — 낡은 ✓를 남기면 그게 거짓말이 된다.
        # 진행 중인 테스트가 있다면 그 결과도 무효화한다(도착해도 화면의 모델과 다른 모델 얘기).
        for combo, probe_label in ((self._model_combo, self._model_probe_status),
                                   (self._ocr_model_combo, self._ocr_model_probe_status),
                                   (self._compare_model_a_combo, self._compare_a_probe_status),
                                   (self._compare_model_b_combo, self._compare_b_probe_status)):
            combo.currentTextChanged.connect(
                lambda _t, lbl=probe_label: self._on_model_text_changed(lbl))

        # 배치: OCR 모델을 위로, 질의 모델 1·2·3을 이어 묶어 "함께 쓰는 질의 모델군"임을
        # 시각적으로 드러낸다(모델 1=평소 답변, 2·3=비교 시 추가).
        ai_form.addRow(self._ocr_model_label, self._stack(
            self._ocr_model_combo, self._ocr_model_probe_status))
        ai_form.addRow(self._model_label, self._stack(
            self._model_combo, self._model_probe_status))

        # 모델 2·3(비교용)은 '여러 모델 비교 사용' 체크박스 뒤로 숨긴다 — 평소엔 안 보이게
        # 해 설정창을 줄인다. 켜져 있을 때만 이 두 행이 뜨고, 저장도 그때만 된다.
        self._compare_enable_check = QCheckBox("여러 모델 비교 사용 (모델 2·3 추가)")
        self._compare_enable_check.setToolTip(
            "켜면 질문창에서 여러 모델로 동시에 물어볼 수 있습니다.\n"
            "끄면 모델 2·3은 저장되지 않아 질문창의 비교 옵션도 사라집니다.")
        self._compare_enable_check.toggled.connect(self._on_compare_toggle)
        ai_form.addRow(self._compare_enable_check)

        self._compare_box = QWidget()
        cbox = QFormLayout(self._compare_box)
        cbox.setContentsMargins(0, 0, 0, 0)
        cbox.setVerticalSpacing(4)
        cbox.addRow(self._compare_a_label, self._stack(
            self._compare_model_a_combo, self._compare_a_probe_status))
        cbox.addRow(self._compare_b_label, self._stack(
            self._compare_model_b_combo, self._compare_b_probe_status))
        self._compare_box.setVisible(False)  # 기본 접힘(로드 시 저장값 있으면 펼침)
        ai_form.addRow(self._compare_box)

        # API 연결 테스트 — 모델명 바로 아래에 배치(설명 힌트보다 위). 힌트를 그룹 맨 아래로
        # 내려 워드랩 공간을 넉넉히 확보한다.
        self._test_btn = QPushButton("연결 테스트")
        self._test_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._test_btn.setToolTip(
            "키·연결을 확인하고, 설정한 AI 모델(1·2·3)과 OCR 모델을 실제로 한 번씩\n"
            "호출해 각 줄에 결과를 보여줍니다. (AI 모델은 이미지 첨부 질문 1회, OCR 모델은\n"
            "작은 테스트 이미지 1회. AI 모델 2·3은 설정했을 때만 테스트)"
        )
        self._test_btn.clicked.connect(self._on_test_api)
        self._test_status = QLabel("")
        self._test_status.setWordWrap(True)
        self._test_status.setStyleSheet(f"color: {COLORS['subtext0']}; font-size: 11px;")
        test_row = QHBoxLayout()
        test_row.setContentsMargins(0, 0, 0, 0)
        test_row.setSpacing(8)
        test_row.addWidget(self._test_btn)
        test_row.addWidget(self._test_status, 1)
        ai_form.addRow("", test_row)

        # ── AI 시스템 프롬프트(멘토 페르소나) ──
        # 답변 톤·구조를 정하는 프롬프트. 크고 드물게 손대므로 별도 편집창으로 접는다.
        # _ai_prompt_edit은 화면에 안 붙는 '데이터 홀더'다(편집은 _open_prompt_editor 창이
        # 자기 에디터에 복사해 하고, 확인 시 여기로 되쓴다). _on_save가 이걸 그대로 읽는다.
        # 비워 두면 엔진이 기본값(ocr_engine.AI_SYSTEM_PROMPT)으로 폴백한다.
        self._ai_prompt_edit = QPlainTextEdit()  # 홀더 — 레이아웃에 추가하지 않음
        self._prompt_edit_btn = QPushButton("프롬프트 편집…")
        self._prompt_edit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._prompt_edit_btn.setToolTip(
            "AI 답변의 톤·구조를 정하는 시스템 프롬프트를 편집합니다.\n"
            "OCR(글자 추출)에는 영향이 없습니다. 비워 두면 기본값이 적용됩니다.")
        self._prompt_edit_btn.clicked.connect(self._open_prompt_editor)
        ai_form.addRow(QLabel("•  AI 시스템 프롬프트:"), self._prompt_edit_btn)

        tab_ai.addWidget(ai_group)

        # ── AI 팔레트 타겟 (Alt+` 자유질문창의 목적지 목록) ──
        # 질문을 어디로 보낼지 사용자가 직접 관리하는 목록 — 순서가 팔레트 번호(Alt+1~9),
        # 종류가 url이면 {q} 자리에 질의가 채워진다(pasteflow/ai_palette.py 참고).
        palette_group = QGroupBox("AI 팔레트 타겟 (Alt+` 자유질문 목적지)")
        palette_layout = QVBoxLayout(palette_group)
        palette_layout.setSpacing(4)
        palette_layout.setContentsMargins(10, 8, 10, 8)

        palette_desc = QLabel(
            "Alt+`로 뜨는 질문창에서 Tab으로 고르거나 Alt+숫자로 즉시 보낼 목적지 목록입니다.\n"
            "\"종류\"가 \"웹사이트 URL\"이면 URL의 {q} 자리에 입력한 질문이 들어갑니다.\n"
            "키워드를 정하면 그 글자+공백으로 문장을 시작할 때 자동으로 그 타겟이 선택됩니다.")
        palette_desc.setStyleSheet(f"color: {COLORS['subtext0']}; font-size: 11px;")
        palette_desc.setWordWrap(True)
        palette_layout.addWidget(palette_desc)

        # 열 제목 — 행이 위/아래 두 줄(위: 손잡이~종류, 아래: URL 전체폭)이라 제목도 같은
        # 두 줄 구조로 맞춘다. 폭은 각 행 위젯의 setFixedWidth와 정확히 짝을 이뤄야 칸이 맞는다.
        _hdr_style = f"color: {_TITLE}; font-size: 10px; font-weight: 600;"

        palette_header_top = QHBoxLayout()
        palette_header_top.setContentsMargins(0, 0, 0, 0)
        palette_header_top.setSpacing(4)

        hdr_drag = QLabel("")
        hdr_drag.setFixedWidth(20)
        palette_header_top.addWidget(hdr_drag)

        hdr_num = QLabel("#")
        hdr_num.setFixedWidth(20)
        hdr_num.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hdr_num.setStyleSheet(_hdr_style)
        palette_header_top.addWidget(hdr_num)

        hdr_label = QLabel("표시 이름")
        hdr_label.setFixedWidth(90)
        hdr_label.setStyleSheet(_hdr_style)
        palette_header_top.addWidget(hdr_label)

        hdr_keyword = QLabel("키워드")
        hdr_keyword.setFixedWidth(48)
        hdr_keyword.setStyleSheet(_hdr_style)
        palette_header_top.addWidget(hdr_keyword)

        hdr_kind = QLabel("종류")
        hdr_kind.setFixedWidth(150)
        hdr_kind.setStyleSheet(_hdr_style)
        palette_header_top.addWidget(hdr_kind)

        palette_header_top.addStretch(1)

        hdr_del = QLabel("")
        hdr_del.setFixedWidth(28)
        palette_header_top.addWidget(hdr_del)

        palette_layout.addLayout(palette_header_top)

        palette_header_url = QHBoxLayout()
        palette_header_url.setContentsMargins(20 + 4 + 20 + 4, 0, 0, 0)  # 행의 URL 줄과 같은 들여쓰기
        hdr_url = QLabel("URL")
        hdr_url.setStyleSheet(_hdr_style)
        palette_header_url.addWidget(hdr_url, 1)
        palette_layout.addLayout(palette_header_url)

        self._palette_rows_layout = QVBoxLayout()
        self._palette_rows_layout.setSpacing(4)
        palette_layout.addLayout(self._palette_rows_layout)
        self._palette_rows: list[_PaletteSiteRow] = []

        add_site_btn = QPushButton("+ 타겟 추가")
        add_site_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        add_site_btn.clicked.connect(self._on_add_palette_site)
        palette_layout.addWidget(add_site_btn, 0, Qt.AlignmentFlag.AlignLeft)

        tab_ai.addWidget(palette_group)

        # ── 구글 드라이브 (선택) — 접이식, 기본 접힘 ──
        # 연결하면 AI 질의가 내 드라이브 문서를 검색해 근거로 삼는다(읽기 전용).
        # 안 하면 도구가 조용히 빠질 뿐 웹 검색·AI 답변은 그대로 동작한다(우아한 열화).
        # 자주 안 쓰므로 헤더 클릭으로 펼치는 접이식으로 감춘다(삭제 아님 — 배관·연결 상태 보존).
        self._gd_toggle = QPushButton("▸  구글 드라이브 (선택)")
        self._gd_toggle.setCheckable(True)
        self._gd_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self._gd_toggle.setStyleSheet(
            f"QPushButton {{ text-align:left; border:none; padding:6px 2px; "
            f"color:{_TITLE}; font-weight:600; background:transparent; }}"
            f"QPushButton:hover {{ color:{COLORS['peach']}; }}")
        self._gd_toggle.toggled.connect(self._on_gd_toggle)
        tab_ai.addWidget(self._gd_toggle)

        self._gd_body = QWidget()
        gd_form = QFormLayout(self._gd_body)
        gd_form.setVerticalSpacing(4)
        gd_form.setContentsMargins(10, 4, 10, 8)

        gd_desc = QLabel(
            "연결하면 AI가 내 구글 드라이브 문서를 찾아 근거로 답합니다(읽기 전용). "
            "Google Cloud Console에서 「데스크톱 앱」 OAuth 클라이언트를 만들어 아래에 입력하세요."
        )
        gd_desc.setStyleSheet(f"color: {COLORS['subtext0']}; font-size: 11px;")
        gd_desc.setWordWrap(True)
        gd_form.addRow(gd_desc)

        self._gdrive_client_id_edit = QLineEdit()
        self._gdrive_client_id_edit.setPlaceholderText("...apps.googleusercontent.com")
        gd_form.addRow(QLabel("•  클라이언트 ID:"), self._gdrive_client_id_edit)

        self._gdrive_client_secret_edit = QLineEdit()
        self._gdrive_client_secret_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._gdrive_client_secret_edit.setPlaceholderText("GOCSPX-...")
        gd_form.addRow(QLabel("•  클라이언트 보안 비밀번호:"), self._gdrive_client_secret_edit)

        self._gdrive_connect_btn = QPushButton("연결")
        self._gdrive_connect_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._gdrive_connect_btn.setToolTip(
            "브라우저에서 구글 동의를 받아 드라이브 읽기 권한을 연결합니다.\n"
            "('확인되지 않은 앱' 경고가 뜨면 [고급] → [계속]으로 넘기세요.)"
        )
        self._gdrive_connect_btn.clicked.connect(self._on_gdrive_connect)
        self._gdrive_disconnect_btn = QPushButton("연결 해제")
        self._gdrive_disconnect_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._gdrive_disconnect_btn.setToolTip("저장된 구글 인증을 지웁니다(드라이브 검색 중지).")
        self._gdrive_disconnect_btn.clicked.connect(self._on_gdrive_disconnect)
        self._gdrive_status = QLabel("")
        self._gdrive_status.setWordWrap(True)
        gd_btn_row = QHBoxLayout()
        gd_btn_row.setContentsMargins(0, 0, 0, 0)
        gd_btn_row.setSpacing(8)
        gd_btn_row.addWidget(self._gdrive_connect_btn)
        gd_btn_row.addWidget(self._gdrive_disconnect_btn)
        gd_btn_row.addWidget(self._gdrive_status, 1)
        gd_form.addRow("", gd_btn_row)

        self._gd_body.setVisible(False)  # 기본 접힘(_load_values가 연결돼 있으면 펼침)
        tab_ai.addWidget(self._gd_body)

        # ── 일반 설정 그룹 ── 「일반」 탭의 맨 위(아래 insertWidget(0, ...) 참고).
        # 탭 제목도 "일반"이라 그룹박스 제목까지 "일반"이면 중복으로 읽혀 "일반 설정"으로 구분.
        general_group = QGroupBox("일반 설정")
        general_form = QFormLayout(general_group)
        general_form.setVerticalSpacing(4)
        general_form.setContentsMargins(10, 8, 10, 8)

        self._history_max_spin = QSpinBox()
        self._history_max_spin.setRange(10, 500)
        self._history_max_spin.setValue(50)
        general_form.addRow("•  히스토리 최대 개수:", self._history_max_spin)

        self._queue_idle_spin = QSpinBox()
        self._queue_idle_spin.setRange(1, 3600)
        self._queue_idle_spin.setSuffix(" 초")
        self._queue_idle_spin.setValue(10)
        self._queue_idle_spin.setToolTip(
            "마지막 복사로부터 이 시간이 지나면 다음 복사는 큐의 첫 항목으로 시작합니다.\n"
            "(일반 Ctrl+V는 시간과 무관하게 즉시 큐를 비웁니다.)"
        )
        general_form.addRow("•  순차 큐 자동 초기화:", self._queue_idle_spin)

        # 캡처 저장 폴더 — 경로 표시 + 찾아보기
        self._capture_folder_edit = QLineEdit()
        self._capture_folder_edit.setReadOnly(True)
        self._capture_folder_edit.setToolTip("영역 캡처(Alt+F2) 이미지를 저장할 폴더")
        browse_btn = QPushButton("찾아보기")
        browse_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        browse_btn.clicked.connect(self._pick_capture_folder)
        folder_row = QHBoxLayout()
        folder_row.setContentsMargins(0, 0, 0, 0)
        folder_row.addWidget(self._capture_folder_edit, 1)
        folder_row.addWidget(browse_btn)
        general_form.addRow("•  캡처 저장 폴더:", folder_row)

        self._auto_start_check = QCheckBox("Windows 시작 시 자동 실행")
        general_form.addRow(_bullet_checkbox_row(self._auto_start_check))

        self._notify_copy_check = QCheckBox("복사 시 우하단 알림 표시")
        general_form.addRow(_bullet_checkbox_row(self._notify_copy_check))

        # insertWidget(0, ...) — 기본/기능 단축키 그룹은 이 시점보다 앞서(위쪽) 이미
        # tab_general에 append돼 있으므로, 맨 위로 오려면 append가 아니라 0번 위치 삽입.
        tab_general.insertWidget(0, general_group)

        # ── 버튼 바 (탭 밖, 항상 노출) ──
        self._btn_bar = QWidget()
        btn_layout = QHBoxLayout(self._btn_bar)
        btn_layout.setContentsMargins(16, 8, 16, 12)
        btn_layout.addStretch()

        cancel_btn = QPushButton("취소")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)

        save_btn = QPushButton("저장")
        save_btn.setObjectName("saveBtn")
        save_btn.clicked.connect(self._on_save)
        btn_layout.addWidget(save_btn)

        outer.addWidget(self._btn_bar)

    def _on_test_api(self):
        """연결 테스트 — 키·연결 + **설정한 질의 모델(1·2·3)과 OCR 모델**을 실호출해 각 줄에
        개별 보고(워커 스레드). 질의 모델 2·3은 설정됐을 때만 테스트하고, 미설정이면 줄을 숨긴다.

        옛 버전은 모델 목록 조회 하나만 하고 "연결 성공 — API 키가 유효합니다"를 띄웠다.
        모델 콤보가 둘인 화면에서 그 문구는 "고른 모델이 된다"로 읽히지만, 실제로는 **어느
        모델도 호출하지 않았다** — 특히 OCR 모델의 이미지 입력 지원 여부는 목록 조회로
        절대 알 수 없어, 텍스트 전용 모델을 골라도 ✓가 떴다가 캡처할 때 400이 났다.

        이제 세 단계를 순서대로 돌리고 각 결과를 제 자리(연결=상태 줄, 모델=콤보 아래)에
        따로 쓴다. 앞 단계가 실패하면 뒤 단계는 'skip'으로 남겨 원인을 흐리지 않는다.
        """
        # creds는 UI 스레드에서 미리 읽어 넣는다(워커에서 위젯 접근 금지).
        api_key, base_url = self._creds()
        chat_model = self._model_combo.currentText().strip()
        ocr_model = self._ocr_model_combo.currentText().strip()
        # 비교가 꺼져 있으면 모델 2·3은 테스트하지 않는다(저장도 안 되는 값이라).
        compare_on = self._compare_enable_check.isChecked()
        cmp_a = self._compare_value(self._compare_model_a_combo) if compare_on else ""
        cmp_b = self._compare_value(self._compare_model_b_combo) if compare_on else ""

        # (slot, 모델, is_ocr). OCR을 맨 앞에 둔다 — 텍스트 전용 모델 오선택으로 캡처 시
        # 400이 가장 자주 나는 지점이라 결과를 먼저 보여준다.
        targets: list[tuple[str, str, bool]] = [
            ("ocr", ocr_model, True),
            ("chat", chat_model, False),
        ]
        if cmp_a:
            targets.append(("chat2", cmp_a, False))
        if cmp_b:
            targets.append(("chat3", cmp_b, False))

        # 회차 번호. 테스트 도중 사용자가 모델을 바꾸거나 다시 누르면 이 값이 올라가고,
        # 뒤늦게 도착한 옛 회차의 결과는 버려진다 — 안 그러면 A 모델의 ✓가 화면에 떠 있는
        # B 모델 아래에 찍힌다.
        self._probe_run_id += 1
        run_id = self._probe_run_id

        self._test_btn.setEnabled(False)
        self._set_probe_status(self._test_status, "run", "연결 확인 중…")
        self._set_probe_status(self._ocr_model_probe_status, "run", "대기 중…")
        self._set_probe_status(self._model_probe_status, "run", "대기 중…")
        # 미설정 질의 모델(2·3)은 줄을 비워 숨긴다(빈 detail → 라벨 hidden).
        self._set_probe_status(self._compare_a_probe_status, "run", "대기 중…" if cmp_a else "")
        self._set_probe_status(self._compare_b_probe_status, "run", "대기 중…" if cmp_b else "")

        import threading

        def _worker():
            try:
                from pasteflow.ocr_engine import (
                    probe_chat_model, probe_connection, probe_ocr_model,
                )
                # 연결 프로브 1회 — 실패하면 모델 프로브는 전부 skip해 원인을 흐리지 않는다.
                if not api_key:
                    conn_ok = False
                    self._probe_done.emit(
                        run_id, "conn", "fail", "API 키가 설정돼 있지 않습니다.")
                else:
                    c = probe_connection(api_key, base_url)
                    conn_ok = c.status == "ok"
                    self._probe_done.emit(run_id, "conn", c.status, c.detail)

                for slot, model, is_ocr in targets:
                    if not model:
                        self._probe_done.emit(
                            run_id, slot, "skip", "모델이 비어 있습니다 — ↻로 목록을 불러오세요.")
                        continue
                    if not conn_ok:
                        self._probe_done.emit(
                            run_id, slot, "skip", "연결이 안 돼 건너뛰었습니다.")
                        continue
                    self._probe_done.emit(run_id, slot, "run", f"{model} 호출 중…")
                    probe = probe_ocr_model if is_ocr else probe_chat_model
                    result = probe(api_key, base_url, model)
                    self._probe_done.emit(run_id, slot, result.status, result.detail)
            except Exception as e:
                # 프로브 함수 밖의 예상 못 한 오류(패키지 미설치 등)
                self._probe_done.emit(run_id, "conn", "fail", f"테스트 실행 실패: {e}")
            finally:
                self._probe_done.emit(run_id, "__end__", "", "")

        threading.Thread(target=_worker, daemon=True).start()

    def _on_model_text_changed(self, probe_label: QLabel):
        """모델명이 바뀌면 그 콤보의 결과 줄을 지우고 진행 중인 회차를 무효화한다."""
        self._set_probe_status(probe_label, "run", "")
        self._probe_run_id += 1
        # 무효화로 워커의 __end__가 버려지므로 버튼은 여기서 되살린다.
        self._test_btn.setEnabled(True)

    def _on_probe_done(self, run_id: int, slot: str, status: str, detail: str):
        """워커의 단계별 결과를 해당 줄에 반영 (Qt 메인 스레드). 옛 회차 결과는 버린다."""
        if run_id != self._probe_run_id:
            return
        if slot == "__end__":
            self._test_btn.setEnabled(True)
            return
        label = {
            "conn": self._test_status,
            "chat": self._model_probe_status,
            "chat2": self._compare_a_probe_status,
            "chat3": self._compare_b_probe_status,
            "ocr": self._ocr_model_probe_status,
        }[slot]
        self._set_probe_status(label, status, detail)

    # ── 구글 드라이브 연결 ──────────────────────────────────────────────────
    #
    # refresh token은 `연결`이 성공한 즉시 DB에 쓰지 않고 `self._gdrive_refresh`에 들고 있다가
    # [저장]에서 함께 emit한다 — 다른 모든 설정과 같은 규칙(취소하면 아무것도 안 바뀜).

    def _on_gdrive_connect(self):
        """`연결` — 브라우저 동의를 받아 refresh token을 얻는다(워커 스레드).

        동의에 수십 초가 걸리므로 UI 스레드에서 부르면 설정창이 통째로 얼어붙는다.
        """
        client_id = self._gdrive_client_id_edit.text().strip()
        client_secret = self._gdrive_client_secret_edit.text().strip()
        if not (client_id and client_secret):
            self._set_gdrive_status("✗ 클라이언트 ID와 보안 비밀번호를 먼저 입력하세요.", "fail")
            return

        self._gdrive_connect_btn.setEnabled(False)
        self._set_gdrive_status("브라우저에서 구글 동의를 진행하세요…", "run")

        import threading

        def _worker():
            try:
                from pasteflow import gdrive
                token = gdrive.authorize(client_id, client_secret)
                self._gdrive_done.emit(token, "")
            except Exception as e:
                self._gdrive_done.emit("", str(e))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_gdrive_done(self, refresh_token: str, err: str):
        """동의 결과 반영 (Qt 메인 스레드)."""
        self._gdrive_connect_btn.setEnabled(True)
        if err:
            self._set_gdrive_status(f"✗ 연결 실패 — {err}", "fail")
            return
        self._gdrive_refresh = refresh_token
        self._set_gdrive_status("✓ 연결됨 — [저장]을 눌러야 적용됩니다.", "ok")

    def _on_gdrive_disconnect(self):
        """`연결 해제` — 보관 중인 인증을 지운다(저장 시 DB에서도 비워진다)."""
        self._gdrive_refresh = ""
        self._set_gdrive_status("연결 해제됨 — [저장]을 눌러야 적용됩니다.", "run")

    def _set_gdrive_status(self, message: str, status: str):
        """드라이브 상태 줄. status는 _PROBE_STYLE 키(ok/fail/run…)와 색을 공유한다."""
        _, color = _PROBE_STYLE.get(status, _PROBE_STYLE["run"])
        self._gdrive_status.setStyleSheet(f"color: {color}; font-size: 11px;")
        self._gdrive_status.setText(message)

    def _pick_capture_folder(self):
        """캡처 저장 폴더 선택 다이얼로그"""
        start = self._capture_folder_edit.text() or ""
        folder = QFileDialog.getExistingDirectory(self, "캡처 저장 폴더 선택", start)
        if folder:
            self._capture_folder_edit.setText(folder)

    def _load_values(self):
        """현재 설정값 로드"""
        self._panel_toggle_hotkey.set_value(
            self._settings.get(self.KEY_PANEL_TOGGLE, "ctrl+space")
        )
        self._ocr_hotkey.set_value(
            self._settings.get(self.KEY_OCR_HOTKEY, "ctrl+shift+s")
        )
        self._image_to_path_hotkey.set_value(
            self._settings.get(self.KEY_IMAGE_TO_PATH_HOTKEY, "ctrl+shift+p")
        )
        self._seq_image_to_path_hotkey.set_value(
            self._settings.get(self.KEY_SEQ_IMAGE_TO_PATH_HOTKEY, "ctrl+shift+[")
        )
        self._pin_image_hotkey.set_value(
            self._settings.get(self.KEY_PIN_IMAGE_HOTKEY, "alt+f3")
        )
        self._seq_pin_hotkey.set_value(
            self._settings.get(self.KEY_SEQ_PIN_HOTKEY, "alt+shift+f3")
        )
        self._capture_hotkey.set_value(
            self._settings.get(self.KEY_CAPTURE_HOTKEY, "alt+f2")
        )
        self._record_gif_hotkey.set_value(
            self._settings.get(self.KEY_RECORD_GIF_HOTKEY, "ctrl+shift+g")
        )
        self._ask_ai_hotkey.set_value(
            self._settings.get(self.KEY_ASK_AI_HOTKEY, "alt+`")
        )
        self._capture_folder_edit.setText(
            self._settings.get(self.KEY_CAPTURE_FOLDER, "")
        )
        self._gateway_key_edit.setText(self._settings.get(self.KEY_OCR_GEMINI_API_KEY_GATEWAY, ""))
        self._base_url_edit.setText(self._settings.get(self.KEY_OCR_GEMINI_BASE_URL, ""))

        # 모델 슬롯 4행 — 캐시된 모델 목록으로 채우고 저장값을 복원한다.
        self._init_model_slots()
        self._init_compare_slots()
        # 저장된 비교 모델(2·3)이 있으면 체크박스를 켜 그 두 행을 펼친다(setChecked가
        # _on_compare_toggle을 불러 _compare_box를 보이게 한다). 없으면 접힌 채로.
        has_compare = bool(
            (self._settings.get(self.KEY_AI_COMPARE_MODEL_A, "") or "").strip()
            or (self._settings.get(self.KEY_AI_COMPARE_MODEL_B, "") or "").strip())
        self._compare_enable_check.setChecked(has_compare)
        self._compare_box.setVisible(has_compare)
        # API 프로필 — 크리덴셜·모델 칸을 채운 뒤 호출(자동 이관이 그 값을 읽는다).
        self._init_profiles()

        # AI 팔레트 타겟(Alt+` 자유질문 목적지) — 저장된 목록(없으면 기본값)으로 행 구성.
        self._load_palette_sites()

        # 구글 드라이브 — refresh token은 화면에 안 띄운다(비밀이고 사용자가 볼 일도 없다).
        # 있으면 "연결됨"으로만 알린다.
        self._gdrive_client_id_edit.setText(self._settings.get(self.KEY_GDRIVE_CLIENT_ID, ""))
        self._gdrive_client_secret_edit.setText(
            self._settings.get(self.KEY_GDRIVE_CLIENT_SECRET, ""))
        self._gdrive_refresh = self._settings.get(self.KEY_GDRIVE_REFRESH_TOKEN, "")
        if self._gdrive_refresh:
            self._set_gdrive_status("✓ 연결됨", "ok")
        else:
            self._set_gdrive_status("연결되지 않음 — AI가 드라이브를 검색하지 않습니다.", "run")
        # 이미 연결돼 있으면 접이식 드라이브 섹션을 펼쳐 상태가 바로 보이게 한다.
        self._gd_toggle.setChecked(bool(self._gdrive_refresh))

        try:
            history_max = int(self._settings.get(self.KEY_HISTORY_MAX, "50"))
        except (ValueError, TypeError):
            history_max = 50
        self._history_max_spin.setValue(history_max)
        try:
            queue_idle = int(float(self._settings.get(self.KEY_QUEUE_IDLE_RESET, "10")))
        except (ValueError, TypeError):
            queue_idle = 10
        self._queue_idle_spin.setValue(max(1, queue_idle))
        self._auto_start_check.setChecked(
            self._settings.get(self.KEY_AUTO_START, "0") == "1"
        )
        self._notify_copy_check.setChecked(
            self._settings.get(self.KEY_NOTIFY_ON_COPY, "1") == "1"
        )
        # AI 시스템 프롬프트 — 저장값이 비었으면 기본 멘토 프롬프트를 보여준다(비워 두면
        # 엔진이 기본값으로 폴백하므로, 화면엔 '실제로 쓰이는 프롬프트'를 노출).
        saved_prompt = self._settings.get(self.KEY_AI_SYSTEM_PROMPT, "")
        self._ai_prompt_edit.setPlainText(saved_prompt or self._default_ai_prompt())

    def _default_ai_prompt(self) -> str:
        """기본 AI 시스템 프롬프트(멘토 페르소나) — ocr_engine 모듈 상수."""
        from pasteflow.ocr_engine import AI_SYSTEM_PROMPT
        return AI_SYSTEM_PROMPT

    def _open_prompt_editor(self):
        """[프롬프트 편집…] — 시스템 프롬프트를 별도 창에서 편집한다.

        _ai_prompt_edit(홀더)의 내용을 이 창의 에디터로 복사해 편집하고, [확인]이면
        되쓴다([취소]면 안 건드림). _on_save는 그대로 _ai_prompt_edit를 읽는다.
        """
        dlg = QDialog(self)
        dlg.setWindowTitle("AI 시스템 프롬프트 편집")
        dlg.setWindowModality(Qt.WindowModality.WindowModal)
        v = QVBoxLayout(dlg)
        desc = QLabel(
            "AI 답변의 톤·구조를 정하는 프롬프트입니다. 비우면 기본값으로 동작합니다.\n"
            "OCR(글자 추출)에는 영향이 없습니다.")
        desc.setStyleSheet(f"color: {COLORS['subtext0']}; font-size: 11px;")
        desc.setWordWrap(True)
        edit = QPlainTextEdit()
        edit.setPlainText(self._ai_prompt_edit.toPlainText())
        edit.setMinimumSize(460, 320)
        edit.setStyleSheet(
            f"QPlainTextEdit {{ background-color: {_INSET}; color: {_TXT}; "
            f"border: 1px solid {_LINE}; border-radius: 5px; padding: 5px 8px; }}"
            f"QPlainTextEdit:focus {{ border-color: {COLORS['peach']}; }}")
        reset_btn = QPushButton("기본값으로 되돌리기")
        reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        reset_btn.clicked.connect(lambda: edit.setPlainText(self._default_ai_prompt()))
        cancel_btn = QPushButton("취소")
        cancel_btn.clicked.connect(dlg.reject)
        ok_btn = QPushButton("확인")
        ok_btn.setObjectName("saveBtn")
        ok_btn.clicked.connect(dlg.accept)
        row = QHBoxLayout()
        row.addWidget(reset_btn)
        row.addStretch(1)
        row.addWidget(cancel_btn)
        row.addWidget(ok_btn)
        v.addWidget(desc)
        v.addWidget(edit, 1)
        v.addLayout(row)
        if dlg.exec():
            self._ai_prompt_edit.setPlainText(edit.toPlainText())

    def _on_compare_toggle(self, on: bool):
        """'여러 모델 비교 사용' — 모델 2·3 행을 펼치거나 접는다."""
        self._compare_box.setVisible(on)

    def _on_gd_toggle(self, on: bool):
        """구글 드라이브 접이식 헤더 — 본문 표시/숨김 + 화살표(▸/▾) 전환."""
        self._gd_body.setVisible(on)
        self._gd_toggle.setText(("▾" if on else "▸") + "  구글 드라이브 (선택)")

    def _creds(self) -> tuple[str, str]:
        """게이트웨이 (api_key, base_url) — 편집칸에서 직접 읽는다."""
        return self._gateway_key_edit.text().strip(), self._base_url_edit.text().strip()

    def _on_key_reveal_toggled(self, on: bool):
        """'보기' 토글 — API 키를 평문↔●●● 전환."""
        self._gateway_key_edit.setEchoMode(
            QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password)
        self._key_reveal_btn.setText("숨김" if on else "보기")

    # ── API 프로필 ─────────────────────────────────────────────────────────────
    # 프로필 = 이름 붙인 (base_url + api_key + 모델 선택 + 모델 캐시) 스냅샷.
    # 엔진이 읽는 라이브 키는 안 건드리고, 프로필 선택 = 그 값을 UI 칸에 채우는 것뿐이다.
    def _is_google_base_url(self, base_url: str) -> bool:
        """base_url이 구글 AI Studio 직결(OpenAI 호환) 경로인지."""
        u = (base_url or "").lower()
        return "generativelanguage" in u or "googleapis" in u

    def _guess_profile_label(self, base_url: str) -> str:
        """base_url로 프로필 이름을 추정한다(자동 이관·[+저장] 기본값)."""
        u = (base_url or "").lower()
        if self._is_google_base_url(base_url):
            return "구글"
        if "mindlogic" in u:
            return "마인드로직"
        return "프로필 1"

    def _capture_current_profile(self, label: str) -> dict:
        """현재 UI 6칸 + 모델 캐시를 프로필 dict로 스냅샷."""
        api_key, base_url = self._creds()
        return {
            "label": label,
            "base_url": base_url,
            "api_key": api_key,
            "model": self._model_combo.currentText().strip(),
            "ocr_model": self._ocr_model_combo.currentText().strip(),
            "compare_a": self._compare_value(self._compare_model_a_combo),
            "compare_b": self._compare_value(self._compare_model_b_combo),
            "model_cache": self._cached_models(),
        }

    def _google_preset(self) -> dict:
        """구글 AI Studio 직결 프리셋(빈 키·기본 모델). base_url만 고정 제공한다."""
        return {
            "label": self.GOOGLE_PRESET_LABEL,
            "base_url": self.GOOGLE_PRESET_BASE_URL,
            "api_key": "",
            "model": self.GOOGLE_PRESET_MODEL,
            "ocr_model": self.GOOGLE_PRESET_MODEL,
            "compare_a": "",
            "compare_b": "",
            "model_cache": [],
        }

    def _init_profiles(self):
        """저장된 프로필을 로드하고 드롭다운을 채운다(_load_values에서 크리덴셜·모델
        칸을 채운 *뒤* 호출 — 자동 이관이 그 값을 읽는다)."""
        import json
        self._profiles = []
        raw = self._settings.get(self.KEY_AI_PROFILES, "")
        if raw:
            try:
                data = json.loads(raw)
                if isinstance(data, list):
                    self._profiles = [
                        p for p in data if isinstance(p, dict) and p.get("label")]
            except (json.JSONDecodeError, ValueError, TypeError):
                self._profiles = []
        # 자동 이관 — 저장된 프로필이 없는데 지금 쓰는 크리덴셜이 있으면, 그것을 첫
        # 프로필로 시드해 기존 설정이 안 날아가게 한다(이름은 base_url로 추정).
        if not self._profiles:
            api_key, base_url = self._creds()
            if api_key or base_url:
                self._profiles = [
                    self._capture_current_profile(self._guess_profile_label(base_url))]
        # 구글 AI Studio는 '관리형 프리셋' — base_url이 구글 직결로 고정이라는 뜻이다.
        # 같은 이름이 구글이 아닌 URL로 저장돼 있으면(옛 게이트웨이 설정이 이 이름으로
        # 잘못 저장된 오염 상태) 그 프로필을 URL 기준 이름으로 개명해 데이터를 보존하고,
        # 캐노니컬 구글 프리셋을 따로 넣는다. 이미 구글 URL이면 사용자가 키·모델을 채운
        # 것이므로 그대로 둔다(중복 시드 안 함).
        has_google = False
        for p in self._profiles:
            if p.get("label") != self.GOOGLE_PRESET_LABEL:
                continue
            if self._is_google_base_url(p.get("base_url", "")):
                has_google = True
            else:
                p["label"] = self._guess_profile_label(p.get("base_url", ""))
        if not has_google:
            self._profiles.append(self._google_preset())
        self._populate_profile_combo(self._settings.get(self.KEY_AI_ACTIVE_PROFILE, ""))

    def _populate_profile_combo(self, active_label: str = ""):
        """드롭다운을 프로필 라벨로 채운다. _loading_profiles 가드로 선택 핸들러
        오발동을 막는다(로드 시 자동 연결 테스트가 튀지 않게)."""
        self._loading_profiles = True
        try:
            self._profile_combo.clear()
            for p in self._profiles:
                self._profile_combo.addItem(p["label"])
            if self._profiles:
                idx = self._profile_combo.findText(active_label) if active_label else -1
                self._profile_combo.setCurrentIndex(idx if idx >= 0 else 0)
        finally:
            self._loading_profiles = False
        self._profile_delete_btn.setEnabled(bool(self._profiles))

    def _apply_profile(self, prof: dict):
        """프로필 값을 UI 6칸 + 모델 캐시에 채운다(선택·저장 안 함)."""
        import json
        self._gateway_key_edit.setText(prof.get("api_key", ""))
        self._base_url_edit.setText(prof.get("base_url", ""))
        cache = prof.get("model_cache", []) or []
        self._settings[self.KEY_OCR_GEMINI_MODEL_CACHE_GATEWAY] = json.dumps(cache)
        self._refill_model_slots(sorted(set(cache)))
        # 모델 선택 복원 — _refill_model_slots가 현재 텍스트를 보존하므로 명시 재설정.
        self._model_combo.setCurrentText(prof.get("model", ""))
        self._ocr_model_combo.setCurrentText(
            prof.get("ocr_model", "") or prof.get("model", ""))
        self._set_compare_text(self._compare_model_a_combo, prof.get("compare_a", ""))
        self._set_compare_text(self._compare_model_b_combo, prof.get("compare_b", ""))

    def _set_compare_text(self, combo: QComboBox, val: str):
        """비교 콤보에 값 설정 — 빈 값이면 '(사용 안 함)'으로."""
        val = (val or "").strip()
        if not val:
            idx = combo.findText(self._COMPARE_UNUSED)
            combo.setCurrentIndex(idx if idx >= 0 else 0)
            return
        idx = combo.findText(val)
        combo.setCurrentIndex(idx) if idx >= 0 else combo.setCurrentText(val)

    def _on_profile_selected(self, idx: int):
        """드롭다운에서 프로필을 고름 → 값만 채운다(연결 테스트는 [연결 테스트] 버튼으로).

        예전엔 여기서 자동으로 _on_test_api()를 돌렸으나, 드롭다운을 훑을 때마다 네트워크
        테스트가 튀어 불편해 제거했다 — 테스트는 사용자가 명시적으로 누를 때만 돈다.
        """
        if self._loading_profiles or idx < 0 or idx >= len(self._profiles):
            return
        self._apply_profile(self._profiles[idx])

    def _on_profile_save(self):
        """[+ 저장] — 현재 입력값을 이름 붙여 프로필로 저장(같은 이름이면 갱신)."""
        cur = self._profile_combo.currentText() if self._profiles else ""
        default = cur or self._guess_profile_label(self._base_url_edit.text())
        name, ok = QInputDialog.getText(self, "프로필 저장", "프로필 이름:", text=default)
        if not ok:
            return
        name = name.strip()
        if not name:
            return
        prof = self._capture_current_profile(name)
        for i, p in enumerate(self._profiles):
            if p["label"] == name:
                self._profiles[i] = prof
                break
        else:
            self._profiles.append(prof)
        self._populate_profile_combo(name)
        self._set_status(
            f"✓ 프로필 '{name}' 저장 — 하단 [저장]을 눌러야 최종 적용됩니다.", ok=True)

    def _on_profile_delete(self):
        """[삭제] — 선택 프로필 제거([저장] 시 DB 반영)."""
        idx = self._profile_combo.currentIndex()
        if idx < 0 or idx >= len(self._profiles):
            return
        label = self._profiles[idx]["label"]
        del self._profiles[idx]
        self._populate_profile_combo()
        self._set_status(f"프로필 '{label}' 삭제 — 하단 [저장] 시 반영됩니다.")

    # ── AI 팔레트 타겟(Alt+` 자유질문 목적지) ────────────────────────────────────
    def _load_palette_sites(self):
        """저장된 목록(없으면 기본값)으로 행들을 채운다."""
        sites = ai_palette.load_sites(self._settings.get(self.KEY_AI_PALETTE_SITES, ""))
        for site in sites:
            self._add_palette_row(site)
        self._renumber_palette_rows()

    def _add_palette_row(self, site: dict):
        row = _PaletteSiteRow(site)
        row.remove_requested.connect(self._on_remove_palette_row)
        row.drag_started.connect(self._on_palette_drag_started)
        row.drag_moved.connect(self._on_palette_drag_moved)
        row.drag_ended.connect(self._on_palette_drag_ended)
        self._palette_rows.append(row)
        self._palette_rows_layout.addWidget(row)

    def _on_add_palette_site(self):
        self._add_palette_row({"label": "", "keyword": "", "kind": ai_palette.KIND_URL, "url": ""})
        self._renumber_palette_rows()

    def _on_remove_palette_row(self, row: "_PaletteSiteRow"):
        if row in self._palette_rows:
            self._palette_rows.remove(row)
        self._palette_rows_layout.removeWidget(row)
        row.deleteLater()
        self._renumber_palette_rows()

    def _on_palette_drag_started(self, row: "_PaletteSiteRow"):
        self._palette_drag_row = row

    def _on_palette_drag_moved(self, row: "_PaletteSiteRow", global_y: int):
        """드래그 중인 행이 이웃 행의 세로 중심을 넘으면 그 자리로 옮긴다(Sortable류 임계 교차 방식)."""
        if getattr(self, "_palette_drag_row", None) is not row:
            return
        cur_index = self._palette_rows.index(row)
        for i, other in enumerate(self._palette_rows):
            if other is row:
                continue
            mid_y = other.mapToGlobal(QPoint(0, 0)).y() + other.height() // 2
            if (i < cur_index and global_y < mid_y) or (i > cur_index and global_y > mid_y):
                self._move_palette_row(cur_index, i)
                return

    def _on_palette_drag_ended(self, row: "_PaletteSiteRow"):
        self._palette_drag_row = None

    def _move_palette_row(self, from_i: int, to_i: int):
        """행 위젯을 물리적으로 새 위치로 옮긴다(값 스왑이 아니라 위젯 자체 이동 —
        드래그는 커서를 따라 실제로 자리를 옮겨야 자연스럽다)."""
        row = self._palette_rows.pop(from_i)
        self._palette_rows.insert(to_i, row)
        self._palette_rows_layout.removeWidget(row)
        self._palette_rows_layout.insertWidget(to_i, row)
        self._renumber_palette_rows()

    def _renumber_palette_rows(self):
        """표시 번호(=Alt+숫자)를 화면 순서에 맞게 갱신한다."""
        for i, row in enumerate(self._palette_rows):
            row.set_number(i + 1)

    def _cached_models(self) -> list[str]:
        """모델 캐시(JSON list)를 파싱해 모델명 목록 반환. 없으면 빈 목록."""
        import json
        cache_str = self._settings.get(self.KEY_OCR_GEMINI_MODEL_CACHE_GATEWAY, "")
        if not cache_str:
            return []
        try:
            parsed = json.loads(cache_str)
            return [str(m) for m in parsed if m] if isinstance(parsed, list) else []
        except (json.JSONDecodeError, ValueError, TypeError):
            return []

    def _init_model_slots(self):
        """OCR·AI 모델 1 콤보를 캐시로 채우고 저장된 모델명을 복원한다."""
        cached = self._cached_models()
        self._fill_model_combo(self._model_combo, cached)
        saved_ai = self._settings.get(self.KEY_OCR_GEMINI_MODEL_GATEWAY, "")
        if saved_ai:
            self._model_combo.setCurrentText(saved_ai)

        self._fill_model_combo(self._ocr_model_combo, cached)
        # OCR 슬롯이 비었으면(분리 이전 사용자) AI 모델을 그대로 보여준다 —
        # main._resolve_gemini_cfg("ocr")의 폴백과 같은 규칙이라 화면과 실동작이 일치한다.
        saved_ocr = self._settings.get(self.KEY_OCR_MODEL_GATEWAY, "") or saved_ai
        if saved_ocr:
            self._ocr_model_combo.setCurrentText(saved_ocr)

    # ── 비교 슬롯(모델 2·3) ────────────────────────────────────────────────────
    def _compare_slot_widgets(self, slot: str):
        """slot='a'|'b' → (model_combo, probe_label)."""
        if slot == "a":
            return (self._compare_model_a_combo, self._compare_a_probe_status)
        return (self._compare_model_b_combo, self._compare_b_probe_status)

    def _fill_compare_slot(self, slot: str, *, preserve: bool = True):
        """비교 슬롯 모델 콤보를 캐시로 채운다(선택값 보존 옵션)."""
        model_combo, _ = self._compare_slot_widgets(slot)
        current = model_combo.currentText() if preserve else ""
        self._fill_compare_combo(model_combo, self._cached_models())
        if current and current != self._COMPARE_UNUSED:
            idx = model_combo.findText(current)
            if idx >= 0:
                model_combo.setCurrentIndex(idx)
            else:
                model_combo.setCurrentText(current)  # 캐시에 없어도 사용자 입력 보존

    def _init_compare_slots(self):
        """저장값으로 비교 슬롯 모델을 채운다(_load_values에서 1회 호출)."""
        for slot, mkey in (
            ("a", self.KEY_AI_COMPARE_MODEL_A),
            ("b", self.KEY_AI_COMPARE_MODEL_B),
        ):
            model_combo, _ = self._compare_slot_widgets(slot)
            self._fill_compare_slot(slot, preserve=False)  # 캐시로 채우고 '미사용'으로
            saved = (self._settings.get(mkey, "") or "").strip()
            if saved:
                idx = model_combo.findText(saved)
                if idx >= 0:
                    model_combo.setCurrentIndex(idx)
                else:
                    model_combo.setCurrentText(saved)

    def _add_group_header(self, combo: QComboBox, text: str):
        """선택 불가능한 계열 헤더 행을 추가한다 (예: "Gemini (6)").

        헤더를 **비활성 항목**으로 넣는 이유: 이 콤보는 editable이라 표시 텍스트가 곧
        저장되는 모델명이다(`_on_save`가 `currentText()`를 그대로 쓴다). 헤더를 고를 수
        있으면 "Gemini (6)" 같은 문자열이 모델명으로 저장돼 API 호출이 깨진다.
        비활성 항목은 마우스·키보드로 선택할 수 없다.
        """
        combo.addItem(text)
        idx = combo.count() - 1
        item = combo.model().item(idx)
        item.setEnabled(False)
        f = item.font()
        f.setBold(True)
        item.setFont(f)
        item.setForeground(QColor(COLORS['subtext0']))
        # 델리게이트가 이 표시를 보고 헤더는 들여쓰지 않는다.
        combo.setItemData(idx, True, _HEADER_ROLE)

    def _fill_model_combo(self, combo: QComboBox, candidates: list[str]):
        """콤보를 계열 헤더 + 모델명으로 채운다.

        **상태 배지는 달지 않는다.** 옛 버전은 `model_matrix.json`(빌드타임 전수 스윕)을
        읽어 🚫/⚠/📝/❓ 아이콘을 달고 🚫는 선택까지 막았는데, 그 판정은 스윕 시점
        스냅샷이라 게이트웨이가 라인업을 바꾸면 낡았다. 낡은 🚫는 **멀쩡한 모델을 영구히
        못 고르게 만들고** 사용자에겐 우회 수단이 없었다(데이터가 exe에 번들됨).

        어떤 모델이 실제로 되는지는 이제 `연결 테스트` 버튼이 **선택된 두 모델을 그 자리에서
        실호출**해 알려준다(`_on_test_api`) — 항상 최신이고, 판정 근거가 방금 온 응답이다.
        """
        from pasteflow.ocr_engine import group_models

        combo.clear()
        for family, names in group_models(candidates):
            self._add_group_header(combo, f"{family} ({len(names)})")
            for name in names:
                combo.addItem(name)

        self._select_first_enabled(combo)
        self._adjust_model_popup_width(combo)

    def _fill_compare_combo(self, combo: QComboBox, candidates: list[str]):
        """비교 모델 콤보를 '(사용 안 함)' + 계열 헤더 + 모델명으로 채운다(기본=미사용).

        기본 콤보(_fill_model_combo)와 달리 첫 항목이 '(사용 안 함)'이고 기본 선택도 그것 —
        비교는 옵트인이라 아무것도 안 고른 상태가 정상이다. 나머지는 동일(헤더는 선택 불가).
        """
        from pasteflow.ocr_engine import group_models

        combo.clear()
        combo.addItem(self._COMPARE_UNUSED)
        for family, names in group_models(candidates):
            self._add_group_header(combo, f"{family} ({len(names)})")
            for name in names:
                combo.addItem(name)
        combo.setCurrentIndex(0)  # 기본 = 사용 안 함
        self._adjust_model_popup_width(combo)

    def _compare_value(self, combo: QComboBox) -> str:
        """비교 콤보의 저장값 — '(사용 안 함)'/빈 값은 빈 문자열로 환원."""
        text = combo.currentText().strip()
        return "" if text == self._COMPARE_UNUSED else text

    def _select_first_enabled(self, combo: QComboBox):
        """현재 선택이 비활성 헤더에 걸려 있으면 실제 모델로 옮긴다.

        `clear()` 직후 첫 addItem이 헤더면 Qt가 currentIndex=0으로 잡아 헤더 문자열이
        `currentText()`(= 저장되는 모델명)가 된다. 호출부가 이후 저장된 선택을 복원하지만,
        복원할 값이 없는 첫 실행을 대비한 안전망.

        안전망 모델(`_FALLBACK_DEFAULT`)이 목록에 있으면 그것을 고른다 — 계열 안이
        이름순이라 그냥 첫 항목을 잡으면 공식 백엔드에서 `gemini-2.0-flash` 같은 구형
        모델이 기본값이 된다.
        """
        idx = combo.currentIndex()
        if idx >= 0 and combo.model().item(idx).isEnabled():
            return

        from pasteflow.ocr_engine import _FALLBACK_DEFAULT
        preferred = combo.findText(_FALLBACK_DEFAULT)
        if preferred >= 0 and combo.model().item(preferred).isEnabled():
            combo.setCurrentIndex(preferred)
            return
        for i in range(combo.count()):
            if combo.model().item(i).isEnabled():
                combo.setCurrentIndex(i)
                return

    def _adjust_model_popup_width(self, combo: QComboBox):
        """드롭다운 팝업만 최장 모델명에 맞춰 넓힌다 — 콤보 본체/설정창 폭은 불변.

        QComboBox 팝업은 기본적으로 콤보 위젯 폭에 묶여 긴 이름의 가운데가
        생략된다(`gemini-3.1-p...-customtools`). 모델명이 공통 접두사를
        공유하므로 가운데 생략은 구분을 망가뜨린다. 팝업 view의 최소 폭을
        실제 텍스트 폭에 맞춰 늘려 잘림 자체를 없앤다.
        """
        fm = QFontMetrics(combo.view().font())
        widest = 0
        for i in range(combo.count()):
            text = combo.itemText(i)
            if not text:
                continue
            # 모델 행은 델리게이트가 들여쓰므로 그만큼 더 필요하다(헤더는 제자리).
            pad = 0 if combo.itemData(i, _HEADER_ROLE) else _MODEL_INDENT_PX
            widest = max(widest, fm.horizontalAdvance(text) + pad)
        if widest:
            # 스크롤바·여백 여유분 가산
            combo.view().setMinimumWidth(widest + 40)

    def _refill_model_slots(self, candidates: list[str]):
        """새로고침으로 받은 모델 목록을 네 모델 행(OCR·모델 1·2·3)에 모두 반영한다.

        현재 선택은 보존한다. 캐시가 비면 첫 실행처럼 빈 콤보(+placeholder)로 둔다.
        """
        rows = (
            (self._ocr_model_combo, False),
            (self._model_combo, False),
            (self._compare_model_a_combo, True),
            (self._compare_model_b_combo, True),
        )
        for combo, is_compare in rows:
            current = combo.currentText()
            combo.setUpdatesEnabled(False)
            try:
                if is_compare:
                    self._fill_compare_combo(combo, candidates)
                    if current and current != self._COMPARE_UNUSED:
                        idx = combo.findText(current)
                        combo.setCurrentIndex(idx) if idx >= 0 else combo.setCurrentText(current)
                else:
                    self._fill_model_combo(combo, candidates)
                    if current:
                        idx = combo.findText(current)
                        combo.setCurrentIndex(idx) if idx >= 0 else combo.setCurrentText(current)
                le = combo.lineEdit()
                if le is not None:
                    le.deselect()
                    le.setCursorPosition(0)
            finally:
                combo.setUpdatesEnabled(True)

    def _set_status(self, message: str, ok: bool | None = None):
        """모델 새로고침(↻)이 쓰는 상태 줄. ok=None이면 중립 색.

        연결 테스트도 같은 라벨(`_test_status`)에 쓰지만 그쪽은 `_set_probe_status`를 탄다.
        """
        color = COLORS['subtext0'] if ok is None else (
            COLORS['green'] if ok else COLORS['red'])
        self._test_status.setStyleSheet(f"color: {color}; font-size: 11px;")
        self._test_status.setText(message)
        self._test_status.setVisible(True)

    def _make_probe_label(self) -> QLabel:
        """콤보 아래에 붙는 프로브 결과 줄. 결과가 있을 때만 보인다."""
        label = QLabel("")
        label.setWordWrap(True)
        label.setVisible(False)
        label.setStyleSheet(f"color: {COLORS['subtext0']}; font-size: 11px;")
        return label

    def _stack(self, *widgets) -> QVBoxLayout:
        """폼 한 칸에 위젯을 세로로 쌓는다(콤보 + 그 아래 결과 줄)."""
        box = QVBoxLayout()
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(2)
        for w in widgets:
            box.addWidget(w)
        return box

    def _set_probe_status(self, label: QLabel, status: str, detail: str):
        """모델별 프로브 결과 한 줄. 빈 detail이면 라벨을 비우고 숨긴다."""
        if not detail:
            label.clear()
            label.setVisible(False)
            return
        mark, color = _PROBE_STYLE.get(status, _PROBE_STYLE["run"])
        label.setStyleSheet(f"color: {color}; font-size: 11px;")
        label.setText(f"{mark} {detail}".strip())
        label.setVisible(True)

    def _on_refresh_models(self):
        """↻ 버튼 — 게이트웨이에서 모델 목록 조회 (워커 스레드)."""
        api_key, base_url = self._creds()
        if not api_key:
            self._set_status("✗ 먼저 API 키를 입력하세요.", ok=False)
            return

        self._refresh_btn.setEnabled(False)
        self._refresh_btn.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserStop))
        self._set_status("모델 목록 조회 중…")

        import threading
        def _worker():
            try:
                from pasteflow.ocr_engine import OcrEngine
                models = OcrEngine.list_gemini_models(api_key, base_url)
                self._models_fetched.emit(models, "")
            except Exception as e:
                self._models_fetched.emit([], str(e))
        threading.Thread(target=_worker, daemon=True).start()

    def _on_models_fetched(self, models: list, err: str):
        """워커 스레드 결과 반영 (Qt 메인 스레드) — 네 모델 행을 모두 갱신."""
        self._refresh_btn.setEnabled(True)
        self._refresh_btn.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload))

        if err:
            self._set_status(f"✗ 모델 조회 실패 — {err}", ok=False)
            return
        if not models:
            self._set_status("✗ 응답에 사용 가능한 모델이 없습니다. 설정을 확인하세요.", ok=False)
            return

        unique = sorted(set(models))
        import json
        self._settings[self.KEY_OCR_GEMINI_MODEL_CACHE_GATEWAY] = json.dumps(unique)
        self._refill_model_slots(unique)

        # ↻는 '어떤 모델이 있는지'만 안다. '되는지'는 연결 테스트가 실호출로 답한다.
        self._set_status(
            f"✓ 모델 {len(unique)}종을 불러왔습니다. 고른 모델이 실제로 되는지는 "
            f"[연결 테스트]로 확인하세요.", ok=True)

    def _on_save(self):
        """저장 버튼 클릭 — 레지스트리 등록은 main._on_settings_changed에서 처리."""
        import json
        auto_start = self._auto_start_check.isChecked()

        new_settings = {
            self.KEY_PANEL_TOGGLE: self._panel_toggle_hotkey.value() or "ctrl+space",
            self.KEY_OCR_HOTKEY: self._ocr_hotkey.value() or "ctrl+shift+s",
            self.KEY_IMAGE_TO_PATH_HOTKEY: self._image_to_path_hotkey.value() or "ctrl+shift+p",
            self.KEY_SEQ_IMAGE_TO_PATH_HOTKEY: self._seq_image_to_path_hotkey.value() or "ctrl+shift+[",
            self.KEY_PIN_IMAGE_HOTKEY: self._pin_image_hotkey.value() or "alt+f3",
            self.KEY_SEQ_PIN_HOTKEY: self._seq_pin_hotkey.value() or "alt+shift+f3",
            self.KEY_CAPTURE_HOTKEY: self._capture_hotkey.value() or "alt+f2",
            self.KEY_RECORD_GIF_HOTKEY: self._record_gif_hotkey.value() or "ctrl+shift+g",
            self.KEY_ASK_AI_HOTKEY: self._ask_ai_hotkey.value() or "alt+`",
            self.KEY_CAPTURE_FOLDER: self._capture_folder_edit.text(),
            # OCR은 별도 엔진 선택 없이 항상 AI(Gemini/Mindlogic) API로 처리 → kind 고정.
            self.KEY_OCR_ENGINE: "gemini",
            self.KEY_HISTORY_MAX: str(self._history_max_spin.value()),
            self.KEY_QUEUE_IDLE_RESET: str(self._queue_idle_spin.value()),
            self.KEY_AUTO_START: "1" if auto_start else "0",
            self.KEY_NOTIFY_ON_COPY: "1" if self._notify_copy_check.isChecked() else "0",
            # AI 시스템 프롬프트 — 비우면 엔진이 기본값으로 폴백(빈 문자열 그대로 저장).
            self.KEY_AI_SYSTEM_PROMPT: self._ai_prompt_edit.toPlainText().strip(),
        }
        # 크리덴셜 — 화면 편집칸에서 직접 읽어 저장.
        new_settings[self.KEY_OCR_GEMINI_API_KEY_GATEWAY] = self._gateway_key_edit.text()
        new_settings[self.KEY_OCR_GEMINI_BASE_URL] = self._base_url_edit.text()

        # 모델 4행.
        new_settings[self.KEY_OCR_GEMINI_MODEL_GATEWAY] = self._model_combo.currentText()
        new_settings[self.KEY_OCR_MODEL_GATEWAY] = self._ocr_model_combo.currentText()
        # 비교 모델(2·3)은 '여러 모델 비교 사용'이 켜졌을 때만 저장한다 — 꺼져 있으면
        # 빈 값으로 비워 질문창의 비교 옵션도 함께 사라진다("숨김=미사용"과 일치).
        compare_on = self._compare_enable_check.isChecked()
        new_settings[self.KEY_AI_COMPARE_MODEL_A] = (
            self._compare_value(self._compare_model_a_combo) if compare_on else "")
        new_settings[self.KEY_AI_COMPARE_MODEL_B] = (
            self._compare_value(self._compare_model_b_combo) if compare_on else "")

        # 구글 드라이브 — secret 2종은 main._SECRET_KEYS가 DPAPI로 암호화해 저장한다.
        new_settings[self.KEY_GDRIVE_CLIENT_ID] = self._gdrive_client_id_edit.text().strip()
        new_settings[self.KEY_GDRIVE_CLIENT_SECRET] = self._gdrive_client_secret_edit.text().strip()
        new_settings[self.KEY_GDRIVE_REFRESH_TOKEN] = self._gdrive_refresh

        # 모델 캐시(↻로 갱신된 값)는 로드값을 그대로 실어 보낸다 — 안 보내면 사라진다.
        cache_key = self.KEY_OCR_GEMINI_MODEL_CACHE_GATEWAY
        if cache_key in self._settings:
            new_settings[cache_key] = self._settings[cache_key]

        # API 프로필 목록 + 마지막 선택 라벨. ai_profiles는 api_key를 품으므로
        # main._SECRET_KEYS가 JSON 통째로 DPAPI 암호화한다.
        new_settings[self.KEY_AI_PROFILES] = json.dumps(self._profiles, ensure_ascii=False)
        new_settings[self.KEY_AI_ACTIVE_PROFILE] = self._profile_combo.currentText()

        # AI 팔레트 타겟 — 화면의 각 행을 순서 그대로 직렬화(순서=Alt+숫자 번호).
        new_settings[self.KEY_AI_PALETTE_SITES] = ai_palette.dump_sites(
            [row.to_dict() for row in self._palette_rows])

        self.settings_changed.emit(new_settings)
        self.accept()
