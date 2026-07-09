"""설정 다이얼로그 — F10

단축키 커스터마이징, 히스토리 제한, 자동 시작, 자동 닫기 설정.
"""
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QSpinBox, QCheckBox, QGroupBox, QFormLayout, QGridLayout, QComboBox, QLineEdit,
    QStyle, QFileDialog, QScrollArea, QWidget, QFrame, QApplication,
)
from PyQt6.QtCore import Qt, pyqtSignal, QEvent, QSize
from PyQt6.QtGui import QColor, QFontMetrics, QIcon, QPixmap, QPainter, QFont

from pasteflow.ui.theme import COLORS, PEACH_HOVER


# 모델 콤보에서 "이 행은 그룹 헤더" 표시용 커스텀 role (모델명 행과 구분)
_HEADER_ROLE = Qt.ItemDataRole.UserRole + 1

# 이모지 → QIcon 캐시. 콤보 항목 텍스트는 저장되는 모델명 그 자체라 접두사를 붙일 수
# 없으므로(예: "gemini-2.5-flash 🖼"가 API 모델명으로 저장됨), 배지는 아이콘으로만 단다.
_ICON_CACHE: dict = {}


def _emoji_icon(ch: str, px: int = 16) -> QIcon:
    """이모지 한 글자를 QIcon으로 렌더한다(콤보 항목 아이콘용)."""
    icon = _ICON_CACHE.get(ch)
    if icon is not None:
        return icon
    pm = QPixmap(px, px)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    font = QFont()
    font.setPixelSize(px - 2)
    painter.setFont(font)
    painter.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, ch)
    painter.end()
    icon = QIcon(pm)
    _ICON_CACHE[ch] = icon
    return icon


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
    # Gemini는 backend별로 키/모델/캐시 분리 — Mindlogic Gateway와 개인 AI Studio 키를 동시에 보관
    KEY_OCR_GEMINI_BACKEND = "ocr_gemini_backend"  # "official" | "gateway"
    KEY_OCR_GEMINI_BASE_URL = "ocr_gemini_base_url"  # gateway 전용
    KEY_OCR_GEMINI_API_KEY_OFFICIAL = "ocr_gemini_api_key_official"
    KEY_OCR_GEMINI_API_KEY_GATEWAY = "ocr_gemini_api_key_gateway"
    KEY_OCR_GEMINI_MODEL_OFFICIAL = "ocr_gemini_model_official"
    KEY_OCR_GEMINI_MODEL_GATEWAY = "ocr_gemini_model_gateway"
    KEY_OCR_GEMINI_MODEL_CACHE_OFFICIAL = "ocr_gemini_model_cache_official"
    KEY_OCR_GEMINI_MODEL_CACHE_GATEWAY = "ocr_gemini_model_cache_gateway"
    # OCR 전용 모델 슬롯 — AI 질의 모델(KEY_OCR_GEMINI_MODEL_*)과 분리(v1.39.0).
    # OCR은 이미지를 보내므로 비전 가능 모델만 고를 수 있고, 저렴한 모델을 따로 둘 수 있다.
    KEY_OCR_MODEL_OFFICIAL = "ocr_model_official"
    KEY_OCR_MODEL_GATEWAY = "ocr_model_gateway"
    KEY_QUEUE_IDLE_RESET = "queue_idle_reset_sec"

    # 워커 스레드 → UI 안전 통신용 내부 시그널 (models, error_msg)
    _models_fetched = pyqtSignal(list, str)
    _test_done = pyqtSignal(bool, str)  # API 연결 테스트 결과 (ok, message)

    def __init__(self, current_settings: dict, parent=None):
        super().__init__(parent)
        self._settings = dict(current_settings)
        self._setup_window()
        self._setup_ui()
        self._load_values()
        self._finalize_size()
        self._models_fetched.connect(self._on_models_fetched)
        self._test_done.connect(self._on_test_done)

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

        # API 백엔드 — 공식/게이트웨이별로 키·모델·캐시를 각각 보관해 동시 등록·자유 전환.
        self._backend_label = QLabel("•  API 백엔드:")
        self._backend_combo = QComboBox()
        self._backend_combo.setStyleSheet(_combo_style)
        self._backend_combo.addItem("Google AI Studio", "official")
        self._backend_combo.addItem("Mindlogic Gateway", "gateway")
        ai_form.addRow(self._backend_label, self._backend_combo)

        # 행 순서: Base URL → API 키(+↻) → 모델 콤보들.
        # 새로고침은 URL·키가 모두 있어야 동작하므로 그 둘 아래(키 옆)에 둔다. 옛 위치(모델 콤보 옆)는
        # 콤보가 둘로 늘어난 뒤로 "어느 콤보를 새로고침하나?"로 읽혀 오해를 샀다(실제론 둘 다 갱신).
        self._base_url_label = QLabel("•  Base URL:")
        self._base_url_edit = QLineEdit()
        self._base_url_edit.setPlaceholderText("예: https://factchat-cloud.mindlogic.ai/v1/gateway")
        ai_form.addRow(self._base_url_label, self._base_url_edit)

        self._api_key_label = QLabel("•  API 키:")
        self._api_key_edit = QLineEdit()
        self._api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._api_key_edit.setPlaceholderText("Google AI Studio 키")

        self._model_refresh_btn = QPushButton()
        # Qt 내장 표준 아이콘 — 폰트 의존성 없이 모든 환경에서 보장
        self._model_refresh_btn.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload)
        )
        self._model_refresh_btn.setFixedWidth(34)
        self._model_refresh_btn.setToolTip("API에서 사용 가능한 모델 목록 가져오기 (두 콤보 모두 갱신)")
        # NoFocus 필수: 이 버튼은 클릭 시 setEnabled(False)로 꺼진다. StrongFocus면 Qt가 포커스를
        # 다음 위젯(editable 모델 콤보)으로 넘기고, editable 콤보는 포커스를 받으면 텍스트를 전체
        # 선택한다 → 조회 중에 모델명이 파랗게 반전돼 "선택됐다 사라지는" 것처럼 보였다.
        self._model_refresh_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._model_refresh_btn.clicked.connect(self._on_refresh_models)

        key_row = QHBoxLayout()
        key_row.setContentsMargins(0, 0, 0, 0)
        key_row.setSpacing(4)
        key_row.addWidget(self._api_key_edit, 1)
        key_row.addWidget(self._model_refresh_btn)
        ai_form.addRow(self._api_key_label, key_row)

        # 모델 콤보 2개 — OCR(이미지 입력 필요)과 AI 질의(전 모델)를 분리한다.
        # 같은 모델을 공유하면 답변용 고가 모델이 OCR에도 쓰이거나(과금), 텍스트 전용
        # 모델을 고르면 OCR이 400으로 깨진다. ↻ 새로고침 1회로 두 콤보를 함께 채운다.
        self._model_label = QLabel("•  AI 질의 모델:")
        self._model_combo = QComboBox()
        self._model_combo.setEditable(True)
        self._model_combo.setStyleSheet(_combo_style)
        self._model_combo.setToolTip("AI 질문·답변에 쓰는 모델 (게이트웨이 전 모델)")
        # 콤보 초기 채우기는 _load_values에서 backend가 정해진 뒤 수행한다.

        self._ocr_model_label = QLabel("•  AI OCR 모델:")
        self._ocr_model_combo = QComboBox()
        self._ocr_model_combo.setEditable(True)
        self._ocr_model_combo.setStyleSheet(_combo_style)
        self._ocr_model_combo.setToolTip(
            "이미지에서 텍스트를 추출할 때 쓰는 모델.\n"
            "이미지 입력이 가능한 모델만 표시됩니다. 저렴한 모델을 권장합니다."
        )

        ai_form.addRow(self._model_label, self._model_combo)
        ai_form.addRow(self._ocr_model_label, self._ocr_model_combo)

        # API 연결 테스트 — 모델명 바로 아래에 배치(설명 힌트보다 위). 힌트를 그룹 맨 아래로
        # 내려 워드랩 공간을 넉넉히 확보한다.
        self._test_btn = QPushButton("연결 테스트")
        self._test_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._test_btn.setToolTip("입력한 API 키·URL로 실제 연결해 키가 유효한지 확인합니다.")
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

        self._backend_combo.currentIndexChanged.connect(self._on_backend_changed)

        layout.addWidget(ai_group)

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
        """연결 테스트 — 현재 backend/키/URL로 모델 목록을 받아 키·연결 유효성을 확인(워커 스레드).

        모델 조회(list_gemini_models)는 인증·엔드포인트가 맞아야 성공하므로 가벼운 연결 테스트로
        재사용한다. 결과는 _test_done 시그널로 UI 스레드에 전달."""
        api_key = self._api_key_edit.text().strip()
        if not api_key:
            self._test_status.setText("먼저 API 키를 입력하세요.")
            self._test_status.setStyleSheet(f"color: {COLORS['peach']}; font-size: 11px;")
            return
        backend = self._current_backend()
        base_url = self._base_url_edit.text().strip() if backend == "gateway" else ""
        self._test_btn.setEnabled(False)
        self._test_status.setStyleSheet(f"color: {COLORS['subtext0']}; font-size: 11px;")
        self._test_status.setText("연결 확인 중…")
        import threading
        def _worker():
            try:
                from pasteflow.ocr_engine import OcrEngine
                models = OcrEngine.list_gemini_models(api_key, base_url)
                if models:
                    # 개수는 API가 보고한 전체 gemini 모델 수라 드롭다운(화이트리스트) 목록과 다름 →
                    # 혼란 방지 위해 개수 대신 '키 유효' 신호만 표시.
                    self._test_done.emit(True, "연결 성공 — API 키가 유효합니다.")
                else:
                    self._test_done.emit(False, "응답은 왔으나 사용 가능한 모델이 없습니다. 설정을 확인하세요.")
            except Exception as e:
                self._test_done.emit(False, f"실패: {e}")
        threading.Thread(target=_worker, daemon=True).start()

    def _on_test_done(self, ok: bool, msg: str):
        self._test_btn.setEnabled(True)
        # 연결 테스트와 모델 새로고침이 같은 상태 줄을 공유한다(_set_status).
        self._set_status(("✓ " if ok else "✗ ") + msg, ok=ok)

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
            self._settings[self.KEY_OCR_MODEL_OFFICIAL] = self._ocr_model_combo.currentText()
        elif prev_backend == "gateway":
            self._settings[self.KEY_OCR_GEMINI_API_KEY_GATEWAY] = self._api_key_edit.text()
            self._settings[self.KEY_OCR_GEMINI_BASE_URL] = self._base_url_edit.text()
            self._settings[self.KEY_OCR_GEMINI_MODEL_GATEWAY] = self._model_combo.currentText()
            self._settings[self.KEY_OCR_MODEL_GATEWAY] = self._ocr_model_combo.currentText()

        # 2) 새 backend 값으로 입력란 채우기
        if backend == "gateway":
            self._api_key_edit.setPlaceholderText("Mindlogic Gateway 토큰")
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
        # OCR 슬롯이 아직 비었으면(분리 이전 사용자) AI 모델을 그대로 보여준다 —
        # main._resolve_gemini_cfg("ocr")의 폴백과 같은 규칙이라 화면과 실동작이 일치한다.
        saved_ocr = self._current_saved_ocr_model_for(backend) or saved_model
        if saved_ocr:
            self._ocr_model_combo.setCurrentText(saved_ocr)

    def _current_saved_model_for(self, backend: str) -> str:
        if backend == "gateway":
            return self._settings.get(self.KEY_OCR_GEMINI_MODEL_GATEWAY, "")
        return self._settings.get(self.KEY_OCR_GEMINI_MODEL_OFFICIAL, "")

    def _current_saved_ocr_model_for(self, backend: str) -> str:
        if backend == "gateway":
            return self._settings.get(self.KEY_OCR_MODEL_GATEWAY, "")
        return self._settings.get(self.KEY_OCR_MODEL_OFFICIAL, "")

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
        # backend 콤보 — base_url 유무 자동 추론보다 명시 저장값을 우선
        backend = self._settings.get(self.KEY_OCR_GEMINI_BACKEND, "")
        if backend not in ("official", "gateway"):
            backend = "gateway" if (self._settings.get(self.KEY_OCR_GEMINI_BASE_URL, "") or "").strip() else "official"
        b_idx = self._backend_combo.findData(backend)
        # 시그널 차단 후 인덱스 설정 → 아래 _on_backend_changed를 1회만 명시 호출하도록 정렬
        self._backend_combo.blockSignals(True)
        self._backend_combo.setCurrentIndex(b_idx if b_idx >= 0 else 0)
        self._backend_combo.blockSignals(False)
        self._active_backend = None  # _on_backend_changed가 prev_backend 처리 안 하도록

        # AI(Gemini/Mindlogic) API 입력란을 backend 값으로 채운다(OCR·AI 답변 공용).
        self._on_backend_changed(self._backend_combo.currentIndex())

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

    def _add_group_header(self, combo: QComboBox, text: str):
        """선택 불가능한 그룹 헤더 행을 추가한다.

        헤더를 **비활성 항목**으로 넣는 이유: 이 콤보는 editable이라 표시 텍스트가 곧
        저장되는 모델명이다(`_on_save`가 `currentText()`를 그대로 쓴다). 헤더를 고를 수
        있으면 "⭐ 추천 — 실호출 검증됨" 같은 문자열이 모델명으로 저장돼 API 호출이 깨진다.
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
        # 헤더는 모델명이 아니므로 검색·폭 계산에서 구분할 수 있게 표시해 둔다.
        combo.setItemData(idx, True, _HEADER_ROLE)

    def _fill_model_combo(self, combo: QComboBox, verified: list[str], unverified: list[str]):
        """콤보를 그룹 헤더 + 항목 아이콘으로 채운다.

        아이콘은 **항목당 하나**로, 그 항목에 대해 가장 중요한 사실을 보여준다:
          ⭐ = 추천(`_VERIFIED_MODELS` 화이트리스트, 텍스트·이미지 실호출 확인)
          🖼 = 추천 밖이지만 이미지 입력 가능
          📝 = 텍스트 전용 (이미지 질의 실패) — AI 질의 콤보에만 등장
        추천 모델은 전부 비전 가능이므로 ⭐가 🖼을 덮어도 정보 손실이 없다.
        OCR 콤보는 비전 가능 모델만 담으므로 추천 밖 항목엔 아이콘을 달지 않는다.

        헤더에는 그룹별 개수를 함께 적는다("추천 — 실호출 검증됨 (7종)").

        가격(저렴/고가) 배지는 **의도적으로 없다** — 게이트웨이가 chat 단가를 노출하지
        않아 `tier_rank`가 어림값이기 때문이다(정렬 순서로만 쓴다). 추측을 사실처럼
        표시하지 않는다.
        """
        from pasteflow.ocr_engine import is_vision_capable

        combo.clear()
        # OCR 콤보는 이미 비전 가능 모델만 담고 있으므로 이미지 관련 표시를 생략한다.
        show_vision = combo is not self._ocr_model_combo
        gray = QColor(COLORS['subtext0'])

        def _add(name: str, recommended: bool, tip: str):
            combo.addItem(name)
            idx = combo.count() - 1
            if recommended:
                combo.setItemData(idx, _emoji_icon("⭐"), Qt.ItemDataRole.DecorationRole)
            elif show_vision:
                vision = is_vision_capable(name)
                combo.setItemData(idx, _emoji_icon("🖼" if vision else "📝"),
                                  Qt.ItemDataRole.DecorationRole)
                if not vision:
                    tip += "\n\n📝 텍스트 전용 — 이미지를 첨부하는 질의는 실패합니다."
            if not recommended:
                combo.setItemData(idx, gray, Qt.ItemDataRole.ForegroundRole)
            combo.setItemData(idx, tip, Qt.ItemDataRole.ToolTipRole)

        if verified:
            self._add_group_header(combo, f"추천 — 실호출 검증됨 ({len(verified)}종)")
            for name in verified:
                _add(name, recommended=True,
                     tip=f"⭐ {name}\n\nPasteFlow가 텍스트·이미지 양쪽을 실제로 호출해 확인한 모델입니다.")
        if unverified:
            self._add_group_header(combo, f"그 외 모델 ({len(unverified)}종)")
            for name in unverified:
                _add(name, recommended=False,
                     tip=f"{name}\n\n추천 목록 밖 — 게이트웨이가 제공하며 호출은 확인됐지만,\n"
                         "PasteFlow가 품질을 보증하지는 않습니다.")

        self._select_first_enabled(combo)
        self._adjust_model_popup_width(combo)

    def _select_first_enabled(self, combo: QComboBox):
        """현재 선택이 비활성 헤더에 걸려 있으면 첫 번째 실제 모델로 옮긴다.

        `clear()` 직후 첫 addItem이 헤더면 Qt가 currentIndex=0으로 잡아 헤더 문자열이
        `currentText()`(= 저장되는 모델명)가 된다. 호출부가 이후 선택을 복원하지만,
        복원할 값이 없는 경우(첫 실행 등)를 대비한 안전망.
        """
        idx = combo.currentIndex()
        if idx >= 0 and not combo.model().item(idx).isEnabled():
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
            if text:
                widest = max(widest, fm.horizontalAdvance(text))
        if widest:
            # 스크롤바·여백 여유분 가산
            combo.view().setMinimumWidth(widest + 40)

    def _populate_model_combo(self):
        """현재 backend의 캐시로 두 모델 콤보를 구성. 캐시 없으면 화이트리스트 기본값."""
        import json

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
        self._fill_both_combos(cached, backend)

    def _fill_both_combos(self, candidates: list[str], backend: str):
        """후보 목록 하나로 AI 질의 콤보(전 모델)와 AI OCR 콤보(비전 가능만)를 채운다.

        candidates가 비면(캐시 없는 첫 실행) 화이트리스트 기본값으로 대체한다.
        _populate_model_combo(캐시 로드)와 _on_models_fetched(↻ 새로고침)가 공유.
        """
        from pasteflow.ocr_engine import (
            sort_models_with_whitelist, whitelist_model_names, vision_capable_models,
        )
        if candidates:
            ai_verified, ai_unverified = sort_models_with_whitelist(candidates, backend)
            ocr_verified, ocr_unverified = sort_models_with_whitelist(
                vision_capable_models(candidates), backend)
        else:
            # 현 화이트리스트는 전원 비전 확인된 모델이지만, 나중에 텍스트 전용 모델이
            # 등재돼도 OCR 콤보로 새지 않도록 방어적으로 같은 필터를 통과시킨다.
            ai_verified = whitelist_model_names(backend)
            ocr_verified = vision_capable_models(ai_verified)
            ai_unverified = ocr_unverified = []
        self._fill_model_combo(self._model_combo, ai_verified, ai_unverified)
        self._fill_model_combo(self._ocr_model_combo, ocr_verified, ocr_unverified)

    def _set_status(self, message: str, ok: bool | None = None):
        """연결 테스트 / 모델 새로고침이 공유하는 상태 줄. ok=None이면 중립 색."""
        color = COLORS['subtext0'] if ok is None else (
            COLORS['green'] if ok else COLORS['red'])
        self._test_status.setStyleSheet(f"color: {color}; font-size: 11px;")
        self._test_status.setText(message)

    def _on_refresh_models(self):
        """🔄 버튼 — 현재 backend에 맞는 API에서 모델 목록 조회 (워커 스레드)."""
        api_key = self._api_key_edit.text().strip()
        if not api_key:
            self._set_status("✗ 먼저 API 키를 입력하세요.", ok=False)
            return
        backend = self._current_backend()
        base_url = self._base_url_edit.text().strip() if backend == "gateway" else ""

        self._model_refresh_btn.setEnabled(False)
        # 로딩 중: 아이콘 제거 + "..." 텍스트 (단순/명확)
        self._model_refresh_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserStop))
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
        """워커 스레드 결과 반영 (Qt 메인 스레드)."""
        self._model_refresh_btn.setEnabled(True)
        self._model_refresh_btn.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload)
        )

        if err:
            self._set_status(f"✗ 모델 조회 실패 — {err}", ok=False)
            return
        if not models:
            self._set_status("✗ 응답에 사용 가능한 모델이 없습니다. 설정을 확인하세요.", ok=False)
            return

        backend = self._current_backend()
        unique = sorted(set(models))

        # 재구성 전 두 콤보의 현재 선택을 보존했다가 복원.
        # setUpdatesEnabled: 39개 항목을 지웠다 다시 넣는 동안의 중간 상태 repaint를 한 프레임으로 묶는다.
        # (조회 중 모델명이 파랗게 반전되던 문제는 여기가 아니라 새로고침 버튼의 포커스 이동이
        #  원인이었다 → _model_refresh_btn.setFocusPolicy(NoFocus)로 해결. 아래 deselect는 안전망.)
        current_ai = self._model_combo.currentText()
        current_ocr = self._ocr_model_combo.currentText()
        for combo in (self._model_combo, self._ocr_model_combo):
            combo.setUpdatesEnabled(False)
        try:
            self._fill_both_combos(unique, backend)
            for combo, current in ((self._model_combo, current_ai),
                                   (self._ocr_model_combo, current_ocr)):
                idx = combo.findText(current)
                if idx >= 0:
                    combo.setCurrentIndex(idx)
                elif current:
                    # 목록에 없어도 사용자가 직접 입력한 값은 지우지 않는다(editable 콤보).
                    combo.setCurrentText(current)
                # Qt가 프로그램적 텍스트 설정 시 전체 선택 상태로 두는 것을 해제 —
                # 새로고침 직후 모델명이 파랗게 반전돼 보이던 잔상 제거.
                le = combo.lineEdit()
                if le is not None:
                    le.deselect()
                    le.setCursorPosition(0)
        finally:
            for combo in (self._model_combo, self._ocr_model_combo):
                combo.setUpdatesEnabled(True)

        n_ai = sum(1 for i in range(self._model_combo.count())
                   if not self._model_combo.itemData(i, _HEADER_ROLE))
        n_ocr = sum(1 for i in range(self._ocr_model_combo.count())
                    if not self._ocr_model_combo.itemData(i, _HEADER_ROLE))
        self._set_status(f"✓ 새로고침 완료 — 질의 {n_ai}종 · OCR {n_ocr}종", ok=True)

        import json
        # 캐시도 backend별로 분리 저장 — 공식/게이트웨이 모델 라인업이 달라 섞이면 안 됨
        self._settings[self._cache_key_for(backend)] = json.dumps(unique)

    def _on_save(self):
        """저장 버튼 클릭 — 레지스트리 등록은 main._on_settings_changed에서 처리.

        Gemini는 backend별 키/모델을 각각 보존:
        - 활성 backend의 입력값은 화면에서 읽어 갱신
        - 비활성 backend의 값은 self._settings에 stash된 것을 그대로 전달
        """
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
            self._settings[self.KEY_OCR_MODEL_GATEWAY] = self._ocr_model_combo.currentText()
        else:
            self._settings[self.KEY_OCR_GEMINI_API_KEY_OFFICIAL] = self._api_key_edit.text()
            self._settings[self.KEY_OCR_GEMINI_MODEL_OFFICIAL] = self._model_combo.currentText()
            self._settings[self.KEY_OCR_MODEL_OFFICIAL] = self._ocr_model_combo.currentText()

        # 양쪽 backend의 키/모델/캐시를 모두 같이 전달 — 일부만 보내면 다른 쪽이 사라질 위험
        for k in (
            self.KEY_OCR_GEMINI_API_KEY_OFFICIAL,
            self.KEY_OCR_GEMINI_API_KEY_GATEWAY,
            self.KEY_OCR_GEMINI_BASE_URL,
            self.KEY_OCR_GEMINI_MODEL_OFFICIAL,
            self.KEY_OCR_GEMINI_MODEL_GATEWAY,
            self.KEY_OCR_MODEL_OFFICIAL,
            self.KEY_OCR_MODEL_GATEWAY,
            self.KEY_OCR_GEMINI_MODEL_CACHE_OFFICIAL,
            self.KEY_OCR_GEMINI_MODEL_CACHE_GATEWAY,
        ):
            if k in self._settings:
                new_settings[k] = self._settings[k]
        self.settings_changed.emit(new_settings)
        self.accept()
