"""설정 다이얼로그 — F10

단축키 커스터마이징, 히스토리 제한, 자동 시작, 자동 닫기 설정.
"""
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QSpinBox, QCheckBox, QGroupBox, QFormLayout, QGridLayout, QComboBox, QLineEdit,
    QStyle, QFileDialog, QScrollArea, QWidget, QFrame, QApplication,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFontMetrics

from pasteflow.ui.theme import COLORS, PEACH_HOVER


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
        top: 9px;
        padding: 0;
        color: {_TITLE};
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

    def _update_display(self):
        if self._listening:
            self.setText("키를 누르세요...")
            self._apply_style(True)
        else:
            self.setText(self._value or "클릭하여 설정")
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
    KEY_PIN_IMAGE_HOTKEY = "hotkey_pin_image"
    KEY_CAPTURE_HOTKEY = "hotkey_capture"
    KEY_ASK_AI_HOTKEY = "hotkey_ask_ai"
    KEY_CAPTURE_FOLDER = "capture_save_folder"
    KEY_OCR_LANG = "ocr_language"
    KEY_OCR_ENGINE = "ocr_engine"
    # Gemini는 backend별로 키/모델/캐시 분리 — 학교 게이트웨이와 개인 AI Studio 키를 동시에 보관
    KEY_OCR_GEMINI_BACKEND = "ocr_gemini_backend"  # "official" | "gateway"
    KEY_OCR_GEMINI_BASE_URL = "ocr_gemini_base_url"  # gateway 전용
    KEY_OCR_GEMINI_API_KEY_OFFICIAL = "ocr_gemini_api_key_official"
    KEY_OCR_GEMINI_API_KEY_GATEWAY = "ocr_gemini_api_key_gateway"
    KEY_OCR_GEMINI_MODEL_OFFICIAL = "ocr_gemini_model_official"
    KEY_OCR_GEMINI_MODEL_GATEWAY = "ocr_gemini_model_gateway"
    KEY_OCR_GEMINI_MODEL_CACHE_OFFICIAL = "ocr_gemini_model_cache_official"
    KEY_OCR_GEMINI_MODEL_CACHE_GATEWAY = "ocr_gemini_model_cache_gateway"
    KEY_QUEUE_IDLE_RESET = "queue_idle_reset_sec"

    # 워커 스레드 → UI 안전 통신용 내부 시그널 (models, error_msg)
    _models_fetched = pyqtSignal(list, str)

    def __init__(self, current_settings: dict, parent=None):
        super().__init__(parent)
        self._settings = dict(current_settings)
        self._setup_window()
        self._setup_ui()
        self._load_values()
        self._finalize_size()
        self._models_fetched.connect(self._on_models_fetched)

    def _setup_window(self):
        self.setWindowTitle("PasteFlow 설정")
        # 고정 크기는 _finalize_size()에서 콘텐츠·화면에 맞춰 결정(스크롤 영역과 분리).
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.WindowCloseButtonHint
        )
        self.setStyleSheet(DIALOG_STYLE)

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
        layout.setSpacing(8)
        layout.setContentsMargins(16, 16, 16, 16)

        # ── 기능 단축키 그룹 (변경 가능) — 아래 기본 단축키 다음에 배치 ──
        hotkey_group = QGroupBox("기능 단축키 (변경 가능)")
        hotkey_form = QFormLayout(hotkey_group)

        self._panel_toggle_hotkey = HotkeyEdit()
        hotkey_form.addRow("패널 토글:", self._panel_toggle_hotkey)

        self._image_to_path_hotkey = HotkeyEdit()
        self._image_to_path_hotkey.setToolTip(
            "현재 클립보드 이미지를 임시 PNG로 저장하고 절대경로를 클립보드 텍스트로 교체합니다.\n"
            "이어서 포그라운드 창에 Ctrl+V를 자동 전송합니다.\n"
            "Claude Code CLI 등 '파일 경로 텍스트'를 첨부로 받는 앱에 한 키로 붙여넣기 위한 단축키."
        )
        hotkey_form.addRow("이미지→경로:", self._image_to_path_hotkey)

        self._pin_image_hotkey = HotkeyEdit()
        self._pin_image_hotkey.setToolTip(
            "현재 클립보드 이미지를 화면 위에 떠 있는 창으로 띄웁니다(Snipaste의 화면 핀).\n"
            "여러 개를 동시에 띄울 수 있고, ESC로 닫습니다.\n"
            "띄운 창에서 Space를 누르면 주석 편집 모드로 들어갑니다."
        )
        hotkey_form.addRow("화면에 핀:", self._pin_image_hotkey)

        self._capture_hotkey = HotkeyEdit()
        self._capture_hotkey.setToolTip(
            "화면 영역을 드래그로 선택해 캡처합니다(Snipaste의 영역 캡처).\n"
            "캡처 즉시 클립보드에 복사되고 지정 폴더에 PNG로 저장됩니다.\n"
            "ESC 또는 우클릭으로 취소합니다."
        )
        hotkey_form.addRow("영역 캡처:", self._capture_hotkey)

        self._ask_ai_hotkey = HotkeyEdit()
        self._ask_ai_hotkey.setToolTip(
            "컨텍스트 없이 즉석에서 AI에게 질문하는 입력창을 띄웁니다.\n"
            "클립보드 항목과 무관하게 아무 때나 한 키로 AI를 호출해 자유 질문하고 답변을 받습니다.\n"
            "(우클릭 'AI에게 질문'은 선택한 항목을 컨텍스트로 묻는 방식 — 이건 컨텍스트 없는 자유 질문)"
        )
        hotkey_form.addRow("AI 자유질문:", self._ask_ai_hotkey)

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
            action_lbl = QLabel(action)
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

        # ── OCR 설정 그룹 (화면 텍스트 인식) ──
        ocr_group = QGroupBox("OCR (화면 텍스트 인식)")
        self._ocr_form = QFormLayout(ocr_group)
        ocr_form = self._ocr_form

        self._ocr_hotkey = HotkeyEdit()
        ocr_form.addRow("OCR 단축키:", self._ocr_hotkey)

        self._ocr_engine_combo = QComboBox()
        self._ocr_engine_combo.setStyleSheet(_combo_style)
        self._ocr_engine_combo.addItem("Windows WinRT (무료)", "winrt")
        self._ocr_engine_combo.addItem("Google Gemini (API 키 필요)", "gemini")
        ocr_form.addRow("OCR 엔진:", self._ocr_engine_combo)

        self._ocr_lang_combo = QComboBox()
        self._ocr_lang_combo.setStyleSheet(_combo_style)
        # 설치된 언어팩 동적 탐색 — winocr 미설치 시 기본 목록 폴백
        try:
            from pasteflow.ocr_engine import OcrEngine
            supported = OcrEngine.winrt_supported_languages()
        except Exception:
            supported = []
        if supported:
            self._ocr_lang_combo.addItems(supported)
        else:
            self._ocr_lang_combo.addItems(["ko", "en-US", "ja", "zh-Hans"])
            lang_hint = QLabel("winocr 미설치 또는 언어팩 미확인 — 기본 목록 표시")
            lang_hint.setStyleSheet(f"color: {COLORS['subtext0']}; font-size: 11px;")
            ocr_form.addRow("", lang_hint)
        ocr_form.addRow("인식 언어:", self._ocr_lang_combo)

        layout.addWidget(ocr_group)

        # ── AI 연동 그룹 (Gemini API) ──
        # OCR(Gemini 엔진)과 AI 답변(우클릭 'AI에게 질문')이 동일 API를 공유하므로
        # OCR 엔진 선택과 무관하게 항상 표시한다(WinRT OCR이어도 AI 답변엔 키가 필요).
        ai_group = QGroupBox("AI 연동 (Gemini API)")
        self._ai_form = QFormLayout(ai_group)
        ai_form = self._ai_form

        ai_desc = QLabel("OCR(Gemini 엔진)과 AI 답변(우클릭 'AI에게 질문')이 함께 사용합니다.")
        ai_desc.setStyleSheet(f"color: {COLORS['subtext0']}; font-size: 11px;")
        ai_desc.setWordWrap(True)
        ai_form.addRow(ai_desc)

        # API 백엔드 — 공식/게이트웨이별로 키·모델·캐시를 각각 보관해 동시 등록·자유 전환.
        self._backend_label = QLabel("API 백엔드:")
        self._backend_combo = QComboBox()
        self._backend_combo.setStyleSheet(_combo_style)
        self._backend_combo.addItem("공식 Google AI Studio", "official")
        self._backend_combo.addItem("학교 게이트웨이", "gateway")
        ai_form.addRow(self._backend_label, self._backend_combo)

        self._api_key_label = QLabel("API 키:")
        self._api_key_edit = QLineEdit()
        self._api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._api_key_edit.setPlaceholderText("Google AI Studio 키")
        ai_form.addRow(self._api_key_label, self._api_key_edit)

        self._base_url_label = QLabel("Base URL:")
        self._base_url_edit = QLineEdit()
        self._base_url_edit.setPlaceholderText("예: https://factchat-cloud.mindlogic.ai/v1/gateway")
        ai_form.addRow(self._base_url_label, self._base_url_edit)

        self._model_label = QLabel("모델명:")
        self._model_combo = QComboBox()
        self._model_combo.setEditable(True)
        self._model_combo.setStyleSheet(_combo_style)
        # 콤보 초기 채우기는 _load_values에서 backend가 정해진 뒤 수행한다.

        self._model_refresh_btn = QPushButton()
        # Qt 내장 표준 아이콘 — 폰트 의존성 없이 모든 환경에서 보장
        self._model_refresh_btn.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload)
        )
        self._model_refresh_btn.setFixedWidth(34)
        self._model_refresh_btn.setToolTip("API에서 사용 가능한 Gemini 모델 목록 가져오기")
        self._model_refresh_btn.clicked.connect(self._on_refresh_models)

        model_row = QHBoxLayout()
        model_row.setContentsMargins(0, 0, 0, 0)
        model_row.setSpacing(4)
        model_row.addWidget(self._model_combo, 1)
        model_row.addWidget(self._model_refresh_btn)
        ai_form.addRow(self._model_label, model_row)

        # 가장 저렴 모델 안내 힌트 — 콤보 목록 변경 시 _update_model_hint()로 갱신
        self._model_hint = QLabel()
        self._model_hint.setStyleSheet(f"color: {COLORS['subtext0']}; font-size: 11px;")
        self._model_hint.setWordWrap(True)
        ai_form.addRow("", self._model_hint)
        self._update_model_hint()

        self._ocr_engine_combo.currentIndexChanged.connect(self._on_engine_changed)
        self._backend_combo.currentIndexChanged.connect(self._on_backend_changed)

        layout.addWidget(ai_group)

        # ── 일반 설정 그룹 ──
        general_group = QGroupBox("일반")
        general_form = QFormLayout(general_group)

        self._history_max_spin = QSpinBox()
        self._history_max_spin.setRange(10, 500)
        self._history_max_spin.setValue(50)
        general_form.addRow("히스토리 최대 개수:", self._history_max_spin)

        self._queue_idle_spin = QSpinBox()
        self._queue_idle_spin.setRange(1, 3600)
        self._queue_idle_spin.setSuffix(" 초")
        self._queue_idle_spin.setValue(10)
        self._queue_idle_spin.setToolTip(
            "마지막 복사로부터 이 시간이 지나면 다음 복사는 큐의 첫 항목으로 시작합니다.\n"
            "(일반 Ctrl+V는 시간과 무관하게 즉시 큐를 비웁니다.)"
        )
        general_form.addRow("순차 큐 자동 초기화:", self._queue_idle_spin)

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
        general_form.addRow("캡처 저장 폴더:", folder_row)

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

    def _on_engine_changed(self, _idx: int):
        # AI(Gemini) API 설정은 OCR 엔진과 무관하게 항상 표시(AI 답변이 늘 사용).
        # OCR 엔진 선택은 '인식 언어'(WinRT 전용) 노출 여부에만 영향을 준다.
        engine = self._ocr_engine_combo.currentData()
        is_winrt = engine == "winrt"
        if hasattr(self, '_ocr_form'):
            self._ocr_form.setRowVisible(self._ocr_lang_combo, is_winrt)

    def _on_backend_changed(self, _idx: int):
        """API 백엔드 콤보 전환 — 해당 백엔드의 키/URL/모델/캐시로 입력란 스왑.

        편집 중 모델명 변경분(`_model_combo.currentText()`)은 이전 백엔드의 모델 슬롯에 저장하고,
        새 백엔드의 저장값을 다시 띄운다. 사용자가 backend를 왔다갔다 해도 양쪽 값이 보존되도록.
        """
        backend = self._backend_combo.currentData() or "official"
        prev_backend = getattr(self, "_active_backend", None)

        # 1) 이전 backend의 현재 입력값을 self._settings에 stash (저장은 _on_save에서 일괄)
        if prev_backend == "official":
            self._settings[self.KEY_OCR_GEMINI_API_KEY_OFFICIAL] = self._api_key_edit.text()
            self._settings[self.KEY_OCR_GEMINI_MODEL_OFFICIAL] = self._model_combo.currentText()
        elif prev_backend == "gateway":
            self._settings[self.KEY_OCR_GEMINI_API_KEY_GATEWAY] = self._api_key_edit.text()
            self._settings[self.KEY_OCR_GEMINI_BASE_URL] = self._base_url_edit.text()
            self._settings[self.KEY_OCR_GEMINI_MODEL_GATEWAY] = self._model_combo.currentText()

        # 2) 새 backend 값으로 입력란 채우기
        if backend == "gateway":
            self._api_key_edit.setPlaceholderText("학교 게이트웨이 토큰")
            self._api_key_edit.setText(self._settings.get(self.KEY_OCR_GEMINI_API_KEY_GATEWAY, ""))
            self._base_url_edit.setText(self._settings.get(self.KEY_OCR_GEMINI_BASE_URL, ""))
            self._base_url_label.setVisible(True)
            self._base_url_edit.setVisible(True)
        else:  # official
            self._api_key_edit.setPlaceholderText("Google AI Studio 키")
            self._api_key_edit.setText(self._settings.get(self.KEY_OCR_GEMINI_API_KEY_OFFICIAL, ""))
            self._base_url_label.setVisible(False)
            self._base_url_edit.setVisible(False)

        self._active_backend = backend
        # 3) 모델 콤보는 backend별 캐시로 재구성 후 저장된 모델명 선택
        self._populate_model_combo()
        saved_model = self._current_saved_model_for(backend)
        if saved_model:
            self._model_combo.setCurrentText(saved_model)

    def _current_saved_model_for(self, backend: str) -> str:
        if backend == "gateway":
            return self._settings.get(self.KEY_OCR_GEMINI_MODEL_GATEWAY, "")
        return self._settings.get(self.KEY_OCR_GEMINI_MODEL_OFFICIAL, "")

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
        # backend 콤보 — base_url 유무 자동 추론보다 명시 저장값을 우선
        backend = self._settings.get(self.KEY_OCR_GEMINI_BACKEND, "")
        if backend not in ("official", "gateway"):
            backend = "gateway" if (self._settings.get(self.KEY_OCR_GEMINI_BASE_URL, "") or "").strip() else "official"
        b_idx = self._backend_combo.findData(backend)
        # 시그널 차단 후 인덱스 설정 → _on_engine_changed가 1회 호출하도록 정렬
        self._backend_combo.blockSignals(True)
        self._backend_combo.setCurrentIndex(b_idx if b_idx >= 0 else 0)
        self._backend_combo.blockSignals(False)
        self._active_backend = None  # _on_backend_changed가 prev_backend 처리 안 하도록

        engine = self._settings.get(self.KEY_OCR_ENGINE, "winrt")
        idx = self._ocr_engine_combo.findData(engine)
        self._ocr_engine_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self._on_engine_changed(self._ocr_engine_combo.currentIndex())  # 인식 언어 노출
        # AI(Gemini) API 입력란은 엔진과 무관하게 항상 backend 값으로 채운다.
        self._on_backend_changed(self._backend_combo.currentIndex())

        lang = self._settings.get(self.KEY_OCR_LANG, "ko")
        idx = self._ocr_lang_combo.findText(lang)
        self._ocr_lang_combo.setCurrentIndex(idx if idx >= 0 else 0)
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

    def _current_backend(self) -> str:
        """현재 backend 콤보 선택값 — 'official' 또는 'gateway'."""
        return self._backend_combo.currentData() or "official"

    def _cache_key_for(self, backend: str) -> str:
        return (self.KEY_OCR_GEMINI_MODEL_CACHE_GATEWAY
                if backend == "gateway"
                else self.KEY_OCR_GEMINI_MODEL_CACHE_OFFICIAL)

    def _fill_model_combo(self, verified: list[str], unverified: list[str]):
        """콤보를 검증 모델(상단) + 구분선 + 미검증 모델(하단, 회색)로 채운다."""
        self._verified_models = verified
        self._model_combo.clear()
        for name in verified:
            self._model_combo.addItem(name)
            # 모든 항목에 전체 이름 툴팁 — 팝업이 화면 폭을 넘어 잘릴 때의 안전망
            idx = self._model_combo.count() - 1
            self._model_combo.setItemData(idx, name, Qt.ItemDataRole.ToolTipRole)
        if verified and unverified:
            self._model_combo.insertSeparator(self._model_combo.count())
        gray = QColor(COLORS['subtext0'])
        for name in unverified:
            self._model_combo.addItem(name)
            idx = self._model_combo.count() - 1
            self._model_combo.setItemData(idx, gray, Qt.ItemDataRole.ForegroundRole)
            self._model_combo.setItemData(
                idx,
                "PasteFlow가 검증하지 않은 모델 — 게이트웨이가 광고하지만 호출 실패 가능",
                Qt.ItemDataRole.ToolTipRole,
            )
        self._adjust_model_popup_width()
        self._update_model_hint()

    def _adjust_model_popup_width(self):
        """드롭다운 팝업만 최장 모델명에 맞춰 넓힌다 — 콤보 본체/설정창 폭은 불변.

        QComboBox 팝업은 기본적으로 콤보 위젯 폭에 묶여 긴 이름의 가운데가
        생략된다(`gemini-3.1-p...-customtools`). 모델명이 공통 접두사를
        공유하므로 가운데 생략은 구분을 망가뜨린다. 팝업 view의 최소 폭을
        실제 텍스트 폭에 맞춰 늘려 잘림 자체를 없앤다.
        """
        fm = QFontMetrics(self._model_combo.view().font())
        widest = 0
        for i in range(self._model_combo.count()):
            text = self._model_combo.itemText(i)
            if text:
                widest = max(widest, fm.horizontalAdvance(text))
        if widest:
            # 스크롤바·여백 여유분 가산
            self._model_combo.view().setMinimumWidth(widest + 40)

    def _update_model_hint(self):
        """검증 모델 중 가장 저렴한 것(verified[0])을 힌트 라벨에 표시."""
        if not hasattr(self, "_model_hint"):
            return
        verified = getattr(self, "_verified_models", [])
        if not verified:
            self._model_hint.setText("(검증된 모델 없음 — ↻ 새로고침을 시도하세요)")
            return
        self._model_hint.setText(f"💡 가장 저렴: {verified[0]}")

    def _populate_model_combo(self):
        """현재 backend의 캐시로 모델 콤보 구성. 캐시 없으면 화이트리스트 기본값."""
        import json
        from pasteflow.ocr_engine import sort_models_with_whitelist, whitelist_model_names

        backend = self._current_backend()
        cache_str = self._settings.get(self._cache_key_for(backend), "")
        cached: list[str] = []
        if cache_str:
            try:
                parsed = json.loads(cache_str)
                if isinstance(parsed, list):
                    cached = [str(m) for m in parsed if m]
            except (json.JSONDecodeError, ValueError, TypeError):
                pass

        if cached:
            verified, unverified = sort_models_with_whitelist(cached, backend)
        else:
            verified = whitelist_model_names(backend)
            unverified = []
        self._fill_model_combo(verified, unverified)

    def _on_refresh_models(self):
        """🔄 버튼 — 현재 backend에 맞는 API에서 모델 목록 조회 (워커 스레드)."""
        api_key = self._api_key_edit.text().strip()
        if not api_key:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "API 키 필요",
                                "먼저 API 키를 입력하세요.")
            return
        backend = self._current_backend()
        base_url = self._base_url_edit.text().strip() if backend == "gateway" else ""

        self._model_refresh_btn.setEnabled(False)
        # 로딩 중: 아이콘 제거 + "..." 텍스트 (단순/명확)
        self._model_refresh_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserStop))

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
        """워커 스레드 결과 반영 (Qt 메인 스레드)."""
        self._model_refresh_btn.setEnabled(True)
        self._model_refresh_btn.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload)
        )

        if err:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "모델 조회 실패",
                                f"모델 목록을 가져오지 못했습니다.\n\n{err}")
            return
        if not models:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.information(self, "결과 없음",
                                    "API 응답에 Gemini 모델이 없습니다.\n게이트웨이 설정을 확인하세요.")
            return

        from pasteflow.ocr_engine import sort_models_with_whitelist
        backend = self._current_backend()
        unique = sorted(set(models))
        verified, unverified = sort_models_with_whitelist(unique, backend)

        current = self._model_combo.currentText()
        self._fill_model_combo(verified, unverified)
        idx = self._model_combo.findText(current)
        if idx >= 0:
            self._model_combo.setCurrentIndex(idx)
        elif current:
            self._model_combo.setCurrentText(current)

        import json
        # 캐시도 backend별로 분리 저장 — 공식/게이트웨이 모델 라인업이 달라 섞이면 안 됨
        self._settings[self._cache_key_for(backend)] = json.dumps(unique)

    def _on_save(self):
        """저장 버튼 클릭 — 레지스트리 등록은 main._on_settings_changed에서 처리.

        Gemini는 backend별 키/모델을 각각 보존:
        - 활성 backend의 입력값은 화면에서 읽어 갱신
        - 비활성 backend의 값은 self._settings에 stash된 것을 그대로 전달
        """
        engine = self._ocr_engine_combo.currentData()
        auto_start = self._auto_start_check.isChecked()

        new_settings = {
            self.KEY_PANEL_TOGGLE: self._panel_toggle_hotkey.value() or "ctrl+space",
            self.KEY_OCR_HOTKEY: self._ocr_hotkey.value() or "ctrl+shift+s",
            self.KEY_IMAGE_TO_PATH_HOTKEY: self._image_to_path_hotkey.value() or "ctrl+shift+p",
            self.KEY_PIN_IMAGE_HOTKEY: self._pin_image_hotkey.value() or "alt+f3",
            self.KEY_CAPTURE_HOTKEY: self._capture_hotkey.value() or "alt+f2",
            self.KEY_ASK_AI_HOTKEY: self._ask_ai_hotkey.value() or "alt+`",
            self.KEY_CAPTURE_FOLDER: self._capture_folder_edit.text(),
            self.KEY_OCR_LANG: self._ocr_lang_combo.currentText(),
            self.KEY_OCR_ENGINE: engine,
            self.KEY_HISTORY_MAX: str(self._history_max_spin.value()),
            self.KEY_QUEUE_IDLE_RESET: str(self._queue_idle_spin.value()),
            self.KEY_AUTO_START: "1" if auto_start else "0",
            self.KEY_NOTIFY_ON_COPY: "1" if self._notify_copy_check.isChecked() else "0",
        }
        # AI(Gemini) 설정은 OCR 엔진과 무관하게 항상 저장 — AI 답변이 늘 사용하므로
        # WinRT OCR이어도 키/모델이 보존돼야 한다.
        backend = self._current_backend()
        new_settings[self.KEY_OCR_GEMINI_BACKEND] = backend

        # 활성 backend의 현재 입력값 → self._settings에 반영(반대편 stash와 일관성 유지)
        if backend == "gateway":
            self._settings[self.KEY_OCR_GEMINI_API_KEY_GATEWAY] = self._api_key_edit.text()
            self._settings[self.KEY_OCR_GEMINI_BASE_URL] = self._base_url_edit.text()
            self._settings[self.KEY_OCR_GEMINI_MODEL_GATEWAY] = self._model_combo.currentText()
        else:
            self._settings[self.KEY_OCR_GEMINI_API_KEY_OFFICIAL] = self._api_key_edit.text()
            self._settings[self.KEY_OCR_GEMINI_MODEL_OFFICIAL] = self._model_combo.currentText()

        # 양쪽 backend의 키/모델/캐시를 모두 같이 전달 — 일부만 보내면 다른 쪽이 사라질 위험
        for k in (
            self.KEY_OCR_GEMINI_API_KEY_OFFICIAL,
            self.KEY_OCR_GEMINI_API_KEY_GATEWAY,
            self.KEY_OCR_GEMINI_BASE_URL,
            self.KEY_OCR_GEMINI_MODEL_OFFICIAL,
            self.KEY_OCR_GEMINI_MODEL_GATEWAY,
            self.KEY_OCR_GEMINI_MODEL_CACHE_OFFICIAL,
            self.KEY_OCR_GEMINI_MODEL_CACHE_GATEWAY,
        ):
            if k in self._settings:
                new_settings[k] = self._settings[k]
        self.settings_changed.emit(new_settings)
        self.accept()
