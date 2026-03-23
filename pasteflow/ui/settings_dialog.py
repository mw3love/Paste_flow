"""설정 다이얼로그 — F10

단축키 커스터마이징, 히스토리 제한, 자동 시작, 자동 닫기 설정.
"""
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QSpinBox, QCheckBox, QLineEdit, QGroupBox, QFormLayout,
)
from PyQt6.QtCore import Qt, pyqtSignal

# Catppuccin Mocha
COLORS = {
    "base": "#1e1e2e",
    "mantle": "#181825",
    "surface0": "#313244",
    "surface1": "#45475a",
    "teal": "#94e2d5",
    "text": "#cdd6f4",
    "subtext0": "#a6adc8",
    "blue": "#89b4fa",
    "red": "#f38ba8",
}

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
        background-color: #7dd6c8;
    }}
"""


class SettingsDialog(QDialog):
    """설정 다이얼로그"""

    settings_changed = pyqtSignal(dict)  # 변경된 설정 dict

    # 설정 키 상수
    KEY_PANEL_TOGGLE = "hotkey_panel_toggle"
    KEY_HISTORY_MAX = "history_max"
    KEY_AUTO_START = "auto_start"
    KEY_AUTO_CLOSE = "panel_auto_close"

    def __init__(self, current_settings: dict, parent=None):
        super().__init__(parent)
        self._settings = dict(current_settings)
        self._setup_window()
        self._setup_ui()
        self._load_values()

    def _setup_window(self):
        self.setWindowTitle("PasteFlow 설정")
        self.setFixedSize(360, 380)
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

        self._panel_toggle_edit = QLineEdit()
        self._panel_toggle_edit.setPlaceholderText("예: alt+v")
        hotkey_form.addRow("패널 토글:", self._panel_toggle_edit)

        layout.addWidget(hotkey_group)

        # ── 일반 설정 그룹 ──
        general_group = QGroupBox("일반")
        general_form = QFormLayout(general_group)

        self._history_max_spin = QSpinBox()
        self._history_max_spin.setRange(10, 500)
        self._history_max_spin.setValue(50)
        general_form.addRow("히스토리 최대 개수:", self._history_max_spin)

        self._auto_close_check = QCheckBox("패널 외부 클릭 시 자동 닫기")
        general_form.addRow(self._auto_close_check)

        self._auto_start_check = QCheckBox("Windows 시작 시 자동 실행")
        general_form.addRow(self._auto_start_check)

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

    def _load_values(self):
        """현재 설정값 로드"""
        self._panel_toggle_edit.setText(
            self._settings.get(self.KEY_PANEL_TOGGLE, "alt+v")
        )
        self._history_max_spin.setValue(
            int(self._settings.get(self.KEY_HISTORY_MAX, "50"))
        )
        self._auto_close_check.setChecked(
            self._settings.get(self.KEY_AUTO_CLOSE, "1") == "1"
        )
        self._auto_start_check.setChecked(
            self._settings.get(self.KEY_AUTO_START, "0") == "1"
        )

    def _on_save(self):
        """저장 버튼 클릭"""
        new_settings = {
            self.KEY_PANEL_TOGGLE: self._panel_toggle_edit.text().strip() or "alt+v",
            self.KEY_HISTORY_MAX: str(self._history_max_spin.value()),
            self.KEY_AUTO_CLOSE: "1" if self._auto_close_check.isChecked() else "0",
            self.KEY_AUTO_START: "1" if self._auto_start_check.isChecked() else "0",
        }
        self.settings_changed.emit(new_settings)
        self.accept()
