"""OCR 영역 선택 오버레이 — 모니터별 분리 위젯으로 영역 선택

흐름
----
1. OcrOverlay.start() → 각 QScreen마다 _ScreenOverlay 위젯 생성 후 표시
2. 사용자 드래그 → 해당 모니터의 오버레이 위에 선택 영역 강조
3. 마우스 업 → 매니저가 region_captured(QPixmap) emit
4. ESC 또는 우클릭 → 모든 오버레이 닫고 cancelled() emit

다중 DPI 모니터 환경 대응
------------------------
가상 데스크톱 전체를 단일 위젯으로 덮으면 Qt 백킹 스토어 DPR이 하나로 고정되어,
DPR이 다른 모니터에 진입할 때 좌표·크기 변환이 어긋난다(고DPI 노트북에서 화면이
좌상단 일부로 축소되는 증상). 각 모니터마다 별도 위젯을 두면 Qt가 모니터별
DPR을 독립적으로 처리하므로 문제 자체가 발생하지 않는다.
"""
from __future__ import annotations

import ctypes

from PyQt6.QtWidgets import QWidget, QApplication
from PyQt6.QtCore import Qt, QRect, QPoint, QObject, pyqtSignal
from PyQt6.QtGui import QPainter, QPixmap, QColor, QPen, QFont, QScreen

from pasteflow.ui.theme import TEAL

_MIN_SEL = 5       # 유효 선택 최소 크기 (논리 px)
_MASK_ALPHA = 100  # 어두운 마스크 알파 (0=투명, 255=불투명)
_BORDER_W = 2      # 선택 테두리 두께


class _ScreenOverlay(QWidget):
    """단일 QScreen을 덮는 오버레이.

    자기 화면만 캡처해 표시하고, 드래그 영역을 매니저에 보고한다.
    """

    drag_started = pyqtSignal(object)        # (self) — 드래그 시작 알림
    drag_finished = pyqtSignal(object, QRect)  # (self, sel_rect) — 마우스 업 시
    cancel_requested = pyqtSignal()          # ESC / 우클릭 / 5px 미만

    def __init__(self, screen: QScreen):
        super().__init__(None)
        self._screen = screen
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setCursor(Qt.CursorShape.CrossCursor)
        # 이 위젯을 해당 모니터에 명시적으로 바인딩 (Qt에게 DPR 힌트)
        self.setScreen(screen)

        self._screenshot: QPixmap | None = None
        self._start: QPoint | None = None
        self._end: QPoint | None = None
        self._dragging = False
        self._hint_visible = True
        self._active = True  # 다른 모니터에서 드래그 시작되면 False (마스크만 표시)

    # ── public ──────────────────────────────────────────────────────────────

    def prepare(self):
        """해당 모니터를 캡처하고 위젯을 화면 영역에 맞춰 위치시킨다.

        QScreen.grabWindow(0)는 해당 모니터의 native 픽셀을 반환하며,
        DPR은 grabWindow가 자동으로 설정한다(보통 screen.devicePixelRatio()와 동일).
        그대로 사용하면 paintEvent에서 논리 좌표로 정확히 그려진다.
        """
        self._start = None
        self._end = None
        self._dragging = False
        self._hint_visible = True
        self._active = True

        sg = self._screen.geometry()
        # 해당 모니터 영역만 캡처 (가상 데스크톱 좌표 기준이 아닌 0,0,w,h)
        self._screenshot = self._screen.grabWindow(0, 0, 0, sg.width(), sg.height())

        self.setGeometry(sg)

    def show_overlay(self):
        self.show()
        self.raise_()

    def deactivate(self):
        """다른 모니터에서 드래그가 시작됐을 때 이 오버레이는 입력만 차단."""
        self._active = False
        self._dragging = False
        self._start = None
        self._end = None
        self._hint_visible = False
        self.update()

    # ── painting ─────────────────────────────────────────────────────────────

    def paintEvent(self, event):
        if self._screenshot is None:
            return

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        # 1) 배경: 캡처된 스크린샷 전체 (논리 좌표 = self.rect())
        p.drawPixmap(self.rect(), self._screenshot)

        # 2) 전체에 반투명 어두운 마스크
        p.fillRect(self.rect(), QColor(0, 0, 0, _MASK_ALPHA))

        sel = self._sel_rect()
        if not sel.isEmpty():
            dpr = self._screenshot.devicePixelRatio()

            # 3) 선택 영역: 마스크 없는 원본 스크린샷 복원
            src = QRect(
                round(sel.x() * dpr),
                round(sel.y() * dpr),
                round(sel.width() * dpr),
                round(sel.height() * dpr),
            )
            p.drawPixmap(sel, self._screenshot, src)

            # 4) Catppuccin teal 2px 테두리
            pen = QPen(QColor(TEAL), _BORDER_W)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(sel.adjusted(0, 0, -1, -1))

        # 안내 텍스트 — 드래그 전, 활성 상태일 때만
        if self._hint_visible and self._active:
            hint = "드래그하여 영역 선택  ·  ESC 또는 우클릭으로 취소"
            font = QFont()
            font.setPointSize(13)
            font.setBold(True)
            p.setFont(font)

            fm = p.fontMetrics()
            text_w = fm.horizontalAdvance(hint)
            text_h = fm.height()
            pad_x, pad_y = 16, 8
            rect_w = text_w + pad_x * 2
            rect_h = text_h + pad_y * 2
            rx = (self.width() - rect_w) // 2
            ry = 32

            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, 160))
            p.drawRoundedRect(rx, ry, rect_w, rect_h, 6, 6)

            p.setPen(QColor(255, 255, 255))
            p.drawText(rx + pad_x, ry + pad_y + fm.ascent(), hint)

        p.end()

    # ── mouse ─────────────────────────────────────────────────────────────────

    def mousePressEvent(self, event):
        if not self._active:
            return
        if event.button() == Qt.MouseButton.RightButton:
            self.cancel_requested.emit()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._hint_visible = False
            self._start = event.position().toPoint()
            self._end = self._start
            self._dragging = True
            self.drag_started.emit(self)
            self.update()

    def mouseMoveEvent(self, event):
        if self._dragging:
            self._end = event.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or not self._dragging:
            return
        self._dragging = False
        self._end = event.position().toPoint()
        sel = self._sel_rect()

        if sel.width() < _MIN_SEL or sel.height() < _MIN_SEL:
            self.cancel_requested.emit()
            return

        self.drag_finished.emit(self, sel)

    # ── keyboard ──────────────────────────────────────────────────────────────

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.cancel_requested.emit()
        else:
            super().keyPressEvent(event)

    # ── helper ───────────────────────────────────────────────────────────────

    def _sel_rect(self) -> QRect:
        if self._start is None or self._end is None:
            return QRect()
        return QRect(self._start, self._end).normalized().intersected(self.rect())

    def crop_selection(self, sel: QRect) -> QPixmap:
        """선택 영역을 물리 픽셀로 잘라낸 픽맵 반환 (DPR 메타데이터 포함)."""
        dpr = self._screenshot.devicePixelRatio()
        src = QRect(
            int(sel.x() * dpr),
            int(sel.y() * dpr),
            int(sel.width() * dpr),
            int(sel.height() * dpr),
        )
        cropped = self._screenshot.copy(src)
        cropped.setDevicePixelRatio(dpr)
        return cropped


class _OcrOverlayBridge(QObject):
    """OcrOverlay 매니저의 외부 노출 시그널 컨테이너."""

    region_captured = pyqtSignal(QPixmap)
    cancelled = pyqtSignal()


class OcrOverlay:
    """모니터별 _ScreenOverlay를 관리하는 매니저(QWidget 아님)."""

    def __init__(self):
        self._bridge = _OcrOverlayBridge()
        self.region_captured = self._bridge.region_captured
        self.cancelled = self._bridge.cancelled

        self._overlays: list[_ScreenOverlay] = []

    # ── public ──────────────────────────────────────────────────────────────

    def start(self):
        """각 모니터에 오버레이를 생성하고 표시한다."""
        # 이전 오버레이 정리 (재사용 시)
        self._close_all()

        screens = QApplication.screens()
        # 첫 번째 활성 모니터: 마우스 커서가 있는 화면 (포커스 우선)
        cursor_pos = QApplication.instance().primaryScreen().geometry().topLeft()
        try:
            from PyQt6.QtGui import QCursor
            cursor_pos = QCursor.pos()
        except Exception:
            pass
        focus_screen = QApplication.screenAt(cursor_pos) or QApplication.primaryScreen()

        for screen in screens:
            ov = _ScreenOverlay(screen)
            ov.drag_started.connect(self._on_drag_started)
            ov.drag_finished.connect(self._on_drag_finished)
            ov.cancel_requested.connect(self._on_cancel)
            ov.prepare()
            self._overlays.append(ov)

        # 모두 표시
        for ov in self._overlays:
            ov.show_overlay()

        # 커서 있는 모니터의 오버레이에 포그라운드 포커스 부여
        target = next((o for o in self._overlays if o._screen == focus_screen), None)
        if target is None and self._overlays:
            target = self._overlays[0]
        if target is not None:
            target.activateWindow()
            try:
                ctypes.windll.user32.SetForegroundWindow(int(target.winId()))
            except Exception:
                pass
            target.setFocus()

    # ── internal ────────────────────────────────────────────────────────────

    def _on_drag_started(self, source: _ScreenOverlay):
        """한 모니터에서 드래그 시작 → 다른 모니터는 비활성화."""
        for ov in self._overlays:
            if ov is not source:
                ov.deactivate()

    def _on_drag_finished(self, source: _ScreenOverlay, sel: QRect):
        cropped = source.crop_selection(sel)
        self._close_all()
        self.region_captured.emit(cropped)

    def _on_cancel(self):
        self._close_all()
        self.cancelled.emit()

    def _close_all(self):
        for ov in self._overlays:
            try:
                ov.close()
                ov.deleteLater()
            except Exception:
                pass
        self._overlays = []


# ── 단독 실행 검증 ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    import os

    app = QApplication(sys.argv)

    overlay = OcrOverlay()

    def _on_captured(pixmap: QPixmap):
        path = os.path.join(os.path.expanduser("~"), "ocr_test_capture.png")
        ok = pixmap.save(path)
        if ok:
            print(f"[OCR] 저장됨: {path}  ({pixmap.width()}x{pixmap.height()} px, DPR={pixmap.devicePixelRatio()})")
        else:
            print(f"[OCR] 저장 실패: {path}", file=sys.stderr)
        app.quit()

    def _on_cancelled():
        print("[OCR] 선택 취소됨")
        app.quit()

    overlay.region_captured.connect(_on_captured)
    overlay.cancelled.connect(_on_cancelled)
    overlay.start()

    sys.exit(app.exec())
