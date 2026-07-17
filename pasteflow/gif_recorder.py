"""GIF 녹화 — 화면 영역을 라이브로 연속 캡처해 애니메이션 GIF로 인코딩.

캡처 오버레이(capture_overlay)가 '얼린 스크린샷'에서 한 장을 잘라내는 것과 달리,
녹화는 영역을 정한 뒤 그 자리를 시간에 걸쳐 연속 grab한다. 그래서 오버레이의
'선택 전용'(select_only) 모드로 사각형만 받아, 여기서 라이브 캡처 루프를 돈다.

구조
----
- GifRecorder(매니저): QTimer로 fps마다 지정 영역을 grabWindow → QImage 프레임 버퍼에
  누적. 최대 길이 초과 / stop() / ESC 시 종료하고 finished(frames, interval_ms) emit.
- _RecordController: 녹화 중 화면에 떠 있는 ■ 정지 위젯(항상 위, 경과시간 표시).
- encode_gif(): 프레임 리스트를 Pillow로 GIF 저장(다운스케일·팔레트 양자화·optimize).

한계(MVP)
---------
- 커서 미포함(grabWindow는 마우스 커서를 안 담는다).
- 선택이 시작된 단일 모니터 한정(크로스 모니터 녹화 미지원).
- GIF는 클립보드에 애니메이션으로 못 올라가므로 파일 저장 + 경로 복사로 넘긴다(main).
"""
from __future__ import annotations

import ctypes

from PyQt6.QtWidgets import QWidget, QApplication, QLabel, QPushButton, QHBoxLayout
from PyQt6.QtCore import Qt, QRect, QObject, pyqtSignal, QTimer, QElapsedTimer
from PyQt6.QtGui import QImage

from pasteflow.ui.theme import PEACH, PEACH_HOVER, BASE, TEXT, SURFACE2

_VK_ESCAPE = 0x1B
_user32 = ctypes.windll.user32


# ── QImage → PIL 변환 / GIF 인코딩 ───────────────────────────────────────────────

def _qimage_to_pil(qimg: QImage):
    """QImage를 PIL RGBA Image로 변환. 스트라이드 패딩이 있으면 행 단위로 벗겨낸다."""
    from PIL import Image

    img = qimg.convertToFormat(QImage.Format.Format_RGBA8888)
    w, h = img.width(), img.height()
    bpl = img.bytesPerLine()
    ptr = img.constBits()
    ptr.setsize(img.sizeInBytes())
    raw = bytes(ptr)
    if bpl != w * 4:  # 정렬 패딩 제거(RGBA8888은 보통 패딩 없음이지만 방어적으로)
        raw = b"".join(raw[i * bpl:i * bpl + w * 4] for i in range(h))
    return Image.frombytes("RGBA", (w, h), raw)


def encode_gif(frames: list[QImage], path: str, interval_ms: int,
               max_width: int = 800, max_colors: int = 256) -> str:
    """QImage 프레임 리스트를 애니메이션 GIF로 저장하고 경로를 반환한다.

    - max_width 초과 시 비율 유지 다운스케일(용량 억제 — GIF는 크기가 쉽게 폭발한다).
    - 각 프레임을 팔레트(max_colors)로 양자화하고 optimize로 프레임 간 diff를 압축한다.
    """
    from PIL import Image

    if not frames:
        raise ValueError("빈 프레임 — 녹화된 내용이 없습니다.")

    pil_frames = []
    for qimg in frames:
        pil = _qimage_to_pil(qimg).convert("RGB")
        if max_width and pil.width > max_width:
            ratio = max_width / pil.width
            pil = pil.resize((max_width, max(1, round(pil.height * ratio))),
                             Image.Resampling.LANCZOS)
        pil_frames.append(pil.quantize(colors=max_colors, method=Image.Quantize.MEDIANCUT))

    pil_frames[0].save(
        path, save_all=True, append_images=pil_frames[1:],
        duration=max(20, interval_ms), loop=0, optimize=True, disposal=2)
    return path


# ── 녹화 중 정지 컨트롤러 ─────────────────────────────────────────────────────────

class _RecordController(QWidget):
    """녹화 중 화면에 떠 있는 작은 컨트롤 바 — ● REC · 경과시간 · ■ 정지.

    항상 위(Tool)·비활성 표시(WA_ShowWithoutActivating)라 녹화 대상 앱의 포커스를
    뺏지 않는다. 녹화 영역 '밖'(위 또는 아래)에 배치해 프레임에 안 잡히게 한다.
    """

    stop_requested = pyqtSignal()

    def __init__(self, region_global: QRect, screen):
        super().__init__(None)
        self._region = QRect(region_global)
        self._screen = screen
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setStyleSheet(
            f"QWidget{{background:{BASE};border:1px solid {PEACH};border-radius:6px;}}"
            f"QLabel{{color:{TEXT};background:transparent;border:none;font-size:12px;}}"
            f"QLabel#rec{{color:{PEACH};font-weight:bold;}}"
            f"QPushButton{{color:{TEXT};background:{SURFACE2};border:none;border-radius:4px;"
            f"padding:3px 10px;font-size:12px;}}"
            f"QPushButton:hover{{background:{PEACH_HOVER};color:{BASE};}}"
        )
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 6, 10, 6)
        lay.setSpacing(8)
        self._rec = QLabel("● REC")
        self._rec.setObjectName("rec")
        self._elapsed = QLabel("0.0초")
        stop_btn = QPushButton("■ 정지")
        stop_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        stop_btn.clicked.connect(self.stop_requested.emit)
        lay.addWidget(self._rec)
        lay.addWidget(self._elapsed)
        lay.addWidget(stop_btn)

        self._clock = QElapsedTimer()
        self._blink = True
        self._ui_timer = QTimer(self)
        self._ui_timer.setInterval(500)
        self._ui_timer.timeout.connect(self._tick_ui)

    def show_controller(self):
        self.adjustSize()
        self._place()
        self._clock.start()
        self.show()
        self.raise_()
        self._ui_timer.start()

    def _place(self):
        """녹화 영역 위(공간 없으면 아래)에 배치 — 프레임에 안 잡히도록 영역 밖."""
        sg = self._screen.geometry()
        w, h = self.width(), self.height()
        x = self._region.left()
        y = self._region.top() - h - 8
        if y < sg.top():                       # 위 공간 없으면 영역 아래로
            y = self._region.bottom() + 8
        x = max(sg.left(), min(x, sg.right() - w))
        y = max(sg.top(), min(y, sg.bottom() - h))
        self.move(x, y)

    def _tick_ui(self):
        secs = self._clock.elapsed() / 1000.0
        self._elapsed.setText(f"{secs:.1f}초")
        self._blink = not self._blink
        self._rec.setText("● REC" if self._blink else "  REC")

    def close(self):
        self._ui_timer.stop()
        super().close()


# ── 녹화 매니저 ───────────────────────────────────────────────────────────────────

class GifRecorder(QObject):
    """지정 영역을 fps마다 grab해 QImage 프레임을 모으는 녹화 매니저.

    finished(frames, interval_ms) — 정지/최대길이 도달 시(프레임 있음).
    cancelled() — ESC 취소 또는 프레임 0.
    """

    finished = pyqtSignal(list, int)   # (list[QImage], interval_ms)
    cancelled = pyqtSignal()

    def __init__(self, fps: int = 12, max_seconds: int = 15):
        super().__init__()
        self._fps = max(1, min(30, int(fps)))
        self._interval_ms = round(1000 / self._fps)
        self._max_frames = self._fps * max(1, int(max_seconds))
        self._timer = QTimer(self)
        self._timer.setInterval(self._interval_ms)
        self._timer.timeout.connect(self._tick)
        self._frames: list[QImage] = []
        self._screen = None
        self._local_rect: QRect | None = None
        self._controller: _RecordController | None = None
        self._active = False

    def is_active(self) -> bool:
        return self._active

    def start(self, rect: QRect):
        """rect(논리 전역)를 품는 모니터를 정하고 라이브 캡처를 시작한다."""
        screen = QApplication.screenAt(rect.center()) or QApplication.primaryScreen()
        self._screen = screen
        self._local_rect = rect.translated(-screen.geometry().topLeft())
        self._frames = []
        self._active = True
        self._controller = _RecordController(rect, screen)
        self._controller.stop_requested.connect(self.stop)
        self._controller.show_controller()
        self._timer.start()
        self._tick()  # 첫 프레임 즉시

    def _tick(self):
        if _user32.GetAsyncKeyState(_VK_ESCAPE) & 0x8000:
            self.cancel()
            return
        r = self._local_rect
        if r is None or self._screen is None:
            return
        pm = self._screen.grabWindow(0, r.x(), r.y(), r.width(), r.height())
        if pm is not None and not pm.isNull():
            self._frames.append(pm.toImage())
        if len(self._frames) >= self._max_frames:
            self.stop()

    def stop(self):
        if not self._active:
            return
        self._active = False
        self._timer.stop()
        self._teardown()
        frames, self._frames = self._frames, []
        if frames:
            self.finished.emit(frames, self._interval_ms)
        else:
            self.cancelled.emit()

    def cancel(self):
        if not self._active:
            return
        self._active = False
        self._timer.stop()
        self._teardown()
        self._frames = []
        self.cancelled.emit()

    def _teardown(self):
        if self._controller is not None:
            try:
                self._controller.stop_requested.disconnect()
            except Exception:
                pass
            self._controller.close()
            self._controller.deleteLater()
            self._controller = None


# ── 단독 검증 (비대화형: 화면 좌상단 고정 영역을 잠깐 녹화 → GIF 저장) ─────────────
if __name__ == "__main__":
    import sys
    import os

    app = QApplication(sys.argv)
    scr = QApplication.primaryScreen()
    g = scr.geometry()
    # 좌상단 400x240 영역을 1.5초(≈18프레임 @12fps) 녹화
    region = QRect(g.left() + 40, g.top() + 40, 400, 240)

    rec = GifRecorder(fps=12, max_seconds=2)
    out = os.path.join(os.path.dirname(__file__), "_gif_selftest.gif")

    def _done(frames, interval):
        print(f"[gif] 프레임 {len(frames)}장, interval={interval}ms → 인코딩")
        path = encode_gif(frames, out, interval)
        sz = os.path.getsize(path)
        from PIL import Image
        im = Image.open(path)
        n = getattr(im, "n_frames", 1)
        print(f"[gif] 저장됨 {path} — {sz/1024:.1f}KB, {n}프레임, {im.size} "
              f"(애니메이션={'예' if n > 1 else '아니오'})")
        app.quit()

    def _cancel():
        print("[gif] 취소/빈 프레임")
        app.quit()

    rec.finished.connect(_done)
    rec.cancelled.connect(_cancel)
    # 컨트롤러 없이 순수 캡처+인코딩만 보려면 아래처럼 직접 start(컨트롤러는 뜨지만 무해)
    rec.start(region)
    # 2초 후 자동 정지(최대길이로도 멈추지만 안전망)
    QTimer.singleShot(2000, rec.stop)
    sys.exit(app.exec())
