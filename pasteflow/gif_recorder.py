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
- composite_cursor(): 프레임에 실제 커서를 GDI로 합성(video_recorder.py도 공유 재사용).
  저수준 조각(sample_cursor·cursor_hotspot·blit_icon_at)은 capture_overlay.py의 정지 캡처
  '커서 포함' 미리보기 마커도 직접 가져다 쓴다(오버레이가 커서를 가로채기 전에 얼려두는 값).

커서 합성 방식(2026-08-03)
--------------------------
`screen.grabWindow()`(Qt/BitBlt)는 마우스 커서를 안 담는다. 커서 아이콘을 별도로 추출해
알파 블렌딩하는 대신, **이미 캡처된 프레임을 GDI 비트맵에 그대로 실어(SetDIBits) 그 위에
`DrawIconEx`로 커서를 그린 뒤 다시 꺼낸다(GetDIBits)** — Windows가 실제 배경 위에 커서를
합성하므로 모노크롬(AND/XOR 마스크) 커서든 최신 32bpp ARGB 커서든 형식 무관하게 정확하다.
좌표 변환(물리 커서 위치 → 캡처 영역의 로컬 물리 픽셀)은 `capture_overlay._monitor_phys_origin`과
동일한 패턴(모니터 물리 원점 + QScreen DPR)을 그대로 재사용한다.

한계(MVP)
---------
- 선택이 시작된 단일 모니터 한정(크로스 모니터 녹화 미지원).
- GIF는 클립보드에 애니메이션으로 못 올라가므로 파일 저장 + 경로 복사로 넘긴다(main).
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes

from PyQt6.QtWidgets import QWidget, QApplication, QLabel, QPushButton, QHBoxLayout
from PyQt6.QtCore import Qt, QRect, QObject, pyqtSignal, QTimer, QElapsedTimer
from PyQt6.QtGui import QImage, QScreen

from pasteflow.ui.theme import PEACH, PEACH_HOVER, BASE, TEXT, SURFACE2
from pasteflow.ui.capture_overlay import _monitor_phys_origin

_VK_ESCAPE = 0x1B
_user32 = ctypes.windll.user32

# 커서 합성 전용 — 다른 모듈(capture_overlay·uia)의 argtypes와 절대 안 섞이도록 전용
# WinDLL 인스턴스를 쓴다(uia.py가 이미 겪은 교훈: 공유 windll.user32에 argtypes를 걸면
# 같은 함수를 쓰는 다른 모듈의 설정과 충돌해 ArgumentError가 난다).
_cur_user32 = ctypes.WinDLL("user32", use_last_error=True)
_cur_gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

_CURSOR_SHOWING = 0x00000001
_DI_NORMAL = 0x0003
_DIB_RGB_COLORS = 0
_SRCCOPY = 0x00CC0020


class _CURSORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("hCursor", wintypes.HANDLE),
        ("ptScreenPos", wintypes.POINT),
    ]


class _ICONINFO(ctypes.Structure):
    _fields_ = [
        ("fIcon", wintypes.BOOL),
        ("xHotspot", wintypes.DWORD),
        ("yHotspot", wintypes.DWORD),
        ("hbmMask", wintypes.HANDLE),
        ("hbmColor", wintypes.HANDLE),
    ]


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", ctypes.c_long),
        ("biHeight", ctypes.c_long),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", ctypes.c_long),
        ("biYPelsPerMeter", ctypes.c_long),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


_cur_user32.GetCursorInfo.argtypes = [ctypes.POINTER(_CURSORINFO)]
_cur_user32.GetCursorInfo.restype = wintypes.BOOL
_cur_user32.GetIconInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ICONINFO)]
_cur_user32.GetIconInfo.restype = wintypes.BOOL
_cur_user32.DrawIconEx.argtypes = [
    wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.HANDLE,
    ctypes.c_int, ctypes.c_int, wintypes.UINT, wintypes.HANDLE, wintypes.UINT]
_cur_user32.DrawIconEx.restype = wintypes.BOOL
_cur_user32.GetDC.argtypes = [wintypes.HWND]
_cur_user32.GetDC.restype = wintypes.HDC
_cur_user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
_cur_user32.ReleaseDC.restype = ctypes.c_int
_cur_gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
_cur_gdi32.CreateCompatibleDC.restype = wintypes.HDC
_cur_gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
_cur_gdi32.CreateCompatibleBitmap.restype = wintypes.HANDLE
_cur_gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HANDLE]
_cur_gdi32.SelectObject.restype = wintypes.HANDLE
_cur_gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
_cur_gdi32.DeleteObject.restype = wintypes.BOOL
_cur_gdi32.DeleteDC.argtypes = [wintypes.HDC]
_cur_gdi32.DeleteDC.restype = wintypes.BOOL
_cur_gdi32.SetDIBits.argtypes = [
    wintypes.HDC, wintypes.HANDLE, wintypes.UINT, wintypes.UINT,
    ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
_cur_gdi32.SetDIBits.restype = ctypes.c_int
_cur_gdi32.GetDIBits.argtypes = [
    wintypes.HDC, wintypes.HANDLE, wintypes.UINT, wintypes.UINT,
    ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
_cur_gdi32.GetDIBits.restype = ctypes.c_int


def _make_bmi_top_down(w: int, h: int) -> _BITMAPINFOHEADER:
    """32bpp BI_RGB, biHeight를 음수로 줘 top-down(=QImage와 같은 행 순서)."""
    bmi = _BITMAPINFOHEADER()
    bmi.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
    bmi.biWidth = w
    bmi.biHeight = -h
    bmi.biPlanes = 1
    bmi.biBitCount = 32
    bmi.biCompression = 0  # BI_RGB
    return bmi


def sample_cursor() -> tuple[int, int, int] | None:
    """지금 화면에 떠 있는 커서를 (hCursor, 물리 x, 물리 y)로 1회 샘플링. 숨겨져 있으면 None.

    capture_overlay가 자기 오버레이(십자선)로 커서를 가로채기 '전'에 이 함수로 진짜 커서를
    얼려두는 데도 쓴다(정지 캡처의 '커서 포함' 기능) — GIF/영상 녹화는 이 값을 매 프레임
    라이브로 다시 구해 composite_cursor에 넘긴다.
    """
    try:
        ci = _CURSORINFO()
        ci.cbSize = ctypes.sizeof(_CURSORINFO)
        if not _cur_user32.GetCursorInfo(ctypes.byref(ci)):
            return None
        if not (ci.flags & _CURSOR_SHOWING):
            return None
        return (ci.hCursor, ci.ptScreenPos.x, ci.ptScreenPos.y)
    except Exception:
        return None


def cursor_hotspot(hcursor) -> tuple[int, int] | None:
    """hcursor의 핫스팟(아이콘 좌상단 기준 오프셋). 실패하면 None."""
    try:
        icon_info = _ICONINFO()
        if not _cur_user32.GetIconInfo(hcursor, ctypes.byref(icon_info)):
            return None
        # hbmMask/hbmColor는 GetIconInfo 문서상 호출자가 항상 delete해야 한다.
        # hCursor 자체는 시스템 공유 핸들이라 파괴하면 안 된다(DestroyIcon 호출 금지).
        if icon_info.hbmMask:
            _cur_gdi32.DeleteObject(icon_info.hbmMask)
        if icon_info.hbmColor:
            _cur_gdi32.DeleteObject(icon_info.hbmColor)
        return (icon_info.xHotspot, icon_info.yHotspot)
    except Exception:
        return None


def blit_icon_at(qimg: QImage, hcursor, cx: int, cy: int) -> QImage:
    """qimg(물리픽셀 버퍼)의 (cx,cy)(아이콘 좌상단 기준, 물리픽셀)에 hcursor를 GDI로 그려 합성.

    composite_cursor의 실제 GDI 블릿 부분(SetDIBits→DrawIconEx→GetDIBits)을 그대로 뽑아낸
    저수준 조각 — capture_overlay의 커서 미리보기 마커도 작은 패치에 이 함수를 직접 쓴다.
    실패하면 원본을 그대로 반환(최선 노력형).
    """
    try:
        w, h = qimg.width(), qimg.height()
        # 캡처 영역과 커서 아이콘 박스가 전혀 안 겹치면 합성할 필요 없음
        if cx <= -128 or cy <= -128 or cx >= w or cy >= h:
            return qimg

        img = qimg.convertToFormat(QImage.Format.Format_RGB32)
        bits_ptr = img.constBits()
        bits_ptr.setsize(img.sizeInBytes())
        src_bytes = bytes(bits_ptr)

        hdc_screen = _cur_user32.GetDC(None)
        hdc_mem = _cur_gdi32.CreateCompatibleDC(hdc_screen)
        hbmp = _cur_gdi32.CreateCompatibleBitmap(hdc_screen, w, h)
        old = _cur_gdi32.SelectObject(hdc_mem, hbmp)
        try:
            bmi = _make_bmi_top_down(w, h)
            buf = ctypes.create_string_buffer(src_bytes)
            _cur_gdi32.SetDIBits(
                hdc_mem, hbmp, 0, h, buf, ctypes.byref(bmi), _DIB_RGB_COLORS)
            _cur_user32.DrawIconEx(
                hdc_mem, cx, cy, hcursor, 0, 0, 0, None, _DI_NORMAL)
            out = ctypes.create_string_buffer(w * h * 4)
            _cur_gdi32.GetDIBits(
                hdc_mem, hbmp, 0, h, out, ctypes.byref(bmi), _DIB_RGB_COLORS)
        finally:
            _cur_gdi32.SelectObject(hdc_mem, old)
            _cur_gdi32.DeleteObject(hbmp)
            _cur_gdi32.DeleteDC(hdc_mem)
            _cur_user32.ReleaseDC(None, hdc_screen)

        result = QImage(bytes(out.raw), w, h, QImage.Format.Format_RGB32)
        return result.copy()
    except Exception:
        return qimg


def composite_cursor(qimg: QImage, screen: QScreen, local_rect: QRect, cursor=None) -> QImage:
    """qimg(local_rect를 grab한 프레임)에 커서를 합성해 반환.

    local_rect: screen 안에서의 캡처 사각형(screen-local **논리** 좌표 — GifRecorder가
    쓰는 것과 동일한 rect).
    cursor: (hCursor, 물리x, 물리y)를 주면 그 '얼려둔' 커서를 굽는다(정지 캡처의 커서 포함
    기능 — capture_overlay가 오버레이를 띄우기 전에 sample_cursor()로 미리 떠둔 값).
    None(기본)이면 지금 이 순간을 sample_cursor()로 라이브 샘플링한다(GIF/영상 녹화가 매
    프레임 호출하는 기존 동작, 하위호환).
    커서가 숨겨져 있거나 이 화면 밖이면 원본을 그대로 반환한다. 실패해도 녹화 자체는
    계속돼야 하므로 어떤 예외든 원본 프레임을 반환(최선 노력형).
    """
    try:
        if cursor is None:
            cursor = sample_cursor()
            if cursor is None:
                return qimg
        hcursor, sx, sy = cursor

        hs = cursor_hotspot(hcursor)
        if hs is None:
            return qimg
        hotspot_x, hotspot_y = hs

        mon_x, mon_y = _monitor_phys_origin(sx, sy)
        dpr = screen.devicePixelRatio()
        region_phys_x = mon_x + round(local_rect.x() * dpr)
        region_phys_y = mon_y + round(local_rect.y() * dpr)
        cx = (sx - region_phys_x) - hotspot_x
        cy = (sy - region_phys_y) - hotspot_y

        return blit_icon_at(qimg, hcursor, cx, cy)
    except Exception:
        return qimg


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

    def __init__(self, fps: int = 12, max_seconds: int = 15, show_cursor: bool = True):
        super().__init__()
        self._fps = max(1, min(30, int(fps)))
        self._interval_ms = round(1000 / self._fps)
        self._max_frames = self._fps * max(1, int(max_seconds))
        self._show_cursor = show_cursor
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
            img = pm.toImage()
            if self._show_cursor:
                img = composite_cursor(img, self._screen, r)
            self._frames.append(img)
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
