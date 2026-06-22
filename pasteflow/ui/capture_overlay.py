"""마그네틱 영역 캡처 오버레이 (Snipaste식 요소 스냅).

3a 범위: **하이라이트 추적만**. 캡처·클릭 처리는 3b에서 추가한다.

흐름
----
1. CaptureOverlay.start() → 각 QScreen마다 _CaptureScreen(클릭-통과) 위젯 생성·표시
2. 매니저의 QTimer(~30ms)가:
   - GetCursorPos(물리 픽셀) → uia.rect_at → 커서 아래 요소 사각형(물리)
   - 커서가 있는 모니터의 DPR·원점으로 물리→논리 변환
   - 각 오버레이에 하이라이트(논리 가상좌표) 주입 → 해당 모니터 오버레이만 표시
   - GetAsyncKeyState(ESC) 폴링 → 취소

클릭-통과(WindowTransparentForInput)인 이유
------------------------------------------
오버레이가 화면을 덮으면 UIA ElementFromPoint가 오버레이 자신을 짚는다. 클릭-통과로
만들면 ElementFromPoint가 아래 실제 창을 짚는다. 대신 오버레이는 마우스·키 입력을
받지 못하므로 커서 추적·ESC를 매니저 QTimer가 폴링한다(3b에서 클릭은 마우스 훅으로).

좌표계
------
UIA·GetCursorPos = 물리 픽셀(DPI-aware 프로세스). Qt 위젯 geometry·페인트 = 논리 좌표.
변환: 커서가 있는 모니터의 물리 원점(Win32 GetMonitorInfo)과 DPR(QScreen)로
물리 가상좌표 → 논리 가상좌표를 계산한다.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes

from PyQt6.QtWidgets import QWidget, QApplication
from PyQt6.QtCore import Qt, QRect, QObject, pyqtSignal, QTimer
from PyQt6.QtGui import QPainter, QPixmap, QColor, QPen, QScreen, QCursor

from pasteflow.ui.theme import TEAL
from pasteflow import uia

_MASK_ALPHA = 100  # 어두운 마스크 알파
_BORDER_W = 2
_POLL_MS = 30      # 커서 추적 주기

_VK_ESCAPE = 0x1B
_MONITOR_DEFAULTTONEAREST = 2

_user32 = ctypes.windll.user32


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", _RECT),
                ("rcWork", _RECT), ("dwFlags", wintypes.DWORD)]


def _cursor_phys() -> tuple[int, int]:
    p = _POINT()
    _user32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


def _monitor_phys_origin(x: int, y: int) -> tuple[int, int]:
    """물리 점 (x,y)가 속한 모니터의 물리 좌상단 원점."""
    hmon = _user32.MonitorFromPoint(_POINT(x, y), _MONITOR_DEFAULTTONEAREST)
    mi = _MONITORINFO()
    mi.cbSize = ctypes.sizeof(_MONITORINFO)
    _user32.GetMonitorInfoW(hmon, ctypes.byref(mi))
    return mi.rcMonitor.left, mi.rcMonitor.top


class _CaptureScreen(QWidget):
    """단일 QScreen을 덮는 클릭-통과 오버레이. 얼린 스크린샷+딤+하이라이트만 그린다."""

    def __init__(self, screen: QScreen):
        super().__init__(None)
        self._screen = screen
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput  # 클릭-통과 (ElementFromPoint가 아래 창을 짚도록)
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setScreen(screen)
        self._screenshot: QPixmap | None = None
        self._hl_local: QRect | None = None  # 하이라이트(이 화면 로컬 논리좌표) 또는 None

    def prepare(self):
        sg = self._screen.geometry()
        self._screenshot = self._screen.grabWindow(0, 0, 0, sg.width(), sg.height())
        self.setGeometry(sg)
        self._hl_local = None

    def show_overlay(self):
        self.show()
        self.raise_()

    def set_highlight_global(self, gr: QRect | None):
        """논리 가상좌표 하이라이트를 이 화면 로컬로 변환해 저장(바뀌면 repaint)."""
        sg = self._screen.geometry()
        if gr is None:
            new = None
        else:
            inter = gr.intersected(sg)
            new = inter.translated(-sg.topLeft()) if not inter.isEmpty() else None
        if new != self._hl_local:
            self._hl_local = new
            self.update()

    def paintEvent(self, event):
        if self._screenshot is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        # 1) 배경 스크린샷
        p.drawPixmap(self.rect(), self._screenshot)
        # 2) 어두운 마스크
        p.fillRect(self.rect(), QColor(0, 0, 0, _MASK_ALPHA))
        # 3) 하이라이트: 마스크 없는 원본 복원 + teal 테두리
        hl = self._hl_local
        if hl is not None and not hl.isEmpty():
            dpr = self._screenshot.devicePixelRatio()
            src = QRect(round(hl.x() * dpr), round(hl.y() * dpr),
                        round(hl.width() * dpr), round(hl.height() * dpr))
            p.drawPixmap(hl, self._screenshot, src)
            pen = QPen(QColor(TEAL), _BORDER_W)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(hl.adjusted(0, 0, -1, -1))
        p.end()


class _Bridge(QObject):
    cancelled = pyqtSignal()
    # region_captured = pyqtSignal(QPixmap)  # 3b에서 추가


class CaptureOverlay:
    """모니터별 _CaptureScreen을 관리하고 커서 아래 요소를 하이라이트하는 매니저."""

    def __init__(self):
        self._bridge = _Bridge()
        self.cancelled = self._bridge.cancelled
        self._overlays: list[_CaptureScreen] = []
        self._timer = QTimer()
        self._timer.setInterval(_POLL_MS)
        self._timer.timeout.connect(self._tick)

    def start(self):
        self._close_all()
        for screen in QApplication.screens():
            ov = _CaptureScreen(screen)
            ov.prepare()
            self._overlays.append(ov)
        for ov in self._overlays:
            ov.show_overlay()
        self._timer.start()

    # ── internal ──────────────────────────────────────────────────────────────

    def _tick(self):
        # ESC → 취소
        if _user32.GetAsyncKeyState(_VK_ESCAPE) & 0x8000:
            self._cancel()
            return
        gr = self._element_rect_logical()
        for ov in self._overlays:
            ov.set_highlight_global(gr)

    def _element_rect_logical(self) -> QRect | None:
        """커서 아래 UIA 요소 사각형을 논리 가상좌표로 반환. 없으면 None."""
        px, py = _cursor_phys()
        rect_phys = uia.rect_at(px, py)
        if rect_phys is None:
            return None
        screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        if screen is None:
            return None
        dpr = screen.devicePixelRatio()
        mon_x, mon_y = _monitor_phys_origin(px, py)
        sg = screen.geometry()  # 논리
        lx = sg.left() + (rect_phys.left() - mon_x) / dpr
        ly = sg.top() + (rect_phys.top() - mon_y) / dpr
        lw = rect_phys.width() / dpr
        lh = rect_phys.height() / dpr
        return QRect(round(lx), round(ly), round(lw), round(lh))

    def _cancel(self):
        self._timer.stop()
        self._close_all()
        self.cancelled.emit()

    def _close_all(self):
        self._timer.stop()
        for ov in self._overlays:
            try:
                ov.close()
                ov.deleteLater()
            except Exception:
                pass
        self._overlays = []


# ── 3a 단독 검증 ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys

    app = QApplication(sys.argv)
    if not uia.is_available():
        print("UIA 사용 불가 — comtypes/UIAutomationCore 확인 필요", file=sys.stderr)
        sys.exit(1)

    overlay = CaptureOverlay()
    overlay.cancelled.connect(lambda: (print("[capture] 취소(ESC)"), app.quit()))
    overlay.start()
    print("마그네틱 하이라이트 테스트: 크롬 탭/북마크/작업표시줄에 마우스를 올려보세요. ESC로 종료.")
    sys.exit(app.exec())
