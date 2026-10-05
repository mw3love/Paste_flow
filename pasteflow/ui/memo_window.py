"""빠른 메모창 — 메모장(고정 섹션) 텍스트 항목을 쓰는 동안 자동 저장한다.

UX 정책:
- 윈도우 메모장 대체용이라 일반 창이다(제목 표시줄·작업 표시줄·크기 조절, 항상 위 아님).
- 타이핑이 _SAVE_DELAY_MS 동안 멈추면 저장을 요청한다. 닫을 때(앱 종료 포함) 남은 변경을 마저 저장.
- 비어 있는 채로 닫으면 그 메모를 지워 달라고 요청한다(잠긴 메모는 main/DB가 거른다).
- DB는 직접 만지지 않고 시그널로 main에 넘긴다. 클립보드·순차 큐와는 무관하다.
- 같은 메모를 다시 열면 새 창 대신 열려 있는 창을 앞으로 가져온다.
- Ctrl+휠로 글자 크기를 바꾸고 Ctrl+0으로 되돌린다(윈도우 메모장처럼).
- 글자 크기와 마지막 창 크기·위치는 main이 DB에 저장해 다음에도 그대로 연다(set_prefs/prefs_saver).
- Esc·Ctrl+W = 닫기, Ctrl+T = 항상 위 켜기/끄기(창마다, 저장 안 함).
"""
import base64
import ctypes
import ctypes.wintypes

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QPlainTextEdit, QFrame, QApplication
from PyQt6.QtCore import Qt, QTimer, QRect, QPoint, QEvent, QByteArray, pyqtSignal
from PyQt6.QtGui import QFont, QCursor, QKeySequence, QShortcut

from pasteflow.ui.theme import BASE as _BG, TEXT as _TEXT, PEACH as _PEACH
from pasteflow.ui.image_preview import compute_preview_pos, _CASCADE_STEP

_SAVE_DELAY_MS = 500
_DEFAULT_W = 420
_DEFAULT_H = 320
_FONT_FAMILY = "맑은 고딕"
_FONT_PX = 14
_FONT_PX_MIN = 8
_FONT_PX_MAX = 72
_ZOOM_STEP = 1.1  # 휠 한 칸당 ~10%

# 항상 위 — Qt 플래그를 바꾸면 창이 다시 만들어지며 깜빡여서 SetWindowPos로 TOPMOST만 바꾼다.
# 공유 windll.user32에 argtypes를 걸지 않도록 전용 인스턴스를 쓴다(CLAUDE.md 함정).
_user32 = ctypes.WinDLL("user32")
# argtypes 없이 -1을 넘기면 64비트 HWND로 부호 확장이 안 돼 TOPMOST가 안 걸린다(2026-10-05 실측)
_user32.SetWindowPos.argtypes = [
    ctypes.wintypes.HWND, ctypes.wintypes.HWND,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.wintypes.UINT,
]
_HWND_TOPMOST = -1
_HWND_NOTOPMOST = -2
_SWP_FLAGS = 0x0001 | 0x0002 | 0x0010  # NOSIZE | NOMOVE | NOACTIVATE


class MemoWindow(QWidget):
    """메모 하나를 편집하는 창 — item_id 하나당 창 하나"""

    _instances: dict[int, "MemoWindow"] = {}
    _font_px: int = _FONT_PX  # 마지막으로 맞춘 글자 크기 — 새 메모창도 이 크기로 연다
    _last_geometry: QByteArray | None = None  # 마지막으로 닫은 창의 saveGeometry() — 다음 창 자리
    prefs_saver = None  # main이 넣는 콜백(dict) — 글자 크기·창 자리가 바뀌면 DB에 저장

    @classmethod
    def set_prefs(cls, prefs: dict):
        """시작 시 main이 DB에서 읽은 값으로 글자 크기·마지막 창 자리를 되살린다."""
        px = prefs.get("font_px")
        if isinstance(px, int):
            cls._font_px = max(_FONT_PX_MIN, min(_FONT_PX_MAX, px))
        geom = prefs.get("geometry")
        if isinstance(geom, str) and geom:
            try:
                cls._last_geometry = QByteArray(base64.b64decode(geom))
            except Exception:
                cls._last_geometry = None

    @classmethod
    def _save_prefs(cls):
        if cls.prefs_saver is None:
            return
        geom = cls._last_geometry
        cls.prefs_saver({
            "font_px": cls._font_px,
            "geometry": base64.b64encode(bytes(geom)).decode("ascii") if geom else "",
        })

    save_requested = pyqtSignal(int, str)   # (item_id, text) — 내용이 바뀌었을 때만
    discard_requested = pyqtSignal(int)     # item_id — 빈 채로 닫힘
    closed = pyqtSignal(int)                # item_id — 패널 갱신용

    @classmethod
    def get(cls, item_id: int) -> "MemoWindow | None":
        return cls._instances.get(item_id)

    @classmethod
    def flush_all(cls):
        """열린 모든 메모창의 저장 대기 중인 변경을 지금 저장한다(메모장 비우기 스냅샷 직전)."""
        for win in list(cls._instances.values()):
            win._flush()

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
        self._apply_font_px(type(self)._font_px)
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
        # 편집 가능한 QPlainTextEdit은 Ctrl+휠 확대를 스스로 하지 않는다(읽기 전용일 때만) → 직접 처리
        self._editor.viewport().installEventFilter(self)

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(_SAVE_DELAY_MS)
        self._save_timer.timeout.connect(self._flush)
        self._editor.textChanged.connect(self._on_text_changed)

        self._topmost = False
        for keys, slot in (("Ctrl+W", self.close), ("Ctrl+T", self._toggle_topmost),
                           ("Ctrl+0", self._reset_zoom)):
            QShortcut(QKeySequence(keys), self, slot)

        self._update_title()
        self.resize(_DEFAULT_W, _DEFAULT_H)
        type(self)._instances[item_id] = self

    @property
    def item_id(self) -> int:
        return self._item_id

    def show_near(self, panel_geom: QRect | None):
        """마지막으로 닫은 메모창 자리(있으면)에, 없으면 패널 옆이나 커서 화면 가운데에 띄운다.

        다른 메모창이 열려 있으면 그만큼 비켜 놓아 완전히 겹치지 않게 한다.
        """
        cascade = (len(type(self)._instances) - 1) * _CASCADE_STEP
        if type(self)._last_geometry is not None and self.restoreGeometry(type(self)._last_geometry):
            if cascade:  # 표시 전엔 pos()에 제목 표시줄이 안 잡혀 → 안쪽 영역(geometry) 기준으로 비킨다
                self.setGeometry(self.geometry().translated(cascade, cascade))
        elif panel_geom is not None:
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

    def _apply_font_px(self, px: int):
        font = QFont(_FONT_FAMILY)
        font.setPixelSize(px)
        self._editor.setFont(font)
        self._editor.document().setDocumentMargin(0)  # setFont가 여백을 되돌리는 경우 대비

    def eventFilter(self, obj, event):
        if (obj is self._editor.viewport() and event.type() == QEvent.Type.Wheel
                and event.modifiers() & Qt.KeyboardModifier.ControlModifier):
            delta = event.angleDelta().y()
            if delta:
                cur = self._editor.font().pixelSize()
                new = round(cur * _ZOOM_STEP) if delta > 0 else round(cur / _ZOOM_STEP)
                if new == cur:  # 작은 크기에서 10%가 반올림으로 사라지지 않게 최소 1px
                    new = cur + (1 if delta > 0 else -1)
                new = max(_FONT_PX_MIN, min(_FONT_PX_MAX, new))
                self._set_zoom(new)
            return True
        return super().eventFilter(obj, event)

    def _set_zoom(self, px: int):
        self._apply_font_px(px)
        type(self)._font_px = px
        type(self)._save_prefs()

    def _reset_zoom(self):
        self._set_zoom(_FONT_PX)

    def _toggle_topmost(self):
        self._topmost = not self._topmost
        _user32.SetWindowPos(int(self.winId()),
                             _HWND_TOPMOST if self._topmost else _HWND_NOTOPMOST,
                             0, 0, 0, 0, _SWP_FLAGS)
        self._update_title()

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
        title = f"{first} — 메모" if first else "새 메모"
        if getattr(self, "_topmost", False):
            title += "  [항상 위]"
        self.setWindowTitle(title)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event):
        self._flush()
        type(self)._last_geometry = self.saveGeometry()
        type(self)._save_prefs()
        type(self)._instances.pop(self._item_id, None)
        if not self._editor.toPlainText().strip():
            self.discard_requested.emit(self._item_id)
        self.closed.emit(self._item_id)
        super().closeEvent(event)
