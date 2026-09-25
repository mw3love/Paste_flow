"""설정 다이얼로그 — F10

단축키 커스터마이징, 히스토리 제한, 자동 시작, 자동 닫기 설정.
"""
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QSpinBox, QCheckBox, QGroupBox, QFormLayout, QGridLayout, QComboBox, QLineEdit,
    QStyle, QStyledItemDelegate, QFileDialog, QScrollArea, QWidget, QFrame, QApplication,
    QTabWidget,
)
from PyQt6.QtCore import Qt, pyqtSignal, QEvent, QSize, QTimer
from PyQt6.QtGui import QColor, QFontMetrics

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

    # 녹화 시작/종료(True/False) — SettingsDialog가 모아서 전역 훅 suspend/resume에 쓴다
    # (녹화 중 옛 단축키를 눌러도 그 동작이 실행되지 않아야 재바인딩이 가능하다).
    listening_changed = pyqtSignal(bool)

    def __init__(self, parent=None, allow_mod_only: bool = False):
        """allow_mod_only=True면 Ctrl+Win처럼 일반키 없이 수식키만으로 된 조합도
        캡처할 수 있다(음성 입력의 Wispr Flow 스타일 제스처, 2026-08-02). 다른
        단축키(OCR·캡처 등)는 백엔드 파서가 수식키 전용 조합을 이해하지 못해
        조합이 영구히 안 눌리는 상태로 저장될 수 있으므로 기본은 False로 막는다."""
        super().__init__(parent)
        self._value = ""
        self._listening = False
        self._allow_mod_only = allow_mod_only
        # 리스닝 중 눌린 순수 수식키 집합. keyPressEvent는 일반키가 눌리는 순간 확정하고,
        # 일반키가 안 오고 수식키가 먼저 떼어지면(keyReleaseEvent) 지금까지 눌렸던
        # 수식키 조합으로 확정한다(allow_mod_only일 때만).
        self._held_mods: set = set()
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
        self._held_mods = set()
        self._update_display()
        self.grabKeyboard()
        self.listening_changed.emit(True)

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

        # 순수 modifier 키는 무시하고 계속 대기 — allow_mod_only면 나중에 keyReleaseEvent가
        # 이 집합을 보고 "일반키 없이 수식키만" 조합을 확정한다.
        if key in (Qt.Key.Key_Control, Qt.Key.Key_Alt, Qt.Key.Key_Shift, Qt.Key.Key_Meta):
            if self._allow_mod_only:
                self._held_mods.add(key)
            return

        # Escape → 취소
        if key == Qt.Key.Key_Escape:
            self._listening = False
            self._held_mods = set()
            self.releaseKeyboard()
            self._update_display()
            self.listening_changed.emit(False)
            return

        modifiers = event.modifiers()
        parts = []
        if modifiers & Qt.KeyboardModifier.ControlModifier:
            parts.append("ctrl")
        if modifiers & Qt.KeyboardModifier.AltModifier:
            parts.append("alt")
        if modifiers & Qt.KeyboardModifier.ShiftModifier:
            parts.append("shift")
        if self._allow_mod_only and (modifiers & Qt.KeyboardModifier.MetaModifier):
            parts.append("win")

        # 매핑 안 되는 키(미할당 가상 키 등)는 무시하고 계속 대기한다 — 그냥 넘기면
        # 아무 것도 캡처하지 않은 채 녹화가 취소돼 버린다. 훅이 Win 단독 눌림 오인을
        # 막으려 주입하는 더미 키(VK_MASK)가 바로 이런 매핑 안 되는 키라, 이 가드가
        # 없으면 그 더미 키 자체가 진행 중인 녹화를 끊어버린다(2026-08-03).
        key_name = self._qt_key_to_name(key)
        if not key_name:
            return
        parts.append(key_name)
        self._value = "+".join(parts)

        self._listening = False
        self._held_mods = set()
        self.releaseKeyboard()
        self._update_display()
        self.listening_changed.emit(False)

    def keyReleaseEvent(self, event):
        """`allow_mod_only`일 때만 — 일반키 없이 수식키를 뗀 순간 그 조합으로 확정한다
        (Ctrl+Win처럼 눌렀던 수식키 중 아무거나 먼저 떼면 완성, Wispr Flow와 동일 제스처).
        수식키 하나만 눌렀다 떼면(조합이 안 됨) 계속 대기한다."""
        if not self._listening or not self._allow_mod_only:
            super().keyReleaseEvent(event)
            return

        key = event.key()
        if key not in (Qt.Key.Key_Control, Qt.Key.Key_Alt, Qt.Key.Key_Shift, Qt.Key.Key_Meta):
            super().keyReleaseEvent(event)
            return

        if len(self._held_mods) >= 2:
            parts = []
            if Qt.Key.Key_Control in self._held_mods:
                parts.append("ctrl")
            if Qt.Key.Key_Alt in self._held_mods:
                parts.append("alt")
            if Qt.Key.Key_Shift in self._held_mods:
                parts.append("shift")
            if Qt.Key.Key_Meta in self._held_mods:
                parts.append("win")
            self._value = "+".join(parts)
            self._listening = False
            self._held_mods = set()
            self.releaseKeyboard()
            self._update_display()
            self.listening_changed.emit(False)

    def focusOutEvent(self, event):
        if self._listening:
            self._listening = False
            self._held_mods = set()
            self.releaseKeyboard()
            self._update_display()
            self.listening_changed.emit(False)
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
            # `]` 도 동일 — Shift 시 `}`(BraceRight)로 오므로 둘 다 "]"로 취급
            Qt.Key.Key_BracketRight: "]", Qt.Key.Key_BraceRight: "]",
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
    # True=단축키 녹화 시작, False=종료 — main이 이 동안 전역 훅(interceptor)을
    # suspend/resume해, 재바인딩하려는 옛 단축키를 눌러도 그 동작이 실행되지 않게 한다.
    recording_active = pyqtSignal(bool)

    # 설정 키 상수
    KEY_PANEL_TOGGLE = "hotkey_panel_toggle"
    KEY_HISTORY_MAX = "history_max"
    KEY_AUTO_START = "auto_start"
    KEY_NOTIFY_ON_COPY = "notify_on_copy"
    KEY_OCR_HOTKEY = "hotkey_ocr_trigger"
    KEY_IMAGE_TO_PATH_HOTKEY = "hotkey_image_to_path"
    KEY_SEQ_IMAGE_TO_PATH_HOTKEY = "hotkey_seq_image_to_path"
    KEY_BULK_PASTE_HOTKEY = "hotkey_bulk_paste"
    KEY_BULK_PATH_PASTE_HOTKEY = "hotkey_bulk_path_paste"
    KEY_PIN_IMAGE_HOTKEY = "hotkey_pin_image"
    KEY_CAPTURE_HOTKEY = "hotkey_capture"
    KEY_CAPTURE_USE_PRINTSCREEN = "capture_use_printscreen"
    KEY_RECORD_HOTKEY = "hotkey_record"  # GIF/영상 공용(영역 선택 뒤 방식 선택)
    KEY_GIF_SHOW_CURSOR = "gif_show_cursor"
    KEY_GIF_FPS = "gif_fps"
    KEY_GIF_MAX_SECONDS = "gif_max_seconds"
    KEY_VIDEO_FPS = "video_fps"
    KEY_VIDEO_MAX_SECONDS = "video_max_seconds"
    KEY_STT_HOTKEY = "hotkey_stt"
    KEY_STT_MIC_DEVICE = "stt_mic_device"  # 빈 문자열=시스템 기본, 아니면 특정 장치 이름
    KEY_CAPTURE_FOLDER = "capture_save_folder"
    KEY_OCR_ENGINE = "ocr_engine"
    # AI 크리덴셜 — Mindlogic 게이트웨이 한 벌(키 + Base URL)뿐이다.
    # v1.50.0: Google AI Studio(공식) 백엔드를 제거하고 backend 개념 자체를 없앴다.
    KEY_OCR_GEMINI_BASE_URL = "ocr_gemini_base_url"
    KEY_OCR_GEMINI_API_KEY_GATEWAY = "ocr_gemini_api_key_gateway"
    KEY_OCR_GEMINI_MODEL_CACHE_GATEWAY = "ocr_gemini_model_cache_gateway"
    # OCR 전용 모델 슬롯 — API 키를 쓰는 유일한 경로(v1.6x에서 AI 질의 기능 제거).
    KEY_OCR_MODEL_GATEWAY = "ocr_model_gateway"
    # STT(음성 입력) 전용 모델 슬롯 — Gemini 계열만 지원(2026-08-02 실측, ocr_model_gateway와
    # 분리하는 이유는 OCR/AI 모델 분리와 동일: 한 모델을 공유하면 GPT/Claude를 고른 경우
    # STT가 항상 400으로 실패한다).
    KEY_STT_MODEL_GATEWAY = "stt_model_gateway"
    KEY_QUEUE_IDLE_RESET = "queue_idle_reset_sec"

    # 워커 스레드 → UI 안전 통신용 내부 시그널 (models, error_msg)
    _models_fetched = pyqtSignal(list, str)  # (models, error)
    # 연결 테스트 단계별 결과 (run_id, slot, status, detail).
    # slot: "conn" | "ocr" | "credit" | "__end__"(버튼 복구 신호)
    # status: ProbeResult.status + "run"(진행 중) / "skip"(앞 단계 실패로 건너뜀)
    # run_id: 이 결과를 만든 테스트 회차. 최신 회차가 아니면 UI가 버린다(아래 _on_probe_done).
    # 크레딧 확인(2026-08-12)은 별도 버튼·시그널 없이 이 회차에 합류한다 — 연결 테스트와
    # 크레딧 확인 둘 다 "지금 이 키로 뭐가 되는지" 확인이라는 성격이 같아 한 클릭으로 묶었다
    # (사용자 요청, 모델조회는 콤보를 채우는 별개 준비 동작이라 그대로 분리 유지).
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

    def done(self, r):
        # 안전망 ①: accept()(저장)·reject()(취소·Esc)가 거치는 공통 종료 경로. 단축키
        # 필드가 리스닝 중인 채로 어떤 경로로 닫히든 recording_active(False)를 무조건
        # 한 번 더 보내 전역 훅이 suspend 상태에 영구히 갇히지 않게 한다(그러면 재시작
        # 전까지 전역 단축키가 전부 죽는다) — 이미 False면 main 쪽에서 idempotent.
        self.recording_active.emit(False)
        super().done(r)

    def closeEvent(self, event):
        # 안전망 ②: 네이티브 X 버튼·Alt+F4 등 done()을 안 거칠 수 있는 경로 대비
        # (오프스크린 테스트로 close()가 done()을 안 태우는 경우가 실제 관측됨).
        self.recording_active.emit(False)
        super().closeEvent(event)

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

        ⚠ **크기 조절 가능**(2026-07-29 사용자 요청 — 이전엔 `setFixedSize`라 Base URL
        같은 긴 값이 입력칸 안에서 잘려 보였는데도 창을 늘려 볼 수 없었다). `setFixedSize`
        대신 `setMinimumSize`(콘텐츠가 필요로 하는 최소치, 이보다 작아지면 다시 잘림)
        + `resize`(시작 폭을 최소치보다 조금 더 여유 있게)로 바꿔 사용자가 필요하면
        가로·세로를 자유롭게 늘릴 수 있다. 세로는 탭 전환으로 다른 탭의 최소 높이보다
        작아지는 일이 없도록 그대로 '가장 큰 탭' 기준을 최소값으로 쓴다.
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
        min_w = min(content_w + 24, avail_w - 80)   # +24: 세로 스크롤바 + 탭 프레임 여유
        min_h = min(content_h + tabbar_h + btn_h + 8, avail_h - 64)
        min_w = max(360, min_w)
        min_h = max(420, min_h)
        self.setMinimumSize(min_w, min_h)
        # 시작 폭은 최소치보다 60px 더 넓게 — Base URL 등 긴 입력값이 잘려 보이던 문제
        # 완화(사용자 실측 스크린샷: "tps://..."로 앞부분이 밀려 보임). 화면을 넘지 않게 cap.
        start_w = min(min_w + 60, avail_w - 80)
        self.resize(start_w, min_h)

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
        #  ① 패널  ② 경로 붙여넣기류  ③ 일괄 붙여넣기(벌크)  ④ 영역 캡처·핀류
        # AI 호출류(Gemini 호출·Gemini(캡처)·OCR·STT)는 「AI」 탭의 별도 그룹으로 옮겼다
        # (2026-08-04, 사용자 요청 — 이 4개만 AI 관련이라 AI 탭에 있는 편이 자연스럽다).
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
            "Claude Code CLI 등 '파일 경로 텍스트'를 첨부로 받는 앱에 한 키로 붙여넣기 위한 단축키.\n"
            "팁: 패널에서 이미지 항목을 Alt를 누른 채 그 앱으로 드래그해도 같은 방식(경로 붙여넣기)으로 동작합니다."
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

        # ③ 일괄 붙여넣기 — 순차/순차 경로 붙여넣기를 한 번에 큐 끝까지 자동 주입하는 '벌크' 버전.
        # 반복해 누르지 않고 한 번 눌러 여러 항목을 순서대로 붙여넣고 싶을 때 사용(2026-09-06 도입).
        self._bulk_paste_hotkey = HotkeyEdit()
        self._bulk_paste_hotkey.setToolTip(
            "순차 붙여넣기(Ctrl+Shift+V)의 '전체 자동주입' 버전.\n"
            "한 번 눌러 큐에 남은 항목 전체를 짧은 간격을 두고 순서대로 자동 붙여넣습니다.\n"
            "10개를 반복해 누르지 않고 한 번에 붙이고 싶을 때 사용합니다."
        )
        hotkey_form.addRow("•  순차 붙여넣기 전체:", self._bulk_paste_hotkey)

        self._bulk_path_paste_hotkey = HotkeyEdit()
        self._bulk_path_paste_hotkey.setToolTip(
            "순차 경로 붙여넣기(Ctrl+Shift+[)의 '전체 자동주입' 버전.\n"
            "큐에 남은 항목 전체를 순서대로 자동 붙여넣되, 이미지는 임시 PNG 경로 텍스트로 바꿔 붙입니다."
        )
        hotkey_form.addRow("•  순차 경로 붙여넣기 전체:", self._bulk_path_paste_hotkey)
        hotkey_form.addRow(_hk_sep())

        # ④ 영역 캡처(Alt+F2) / 핀(Alt+F3)
        self._capture_hotkey = HotkeyEdit()
        self._capture_hotkey.setToolTip(
            "화면 영역을 드래그로 선택해 캡처합니다(Snipaste의 영역 캡처).\n"
            "캡처 즉시 클립보드에 복사되고 지정 폴더에 PNG로 저장됩니다.\n"
            "ESC 또는 우클릭으로 취소합니다."
        )
        hotkey_form.addRow("•  영역 캡처:", self._capture_hotkey)

        self._capture_printscreen_check = QCheckBox("PrintScreen 키로도 실행")
        self._capture_printscreen_check.setToolTip(
            "PrintScreen 키를 단독으로 누르면(Alt/Ctrl/Shift/Win 없이) 위 영역 캡처와\n"
            "완전히 동일하게 동작합니다. Alt를 누르면 사라지는 메뉴 등을 캡처할 때 유용합니다.\n"
            "⚠ 켜면 Windows 기본 PrintScreen 동작(전체화면 클립보드 복사/스니핑 도구 실행)을\n"
            "대체합니다. Alt+PrtScn·Win+PrtScn 등 다른 조합은 그대로 OS가 처리합니다."
        )
        hotkey_form.addRow(_bullet_checkbox_row(self._capture_printscreen_check))

        self._pin_image_hotkey = HotkeyEdit()
        self._pin_image_hotkey.setToolTip(
            "순차 큐의 다음 항목을 화면 위에 떠 있는 창으로 띄웁니다(Snipaste의 화면 핀).\n"
            "예: 영역 캡처(Alt+F2)를 여러 장 찍은 뒤 이 키를 차례로 눌러 캡처1·캡처2… 순서대로 핀.\n"
            "큐가 비어 있으면 현재 클립보드를 핀합니다. 순차 붙여넣기와 같은 큐를 씁니다.\n"
            "같은 걸 또 띄우려면 핀 창 우클릭 → 복제. ESC로 닫고, Space로 주석 편집합니다."
        )
        hotkey_form.addRow("•  핀:", self._pin_image_hotkey)

        self._record_hotkey = HotkeyEdit()
        self._record_hotkey.setToolTip(
            "화면 영역을 드래그로 선택한 뒤, 뜨는 버튼에서 GIF 또는 영상(MP4)을 골라 녹화합니다.\n"
            "G=GIF, V=영상, Enter=지난번 선택, ESC=취소. 녹화 중 ■ 정지 또는 ESC로 끝냅니다.\n"
            "저장된 파일 경로가 클립보드에 복사됩니다."
        )
        hotkey_form.addRow("•  녹화:", self._record_hotkey)

        # ── AI 단축키 그룹 — Gemini 호출/Gemini(캡처)/OCR/STT(2026-08-04: 「일반」 탭
        # 기능 단축키에서 「AI」 탭으로 이동, 사용자 요청 — 이 4개만 AI 호출 기능이라
        # AI 탭에 있는 편이 자연스럽다). 회색 테두리(그룹박스 전역 스타일)와 OCR↔STT
        # 사이 구분선은 그대로 유지.
        ai_hotkey_group = QGroupBox("AI 단축키 (변경 가능)")
        ai_hotkey_form = QFormLayout(ai_hotkey_group)
        ai_hotkey_form.setVerticalSpacing(4)
        ai_hotkey_form.setContentsMargins(10, 8, 10, 8)

        # OCR(옛 이름 "AI OCR") — 화면 영역을 AI(설정된 API)로 텍스트 인식. 별도 엔진 없음.
        # 음성 입력(STT) 바로 위로 이동(2026-08-03, 사용자 요청) — 텍스트 인식·음성 인식이
        # 나란히 있는 편이 자연스럽다.
        self._ocr_hotkey = HotkeyEdit()
        self._ocr_hotkey.setToolTip(
            "화면 영역을 드래그로 선택해 그 안의 텍스트를 AI(설정된 API)로 인식합니다.\n"
            "결과 텍스트가 클립보드·히스토리에 들어갑니다."
        )
        ai_hotkey_form.addRow("•  OCR:", self._ocr_hotkey)
        ai_hotkey_form.addRow(_hk_sep())

        # 음성 입력(STT) — 누르고 있는 동안 녹음(푸시투토크), 떼면 인식 후 자동 붙여넣기.
        # allow_mod_only=True — Ctrl+Win처럼 일반키 없이 수식키만으로 된 조합도 캡처
        # 가능(Wispr Flow와 동일 제스처로 비교하려는 사용자 요청, 2026-08-02). 기본값
        # 자체가 ctrl+win(main.py)이라 여기서도 그 조합을 재캡처할 수 있어야 한다.
        self._stt_hotkey = HotkeyEdit(allow_mod_only=True)
        self._stt_hotkey.setToolTip(
            "누르고 있는 동안 마이크로 녹음하고, 떼는 순간 음성을 인식해 텍스트로\n"
            "변환한 뒤 포커스된 입력창에 자동으로 붙여넣습니다(최대 30초).\n"
            "Ctrl+Win처럼 일반키 없이 수식키만으로 된 조합도 가능합니다(Wispr Flow와 동일 제스처).\n"
            "게이트웨이 오디오 입력은 Gemini 계열 모델만 지원합니다 — 아래 STT 모델에서 선택하세요."
        )
        ai_hotkey_form.addRow("•  음성 입력(STT):", self._stt_hotkey)
        # tab_ai.addWidget(ai_hotkey_group)는 AI 탭 맨 아래(API 연동 그룹 다음)에 배치하려고
        # 여기서 바로 호출하지 않고 뒤로 미룬다(2026-08-04, 사용자 요청).

        # 녹화 시작/종료를 하나의 다이얼로그 시그널로 모은다 — main이 이걸로 전역 훅을
        # suspend/resume한다(어느 HotkeyEdit이든 녹화를 시작하면 suspend, 끝나면 resume).
        for _hk in (
            self._panel_toggle_hotkey, self._image_to_path_hotkey,
            self._seq_image_to_path_hotkey, self._capture_hotkey,
            self._pin_image_hotkey, self._record_hotkey,
            self._ocr_hotkey,
            self._stt_hotkey,
        ):
            _hk.listening_changed.connect(self.recording_active.emit)

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

        # ── AI 연결 그룹 (OpenAI 호환 API) ──
        # 이 API 키를 쓰는 경로는 OCR·STT뿐이다. 호출은 `openai` 패키지의 chat.completions
        # 라 프로토콜 이름(OpenAI 호환)으로 부른다 — Mindlogic은 그 프로토콜을 쓰는
        # 게이트웨이 회사명일 뿐이다(2026-09-25 사용자 요청으로 명칭 정리). 여러 API를
        # 전환하던 'API 프로필' 드롭다운은 실사용이 없어 같은 날 제거했다.
        ai_group = QGroupBox("AI 연결 (OpenAI 호환 API)")
        self._ai_form = QFormLayout(ai_group)
        ai_form = self._ai_form
        ai_form.setVerticalSpacing(4)
        ai_form.setContentsMargins(10, 8, 10, 8)

        # 섹션 구분선(크리덴셜 ↔ 모델).
        def _ai_sep() -> QFrame:
            line = QFrame()
            line.setFrameShape(QFrame.Shape.HLine)
            line.setFrameShadow(QFrame.Shadow.Plain)
            line.setStyleSheet(f"color:{_LINE}; background-color:{_LINE};")
            line.setFixedHeight(1)
            return line

        # 크리덴셜 — (API 키 + Base URL) 한 벌. 게이트웨이든 구글 직결이든 OpenAI 호환
        # 경로라 base_url만 바꾸면 된다(구글: .../v1beta/openai).
        # Base URL을 API 키보다 먼저 보여준다 — 엔드포인트를 먼저 정하고 그다음 키를
        # 입력하는 게 자연스러운 순서(어느 API인지 모르는 채로 키부터 채우면 어색하다).
        self._base_url_edit = QLineEdit()
        self._base_url_edit.setPlaceholderText(
            "OpenAI 호환 주소 — 예: https://…mindlogic.ai/v1/gateway"
            "  /  https://generativelanguage.googleapis.com/v1beta/openai")
        ai_form.addRow(QLabel("•  Base URL:"), self._base_url_edit)

        self._gateway_key_edit = QLineEdit()
        self._gateway_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._gateway_key_edit.setPlaceholderText("API 키")
        # 키 보기 토글 — Password↔평문. '무슨 키가 들었나' 확인용.
        # ⚠ 이모지(👁)는 Qt 컬러 이모지 폴백으로 버튼에서 깨져 렌더되므로(ai_query.py의
        # 🕘·🔀 제거 전례) 텍스트로 둔다.
        self._key_reveal_btn = QPushButton("보기")
        self._key_reveal_btn.setCheckable(True)
        # 40px는 '보기'/'숨김' 두 글자 + 버튼 패딩에 모자라 글자가 잘렸다 → 56px로.
        self._key_reveal_btn.setFixedWidth(56)
        self._key_reveal_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._key_reveal_btn.setToolTip("API 키 보기/숨기기")
        self._key_reveal_btn.toggled.connect(self._on_key_reveal_toggled)
        # 아이콘만으로는 뜻이 안 와닿는다는 사용자 피드백(2026-07-29)으로 "모델조회"
        # 텍스트를 붙였다 — 폭은 아이콘+글자에 맞춰 자연스럽게(고정폭 제거).
        self._refresh_btn = QPushButton("모델조회")
        # Qt 내장 표준 아이콘 — 폰트 의존성 없이 모든 환경에서 보장
        self._refresh_btn.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload))
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

        ai_form.addRow(_ai_sep())  # 크리덴셜 ↔ 모델 섹션 구분

        # OCR 모델 — 이미지 입력을 받는 모델이어야 한다.
        self._ocr_model_label = QLabel("•  OCR 모델:")
        self._ocr_model_combo = QComboBox()
        self._ocr_model_combo.setEditable(True)
        self._ocr_model_combo.setStyleSheet(_combo_style)
        self._ocr_model_combo.setToolTip(
            "이미지에서 텍스트를 추출할 때 쓰는 모델.\n"
            "이미지 입력을 받는 모델이어야 합니다 — [연결 테스트]로 확인하세요."
        )

        # 콤보 초기 채우기는 _load_values(_init_model_slots)에서 캐시를 읽어 수행한다.
        # 캐시가 없으면 빈 콤보라, 무엇을 해야 할지 placeholder로 안내한다(빈 값은 엔진
        # 기본 모델로 폴백).
        le = self._ocr_model_combo.lineEdit()
        if le is not None:
            le.setPlaceholderText("↻를 눌러 모델 목록을 불러오세요")
        # 계열 헤더 아래 모델명을 들여써 상하위를 구분(팝업 view 한정 — 닫힌 콤보는 불변).
        # 델리게이트는 combo를 부모로 둬야 GC로 사라지지 않는다.
        self._ocr_model_combo.view().setItemDelegate(_ModelIndentDelegate(self._ocr_model_combo))

        # 프로브 결과 줄 — 연결 테스트가 실호출해 여기에 쓴다.
        self._ocr_model_probe_status = self._make_probe_label()

        # 모델을 바꾸면 직전 결과는 다른 모델 이야기다 — 낡은 ✓를 남기면 그게 거짓말이 된다.
        self._ocr_model_combo.currentTextChanged.connect(
            lambda _t: self._on_model_text_changed(self._ocr_model_probe_status))

        # 연결 테스트 — 모델 콤보 오른쪽(다른 버튼들과 같은 자리)에 배치. "테스트"로
        # 줄였다가(칸이 좁던 시절) 창을 넓힌 뒤 다시 "연결 테스트"로 되돌렸다
        # (2026-07-29 사용자 요청). setFixedWidth는 쓰지 않는다 — 전역 QPushButton
        # padding(6px 16px)보다 좁게 고정하면 글자가 양옆으로 잘린다(실측 확인됨).
        # **크레딧 확인 병합(2026-08-12 사용자 요청)**: 원래 별도 버튼이던 크레딧 확인을
        # 여기 흡수했다 — 둘 다 "지금 이 키로 뭐가 되는지" 그 자리에서 확인하는 동일 성격.
        # 모델조회(콤보를 채우는 준비 동작, API 키 행 옆)는 성격이 달라 그대로 분리 유지.
        self._test_btn = QPushButton("연결 테스트")
        self._test_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._test_btn.setToolTip(
            "키·연결을 확인하고, OCR 모델에 작은 테스트 이미지를 1회 실제로 보내\n"
            "그 자리에서 되는지 확인한 뒤, 게이트웨이 크레딧 잔액도 함께 조회합니다.\n"
            "크레딧 조회는 Mindlogic 게이트웨이 전용 — 다른 API에서는 지원하지 않을 수 있습니다."
        )
        self._test_btn.clicked.connect(self._on_test_api)

        model_row = QHBoxLayout()
        model_row.setContentsMargins(0, 0, 0, 0)
        model_row.setSpacing(4)
        model_row.addWidget(self._ocr_model_combo, 1)
        model_row.addWidget(self._test_btn)
        ai_form.addRow(self._ocr_model_label, model_row)

        # 연결·모델·크레딧 프로브 결과 — 실행 순서(연결→모델→크레딧)대로 콤보 아래에 쌓는다.
        self._test_status = QLabel("")
        self._test_status.setWordWrap(True)
        self._test_status.setStyleSheet(f"color: {COLORS['subtext0']}; font-size: 11px;")
        self._credit_status = self._make_probe_label()
        ai_form.addRow("", self._stack(self._test_status, self._ocr_model_probe_status, self._credit_status))

        ai_form.addRow(_ai_sep())  # 모델 ↔ STT 모델 구분

        # STT 모델 — 음성 입력(Alt+R 기본) 전용. 게이트웨이 오디오 입력은 Gemini 계열만
        # 지원해(2026-08-02 실측: 18개 모델 실호출, Gemini 8/8·GPT/Claude/Grok/Perplexity
        # 0/10) 목록 자체를 Gemini로 필터링한다 — GPT/Claude를 골라 400을 겪을 일이 없다.
        self._stt_model_label = QLabel("•  STT 모델:")
        self._stt_model_combo = QComboBox()
        self._stt_model_combo.setEditable(True)
        self._stt_model_combo.setStyleSheet(_combo_style)
        self._stt_model_combo.setToolTip(
            "음성 입력(Alt+R 기본)에 쓰는 모델 — Gemini 계열만 표시됩니다.\n"
            "게이트웨이 오디오 입력이 Gemini 계열에서만 확인됐기 때문입니다(2026-08-02)."
        )
        le = self._stt_model_combo.lineEdit()
        if le is not None:
            le.setPlaceholderText("↻(모델조회)를 눌러 목록을 불러오세요")
        self._stt_model_combo.view().setItemDelegate(_ModelIndentDelegate(self._stt_model_combo))
        ai_form.addRow(self._stt_model_label, self._stt_model_combo)

        # 마이크 — 시스템 기본 입력 장치 또는 특정 장치를 지정(2026-08-02 사용자 요청).
        # 목록은 MME 호스트 API로 한정(중복 표기 방지 — stt_engine.list_input_devices 참고).
        self._mic_combo = QComboBox()
        self._mic_combo.setStyleSheet(_combo_style)
        self._mic_combo.setToolTip(
            "음성 입력에 쓸 마이크. '시스템 기본'을 고르면 Windows 설정의 기본 녹음\n"
            "장치를 그대로 따라가고, 특정 장치를 고르면 그 장치가 없어질 때까지 고정됩니다.\n"
            "저장된 장치를 찾을 수 없으면(USB 마이크 분리 등) 자동으로 시스템 기본으로 대체됩니다."
        )
        self._refresh_mic_btn = QPushButton("새로고침")
        self._refresh_mic_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._refresh_mic_btn.setToolTip("방금 연결한 마이크가 안 보이면 눌러서 목록을 다시 불러오세요")
        self._refresh_mic_btn.clicked.connect(self._reload_mic_combo)
        self._mic_test_btn = QPushButton("테스트")
        self._mic_test_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._mic_test_btn.setToolTip(
            "선택한 마이크로 2.5초간 녹음해 소리가 실제로 감지되는지 확인합니다.\n"
            "마이크가 무음이면 음성 입력(STT)이 게이트웨이의 엉뚱한 인식 결과를\n"
            "그대로 붙여넣을 수 있어(예: \"00:01\"), 미리 상태를 확인하는 용도입니다."
        )
        self._mic_test_btn.clicked.connect(self._on_test_mic)
        mic_row = QHBoxLayout()
        mic_row.setContentsMargins(0, 0, 0, 0)
        mic_row.setSpacing(4)
        mic_row.addWidget(self._mic_combo, 1)
        mic_row.addWidget(self._refresh_mic_btn)
        mic_row.addWidget(self._mic_test_btn)
        ai_form.addRow(QLabel("•  마이크:"), mic_row)
        self._mic_test_status = self._make_probe_label()
        ai_form.addRow("", self._mic_test_status)

        tab_ai.addWidget(ai_group)

        # AI 단축키 그룹은 AI 탭 맨 아래에 배치(2026-08-04, 사용자 요청 — 크리덴셜이
        # 우선이고 단축키는 참고용으로 하단에).
        tab_ai.addWidget(ai_hotkey_group)

        # ── 일반 설정 그룹 ── 「일반」 탭의 맨 위(아래 insertWidget(0, ...) 참고).
        # 탭 제목도 "일반"이라 그룹박스 제목까지 "일반"이면 중복으로 읽혀 "일반 설정"으로 구분.
        general_group = QGroupBox("일반 설정")
        general_form = QFormLayout(general_group)
        general_form.setVerticalSpacing(4)
        general_form.setContentsMargins(10, 8, 10, 8)

        def _gen_sep():
            line = QFrame()
            line.setFrameShape(QFrame.Shape.HLine)
            line.setFrameShadow(QFrame.Shadow.Plain)
            line.setStyleSheet(f"color:{_LINE}; background-color:{_LINE};")
            line.setFixedHeight(1)
            return line

        self._auto_start_check = QCheckBox("Windows 시작 시 자동 실행")
        general_form.addRow(_bullet_checkbox_row(self._auto_start_check))

        self._notify_copy_check = QCheckBox("복사 시 우하단 알림 표시")
        general_form.addRow(_bullet_checkbox_row(self._notify_copy_check))

        general_form.addRow(_gen_sep())

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

        # ── GIF/녹화 설정 그룹 ── 「일반 설정」과 별도 카드로 분리해 녹화 관련
        # 옵션(5개)을 한눈에 묶어 보여준다(2026-08-04 — 색깔 구분선 대신 그룹박스 분리로
        # 정리, 코랄 강조선은 이 카드 하나 크기에 비해 과했다는 사용자 피드백 반영).
        recording_group = QGroupBox("GIF/녹화 설정")
        recording_form = QFormLayout(recording_group)
        recording_form.setVerticalSpacing(4)
        recording_form.setContentsMargins(10, 8, 10, 8)

        self._gif_cursor_check = QCheckBox("GIF/영상 녹화 시 마우스 커서 표시")
        recording_form.addRow(_bullet_checkbox_row(self._gif_cursor_check))

        recording_form.addRow(_gen_sep())

        self._gif_fps_spin = QSpinBox()
        self._gif_fps_spin.setRange(1, 30)
        self._gif_fps_spin.setSuffix(" fps")
        self._gif_fps_spin.setValue(12)
        self._gif_fps_spin.setToolTip(
            "GIF 녹화 초당 프레임 수. 높을수록 부드럽지만 파일 용량이 커집니다."
        )
        recording_form.addRow("•  GIF 녹화 fps:", self._gif_fps_spin)

        self._gif_max_sec_spin = QSpinBox()
        self._gif_max_sec_spin.setRange(5, 60)
        self._gif_max_sec_spin.setSuffix(" 초")
        self._gif_max_sec_spin.setValue(15)
        self._gif_max_sec_spin.setToolTip(
            "GIF 녹화 최대 길이. 프레임을 전부 메모리에 모았다가 인코딩하므로,\n"
            "fps·녹화 영역이 클수록 메모리 사용량이 커집니다(영상(MP4) 녹화는 이 제약이 없습니다)."
        )
        recording_form.addRow("•  GIF 최대 길이:", self._gif_max_sec_spin)

        recording_form.addRow(_gen_sep())

        self._video_fps_spin = QSpinBox()
        self._video_fps_spin.setRange(1, 30)
        self._video_fps_spin.setSuffix(" fps")
        self._video_fps_spin.setValue(15)
        self._video_fps_spin.setToolTip(
            "영상(MP4) 녹화 초당 프레임 수. 높을수록 부드럽지만 파일 용량이 커집니다."
        )
        recording_form.addRow("•  영상 녹화 fps:", self._video_fps_spin)

        self._video_max_sec_spin = QSpinBox()
        self._video_max_sec_spin.setRange(10, 3600)
        self._video_max_sec_spin.setSuffix(" 초")
        self._video_max_sec_spin.setValue(600)
        self._video_max_sec_spin.setToolTip(
            "영상 녹화 최대 길이. 프레임을 디스크에 즉시 흘려쓰므로 메모리 제약이 없어\n"
            "GIF보다 훨씬 길게 잡아도 안전합니다(디스크 용량만 소비)."
        )
        recording_form.addRow("•  영상 최대 길이:", self._video_max_sec_spin)

        # insertWidget(0/1, ...) — 기본/기능 단축키 그룹은 이 시점보다 앞서(위쪽) 이미
        # tab_general에 append돼 있으므로, 맨 위로 오려면 append가 아니라 인덱스 삽입.
        # 순서: 일반 설정(0) → GIF/녹화 설정(1) → 기본 단축키 → 기능 단축키.
        tab_general.insertWidget(0, general_group)
        tab_general.insertWidget(1, recording_group)

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
        """연결 테스트 — 키·연결 + OCR 모델 + 크레딧 잔액을 실호출해 콤보 아래에 보고한다(워커 스레드).

        옛 버전은 모델 목록 조회 하나만 하고 "연결 성공 — API 키가 유효합니다"를 띄웠다.
        그 문구는 "고른 모델이 된다"로 읽히지만 실제로는 **모델을 호출하지 않았다** —
        OCR 모델의 이미지 입력 지원 여부는 목록 조회로 절대 알 수 없어, 텍스트 전용 모델을
        골라도 ✓가 떴다가 캡처할 때 400이 났다.

        연결·OCR 모델·크레딧을 순서대로 실호출하고 각 결과를 제 자리(연결=상태 줄, 모델·
        크레딧=콤보 아래)에 따로 쓴다. 연결이 실패하면 모델 테스트는 'skip'으로 남겨 원인을
        흐리지 않는다. **크레딧 조회는 연결 성패와 무관하게 항상 시도한다**(2026-08-12
        병합 — 원래 별도 버튼이었고, 연결 프로브가 막혀도 크레딧 엔드포인트는 따로 열려
        있을 수 있어 conn_ok에 종속시키지 않았다).
        """
        # creds는 UI 스레드에서 미리 읽어 넣는다(워커에서 위젯 접근 금지).
        api_key, base_url = self._creds()
        ocr_model = self._ocr_model_combo.currentText().strip()

        # 테스트 도중 사용자가 모델을 바꾸거나 다시 누르면 이 값이 올라가고, 뒤늦게 도착한
        # 옛 회차의 결과는 버려진다.
        self._probe_run_id += 1
        run_id = self._probe_run_id

        self._test_btn.setEnabled(False)
        self._set_probe_status(self._test_status, "run", "연결 확인 중…")
        self._set_probe_status(self._ocr_model_probe_status, "run", "대기 중…")
        self._set_probe_status(self._credit_status, "run", "대기 중…")

        import threading

        def _worker():
            try:
                from pasteflow.ocr_engine import probe_connection, probe_ocr_model, get_credit_balance
                # 연결 프로브 1회 — 실패하면 모델 프로브는 skip해 원인을 흐리지 않는다.
                if not api_key:
                    conn_ok = False
                    self._probe_done.emit(
                        run_id, "conn", "fail", "API 키가 설정돼 있지 않습니다.")
                else:
                    c = probe_connection(api_key, base_url)
                    conn_ok = c.status == "ok"
                    self._probe_done.emit(run_id, "conn", c.status, c.detail)

                if not ocr_model:
                    self._probe_done.emit(
                        run_id, "ocr", "skip", "모델이 비어 있습니다 — ↻로 목록을 불러오세요.")
                elif not conn_ok:
                    self._probe_done.emit(run_id, "ocr", "skip", "연결이 안 돼 건너뛰었습니다.")
                else:
                    self._probe_done.emit(run_id, "ocr", "run", f"{ocr_model} 호출 중…")
                    result = probe_ocr_model(api_key, base_url, ocr_model)
                    self._probe_done.emit(run_id, "ocr", result.status, result.detail)

                if not api_key:
                    self._probe_done.emit(run_id, "credit", "skip", "API 키가 없어 건너뛰었습니다.")
                else:
                    self._probe_done.emit(run_id, "credit", "run", "크레딧 조회 중…")
                    try:
                        remaining, quota = get_credit_balance(api_key, base_url)
                        self._probe_done.emit(
                            run_id, "credit", "ok",
                            f"잔여 {remaining:,.1f} / {quota:,.0f} 크레딧")
                    except Exception as e:
                        self._probe_done.emit(
                            run_id, "credit", "fail",
                            f"크레딧 조회 실패(이 API는 지원하지 않을 수 있습니다) — {e}")
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
            "ocr": self._ocr_model_probe_status,
            "credit": self._credit_status,
        }[slot]
        self._set_probe_status(label, status, detail)

    def _fill_mic_combo(self, selected: str):
        """마이크 콤보를 채운다 — 첫 항목은 항상 '시스템 기본'(값 ""), 나머지는 실제 장치명."""
        from pasteflow.stt_engine import list_input_devices, default_input_device_name

        self._mic_combo.clear()
        default_name = default_input_device_name()
        default_label = f"시스템 기본 (현재: {default_name})" if default_name else "시스템 기본"
        self._mic_combo.addItem(default_label, "")
        try:
            devices = list_input_devices()
        except Exception:
            devices = []
        for name in devices:
            self._mic_combo.addItem(name, name)
        idx = self._mic_combo.findData(selected)
        self._mic_combo.setCurrentIndex(idx if idx >= 0 else 0)

    def _reload_mic_combo(self):
        """새로고침 버튼 — 방금 꽂은 마이크를 반영. 현재 선택은 보존한다."""
        current = self._mic_combo.currentData() or ""
        self._fill_mic_combo(current)

    def _on_test_mic(self):
        """마이크 테스트 — 선택한 마이크로 2.5초 녹음해 소리가 실제로 잡히는지 확인한다.

        STT 파이프라인이 무음을 걸러내는 기준(stt_engine.SILENCE_RMS_THRESHOLD)과 반드시
        같은 값으로 판정한다 — 여기서 "정상"이라 떴는데 실제 음성 입력에선 차단되는 것 같은
        불일치를 막기 위함(main.py `_finish_stt_recording`이 같은 상수를 쓴다).
        """
        from pasteflow.stt_engine import Recorder, SILENCE_RMS_THRESHOLD

        device = self._mic_combo.currentData() or ""
        recorder = Recorder()
        try:
            recorder.start(device=device)
        except Exception as e:
            self._set_probe_status(self._mic_test_status, "fail", f"마이크를 열 수 없습니다 — {e}")
            return

        self._mic_test_btn.setEnabled(False)
        self._set_probe_status(self._mic_test_status, "run", "녹음 중… 지금 2~3초간 말해보세요")

        def _finish():
            try:
                wav_bytes = recorder.stop()
                peak = recorder.last_peak_rms
                if not wav_bytes or peak < SILENCE_RMS_THRESHOLD:
                    self._set_probe_status(
                        self._mic_test_status, "fail",
                        "소리가 감지되지 않았습니다 — 마이크 연결·음소거·Windows 마이크 권한을 확인하세요",
                    )
                else:
                    pct = min(100, round(peak * 300))
                    self._set_probe_status(self._mic_test_status, "ok", f"마이크 정상 (감지된 음량 {pct}%)")
            except RuntimeError:
                pass  # 2.5초 사이 다이얼로그가 닫혀 위젯이 사라진 경우 — 무시
            finally:
                try:
                    self._mic_test_btn.setEnabled(True)
                except RuntimeError:
                    pass

        QTimer.singleShot(2500, _finish)

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
        self._bulk_paste_hotkey.set_value(
            self._settings.get(self.KEY_BULK_PASTE_HOTKEY, "ctrl+shift+a")
        )
        self._bulk_path_paste_hotkey.set_value(
            self._settings.get(self.KEY_BULK_PATH_PASTE_HOTKEY, "ctrl+shift+]")
        )
        self._pin_image_hotkey.set_value(
            self._settings.get(self.KEY_PIN_IMAGE_HOTKEY, "alt+f3")
        )
        self._capture_hotkey.set_value(
            self._settings.get(self.KEY_CAPTURE_HOTKEY, "alt+f2")
        )
        self._capture_printscreen_check.setChecked(
            self._settings.get(self.KEY_CAPTURE_USE_PRINTSCREEN, "1") == "1"
        )
        self._record_hotkey.set_value(
            self._settings.get(self.KEY_RECORD_HOTKEY, "ctrl+shift+r")
        )
        self._stt_hotkey.set_value(
            self._settings.get(self.KEY_STT_HOTKEY, "ctrl+win")
        )
        self._fill_mic_combo(self._settings.get(self.KEY_STT_MIC_DEVICE, ""))
        self._capture_folder_edit.setText(
            self._settings.get(self.KEY_CAPTURE_FOLDER, "")
        )
        self._gateway_key_edit.setText(self._settings.get(self.KEY_OCR_GEMINI_API_KEY_GATEWAY, ""))
        self._base_url_edit.setText(self._settings.get(self.KEY_OCR_GEMINI_BASE_URL, ""))

        # OCR 모델 슬롯 — 캐시된 모델 목록으로 채우고 저장값을 복원한다.
        self._init_model_slots()

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
        self._gif_cursor_check.setChecked(
            self._settings.get(self.KEY_GIF_SHOW_CURSOR, "1") == "1"
        )
        try:
            gif_fps = int(self._settings.get(self.KEY_GIF_FPS, "12"))
        except (ValueError, TypeError):
            gif_fps = 12
        self._gif_fps_spin.setValue(gif_fps)
        try:
            gif_max_sec = int(self._settings.get(self.KEY_GIF_MAX_SECONDS, "15"))
        except (ValueError, TypeError):
            gif_max_sec = 15
        self._gif_max_sec_spin.setValue(gif_max_sec)
        try:
            video_fps = int(self._settings.get(self.KEY_VIDEO_FPS, "15"))
        except (ValueError, TypeError):
            video_fps = 15
        self._video_fps_spin.setValue(video_fps)
        try:
            video_max_sec = int(self._settings.get(self.KEY_VIDEO_MAX_SECONDS, "600"))
        except (ValueError, TypeError):
            video_max_sec = 600
        self._video_max_sec_spin.setValue(video_max_sec)

    def _creds(self) -> tuple[str, str]:
        """게이트웨이 (api_key, base_url) — 편집칸에서 직접 읽는다."""
        return self._gateway_key_edit.text().strip(), self._base_url_edit.text().strip()

    def _on_key_reveal_toggled(self, on: bool):
        """'보기' 토글 — API 키를 평문↔●●● 전환."""
        self._gateway_key_edit.setEchoMode(
            QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password)
        self._key_reveal_btn.setText("숨김" if on else "보기")

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
        """OCR 모델 콤보를 캐시로 채우고 저장된 모델명을 복원한다."""
        cached = self._cached_models()
        self._fill_model_combo(self._ocr_model_combo, cached)
        saved_ocr = self._settings.get(self.KEY_OCR_MODEL_GATEWAY, "")
        if saved_ocr:
            self._ocr_model_combo.setCurrentText(saved_ocr)
        self._init_stt_model_slot()

    def _gemini_only(self, candidates: list[str]) -> list[str]:
        """Gemini 계열만 남긴다 — STT 모델 콤보 전용 필터(2026-08-02 실측 근거는 위 참고)."""
        from pasteflow.ocr_engine import family_of
        return [m for m in candidates if family_of(m) == "Gemini"]

    def _init_stt_model_slot(self):
        """STT 모델 콤보를 캐시(Gemini만)로 채우고 저장된 모델명을 복원한다.

        저장값이 없는 첫 실행은 `STT_FALLBACK_DEFAULT`(gemini-3.1-flash-lite)를 우선
        선택한다 — `_fill_model_combo`의 일반 폴백(`_FALLBACK_DEFAULT`=gemini-2.5-flash,
        OCR 콤보와 공유)보다 STT는 지연시간이 더 중요해 별도로 오버라이드한다
        (2026-08-02 실측: 동일 문장 인식에 flash-lite가 일관되게 더 빠름).
        """
        from pasteflow.ocr_engine import STT_FALLBACK_DEFAULT

        cached = self._gemini_only(self._cached_models())
        self._fill_model_combo(self._stt_model_combo, cached)
        saved_stt = self._settings.get(self.KEY_STT_MODEL_GATEWAY, "")
        if saved_stt:
            self._stt_model_combo.setCurrentText(saved_stt)
        else:
            idx = self._stt_model_combo.findText(STT_FALLBACK_DEFAULT)
            if idx >= 0:
                self._stt_model_combo.setCurrentIndex(idx)

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
        """새로고침으로 받은 모델 목록을 OCR 모델 콤보에 반영한다.

        현재 선택은 보존한다. 캐시가 비면 첫 실행처럼 빈 콤보(+placeholder)로 둔다.
        """
        combo = self._ocr_model_combo
        current = combo.currentText()
        combo.setUpdatesEnabled(False)
        try:
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

    def _refill_stt_model_slots(self, candidates: list[str]):
        """새로고침으로 받은 모델 목록을 Gemini로 필터링해 STT 모델 콤보에 반영한다."""
        combo = self._stt_model_combo
        current = combo.currentText()
        gemini_candidates = self._gemini_only(candidates)
        combo.setUpdatesEnabled(False)
        try:
            self._fill_model_combo(combo, gemini_candidates)
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
        self._refill_stt_model_slots(unique)

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
            self.KEY_BULK_PASTE_HOTKEY: self._bulk_paste_hotkey.value() or "ctrl+shift+a",
            self.KEY_BULK_PATH_PASTE_HOTKEY: self._bulk_path_paste_hotkey.value() or "ctrl+shift+]",
            self.KEY_PIN_IMAGE_HOTKEY: self._pin_image_hotkey.value() or "alt+f3",
            self.KEY_CAPTURE_HOTKEY: self._capture_hotkey.value() or "alt+f2",
            self.KEY_CAPTURE_USE_PRINTSCREEN: "1" if self._capture_printscreen_check.isChecked() else "0",
            self.KEY_RECORD_HOTKEY: self._record_hotkey.value() or "ctrl+shift+r",
            self.KEY_STT_HOTKEY: self._stt_hotkey.value() or "ctrl+win",
            self.KEY_CAPTURE_FOLDER: self._capture_folder_edit.text(),
            # OCR은 별도 엔진 선택 없이 항상 AI(Gemini/Mindlogic) API로 처리 → kind 고정.
            self.KEY_OCR_ENGINE: "gemini",
            self.KEY_HISTORY_MAX: str(self._history_max_spin.value()),
            self.KEY_QUEUE_IDLE_RESET: str(self._queue_idle_spin.value()),
            self.KEY_AUTO_START: "1" if auto_start else "0",
            self.KEY_NOTIFY_ON_COPY: "1" if self._notify_copy_check.isChecked() else "0",
            self.KEY_GIF_SHOW_CURSOR: "1" if self._gif_cursor_check.isChecked() else "0",
            self.KEY_GIF_FPS: str(self._gif_fps_spin.value()),
            self.KEY_GIF_MAX_SECONDS: str(self._gif_max_sec_spin.value()),
            self.KEY_VIDEO_FPS: str(self._video_fps_spin.value()),
            self.KEY_VIDEO_MAX_SECONDS: str(self._video_max_sec_spin.value()),
        }
        # 크리덴셜 — 화면 편집칸에서 직접 읽어 저장.
        new_settings[self.KEY_OCR_GEMINI_API_KEY_GATEWAY] = self._gateway_key_edit.text()
        new_settings[self.KEY_OCR_GEMINI_BASE_URL] = self._base_url_edit.text()
        new_settings[self.KEY_OCR_MODEL_GATEWAY] = self._ocr_model_combo.currentText()
        new_settings[self.KEY_STT_MODEL_GATEWAY] = self._stt_model_combo.currentText()
        new_settings[self.KEY_STT_MIC_DEVICE] = self._mic_combo.currentData() or ""

        # 모델 캐시(↻로 갱신된 값)는 로드값을 그대로 실어 보낸다 — 안 보내면 사라진다.
        cache_key = self.KEY_OCR_GEMINI_MODEL_CACHE_GATEWAY
        if cache_key in self._settings:
            new_settings[cache_key] = self._settings[cache_key]

        self.settings_changed.emit(new_settings)
        self.accept()
