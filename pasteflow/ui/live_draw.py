"""라이브 화면 판서 창 — 커서 모니터를 덮는 투명 창 위에 주석 편집기 도구로 그린다.

계획: docs/plan/done/20261125-2040-라이브-화면-판서.md.

두 상태를 단축키(기본 Ctrl+Shift+D) 하나로 오간다(app/live_draw.py가 toggle_mode를 부름).
- 그리기: 창이 클릭·키보드를 받는다. 툴바는 모니터 아래 가운데. Esc = 그림을 버리고 닫기.
- 앱 쓰기: 그림은 남고 클릭은 아래 앱으로 지나간다(WS_EX_TRANSPARENT). 툴바는 숨김.

배경을 알파 1(거의 투명)로 칠하는 이유: Windows는 알파 0 화소의 클릭을 아래 창으로 넘긴다 —
그리기 상태에서 클릭을 받으려면 0이 아니어야 한다(0편 시험판 실측, spikes/live_draw/README.md).
편집 동작(도구·색·두께·되돌리기·스포이드)은 _EditorMixin을 그대로 물려받는다. 이 창에서는
Ctrl+C·툴바 복사·저장이 「주석 복제」가 아니라 「화면 + 판서」 한 장을 만드는 일이다.
"""
from PyQt6.QtCore import QPoint, QRect, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import (
    QFrame, QGraphicsScene, QGraphicsTextItem, QGraphicsView, QHBoxLayout, QVBoxLayout, QWidget,
)

from pasteflow.ui.image_annotator import _AnnotatorView, _EditorMixin
from pasteflow.ui.image_preview import _CHROME_QSS
from pasteflow.ui.memo_window import _force_foreground
from pasteflow.ui.theme import SURFACE2 as _SURFACE2
from pasteflow.winapi import click_through

_TOOLBAR_BOTTOM_GAP = 28     # 툴바와 모니터 아래 끝 사이(논리 px)
_GRAB_DELAY_MS = 50          # 툴바를 숨긴 뒤 화면에 반영될 틈을 주고 찍는다

# 공유 편집기 스타일의 `QWidget{background:_BG}`가 판서 창 전체를 검게 칠하지 않게 되돌린다.
_LIVE_QSS = (
    "\nQWidget#livedrawroot { background: transparent; }"
    "\nQGraphicsView { background: transparent; border: none; }"
    "\nQWidget#livegrip { background: transparent; }"
)


class _ToolbarGrip(QWidget):
    """툴바 왼쪽 손잡이 — 왼쪽 버튼으로 끌면 툴바가 판서 창 안에서 움직인다.

    툴바는 판서 창의 자식이라 move()로 옮겨도 된다(「프레임리스 창은 startSystemMove」 함정은
    모니터를 넘나드는 최상위 창의 일). _DragBar는 전역 좌표를 부모 좌표로 쓰므로 보조 모니터
    (원점이 0이 아님)에서 어긋나 따로 둔다 — 부모 좌표로 바꿔 계산한다.
    """

    def __init__(self, toolbar: QWidget):
        super().__init__(toolbar)
        self.setObjectName("livegrip")
        self._bar = toolbar
        self._grab = None
        self.setFixedSize(14, 26)
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.setToolTip("끌어서 툴바 옮기기")

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#9a9a9a"))
        for row in range(3):
            for col in range(2):
                p.drawEllipse(3 + col * 5, 7 + row * 5, 3, 3)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            parent = self._bar.parentWidget()
            self._grab = parent.mapFromGlobal(e.globalPosition().toPoint()) - self._bar.pos()

    def mouseMoveEvent(self, e):
        if self._grab is None or not (e.buttons() & Qt.MouseButton.LeftButton):
            return
        parent = self._bar.parentWidget()
        p = parent.mapFromGlobal(e.globalPosition().toPoint()) - self._grab
        x = max(0, min(p.x(), parent.width() - self._bar.width()))
        y = max(0, min(p.y(), parent.height() - self._bar.height()))
        self._bar.move(x, y)

    def mouseReleaseEvent(self, e):
        self._grab = None


class LiveDrawOverlay(_EditorMixin, QWidget):
    _allow_fade_pen = True       # 사라지는 펜은 판서에만(이미지 주석은 남기려고 그리는 것)
    _instances: list = []        # _EditorMixin.closeEvent가 정리한다

    annotated_copy_requested = pyqtSignal(bytes)   # _EditorMixin 호스트 요건(이 창은 쓰지 않음)
    export_file_requested = pyqtSignal(bytes)      # 〃
    screen_copy_requested = pyqtSignal(object, object)   # (QPixmap 화면+판서, QRect 논리 전역)
    mode_changed = pyqtSignal(bool)                # True=그리기, False=앱 쓰기
    closed = pyqtSignal()

    def __init__(self, screen):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.setObjectName("livedrawroot")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._screen = screen
        self._drawing = True
        self._prev_fg = click_through.foreground_window()   # 앱 쓰기·끝낼 때 돌려줄 창(그리기로 올 때마다 새로)
        self.setGeometry(screen.geometry())

        self._init_editor_state()
        g = screen.geometry()
        self._scene = QGraphicsScene(self)
        self._scene.setSceneRect(QRectF(0, 0, g.width(), g.height()))
        self._view = _AnnotatorView(self._scene, self)
        self._view.setFrameShape(QFrame.Shape.NoFrame)
        self._view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self._view.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.MinimalViewportUpdate)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self._view)

        self._toolbar = QWidget(self)
        self._toolbar.setObjectName("toolbarhost")
        self._toolbar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        bar = QHBoxLayout(self._toolbar)
        bar.setContentsMargins(2, 0, 0, 0)
        bar.setSpacing(0)
        bar.addWidget(_ToolbarGrip(self._toolbar))
        bar.addLayout(self._build_toolbar())
        self.setStyleSheet(self._editor_stylesheet(_SURFACE2) + _CHROME_QSS + _LIVE_QSS)
        self._refresh_tool_icons()
        type(self)._instances.append(self)

    # ---- 배경(알파 1) -------------------------------------------------------
    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0, 1))

    def _hwnd(self) -> int:
        return int(self.winId())

    # ---- 켜기 · 상태 전환 ----------------------------------------------------
    def start(self):
        self.show()
        self._toolbar.adjustSize()
        # 작업 표시줄 위(사용 가능 영역 아래 끝) 기준 — 판서 창은 작업 표시줄까지 덮는다
        avail_bottom = self._screen.availableGeometry().bottom() - self._screen.geometry().top()
        self._toolbar.move((self.width() - self._toolbar.width()) // 2,
                           avail_bottom - self._toolbar.height() - _TOOLBAR_BOTTOM_GAP)
        self._toolbar.raise_()
        self.set_tool("pen")
        self._enter_draw()

    def toggle_mode(self):
        if self._drawing:
            self._enter_pass()
        else:
            self._enter_draw()

    def _enter_draw(self):
        fg = click_through.foreground_window()
        if fg and fg != self._hwnd():
            self._prev_fg = fg   # 앱 쓰기 동안 옮겨 간 창으로 돌려주게
        self._drawing = True
        click_through.set_click_through(self._hwnd(), False)
        self._toolbar.setVisible(True)
        self._update_text_opts_bar()
        _force_foreground(self._hwnd())
        click_through.claim_topmost(self._hwnd())
        QTimer.singleShot(60, lambda: click_through.claim_topmost(self._hwnd()))  # 다른 TOPMOST와의 레이스 재확인
        self.activateWindow()
        self._view.setFocus()
        self.mode_changed.emit(True)

    def _enter_pass(self):
        self._commit_text()
        self._hide_tool_popups()
        self._scene.clearSelection()
        self._drawing = False
        self._toolbar.setVisible(False)
        bar = getattr(self, "_text_opts_bar", None)
        if bar is not None:
            bar.setVisible(False)
        click_through.set_click_through(self._hwnd(), True)
        self._give_back_foreground()
        self.mode_changed.emit(False)

    def _give_back_foreground(self):
        if click_through.is_window(self._prev_fg) and self._prev_fg != self._hwnd():
            _force_foreground(self._prev_fg)

    def _commit_text(self):
        fi = self._scene.focusItem()
        if isinstance(fi, QGraphicsTextItem) and \
                fi.textInteractionFlags() != Qt.TextInteractionFlag.NoTextInteraction:
            fi.clearFocus()

    # ---- 화면 + 판서 한 장 ---------------------------------------------------
    def copy_screen(self):
        """툴바·미니패널을 숨기고 그 모니터를 찍는다 — grabWindow가 이 창의 그림까지 찍는다
        (0편 실측 「찍힘」). 찍은 뒤 툴바를 다시 보이고 app 쪽에 넘긴다(영역 캡처와 같은 저장)."""
        self._commit_text()
        self._scene.clearSelection()
        self._hide_tool_popups()
        bar = getattr(self, "_text_opts_bar", None)
        text_bar_was = bar is not None and bar.isVisible()
        self._toolbar.setVisible(False)
        if bar is not None:
            bar.setVisible(False)

        def _grab():
            pm = self._screen.grabWindow(0)
            if self._drawing:
                self._toolbar.setVisible(True)
                if text_bar_was:
                    bar.setVisible(True)
            self.screen_copy_requested.emit(pm, QRect(self._screen.geometry()))

        QTimer.singleShot(_GRAB_DELAY_MS, _grab)

    # ---- _EditorMixin / _AnnotatorView 호스트 요건 ---------------------------
    def is_edit_mode(self) -> bool:
        return self._drawing

    def toggle_edit_mode(self):
        pass   # Space — 판서에선 아무것도 안 함(전환은 단축키만)

    def _on_escape(self):
        if self._eyedrop_active:
            self._stop_eyedropper(False)
            return
        self.close()

    def _win_drag_start(self, _pt: QPoint):
        pass   # 판서 창은 움직이지 않는다

    def _win_drag_move(self, _pt: QPoint):
        pass

    def _win_drag_end(self):
        pass

    def _on_wheel_zoom(self, _delta: int):
        pass   # 확대 없음

    def copy_selection(self):
        self.copy_screen()

    def paste_selection(self):
        pass

    def _do_copy(self):
        self.copy_screen()

    def _do_export(self):
        self.copy_screen()   # 저장 버튼도 같은 일 — 캡처 폴더에 PNG가 남는다

    def closeEvent(self, event):
        self._hide_tool_popups()
        super().closeEvent(event)   # _EditorMixin: 스포이드 정리 · _instances에서 빼기
        self._give_back_foreground()
        self.closed.emit()
