"""빠른 메모창 — 메모장(고정 섹션) 텍스트 항목을 쓰는 동안 자동 저장한다.

UX 정책:
- 윈도우 메모장 대체용이라 일반 창이다(제목 표시줄·작업 표시줄·크기 조절, 항상 위 아님).
- 타이핑이 _SAVE_DELAY_MS 동안 멈추면 저장을 요청한다. 닫을 때(앱 종료 포함) 남은 변경을 마저 저장.
- 비어 있는 채로 닫으면 그 메모를 지워 달라고 요청한다(잠긴 메모는 main/DB가 거른다).
- DB는 직접 만지지 않고 시그널로 main에 넘긴다. 클립보드·순차 큐와는 무관하다.
- 같은 메모를 다시 열면 새 창 대신 열려 있는 창을 앞으로 가져온다.
"""
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QPlainTextEdit, QFrame, QApplication
from PyQt6.QtCore import Qt, QTimer, QRect, QPoint, pyqtSignal
from PyQt6.QtGui import QFont, QCursor

from pasteflow.ui.theme import BASE as _BG, TEXT as _TEXT, PEACH as _PEACH
from pasteflow.ui.image_preview import compute_preview_pos, _CASCADE_STEP

_SAVE_DELAY_MS = 500
_DEFAULT_W = 420
_DEFAULT_H = 320
_FONT_FAMILY = "맑은 고딕"
_FONT_PX = 14


class MemoWindow(QWidget):
    """메모 하나를 편집하는 창 — item_id 하나당 창 하나"""

    _instances: dict[int, "MemoWindow"] = {}

    save_requested = pyqtSignal(int, str)   # (item_id, text) — 내용이 바뀌었을 때만
    discard_requested = pyqtSignal(int)     # item_id — 빈 채로 닫힘
    closed = pyqtSignal(int)                # item_id — 패널 갱신용

    @classmethod
    def get(cls, item_id: int) -> "MemoWindow | None":
        return cls._instances.get(item_id)

    @classmethod
    def close_all(cls):
        """앱 종료 직전(DB가 닫히기 전) — 모든 메모창을 닫아 저장·빈 메모 삭제를 마친다."""
        for win in list(cls._instances.values()):
            win.close()

    def __init__(self, item_id: int, text: str):
        super().__init__(None)
        self._item_id = item_id
        self._saved_text = text
        self.setWindowFlags(Qt.WindowType.Window)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setStyleSheet(f"background-color: {_BG};")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._editor = QPlainTextEdit()
        self._editor.setFrameShape(QFrame.Shape.NoFrame)
        self._editor.setViewportMargins(10, 10, 10, 10)
        self._editor.document().setDocumentMargin(0)
        font = QFont(_FONT_FAMILY)
        font.setPixelSize(_FONT_PX)
        self._editor.setFont(font)
        self._editor.setStyleSheet(f"""
            QPlainTextEdit {{
                background: {_BG};
                color: {_TEXT};
                border: none;
                selection-background-color: {_PEACH};
                selection-color: {_BG};
            }}
        """)
        self._editor.setPlainText(text)
        self._editor.moveCursor(self._editor.textCursor().MoveOperation.End)
        layout.addWidget(self._editor)

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(_SAVE_DELAY_MS)
        self._save_timer.timeout.connect(self._flush)
        self._editor.textChanged.connect(self._on_text_changed)

        self._update_title()
        self.resize(_DEFAULT_W, _DEFAULT_H)
        type(self)._instances[item_id] = self

    @property
    def item_id(self) -> int:
        return self._item_id

    def show_near(self, panel_geom: QRect | None):
        """패널이 보이면 그 옆에, 아니면 커서가 있는 화면 가운데에 띄우고 포커스를 준다."""
        cascade = (len(type(self)._instances) - 1) * _CASCADE_STEP
        if panel_geom is not None:
            screen = QApplication.screenAt(panel_geom.center()) or QApplication.primaryScreen()
            self.move(compute_preview_pos(panel_geom, self.size(), screen, cascade))
        else:
            screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
            center = screen.availableGeometry().center()
            self.move(center - QPoint(self.width() // 2 - cascade, self.height() // 2 - cascade))
        self.bring_to_front()

    def bring_to_front(self):
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()
        self._editor.setFocus()

    def _on_text_changed(self):
        self._update_title()
        self._save_timer.start()

    def _flush(self):
        self._save_timer.stop()
        text = self._editor.toPlainText()
        if text != self._saved_text:
            self._saved_text = text
            self.save_requested.emit(self._item_id, text)

    def _update_title(self):
        first = self._editor.toPlainText().strip().split("\n", 1)[0].strip()
        if len(first) > 30:
            first = first[:30] + "…"
        self.setWindowTitle(f"{first} — 메모" if first else "새 메모")

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event):
        self._flush()
        type(self)._instances.pop(self._item_id, None)
        if not self._editor.toPlainText().strip():
            self.discard_requested.emit(self._item_id)
        self.closed.emit(self._item_id)
        super().closeEvent(event)
