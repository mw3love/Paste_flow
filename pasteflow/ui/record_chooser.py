"""녹화 방식 선택 바 — 영역을 고른 직후 [GIF] [영상] 중 하나를 고른다.

GIF·영상 녹화 단축키를 하나로 합치면서(2026-09-25, 사용자 요청) 생겼다. 두 녹화는
영역 선택까지 완전히 같으므로(capture_overlay의 select_only), 선택이 끝난 그 자리에
이 바를 띄워 방식만 고르게 한다.

- 클릭: `GIF` / `영상` 버튼, `✕`=취소
- 키보드: `G`=GIF, `V`=영상, `Enter`=지난번에 고른 쪽(코랄로 강조), `ESC`=취소

바는 녹화 영역 '밖'(아래, 공간 없으면 위, 둘 다 없으면 영역 안쪽 아래)에 띄운다 —
어차피 고르는 즉시 닫히고 녹화는 그 뒤 150ms에 시작하지만, 영역을 가리지 않아야
사용자가 방금 고른 영역을 보며 결정할 수 있다.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes

from PyQt6.QtWidgets import QWidget, QApplication, QPushButton, QHBoxLayout
from PyQt6.QtCore import Qt, QRect, pyqtSignal

from pasteflow.ui.theme import PEACH, PEACH_HOVER, BASE, TEXT, SURFACE2

# 전용 WinDLL 인스턴스 — 공유 ctypes.windll.user32에 argtypes를 걸면 다른 모듈의 설정과
# 서로 덮어쓴다(uia.py의 교훈).
_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_user32.GetForegroundWindow.restype = wintypes.HWND
_user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_user32.GetWindowThreadProcessId.restype = wintypes.DWORD
_user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
_user32.SetForegroundWindow.argtypes = [wintypes.HWND]

_GAP = 8  # 영역과 바 사이 간격(px)


def _force_foreground(hwnd: int):
    """포그라운드 잠금을 AttachThreadInput으로 우회해 키 입력(G/V/Enter/ESC)을 받게 한다.

    영역 선택 오버레이는 키보드 포커스를 원래 앱에 둔 채(WA_ShowWithoutActivating) 떠
    있었으므로, 그냥 activateWindow()만 하면 Windows가 포그라운드 전환을 막을 수 있다.
    """
    try:
        fg = _user32.GetForegroundWindow()
        fg_tid = _user32.GetWindowThreadProcessId(fg, None) if fg else 0
        my_tid = _kernel32.GetCurrentThreadId()
        attached = bool(fg_tid) and fg_tid != my_tid and _user32.AttachThreadInput(my_tid, fg_tid, True)
        _user32.SetForegroundWindow(hwnd)
        if attached:
            _user32.AttachThreadInput(my_tid, fg_tid, False)
    except Exception:
        pass


class RecordModeChooser(QWidget):
    """[GIF] [영상] [✕] 선택 바. chosen("gif"|"video") 또는 cancelled()를 한 번만 emit한다."""

    chosen = pyqtSignal(str)
    cancelled = pyqtSignal()

    def __init__(self, region_global: QRect, default_mode: str = "gif"):
        super().__init__(None)
        self._region = QRect(region_global)
        self._default = default_mode if default_mode in ("gif", "video") else "gif"
        self._done = False
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setStyleSheet(
            f"QWidget{{background:{BASE};border:1px solid {SURFACE2};border-radius:6px;}}"
            f"QPushButton{{color:{TEXT};background:{SURFACE2};border:none;border-radius:4px;"
            f"padding:5px 14px;font-size:12px;}}"
            f"QPushButton#default{{background:{PEACH};color:{BASE};font-weight:bold;}}"
            f"QPushButton:hover{{background:{PEACH_HOVER};color:{BASE};}}"
            f"QPushButton#close{{padding:5px 8px;background:transparent;}}"
        )
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(6)
        for mode, text in (("gif", "GIF  (G)"), ("video", "영상  (V)")):
            btn = QPushButton(text)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # 키는 바 자신이 받는다
            if mode == self._default:
                btn.setObjectName("default")
                btn.setToolTip("Enter")
            btn.clicked.connect(lambda _=False, m=mode: self._choose(m))
            lay.addWidget(btn)
        close_btn = QPushButton("✕")
        close_btn.setObjectName("close")
        close_btn.setToolTip("취소 (ESC)")
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        close_btn.clicked.connect(self._cancel)
        lay.addWidget(close_btn)

    def show_chooser(self):
        self.adjustSize()
        self._place()
        self.show()
        self.raise_()
        _force_foreground(int(self.winId()))
        self.activateWindow()
        self.setFocus()

    def _place(self):
        """영역 아래 가운데(공간 없으면 위, 둘 다 없으면 영역 안쪽 아래)에 배치."""
        screen = QApplication.screenAt(self._region.center()) or QApplication.primaryScreen()
        sg = screen.geometry()
        w, h = self.width(), self.height()
        x = self._region.center().x() - w // 2
        y = self._region.bottom() + _GAP
        if y + h > sg.bottom():
            y = self._region.top() - h - _GAP
            if y < sg.top():
                y = self._region.bottom() - h - _GAP
        x = max(sg.left(), min(x, sg.right() - w))
        y = max(sg.top(), min(y, sg.bottom() - h))
        self.move(x, y)

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key.Key_G:
            self._choose("gif")
        elif key == Qt.Key.Key_V:
            self._choose("video")
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._choose(self._default)
        elif key == Qt.Key.Key_Escape:
            self._cancel()
        else:
            super().keyPressEvent(event)

    def _choose(self, mode: str):
        if self._done:
            return
        self._done = True
        self.close()
        self.chosen.emit(mode)

    def _cancel(self):
        if self._done:
            return
        self._done = True
        self.close()
        self.cancelled.emit()
