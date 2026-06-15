"""AI 질의 입력 다이얼로그.

패널 우클릭 "AI에게 질문" → 이 다이얼로그로 질문을 입력받는다. 우클릭한 클립보드
항목 내용을 컨텍스트로 함께 보여줘 "무엇에 대해 묻는지" 확인할 수 있게 한다.
Enter로 전송, Shift+Enter 줄바꿈, Esc 취소.
"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
)
from PyQt6.QtCore import Qt

from pasteflow.ui.theme import COLORS, TEAL_HOVER


class _QuestionEdit(QPlainTextEdit):
    """Enter=전송, Shift+Enter=줄바꿈."""

    def __init__(self, on_submit, parent=None):
        super().__init__(parent)
        self._on_submit = on_submit

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                super().keyPressEvent(event)
                return
            self._on_submit()
            return
        super().keyPressEvent(event)


class AiQueryDialog(QDialog):
    """AI 질문 입력 — 컨텍스트(우클릭 항목)를 위에 보여주고 질문을 받는다."""

    _CTX_PREVIEW_CHARS = 300

    def __init__(self, context_text: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("AI에게 질문")
        self.setMinimumSize(420, 240)
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {COLORS['base']};
                color: {COLORS['text']};
            }}
            QLabel {{
                color: {COLORS['subtext0']};
                font-size: 11px;
            }}
            QLabel#ctx {{
                background-color: {COLORS['surface0']};
                border: 1px solid {COLORS['surface2']};
                border-radius: 6px;
                padding: 6px;
                color: {COLORS['subtext0']};
                font-size: 11px;
            }}
            QPlainTextEdit {{
                background-color: {COLORS['surface0']};
                color: {COLORS['text']};
                border: 1px solid {COLORS['surface2']};
                border-radius: 6px;
                padding: 6px;
                font-size: 13px;
            }}
            QPlainTextEdit:focus {{
                border: 1px solid {COLORS['teal']};
            }}
            QPushButton {{
                background-color: {COLORS['surface1']};
                color: {COLORS['text']};
                border: none;
                border-radius: 6px;
                padding: 6px 16px;
                font-size: 12px;
            }}
            QPushButton:hover {{
                background-color: {COLORS['surface2']};
            }}
            QPushButton[text="질문"] {{
                background-color: {COLORS['teal']};
                color: {COLORS['base']};
            }}
            QPushButton[text="질문"]:hover {{
                background-color: {TEAL_HOVER};
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(12, 12, 12, 12)

        ctx = (context_text or "").strip()
        if ctx:
            layout.addWidget(QLabel("선택한 항목(컨텍스트):"))
            preview = ctx[: self._CTX_PREVIEW_CHARS].replace("\n", " ")
            if len(ctx) > self._CTX_PREVIEW_CHARS:
                preview += " …"
            ctx_label = QLabel(preview)
            ctx_label.setObjectName("ctx")
            ctx_label.setWordWrap(True)
            layout.addWidget(ctx_label)

        layout.addWidget(QLabel("질문을 입력하세요 (Enter 전송 · Shift+Enter 줄바꿈):"))

        self._editor = _QuestionEdit(self._try_submit)
        self._editor.setFocus()
        layout.addWidget(self._editor, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("취소")
        cancel_btn.clicked.connect(self.reject)
        ask_btn = QPushButton("질문")
        ask_btn.setProperty("text", "질문")
        ask_btn.clicked.connect(self._try_submit)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(ask_btn)
        layout.addLayout(btn_row)

    def _try_submit(self):
        if self._editor.toPlainText().strip():
            self.accept()

    def get_question(self) -> str:
        return self._editor.toPlainText().strip()
