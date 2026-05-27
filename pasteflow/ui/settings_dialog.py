"""설정 다이얼로그 — F10

단축키 커스터마이징, 히스토리 제한, 자동 시작, 자동 닫기 설정.
"""
import sys
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QSpinBox, QCheckBox, QGroupBox, QFormLayout, QGridLayout, QComboBox, QLineEdit,
    QStyle,
)
from PyQt6.QtCore import Qt, pyqtSignal

from pasteflow.ui.theme import COLORS, TEAL_HOVER


DIALOG_STYLE = f"""
    QDialog {{
        background-color: {COLORS['base']};
        color: {COLORS['text']};
    }}
    QGroupBox {{
        background-color: {COLORS['mantle']};
        border: 1px solid {COLORS['surface1']};
        border-radius: 8px;
        margin-top: 12px;
        padding: 12px 8px 8px 8px;
        font-weight: 600;
        color: {COLORS['subtext0']};
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        left: 12px;
        padding: 0 4px;
    }}
    QLabel {{
        color: {COLORS['text']};
    }}
    QLineEdit, QSpinBox {{
        background-color: {COLORS['surface0']};
        color: {COLORS['text']};
        border: 1px solid {COLORS['surface1']};
        border-radius: 4px;
        padding: 4px 8px;
    }}
    QLineEdit:focus, QSpinBox:focus {{
        border-color: {COLORS['blue']};
    }}
    QCheckBox {{
        color: {COLORS['text']};
        spacing: 6px;
    }}
    QCheckBox::indicator {{
        width: 16px;
        height: 16px;
        border-radius: 3px;
        border: 1px solid {COLORS['surface1']};
        background-color: {COLORS['surface0']};
    }}
    QCheckBox::indicator:checked {{
        background-color: {COLORS['teal']};
        border-color: {COLORS['teal']};
    }}
    QPushButton {{
        background-color: {COLORS['surface0']};
        color: {COLORS['text']};
        border: 1px solid {COLORS['surface1']};
        border-radius: 6px;
        padding: 6px 16px;
    }}
    QPushButton:hover {{
        background-color: {COLORS['surface1']};
    }}
    QPushButton#saveBtn {{
        background-color: {COLORS['teal']};
        color: {COLORS['base']};
        font-weight: 600;
    }}
    QPushButton#saveBtn:hover {{
        background-color: {TEAL_HOVER};
    }}
    QComboBox QAbstractItemView {{
        background-color: {COLORS['surface0']};
        color: {COLORS['text']};
        selection-background-color: {COLORS['surface1']};
        selection-color: {COLORS['text']};
        border: 1px solid {COLORS['surface1']};
        outline: none;
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
                f"QPushButton {{ background-color: {COLORS['surface1']}; "
                f"color: {COLORS['teal']}; border: 1px solid {COLORS['teal']}; "
                f"border-radius: 4px; padding: 4px 8px; text-align: left; }}"
            )
        else:
            self.setStyleSheet(
                f"QPushButton {{ background-color: {COLORS['surface0']}; "
                f"color: {COLORS['text']}; border: 1px solid {COLORS['surface1']}; "
                f"border-radius: 4px; padding: 4px 8px; text-align: left; }}"
                f"QPushButton:hover {{ background-color: {COLORS['surface1']}; }}"
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
    KEY_OCR_LANG = "ocr_language"
    KEY_OCR_ENGINE = "ocr_engine"
    KEY_OCR_GEMINI_API_KEY = "ocr_gemini_api_key"
    KEY_OCR_GEMINI_BASE_URL = "ocr_gemini_base_url"
    KEY_OCR_GEMINI_MODEL = "ocr_gemini_model"
    KEY_OCR_GEMINI_MODEL_CACHE = "ocr_gemini_model_cache"
    KEY_QUEUE_IDLE_RESET = "queue_idle_reset_sec"

    # Flash 티어가 가장 저렴 → 기본값으로 첫 번째에 배치
    _DEFAULT_GEMINI_MODELS = ("gemini-3-flash-preview", "gemini-3.1-pro-preview", "gemini-2.5-pro")

    # 워커 스레드 → UI 안전 통신용 내부 시그널 (models, error_msg)
    _models_fetched = pyqtSignal(list, str)

    def __init__(self, current_settings: dict, parent=None):
        super().__init__(parent)
        self._settings = dict(current_settings)
        self._setup_window()
        self._setup_ui()
        self._load_values()
        self._models_fetched.connect(self._on_models_fetched)

    def _setup_window(self):
        self.setWindowTitle("PasteFlow 설정")
        self.setFixedSize(360, 720)
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.WindowCloseButtonHint
        )
        self.setStyleSheet(DIALOG_STYLE)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(16, 16, 16, 16)

        # ── 단축키 그룹 ──
        hotkey_group = QGroupBox("단축키")
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

        layout.addWidget(hotkey_group)

        # ── 단축키 안내 그룹 ──
        info_group = QGroupBox("단축키 안내")
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
                f"color: {COLORS['teal']}; font-size: 12px;"
                f" font-family: 'Consolas', monospace;"
            )
            info_layout.addWidget(action_lbl, row, 0)
            info_layout.addWidget(key_lbl, row, 1)

        layout.addWidget(info_group)

        # ── OCR 설정 그룹 ──
        ocr_group = QGroupBox("OCR (화면 텍스트 인식)")
        self._ocr_form = QFormLayout(ocr_group)
        ocr_form = self._ocr_form

        self._ocr_hotkey = HotkeyEdit()
        ocr_form.addRow("OCR 단축키:", self._ocr_hotkey)

        _combo_style = (
            f"QComboBox {{ background-color: {COLORS['surface0']}; color: {COLORS['text']}; "
            f"border: 1px solid {COLORS['surface1']}; border-radius: 4px; padding: 4px 8px; }}"
        )

        self._ocr_engine_combo = QComboBox()
        self._ocr_engine_combo.setStyleSheet(_combo_style)
        self._ocr_engine_combo.addItem("Windows WinRT (무료)", "winrt")
        self._ocr_engine_combo.addItem("Google Gemini (API 키 필요)", "gemini")
        ocr_form.addRow("OCR 엔진:", self._ocr_engine_combo)

        self._api_key_label = QLabel("API 키:")
        self._api_key_edit = QLineEdit()
        self._api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._api_key_edit.setPlaceholderText("게이트웨이 토큰 또는 Google AI Studio 키")
        ocr_form.addRow(self._api_key_label, self._api_key_edit)

        self._base_url_label = QLabel("Base URL:")
        self._base_url_edit = QLineEdit()
        self._base_url_edit.setPlaceholderText("https://... (공식 API 사용 시 비워두기)")
        ocr_form.addRow(self._base_url_label, self._base_url_edit)

        self._model_label = QLabel("모델명:")
        self._model_combo = QComboBox()
        self._model_combo.setEditable(True)
        self._model_combo.setStyleSheet(_combo_style)
        self._populate_model_combo()
        self._model_combo.setCurrentIndex(0)

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
        ocr_form.addRow(self._model_label, model_row)

        # 가장 저렴 모델 안내 힌트 — 콤보 목록 변경 시 _update_model_hint()로 갱신
        self._model_hint = QLabel()
        self._model_hint.setStyleSheet(
            f"color: {COLORS['subtext0']}; font-size: 11px;"
        )
        self._model_hint.setWordWrap(True)
        ocr_form.addRow("", self._model_hint)
        self._update_model_hint()

        self._ocr_engine_combo.currentIndexChanged.connect(self._on_engine_changed)

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
            lang_hint.setStyleSheet(
                f"color: {COLORS['subtext0']}; font-size: 11px;"
            )
            ocr_form.addRow("", lang_hint)

        ocr_form.addRow("인식 언어:", self._ocr_lang_combo)

        layout.addWidget(ocr_group)

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

        self._auto_start_check = QCheckBox("Windows 시작 시 자동 실행")
        general_form.addRow(self._auto_start_check)

        self._notify_copy_check = QCheckBox("복사 시 우하단 알림 표시")
        general_form.addRow(self._notify_copy_check)

        layout.addWidget(general_group)

        # ── 버튼 ──
        layout.addStretch()
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        cancel_btn = QPushButton("취소")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)

        save_btn = QPushButton("저장")
        save_btn.setObjectName("saveBtn")
        save_btn.clicked.connect(self._on_save)
        btn_layout.addWidget(save_btn)

        layout.addLayout(btn_layout)

    def _on_engine_changed(self, _idx: int):
        engine = self._ocr_engine_combo.currentData()
        needs_key = engine == "gemini"
        needs_url = engine == "gemini"
        needs_model = engine == "gemini"
        is_winrt = engine == "winrt"
        self._api_key_label.setVisible(needs_key)
        self._api_key_edit.setVisible(needs_key)
        self._base_url_label.setVisible(needs_url)
        self._base_url_edit.setVisible(needs_url)
        self._model_label.setVisible(needs_model)
        self._model_combo.setVisible(needs_model)
        self._model_refresh_btn.setVisible(needs_model)
        if hasattr(self, "_model_hint"):
            self._model_hint.setVisible(needs_model)
        # 엔진별 플레이스홀더 + 저장된 값 로드
        if engine == "gemini":
            self._api_key_edit.setPlaceholderText("게이트웨이 토큰 또는 Google AI Studio 키")
            self._base_url_edit.setPlaceholderText(
                "예: https://factchat-cloud.mindlogic.ai/v1/gateway (끝에 /chat/completions 붙이지 말 것)"
            )
            self._api_key_edit.setText(self._settings.get(self.KEY_OCR_GEMINI_API_KEY, ""))
            self._base_url_edit.setText(self._settings.get(self.KEY_OCR_GEMINI_BASE_URL, ""))
            saved_model = self._settings.get(self.KEY_OCR_GEMINI_MODEL, "gemini-3-flash-preview")
            self._model_combo.setCurrentText(saved_model or "gemini-3-flash-preview")
        if hasattr(self, '_ocr_form'):
            self._ocr_form.setRowVisible(self._ocr_lang_combo, is_winrt)

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
        engine = self._settings.get(self.KEY_OCR_ENGINE, "winrt")
        idx = self._ocr_engine_combo.findData(engine)
        self._ocr_engine_combo.setCurrentIndex(idx if idx >= 0 else 0)
        # key/url 텍스트는 _on_engine_changed 에서 엔진별로 로드
        self._on_engine_changed(self._ocr_engine_combo.currentIndex())

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

    @staticmethod
    def _model_cost_rank(name: str) -> int:
        """모델명에서 가격 티어를 추정한 정렬 키. 낮을수록 저렴."""
        n = name.lower()
        if "flash-lite" in n:
            return 0
        if "flash" in n:
            return 1
        if "pro" in n:
            return 2
        return 3  # 알 수 없는 티어는 맨 뒤

    def _update_model_hint(self):
        """콤보 항목 중 가장 저렴한 모델을 힌트 라벨에 표시."""
        if not hasattr(self, "_model_hint"):
            return
        items = [self._model_combo.itemText(i) for i in range(self._model_combo.count())]
        if not items:
            self._model_hint.setText("")
            return
        cheapest = min(items, key=self._model_cost_rank)
        self._model_hint.setText(f"💡 가장 저렴: {cheapest}")

    def _populate_model_combo(self):
        """캐시된 모델 목록이 있으면 사용, 없으면 기본 하드코딩 목록. 가격 순 정렬."""
        import json
        cache_str = self._settings.get(self.KEY_OCR_GEMINI_MODEL_CACHE, "")
        models: list[str] = []
        if cache_str:
            try:
                parsed = json.loads(cache_str)
                if isinstance(parsed, list):
                    models = [str(m) for m in parsed if m]
            except (json.JSONDecodeError, ValueError, TypeError):
                pass
        if not models:
            models = list(self._DEFAULT_GEMINI_MODELS)
        models = sorted(set(models), key=self._model_cost_rank)
        self._model_combo.clear()
        for m in models:
            self._model_combo.addItem(m)
        self._update_model_hint()

    def _on_refresh_models(self):
        """🔄 버튼 — API에서 모델 목록 조회 (워커 스레드)."""
        api_key = self._api_key_edit.text().strip()
        if not api_key:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "API 키 필요",
                                "먼저 API 키를 입력하세요.")
            return
        base_url = self._base_url_edit.text().strip()

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

        models = sorted(set(models), key=self._model_cost_rank)
        current = self._model_combo.currentText()
        self._model_combo.clear()
        for m in models:
            self._model_combo.addItem(m)
        idx = self._model_combo.findText(current)
        if idx >= 0:
            self._model_combo.setCurrentIndex(idx)
        elif current:
            self._model_combo.setCurrentText(current)
        self._update_model_hint()

        import json
        self._settings[self.KEY_OCR_GEMINI_MODEL_CACHE] = json.dumps(models)

    def _on_save(self):
        """저장 버튼 클릭 — 레지스트리 등록은 main._on_settings_changed에서 처리"""
        engine = self._ocr_engine_combo.currentData()
        auto_start = self._auto_start_check.isChecked()

        new_settings = {
            self.KEY_PANEL_TOGGLE: self._panel_toggle_hotkey.value() or "ctrl+space",
            self.KEY_OCR_HOTKEY: self._ocr_hotkey.value() or "ctrl+shift+s",
            self.KEY_IMAGE_TO_PATH_HOTKEY: self._image_to_path_hotkey.value() or "ctrl+shift+p",
            self.KEY_OCR_LANG: self._ocr_lang_combo.currentText(),
            self.KEY_OCR_ENGINE: engine,
            self.KEY_HISTORY_MAX: str(self._history_max_spin.value()),
            self.KEY_QUEUE_IDLE_RESET: str(self._queue_idle_spin.value()),
            self.KEY_AUTO_START: "1" if auto_start else "0",
            self.KEY_NOTIFY_ON_COPY: "1" if self._notify_copy_check.isChecked() else "0",
        }
        # 엔진별 key/url 저장 (서로 덮어쓰지 않음)
        if engine == "gemini":
            new_settings[self.KEY_OCR_GEMINI_API_KEY] = self._api_key_edit.text()
            new_settings[self.KEY_OCR_GEMINI_BASE_URL] = self._base_url_edit.text()
            new_settings[self.KEY_OCR_GEMINI_MODEL] = self._model_combo.currentText()
            # 새로고침으로 갱신된 캐시가 있으면 DB에도 반영
            cache = self._settings.get(self.KEY_OCR_GEMINI_MODEL_CACHE)
            if cache:
                new_settings[self.KEY_OCR_GEMINI_MODEL_CACHE] = cache
        self.settings_changed.emit(new_settings)
        self.accept()
