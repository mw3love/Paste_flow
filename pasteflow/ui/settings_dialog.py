"""설정 다이얼로그 — F10

단축키 커스터마이징, 히스토리 제한, 자동 시작, 자동 닫기 설정.
"""
import sys
import winreg
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QSpinBox, QCheckBox, QGroupBox, QFormLayout, QGridLayout,
)
from PyQt6.QtCore import Qt, pyqtSignal

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_APP_NAME = "PasteFlow"


def _set_auto_start(enabled: bool) -> None:
    """Windows 시작 시 자동 실행 레지스트리 등록/해제"""
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _RUN_KEY,
            0, winreg.KEY_SET_VALUE
        )
        if enabled:
            exe_path = sys.executable
            winreg.SetValueEx(key, _APP_NAME, 0, winreg.REG_SZ, f'"{exe_path}" -m pasteflow.main')
        else:
            try:
                winreg.DeleteValue(key, _APP_NAME)
            except FileNotFoundError:
                pass
        winreg.CloseKey(key)
    except OSError:
        pass

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
    KEY_AUTO_CLOSE = "panel_auto_close"

    def __init__(self, current_settings: dict, parent=None):
        super().__init__(parent)
        self._settings = dict(current_settings)
        self._setup_window()
        self._setup_ui()
        self._load_values()

    def _setup_window(self):
        self.setWindowTitle("PasteFlow 설정")
        self.setFixedSize(360, 500)
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
        self._panel_toggle_hotkey.set_value(
            self._settings.get(self.KEY_PANEL_TOGGLE, "ctrl+space")
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
        """저장 버튼 클릭 — 레지스트리 등록은 main._on_settings_changed에서 처리"""
        auto_start = self._auto_start_check.isChecked()

        new_settings = {
            self.KEY_PANEL_TOGGLE: self._panel_toggle_hotkey.value() or "ctrl+space",
            self.KEY_HISTORY_MAX: str(self._history_max_spin.value()),
            self.KEY_AUTO_CLOSE: "1" if self._auto_close_check.isChecked() else "0",
            self.KEY_AUTO_START: "1" if auto_start else "0",
        }
        self.settings_changed.emit(new_settings)
        self.accept()
