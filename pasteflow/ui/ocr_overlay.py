"""OCR 영역 선택 오버레이 — 전체 화면 캡처 후 드래그로 영역 선택

흐름
----
1. start() 호출 → 가상 데스크톱 전체 캡처 → 오버레이 표시
2. 사용자 드래그 → 반투명 마스크 위에 선택 영역 강조
3. 마우스 업 → region_captured(QPixmap) emit
4. ESC 또는 5px 미만 클릭 → cancelled() emit
"""
from __future__ import annotations

import ctypes

from PyQt6.QtWidgets import QWidget, QApplication
from PyQt6.QtCore import Qt, QRect, QPoint, pyqtSignal
from PyQt6.QtGui import QPainter, QPixmap, QImage, QColor, QPen, QFont

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
        self._hint_visible = True  # 드래그 시작 전까지 안내 텍스트 표시
        self._debug_printed = False

    # ── public ──────────────────────────────────────────────────────────────

    def start(self):
        """가상 데스크톱을 캡처한 뒤 오버레이를 표시한다.

        캡처를 먼저 수행해야 오버레이가 화면을 덮기 전 스크린샷을 확보한다.
        각 QScreen별 native 해상도로 캡처 후 가상 데스크톱 좌표 기준으로 합성한다
        (DPI 배율이 다른 다중 모니터 환경에서 좌표 어긋남 방지).
        """
        # 이전 상태 초기화 및 오버레이 숨기기 (재사용 시 이전 드로잉 제거)
        self._start = None
        self._end = None
        self._dragging = False
        self._hint_visible = True
        self._debug_printed = False
        self.hide()
        QApplication.processEvents()

        # 가상 데스크톱 전체 범위 (논리 좌표)
        vg = QApplication.primaryScreen().virtualGeometry()
        screens = QApplication.screens()

        print(f"[OCR-DBG] virtualGeometry: x={vg.x()} y={vg.y()} w={vg.width()} h={vg.height()}")

        # QImage 기반 합성 — QImage는 DPR 개념 없이 명시적 물리 픽셀로만 동작해
        # Qt 버전·플랫폼별 QPixmap DPR 처리 차이를 완전히 우회한다.
        canvas = QImage(vg.width(), vg.height(), QImage.Format.Format_RGB32)
        canvas.fill(Qt.GlobalColor.black)

        painter = QPainter(canvas)
        for screen in screens:
            sg = screen.geometry()
            dpr = screen.devicePixelRatio()
            pm = screen.grabWindow(0)
            img = pm.toImage()  # 명시적 물리 픽셀 추출 (DPR 메타데이터 제거)
            print(f"[OCR-DBG] screen '{screen.name()}': geo={sg.x()},{sg.y()} {sg.width()}x{sg.height()} dpr={dpr}")
            print(f"[OCR-DBG]   grabWindow → pm={pm.width()}x{pm.height()} pm.dpr={pm.devicePixelRatio()}")
            print(f"[OCR-DBG]   toImage   → img={img.width()}x{img.height()}")
            # 물리 픽셀 크기가 논리 크기와 다를 경우 (DPR>1 화면) 리샘플
            if img.width() != sg.width() or img.height() != sg.height():
                img = img.scaled(
                    sg.width(), sg.height(),
                    Qt.AspectRatioMode.IgnoreAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                print(f"[OCR-DBG]   scaled    → img={img.width()}x{img.height()}")
            dest = QRect(sg.x() - vg.x(), sg.y() - vg.y(), sg.width(), sg.height())
            print(f"[OCR-DBG]   drawImage at dest={dest.x()},{dest.y()} {dest.width()}x{dest.height()}")
            painter.drawImage(dest, img)
        painter.end()

        print(f"[OCR-DBG] canvas={canvas.width()}x{canvas.height()}")

        combined = QPixmap.fromImage(canvas)
        combined.setDevicePixelRatio(1.0)
        self._screenshot = combined
        self.setGeometry(vg)
        self.show()
        self.raise_()
        self.activateWindow()
        # activateWindow()만으로는 포그라운드 잠금에 막혀 포커스를 못 받을 수 있음
        # AllowSetForegroundWindow 허용 후 SetForegroundWindow 직접 호출
        ctypes.windll.user32.SetForegroundWindow(int(self.winId()))
        self.setFocus()

    # ── painting ─────────────────────────────────────────────────────────────

    def paintEvent(self, event):
        if self._screenshot is None:
            return

        if not self._debug_printed:
            self._debug_printed = True
            print(f"[OCR-DBG] paintEvent: widget={self.width()}x{self.height()} rect={self.rect()}")
            print(f"[OCR-DBG] paintEvent: screenshot={self._screenshot.width()}x{self._screenshot.height()} dpr={self._screenshot.devicePixelRatio()}")

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

        # 안내 텍스트 — 드래그 전에만 표시
        if self._hint_visible:
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

            # 배경 박스 (반투명 검정)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, 160))
            p.drawRoundedRect(rx, ry, rect_w, rect_h, 6, 6)

            # 흰색 텍스트
            p.setPen(QColor(255, 255, 255))
            p.drawText(rx + pad_x, ry + pad_y + fm.ascent(), hint)

        p.end()

    # ── mouse ─────────────────────────────────────────────────────────────────

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            # 우클릭 → 취소 (Phase 3-2)
            self.close()
            self.cancelled.emit()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._hint_visible = False  # 드래그 시작 → 안내 텍스트 숨김
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
        r = QRect(self._start, self._end).normalized()
        # 화면 밖으로 나가지 않도록 위젯 경계 안으로 클램프
        return r.intersected(self.rect())


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
