"""OCR 영역 선택 오버레이 — 전체 화면 캡처 후 드래그로 영역 선택

흐름
----
1. start() 호출 → 가상 데스크톱 전체 캡처 → 오버레이 표시
2. 사용자 드래그 → 반투명 마스크 위에 선택 영역 강조
3. 마우스 업 → region_captured(QPixmap) emit
4. ESC 또는 5px 미만 클릭 → cancelled() emit
"""
from __future__ import annotations

from PyQt6.QtWidgets import QWidget, QApplication
from PyQt6.QtCore import Qt, QRect, QPoint, pyqtSignal
from PyQt6.QtGui import QPainter, QPixmap, QColor, QPen

from pasteflow.ui.theme import TEAL

_MIN_SEL = 5       # 유효 선택 최소 크기 (논리 px)
_MASK_ALPHA = 100  # 어두운 마스크 알파 (0=투명, 255=불투명)
_BORDER_W = 2      # 선택 테두리 두께


class OcrOverlay(QWidget):
    """전체 화면을 덮는 반투명 영역 선택 오버레이."""

    region_captured = pyqtSignal(QPixmap)  # 선택 완료 → 잘라낸 영역 픽맵
    cancelled = pyqtSignal()               # ESC 또는 5px 미만 클릭

    def __init__(self):
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setCursor(Qt.CursorShape.CrossCursor)

        self._screenshot: QPixmap | None = None
        self._start: QPoint | None = None
        self._end: QPoint | None = None
        self._dragging = False

    # ── public ──────────────────────────────────────────────────────────────

    def start(self):
        """가상 데스크톱을 캡처한 뒤 오버레이를 표시한다.

        캡처를 먼저 수행해야 오버레이가 화면을 덮기 전 스크린샷을 확보한다.
        """
        screen = QApplication.primaryScreen()
        vg = screen.virtualGeometry()

        # 이전 상태 초기화 및 오버레이 숨기기 (재사용 시 이전 드로잉 제거)
        self._start = None
        self._end = None
        self._dragging = False
        self.hide()
        QApplication.processEvents()

        # 전체 가상 데스크톱 캡처 (vg 원점이 음수일 수 있음 — 다중 모니터 좌측 배치)
        self._screenshot = screen.grabWindow(
            0, vg.x(), vg.y(), vg.width(), vg.height()
        )

        self.setGeometry(vg)
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus()

    # ── painting ─────────────────────────────────────────────────────────────

    def paintEvent(self, event):
        if self._screenshot is None:
            return

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        # 1) 배경: 캡처된 스크린샷 전체
        p.drawPixmap(self.rect(), self._screenshot)

        # 2) 전체에 반투명 어두운 마스크
        p.fillRect(self.rect(), QColor(0, 0, 0, _MASK_ALPHA))

        sel = self._sel_rect()
        if not sel.isEmpty():
            dpr = self._screenshot.devicePixelRatio()

            # 3) 선택 영역: 마스크 없는 원본 스크린샷 복원
            src = QRect(
                int(sel.x() * dpr),
                int(sel.y() * dpr),
                int(sel.width() * dpr),
                int(sel.height() * dpr),
            )
            p.drawPixmap(sel, self._screenshot, src)

            # 4) Catppuccin teal 2px 테두리
            pen = QPen(QColor(TEAL), _BORDER_W)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(sel.adjusted(0, 0, -1, -1))

        p.end()

    # ── mouse ─────────────────────────────────────────────────────────────────

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._start = event.position().toPoint()
            self._end = self._start
            self._dragging = True
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
            self.close()
            self.cancelled.emit()
            return

        # 물리 픽셀 기준으로 영역 잘라내기 (DPI-aware)
        dpr = self._screenshot.devicePixelRatio()
        src = QRect(
            int(sel.x() * dpr),
            int(sel.y() * dpr),
            int(sel.width() * dpr),
            int(sel.height() * dpr),
        )
        cropped = self._screenshot.copy(src)
        cropped.setDevicePixelRatio(dpr)
        self.close()
        self.region_captured.emit(cropped)

    # ── keyboard ──────────────────────────────────────────────────────────────

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            self.cancelled.emit()
        else:
            super().keyPressEvent(event)

    # ── helper ───────────────────────────────────────────────────────────────

    def _sel_rect(self) -> QRect:
        if self._start is None or self._end is None:
            return QRect()
        return QRect(self._start, self._end).normalized()


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
            print(f"[OCR] 저장됨: {path}  ({pixmap.width()}×{pixmap.height()} px, DPR={pixmap.devicePixelRatio()})")
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
