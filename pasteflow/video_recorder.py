"""영상(MP4) 녹화 — GIF 녹화와 같은 영역선택 흐름을 공유하되, 프레임을 메모리에 모으지
않고 곧장 cv2.VideoWriter에 기록한다.

GIF와의 차이
------------
- `gif_recorder.GifRecorder`는 프레임을 리스트에 다 모았다가 끝에 한 번에 Pillow로
  인코딩한다(그래서 max_seconds로 프레임 수 상한을 강제해 메모리 폭주를 막는다).
- 이쪽은 프레임을 받는 즉시 `cv2.VideoWriter.write()`로 디스크에 흘려보낸다 — 메모리에
  쌓이는 게 없어 GIF보다 훨씬 길게(수 분) 녹화해도 안전하고, 파일 용량도 훨씬 작다.
- `_RecordController`(정지 버튼 위젯)와 `composite_cursor`(커서 합성)는 gif_recorder.py
  것을 그대로 재사용한다 — 같은 UX·같은 커서 합성 로직을 두 벌 두지 않기 위함.

새 의존성: opencv-python(cv2.VideoWriter, mp4v 코덱) — Pillow는 GIF 등 이미지 포맷만
다루고 비디오 컨테이너/코덱 인코딩을 지원하지 않아 GIF 인코딩 경로를 재사용할 수 없었다.
mp4v는 별도 ffmpeg 바이너리 없이 오프라인 동작하는 대신 h264보다 압축률·재생 호환성이
낮다 — 실사용에서 재생 문제가 나오면 그때 imageio-ffmpeg(ffmpeg 바이너리 번들) 전환을
검토한다(2026-08-03).
"""
from __future__ import annotations

import ctypes
import os

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QRect, QObject, pyqtSignal, QTimer

from pasteflow.gif_recorder import _RecordController, composite_cursor

_VK_ESCAPE = 0x1B
_user32 = ctypes.windll.user32


def _qimage_to_bgr_array(qimg):
    """QImage(top-down)를 cv2.VideoWriter가 받는 numpy BGR array로 변환.

    QImage.Format_RGB32는 메모리상 바이트 순서가 B,G,R,X(리틀 엔디언)라 OpenCV의 기본
    채널 순서(BGR)와 그대로 맞는다 — 별도 채널 스왑 불필요, 알파/패딩 바이트만 제거.
    """
    import numpy as np
    from PyQt6.QtGui import QImage

    img = qimg.convertToFormat(QImage.Format.Format_RGB32)
    w, h = img.width(), img.height()
    bpl = img.bytesPerLine()
    ptr = img.constBits()
    ptr.setsize(img.sizeInBytes())
    arr = np.frombuffer(bytes(ptr), dtype=np.uint8).reshape((h, bpl // 4, 4))
    return np.ascontiguousarray(arr[:, :w, :3])


def extract_first_frame_png(path: str) -> bytes | None:
    """저장된 mp4의 첫 프레임을 PNG bytes로 반환 — 토스트 썸네일용(실패 시 None).

    GIF는 QPixmap이 파일을 직접 디코딩해 썸네일을 그리지만 mp4는 Qt가 못 읽으므로,
    이미 녹화에 쓰던 cv2로 첫 프레임만 다시 읽어 PNG로 인코딩한다.
    """
    import cv2

    cap = cv2.VideoCapture(path)
    try:
        ok, frame = cap.read()
    finally:
        cap.release()
    if not ok or frame is None:
        return None
    ok2, buf = cv2.imencode(".png", frame)
    if not ok2:
        return None
    return buf.tobytes()


class VideoRecorder(QObject):
    """지정 영역을 fps마다 grab해 곧장 mp4 파일에 기록하는 녹화 매니저.

    finished(path) — 정지/최대길이 도달 시(프레임 있음, 파일이 실제로 만들어짐).
    cancelled() — ESC 취소 또는 프레임 0(빈 파일은 지운다).
    """

    finished = pyqtSignal(str)
    cancelled = pyqtSignal()

    def __init__(self, fps: int = 15, max_seconds: int = 600, show_cursor: bool = True):
        super().__init__()
        self._fps = max(1, min(30, int(fps)))
        self._interval_ms = round(1000 / self._fps)
        self._max_frames = self._fps * max(1, int(max_seconds))
        self._show_cursor = show_cursor
        self._timer = QTimer(self)
        self._timer.setInterval(self._interval_ms)
        self._timer.timeout.connect(self._tick)
        self._writer = None
        self._out_size = (0, 0)
        self._path: str | None = None
        self._frame_count = 0
        self._screen = None
        self._local_rect: QRect | None = None
        self._controller: _RecordController | None = None
        self._active = False

    def is_active(self) -> bool:
        return self._active

    def start(self, rect: QRect, path: str):
        """rect(논리 전역)를 품는 모니터를 정하고 path에 라이브 녹화를 시작한다."""
        import cv2

        screen = QApplication.screenAt(rect.center()) or QApplication.primaryScreen()
        self._screen = screen
        self._local_rect = rect.translated(-screen.geometry().topLeft())
        dpr = screen.devicePixelRatio()
        w = max(2, round(rect.width() * dpr))
        h = max(2, round(rect.height() * dpr))
        w -= w % 2  # 일부 코덱이 짝수 폭/높이를 요구
        h -= h % 2
        self._out_size = (w, h)
        self._path = path
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        self._writer = cv2.VideoWriter(path, fourcc, float(self._fps), (w, h))
        if not self._writer.isOpened():
            # cv2.VideoWriter는 코덱을 못 열어도 예외를 던지지 않고 조용히 무효 상태가
            # 된다 — 열림 여부를 직접 확인해 호출자가 인지할 수 있게 명시적으로 알린다.
            self._writer.release()
            self._writer = None
            raise RuntimeError("영상 파일을 열 수 없습니다(코덱/경로 확인 필요)")
        self._frame_count = 0
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
        if r is None or self._screen is None or self._writer is None:
            return
        pm = self._screen.grabWindow(0, r.x(), r.y(), r.width(), r.height())
        if pm is not None and not pm.isNull():
            img = pm.toImage()
            if self._show_cursor:
                img = composite_cursor(img, self._screen, r)
            arr = _qimage_to_bgr_array(img)
            w, h = self._out_size
            if arr.shape[1] != w or arr.shape[0] != h:
                import cv2
                arr = cv2.resize(arr, (w, h))
            self._writer.write(arr)
            self._frame_count += 1
        if self._frame_count >= self._max_frames:
            self.stop()

    def stop(self):
        if not self._active:
            return
        self._active = False
        self._timer.stop()
        self._teardown()
        self._close_writer()
        if self._frame_count > 0:
            self.finished.emit(self._path)
        else:
            self._discard_empty_file()
            self.cancelled.emit()

    def cancel(self):
        if not self._active:
            return
        self._active = False
        self._timer.stop()
        self._teardown()
        self._close_writer()
        self._discard_empty_file()
        self.cancelled.emit()

    def _close_writer(self):
        if self._writer is not None:
            self._writer.release()
            self._writer = None

    def _discard_empty_file(self):
        if self._path and os.path.exists(self._path):
            try:
                os.remove(self._path)
            except OSError:
                pass

    def _teardown(self):
        if self._controller is not None:
            try:
                self._controller.stop_requested.disconnect()
            except Exception:
                pass
            self._controller.close()
            self._controller.deleteLater()
            self._controller = None


# ── 단독 검증 (비대화형: 화면 좌상단 고정 영역을 잠깐 녹화 → mp4 저장) ─────────────
if __name__ == "__main__":
    import sys

    app = QApplication(sys.argv)
    scr = QApplication.primaryScreen()
    g = scr.geometry()
    region = QRect(g.left() + 40, g.top() + 40, 400, 240)

    rec = VideoRecorder(fps=15, max_seconds=2)
    out = os.path.join(os.path.dirname(__file__), "_video_selftest.mp4")

    def _done(path):
        sz = os.path.getsize(path)
        print(f"[video] 저장됨 {path} — {sz/1024:.1f}KB")
        app.quit()

    def _cancel():
        print("[video] 취소/빈 프레임")
        app.quit()

    rec.finished.connect(_done)
    rec.cancelled.connect(_cancel)
    rec.start(region, out)
    QTimer.singleShot(2000, rec.stop)
    sys.exit(app.exec())
