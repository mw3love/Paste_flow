"""어두운 테마 QMessageBox 스타일 — pasteflow/main.py에서 글자 그대로 옮김(2026-11-16 폴더 구조 2편)."""

from pasteflow.ui.theme import COLORS


_MSGBOX_DARK_STYLE = (
    f"QMessageBox {{ background-color: {COLORS['base']}; color: {COLORS['text']}; }}"
    f" QMessageBox QLabel {{ color: {COLORS['text']}; }}"
    f" QMessageBox QPushButton {{"
    f"   background-color: {COLORS['surface0']}; color: {COLORS['text']};"
    f"   border: 1px solid {COLORS['surface1']}; border-radius: 4px;"
    f"   padding: 4px 12px; min-width: 60px; }}"
    f" QMessageBox QPushButton:hover {{ background-color: {COLORS['surface1']}; }}"
    f" QTextEdit {{ background-color: {COLORS['surface0']}; color: {COLORS['text']};"
    f"   border: 1px solid {COLORS['surface1']}; }}"
)
