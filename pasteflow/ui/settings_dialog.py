"""설정 다이얼로그 — F10

단축키 커스터마이징, 히스토리 제한, 자동 시작, 자동 닫기 설정.
"""
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QSpinBox, QCheckBox, QGroupBox, QFormLayout, QGridLayout, QComboBox, QLineEdit,
    QStyle, QStyledItemDelegate, QFileDialog, QScrollArea, QWidget, QFrame, QApplication,
    QPlainTextEdit,
)
from PyQt6.QtCore import Qt, pyqtSignal, QEvent, QSize
from PyQt6.QtGui import QColor, QFontMetrics

from pasteflow.ui.theme import COLORS, PEACH_HOVER


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
    QCheckBox::indicator:checked {{
        background-color: {COLORS['peach']};
        border-color: {COLORS['peach']};
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
"""


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
    KEY_CAPTURE_HOTKEY = "hotkey_capture"
    KEY_ASK_AI_HOTKEY = "hotkey_ask_ai"
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
        content = self._content.sizeHint()
        btn_h = self._btn_bar.sizeHint().height()
        screen = self.screen() or QApplication.primaryScreen()
        ag = screen.availableGeometry() if screen else None
        avail_w = ag.width() if ag else 1200
        avail_h = ag.height() if ag else 1000
        w = min(content.width() + 16, avail_w - 80)   # +16: 세로 스크롤바 여유
        h = min(content.height() + btn_h + 2, avail_h - 64)
        self.setFixedSize(max(360, w), max(420, h))

    def _setup_ui(self):
        # 콘텐츠를 스크롤 영역에 담아 창 높이와 분리(고정 높이가 콘텐츠를 압박해
        # 드래그 시 떨리던 문제 해결). 버튼은 스크롤 밖에 둬 항상 노출.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(scroll, 1)

        self._content = QWidget()
        scroll.setWidget(self._content)
        layout = QVBoxLayout(self._content)
        layout.setSpacing(6)
        layout.setContentsMargins(16, 12, 16, 12)

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

        # 배치 순서: 기본 단축키(고정) 먼저, 그다음 기능 단축키(변경 가능)
        layout.addWidget(info_group)
        layout.addWidget(hotkey_group)

        _combo_style = (
            f"QComboBox {{ background-color: {_INSET}; color: {_TXT}; "
            f"border: 1px solid {_LINE}; border-radius: 5px; padding: 5px 8px; }}"
            f"QComboBox:focus {{ border-color: {COLORS['peach']}; }}"
        )

        # ── AI 연동 그룹 (Gemini / Mindlogic API) ──
        # OCR(텍스트 인식)과 AI 답변(우클릭 'AI에게 질문')이 동일 API를 공유한다.
        # OCR은 별도 엔진 선택 없이 이 API로 처리하므로(WinRT 제거) 항상 키가 필요하다.
        ai_group = QGroupBox("AI 연동 (Gemini / Mindlogic API)")
        self._ai_form = QFormLayout(ai_group)
        ai_form = self._ai_form
        ai_form.setVerticalSpacing(4)
        ai_form.setContentsMargins(10, 8, 10, 8)

        ai_desc = QLabel("AI 호출 및 AI OCR 사용 시 필수 입력.")
        ai_desc.setStyleSheet(f"color: {COLORS['subtext0']}; font-size: 11px;")
        ai_desc.setWordWrap(True)
        ai_form.addRow(ai_desc)

        # 크리덴셜 — Mindlogic 게이트웨이 한 벌뿐이다(v1.50.0에서 Google AI Studio 제거).
        # 크리덴셜 섹션과 모델 섹션은 얇은 구분선으로 나눈다(기능 단축키 그룹과 같은 방식).
        def _ai_sep() -> QFrame:
            line = QFrame()
            line.setFrameShape(QFrame.Shape.HLine)
            line.setFrameShadow(QFrame.Shadow.Plain)
            line.setStyleSheet(f"color:{_LINE}; background-color:{_LINE};")
            line.setFixedHeight(1)
            return line

        self._gateway_key_edit = QLineEdit()
        self._gateway_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._gateway_key_edit.setPlaceholderText("Mindlogic API 키")
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
        gw_row.addWidget(self._refresh_btn)
        ai_form.addRow(QLabel("•  Mindlogic API 키:"), gw_row)

        self._base_url_edit = QLineEdit()
        self._base_url_edit.setPlaceholderText("예: https://factchat-cloud.mindlogic.ai/v1/gateway")
        ai_form.addRow(QLabel("•  Mindlogic Base URL:"), self._base_url_edit)

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
        ai_form.addRow(self._compare_a_label, self._stack(
            self._compare_model_a_combo, self._compare_a_probe_status))
        ai_form.addRow(self._compare_b_label, self._stack(
            self._compare_model_b_combo, self._compare_b_probe_status))

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

        # ── AI 시스템 프롬프트(멘토 페르소나) 편집 ──
        # AI 질문·답변의 답변 톤·구조를 정하는 시스템 프롬프트. 비워 두면 기본값
        # (ocr_engine.AI_SYSTEM_PROMPT)으로 폴백한다. OCR(글자 추출)에는 영향 없음.
        prompt_header = QHBoxLayout()
        prompt_header.setContentsMargins(0, 0, 0, 0)
        prompt_header.setSpacing(8)
        prompt_label = QLabel("AI 시스템 프롬프트")
        prompt_label.setStyleSheet(f"color: {_TITLE}; font-weight: 600;")
        self._reset_ai_prompt_btn = QPushButton("기본값으로 되돌리기")
        self._reset_ai_prompt_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._reset_ai_prompt_btn.setToolTip("입력칸을 PasteFlow 기본 멘토 프롬프트로 되돌립니다.")
        self._reset_ai_prompt_btn.clicked.connect(self._on_reset_ai_prompt)
        prompt_header.addWidget(prompt_label)
        prompt_header.addStretch(1)
        prompt_header.addWidget(self._reset_ai_prompt_btn)
        ai_form.addRow(prompt_header)

        self._ai_prompt_edit = QPlainTextEdit()
        self._ai_prompt_edit.setMinimumHeight(120)
        self._ai_prompt_edit.setMaximumHeight(180)
        # DIALOG_STYLE은 QLineEdit/QSpinBox만 스타일하므로 QPlainTextEdit엔 입력칸 스타일을
        # 명시 적용(밝은 글자 + inset 배경) — 안 하면 다크 배경에 검은 글자로 안 보인다.
        self._ai_prompt_edit.setStyleSheet(
            f"QPlainTextEdit {{ background-color: {_INSET}; color: {_TXT}; "
            f"border: 1px solid {_LINE}; border-radius: 5px; padding: 5px 8px; }}"
            f"QPlainTextEdit:focus {{ border-color: {COLORS['peach']}; }}"
        )
        self._ai_prompt_edit.setToolTip(
            "AI 질문·답변의 답변 방식(멘토 페르소나·답변 구조·형식)을 정합니다.\n"
            "OCR(글자 추출)에는 영향이 없습니다. 비워 두면 기본값이 적용됩니다."
        )
        ai_form.addRow(self._ai_prompt_edit)

        layout.addWidget(ai_group)

        # ── 구글 드라이브 그룹 (선택) ──
        # 연결하면 AI 질의가 내 드라이브 문서를 검색해 근거로 삼는다(읽기 전용).
        # 안 하면 도구가 조용히 빠질 뿐 웹 검색·AI 답변은 그대로 동작한다(우아한 열화).
        gd_group = QGroupBox("구글 드라이브 (선택)")
        gd_form = QFormLayout(gd_group)
        gd_form.setVerticalSpacing(4)
        gd_form.setContentsMargins(10, 8, 10, 8)

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

        layout.addWidget(gd_group)

        # ── 일반 설정 그룹 ──
        general_group = QGroupBox("일반")
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
        general_form.addRow(self._auto_start_check)

        self._notify_copy_check = QCheckBox("복사 시 우하단 알림 표시")
        general_form.addRow(self._notify_copy_check)

        layout.addWidget(general_group)

        # 콘텐츠가 창보다 짧을 때 남는 세로 공간을 아래로 모아 그룹들이 자연 크기 유지
        layout.addStretch()

        # ── 버튼 바 (스크롤 밖, 항상 노출) ──
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
        cmp_a = self._compare_value(self._compare_model_a_combo)  # 질의 모델 2 ("" = 미설정)
        cmp_b = self._compare_value(self._compare_model_b_combo)  # 질의 모델 3

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
        self._capture_hotkey.set_value(
            self._settings.get(self.KEY_CAPTURE_HOTKEY, "alt+f2")
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

    def _on_reset_ai_prompt(self):
        """'기본값으로 되돌리기' — 입력칸을 기본 멘토 프롬프트로 채운다."""
        self._ai_prompt_edit.setPlainText(self._default_ai_prompt())

    def _creds(self) -> tuple[str, str]:
        """게이트웨이 (api_key, base_url) — 편집칸에서 직접 읽는다."""
        return self._gateway_key_edit.text().strip(), self._base_url_edit.text().strip()

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
        auto_start = self._auto_start_check.isChecked()

        new_settings = {
            self.KEY_PANEL_TOGGLE: self._panel_toggle_hotkey.value() or "ctrl+space",
            self.KEY_OCR_HOTKEY: self._ocr_hotkey.value() or "ctrl+shift+s",
            self.KEY_IMAGE_TO_PATH_HOTKEY: self._image_to_path_hotkey.value() or "ctrl+shift+p",
            self.KEY_SEQ_IMAGE_TO_PATH_HOTKEY: self._seq_image_to_path_hotkey.value() or "ctrl+shift+[",
            self.KEY_PIN_IMAGE_HOTKEY: self._pin_image_hotkey.value() or "alt+f3",
            self.KEY_CAPTURE_HOTKEY: self._capture_hotkey.value() or "alt+f2",
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
        new_settings[self.KEY_AI_COMPARE_MODEL_A] = self._compare_value(self._compare_model_a_combo)
        new_settings[self.KEY_AI_COMPARE_MODEL_B] = self._compare_value(self._compare_model_b_combo)

        # 구글 드라이브 — secret 2종은 main._SECRET_KEYS가 DPAPI로 암호화해 저장한다.
        new_settings[self.KEY_GDRIVE_CLIENT_ID] = self._gdrive_client_id_edit.text().strip()
        new_settings[self.KEY_GDRIVE_CLIENT_SECRET] = self._gdrive_client_secret_edit.text().strip()
        new_settings[self.KEY_GDRIVE_REFRESH_TOKEN] = self._gdrive_refresh

        # 모델 캐시(↻로 갱신된 값)는 로드값을 그대로 실어 보낸다 — 안 보내면 사라진다.
        cache_key = self.KEY_OCR_GEMINI_MODEL_CACHE_GATEWAY
        if cache_key in self._settings:
            new_settings[cache_key] = self._settings[cache_key]
        self.settings_changed.emit(new_settings)
        self.accept()
