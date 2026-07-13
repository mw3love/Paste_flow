"""AI 질문 기록 뷰어.

트레이 "AI 기록"에서 연다. AI 답변창(TextPreviewPopup)은 표시 전용이라 닫으면 사라지므로,
main이 답변을 받을 때마다 DB(ai_history 테이블)에 자동 저장한 것을 여기서 다시 훑어보고
더블클릭으로 재열람한다. 재열람·삭제는 각각 시그널로 main에 위임한다(main이 popup 생성·
db 접근 양쪽을 갖고 있어 목록 UI는 얇게 유지).
"""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMenu,
    QPushButton, QVBoxLayout,
)

from pasteflow.ui.ai_query import BACKEND_LABEL
from pasteflow.ui.theme import COLORS


class AiHistoryDialog(QDialog):
    """저장된 AI 대화 기록 목록 — 더블클릭으로 열기, 우클릭으로 삭제."""

    open_requested = pyqtSignal(int)  # history_id

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self._db = db
        self.setWindowTitle("AI 기록")
        self.setMinimumSize(420, 480)
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {COLORS['base']};
                color: {COLORS['text']};
            }}
            QLabel {{
                color: {COLORS['subtext0']};
                font-size: 11px;
            }}
            QListWidget {{
                background-color: {COLORS['surface0']};
                color: {COLORS['text']};
                border: 1px solid {COLORS['surface2']};
                border-radius: 6px;
                font-size: 12px;
            }}
            QListWidget::item {{
                padding: 8px;
                border-bottom: 1px solid {COLORS['surface1']};
            }}
            QListWidget::item:selected {{
                background-color: {COLORS['surface2']};
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
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        layout.addWidget(QLabel("더블클릭으로 다시 열기 · 우클릭으로 삭제"))

        self._list = QListWidget()
        self._list.itemDoubleClicked.connect(self._on_double_click)
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._on_context_menu)
        layout.addWidget(self._list, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton("닫기")
        close_btn.clicked.connect(self.close)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

        self._reload()

    def _reload(self):
        """DB에서 목록을 다시 읽어 채운다(삭제 직후·최초 오픈 시)."""
        self._list.clear()
        records = self._db.get_ai_history_list()
        if not records:
            placeholder = QListWidgetItem("아직 저장된 AI 대화가 없습니다.")
            placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
            self._list.addItem(placeholder)
            return
        for r in records:
            backend = r.get("backend") or ""
            model = r.get("model") or "기본"
            when = (r.get("updated_at") or "")[:16].replace("T", " ")
            title = r.get("title") or "(질문 없음)"
            text = f"{title}\n{model} ({BACKEND_LABEL.get(backend, backend)}) · {when}"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, r["id"])
            self._list.addItem(item)

    def _on_double_click(self, item: QListWidgetItem):
        hid = item.data(Qt.ItemDataRole.UserRole)
        if hid is not None:
            self.open_requested.emit(hid)

    def _on_context_menu(self, pos):
        item = self._list.itemAt(pos)
        if item is None or item.data(Qt.ItemDataRole.UserRole) is None:
            return
        hid = item.data(Qt.ItemDataRole.UserRole)
        menu = QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{
                background-color: {COLORS['surface0']};
                color: {COLORS['text']};
                border: 1px solid {COLORS['surface2']};
            }}
            QMenu::item:selected {{
                background-color: {COLORS['surface2']};
            }}
        """)
        open_action = menu.addAction("열기")
        delete_action = menu.addAction("삭제")
        chosen = menu.exec(self._list.mapToGlobal(pos))
        if chosen == open_action:
            self.open_requested.emit(hid)
        elif chosen == delete_action:
            self._db.delete_ai_conversation(hid)
            self._reload()
