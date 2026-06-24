"""마그네틱 영역 캡처 오버레이 (Snipaste식 요소 스냅).

hover=커서 아래 UI 요소 하이라이트(3a), 좌클릭=요소 캡처(3b), 좌드래그=자유 사각형(3c),
우클릭/ESC=취소. 클릭/드래그는 WH_MOUSE_LL 마우스 훅으로 감지·suppress한다.

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

import atexit
import ctypes
import threading
import time
from ctypes import wintypes

from PyQt6.QtWidgets import QWidget, QApplication
from PyQt6.QtCore import Qt, QRect, QPoint, QObject, pyqtSignal, QTimer
from PyQt6.QtGui import QPainter, QPixmap, QColor, QPen, QScreen, QCursor

from pasteflow.ui.theme import TEAL
from pasteflow import uia

_MASK_ALPHA = 100  # 어두운 마스크 알파
_BORDER_W = 2
_POLL_MS = 16          # 커서 추적/드래그 repaint 주기 (~60fps)
_UIA_MIN_INTERVAL = 0.030  # hover 요소 hit-test(UIA) 최소 호출 간격(초) — 과호출 방지
_INVAL_MARGIN = _BORDER_W + 2  # 부분 repaint 무효화 영역 여유(테두리 잔상 방지)
_DRAG_THRESHOLD = 4  # 클릭(요소) vs 드래그(자유 사각형) 구분 임계(논리 px)

_VK_ESCAPE = 0x1B
_MONITOR_DEFAULTTONEAREST = 2

_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32

# ── WH_MOUSE_LL 마우스 훅 (3b: 클릭 캡처 — paste_interceptor의 키보드 훅 패턴 복제) ──
WH_MOUSE_LL = 14
WM_QUIT = 0x0012
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205

LRESULT = ctypes.c_ssize_t
HOOKPROC = ctypes.CFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

_user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
_user32.SetWindowsHookExW.restype = ctypes.c_void_p
_user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
_user32.CallNextHookEx.restype = LRESULT
_user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
_user32.UnhookWindowsHookEx.restype = wintypes.BOOL
_user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
_user32.PostThreadMessageW.restype = wintypes.BOOL
_kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
_kernel32.GetModuleHandleW.restype = wintypes.HMODULE
_kernel32.GetCurrentThreadId.argtypes = []
_kernel32.GetCurrentThreadId.restype = wintypes.DWORD

# ── 캡처 모드 십자 커서 (시스템 커서 전역 교체) ──────────────────────────────────
_IDC_CROSS = 32515         # LoadCursorW 표준 십자선
_IMAGE_CURSOR = 2          # CopyImage 타입
_SPI_SETCURSORS = 0x0057   # 시스템 커서 일괄 재로드(복원)
_SPIF_SENDCHANGE = 0x0002
# 호버 시 자주 뜨는 OCR_* 시스템 커서 슬롯 — 전부 십자로 덮어 어떤 창 위에서든 십자 유지
# (NORMAL/IBEAM/CROSS/UP/SIZE*/SIZEALL/NO/HAND/APPSTARTING)
_OCR_SLOTS = (32512, 32513, 32515, 32516, 32642, 32643, 32644, 32645, 32646, 32648, 32649, 32650)

_user32.LoadCursorW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
_user32.LoadCursorW.restype = ctypes.c_void_p
_user32.CopyImage.argtypes = [ctypes.c_void_p, wintypes.UINT, ctypes.c_int, ctypes.c_int, wintypes.UINT]
_user32.CopyImage.restype = ctypes.c_void_p
_user32.SetSystemCursor.argtypes = [ctypes.c_void_p, wintypes.DWORD]
_user32.SetSystemCursor.restype = wintypes.BOOL
_user32.SystemParametersInfoW.argtypes = [wintypes.UINT, wintypes.UINT, ctypes.c_void_p, wintypes.UINT]
_user32.SystemParametersInfoW.restype = wintypes.BOOL

_cursors_swapped = False


def _swap_system_cursor_to_cross():
    """OS 시스템 커서를 십자(IDC_CROSS)로 전역 교체. idempotent."""
    global _cursors_swapped
    if _cursors_swapped:
        return
    base = _user32.LoadCursorW(None, _IDC_CROSS)  # 공유 핸들 — 직접 넘기면 안 됨
    if not base:
        return
    for slot in _OCR_SLOTS:
        # SetSystemCursor는 넘긴 핸들의 소유권을 가져가 파괴하므로 슬롯마다 복사본 전달
        copy = _user32.CopyImage(base, _IMAGE_CURSOR, 0, 0, 0)
        if copy:
            _user32.SetSystemCursor(copy, slot)
    _cursors_swapped = True


def _restore_system_cursors():
    """레지스트리에서 시스템 커서 일괄 재로드해 복원. idempotent."""
    global _cursors_swapped
    if not _cursors_swapped:
        return
    _user32.SystemParametersInfoW(_SPI_SETCURSORS, 0, None, _SPIF_SENDCHANGE)
    _cursors_swapped = False


# 캡처 도중 예외로 죽어도 커서가 십자로 남지 않도록 안전망
atexit.register(_restore_system_cursors)


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
        self._dimmed: QPixmap | None = None  # 마스크 사전합성(물리 픽셀, dpr=1) — 프레임당 알파합성 제거
        self._hl_local: QRect | None = None  # 하이라이트(이 화면 로컬 논리좌표) 또는 None

    def prepare(self):
        sg = self._screen.geometry()
        self._screenshot = self._screen.grabWindow(0, 0, 0, sg.width(), sg.height())
        # 어두운 마스크를 입힌 딤 버전을 1회 미리 합성 → paintEvent에서 매 프레임 전체 알파합성 제거
        dim = self._screenshot.copy()
        dim.setDevicePixelRatio(1.0)  # 물리 픽셀 그대로 인덱싱
        dp = QPainter(dim)
        dp.fillRect(dim.rect(), QColor(0, 0, 0, _MASK_ALPHA))
        dp.end()
        self._dimmed = dim
        self.setGeometry(sg)
        self._hl_local = None

    def show_overlay(self):
        self.show()
        self.raise_()

    def set_highlight_global(self, gr: QRect | None):
        """논리 가상좌표 하이라이트를 이 화면 로컬로 변환해 저장(바뀌면 부분 repaint)."""
        sg = self._screen.geometry()
        if gr is None:
            new = None
        else:
            inter = gr.intersected(sg)
            new = inter.translated(-sg.topLeft()) if not inter.isEmpty() else None
        if new != self._hl_local:
            old = self._hl_local
            self._hl_local = new
            # 이전∪현재 하이라이트(테두리 여유 포함)만 무효화 → paintEvent가 그 띠만 다시 그림
            dirty = self._dirty_union(old, new)
            if dirty is None:
                self.update()
            else:
                self.update(dirty)

    def _dirty_union(self, old: QRect | None, new: QRect | None) -> QRect | None:
        """이전·현재 하이라이트를 합쳐 테두리 여유를 더한 무효화 사각형(위젯 영역으로 클립)."""
        rects = [r for r in (old, new) if r is not None and not r.isEmpty()]
        if not rects:
            return None
        u = QRect(rects[0])
        for r in rects[1:]:
            u = u.united(r)
        u = u.adjusted(-_INVAL_MARGIN, -_INVAL_MARGIN, _INVAL_MARGIN, _INVAL_MARGIN)
        return u.intersected(self.rect())

    def paintEvent(self, event):
        if self._screenshot is None or self._dimmed is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        dpr = self._screenshot.devicePixelRatio()
        er = event.rect()  # 무효화된 논리 dirty 영역(Qt가 painter를 이 영역으로 클립)
        # 1) 딤 배경: 사전합성된 _dimmed에서 dirty 영역만 (전체 알파합성 없음)
        src = QRect(round(er.x() * dpr), round(er.y() * dpr),
                    round(er.width() * dpr), round(er.height() * dpr))
        p.drawPixmap(er, self._dimmed, src)
        # 2) 하이라이트: 마스크 없는 원본 복원 + teal 테두리 (클립 덕에 dirty 밖은 안 그려짐)
        hl = self._hl_local
        if hl is not None and not hl.isEmpty():
            hsrc = QRect(round(hl.x() * dpr), round(hl.y() * dpr),
                         round(hl.width() * dpr), round(hl.height() * dpr))
            p.drawPixmap(hl, self._screenshot, hsrc)
            pen = QPen(QColor(TEAL), _BORDER_W)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(hl.adjusted(0, 0, -1, -1))
        p.end()

    def crop_local(self, sel: QRect) -> QPixmap | None:
        """이 화면 로컬 논리좌표 사각형을 얼린 스크린샷에서 물리 픽셀로 잘라낸다(ocr_overlay 패턴)."""
        if self._screenshot is None:
            return None
        dpr = self._screenshot.devicePixelRatio()
        src = QRect(int(sel.x() * dpr), int(sel.y() * dpr),
                    int(sel.width() * dpr), int(sel.height() * dpr))
        cropped = self._screenshot.copy(src)
        cropped.setDevicePixelRatio(dpr)
        return cropped


class _Bridge(QObject):
    cancelled = pyqtSignal()
    region_captured = pyqtSignal(QPixmap)


class CaptureOverlay:
    """모니터별 _CaptureScreen을 관리하고 커서 아래 요소를 하이라이트하는 매니저."""

    def __init__(self):
        self._bridge = _Bridge()
        self.cancelled = self._bridge.cancelled
        self.region_captured = self._bridge.region_captured
        self._overlays: list[_CaptureScreen] = []
        self._timer = QTimer()
        self._timer.setInterval(_POLL_MS)
        self._timer.timeout.connect(self._tick)
        # 마우스 훅 (3b) — 좌클릭=캡처, 우클릭=취소. 콜백은 flag만 세팅(trivial), 실제 캡처는 _tick.
        self._mouse_hook = None
        self._mouse_thread: threading.Thread | None = None
        self._mouse_thread_id = 0
        self._mouse_running = False
        self._hook_proc = HOOKPROC(self._mouse_proc)  # GC 방지 참조 유지
        self._cancel_pending = False
        # 클릭(요소 캡처) vs 드래그(자유 사각형, 3c) 구분 상태
        self._pending: str | None = None  # None | "click" | "drag" — 마우스 up에서 세팅
        self._lbtn_down = False
        self._dragging = False
        self._need_drag_start = False
        self._drag_start: QPoint | None = None  # 드래그 시작 논리 좌표(첫 tick에서 샘플)
        self._last_uia = 0.0  # hover UIA 호출 스로틀용 마지막 호출 시각(monotonic)

    def start(self):
        self._close_all()
        self._pending = None
        self._cancel_pending = False
        self._lbtn_down = False
        self._dragging = False
        self._need_drag_start = False
        self._drag_start = None
        self._last_uia = 0.0
        for screen in QApplication.screens():
            ov = _CaptureScreen(screen)
            ov.prepare()
            self._overlays.append(ov)
        for ov in self._overlays:
            ov.show_overlay()
        _swap_system_cursor_to_cross()  # 캡처 모드 진입 시각 신호 — 십자 커서
        self._install_mouse_hook()
        self._timer.start()

    # ── internal ──────────────────────────────────────────────────────────────

    def _tick(self):
        # 우클릭(훅) 또는 ESC → 취소
        if self._cancel_pending or (_user32.GetAsyncKeyState(_VK_ESCAPE) & 0x8000):
            self._cancel()
            return
        # 마우스 up에서 확정된 동작 처리 (드래그=자유 사각형 / 클릭=요소)
        if self._pending == "drag":
            self._pending = None
            self._capture(self._drag_rect_current())
            return
        if self._pending == "click":
            self._pending = None
            self._capture(self._element_rect_logical())
            return
        # 좌버튼을 누른 채 임계 이상 이동 중이면 자유 사각형, 아니면 요소 하이라이트
        if self._lbtn_down:
            cur = QCursor.pos()
            if self._need_drag_start:
                self._drag_start = cur
                self._need_drag_start = False
            if self._drag_start is not None and (
                    abs(cur.x() - self._drag_start.x()) > _DRAG_THRESHOLD
                    or abs(cur.y() - self._drag_start.y()) > _DRAG_THRESHOLD):
                self._dragging = True
            if self._dragging:
                gr = self._drag_rect_current()
                for ov in self._overlays:
                    ov.set_highlight_global(gr)
                return
        # hover 요소 hit-test: UIA ElementFromPoint는 무거우므로 최소 간격으로 스로틀
        # (드래그 repaint는 매 tick=60fps로 돌고, UIA만 ~30fps로 떼어냄)
        now = time.monotonic()
        if now - self._last_uia < _UIA_MIN_INTERVAL:
            return
        self._last_uia = now
        gr = self._element_rect_logical()
        for ov in self._overlays:
            ov.set_highlight_global(gr)

    def _drag_rect_current(self) -> QRect | None:
        """드래그 시작점~현재 커서로 정규화된 논리 사각형(자유 사각형 폴백). 너무 작으면 None."""
        if self._drag_start is None:
            return None
        cur = QCursor.pos()
        left = min(self._drag_start.x(), cur.x())
        top = min(self._drag_start.y(), cur.y())
        w = abs(cur.x() - self._drag_start.x())
        h = abs(cur.y() - self._drag_start.y())
        if w <= 0 or h <= 0:
            return None
        return QRect(left, top, w, h)

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

    def _capture(self, gr: QRect | None):
        """하이라이트 영역(요소 또는 자유 사각형, gr)을 얼린 스크린샷에서 잘라 emit.

        gr이 None(빈 영역을 드래그 없이 클릭)이면 무시한다.
        """
        if gr is None or gr.isEmpty():
            return
        pm = self._crop_global(gr)
        self._close_all()
        if pm is not None and not pm.isNull():
            self.region_captured.emit(pm)

    def _crop_global(self, gr: QRect) -> QPixmap | None:
        """논리 가상좌표 사각형을 캡처. 여러 모니터에 걸치면 각 화면 조각을 합성한다.

        단일 모니터면 그 화면 스크린샷에서 바로 crop. 다중 모니터면 가장 높은 DPR을
        타깃으로 빈 캔버스를 만들고 각 화면 조각을 제 위치에 그려 넣는다(배율 다른 화면의
        조각은 타깃 DPR로 스케일 — 기하학은 정확, 저DPI 조각은 약간 소프트).
        """
        pieces = []  # (overlay, inter_global)
        for ov in self._overlays:
            inter = gr.intersected(ov._screen.geometry())
            if not inter.isEmpty() and ov._screenshot is not None:
                pieces.append((ov, inter))
        if not pieces:
            return None
        if len(pieces) == 1:
            ov, inter = pieces[0]
            local = inter.translated(-ov._screen.geometry().topLeft())
            return ov.crop_local(local)

        target_dpr = max(ov._screenshot.devicePixelRatio() for ov, _ in pieces)
        out_w = max(1, round(gr.width() * target_dpr))
        out_h = max(1, round(gr.height() * target_dpr))
        canvas = QPixmap(out_w, out_h)
        canvas.fill(QColor(0, 0, 0))
        painter = QPainter(canvas)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        for ov, inter in pieces:
            sg = ov._screen.geometry()
            piece = ov.crop_local(inter.translated(-sg.topLeft()))
            if piece is None or piece.isNull():
                continue
            piece.setDevicePixelRatio(1.0)  # 캔버스에 raw 물리 픽셀로 그리기 위해 DPR 무력화
            dst = QRect(
                round((inter.left() - gr.left()) * target_dpr),
                round((inter.top() - gr.top()) * target_dpr),
                round(inter.width() * target_dpr),
                round(inter.height() * target_dpr),
            )
            painter.drawPixmap(dst, piece)
        painter.end()
        canvas.setDevicePixelRatio(target_dpr)
        return canvas

    # ── 마우스 훅 (3b) ──────────────────────────────────────────────────────────

    def _install_mouse_hook(self):
        self._mouse_running = True
        self._mouse_thread = threading.Thread(target=self._mouse_hook_thread, daemon=True)
        self._mouse_thread.start()

    def _uninstall_mouse_hook(self):
        self._mouse_running = False
        if self._mouse_hook:
            _user32.UnhookWindowsHookEx(self._mouse_hook)
            self._mouse_hook = None
        if self._mouse_thread_id:
            _user32.PostThreadMessageW(self._mouse_thread_id, WM_QUIT, 0, 0)
            self._mouse_thread_id = 0
        self._mouse_thread = None

    def _mouse_hook_thread(self):
        self._mouse_thread_id = _kernel32.GetCurrentThreadId()
        h_mod = _kernel32.GetModuleHandleW(None)
        self._mouse_hook = _user32.SetWindowsHookExW(WH_MOUSE_LL, self._hook_proc, h_mod, 0)
        if not self._mouse_hook:
            self._mouse_running = False
            return
        msg = wintypes.MSG()
        while self._mouse_running:
            ret = _user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret <= 0:
                break
            _user32.TranslateMessage(ctypes.byref(msg))
            _user32.DispatchMessageW(ctypes.byref(msg))

    def _mouse_proc(self, nCode, wParam, lParam):
        """trivial 콜백: 좌/우클릭 flag만 세팅 후 suppress. 실제 캡처·UIA는 _tick(메인 스레드).

        ctypes 콜백에서 예외가 C로 전파되면 프로세스 크래시 → 모든 예외 포착.
        """
        try:
            if nCode >= 0:
                if wParam == WM_RBUTTONDOWN:
                    return 1  # suppress down — 컨텍스트 메뉴 누수 방지
                if wParam == WM_RBUTTONUP:
                    self._cancel_pending = True
                    return 1  # suppress up + 취소 트리거 (down·up 모두 소비 후 취소)
                if wParam == WM_LBUTTONDOWN:
                    self._lbtn_down = True
                    self._dragging = False
                    self._need_drag_start = True
                    self._pending = None
                    return 1  # suppress down — 아래 앱 클릭(탭 전환 등) 차단
                if wParam == WM_LBUTTONUP:
                    self._lbtn_down = False
                    self._pending = "drag" if self._dragging else "click"
                    return 1  # suppress up + 캡처/드래그 트리거
        except Exception:
            pass
        return _user32.CallNextHookEx(self._mouse_hook, nCode, wParam, lParam)

    def _cancel(self):
        self._close_all()
        self.cancelled.emit()

    def _close_all(self):
        self._timer.stop()
        self._uninstall_mouse_hook()
        _restore_system_cursors()  # 십자 커서 → 기본 커서 복원
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
    overlay.cancelled.connect(lambda: (print("[capture] 취소(우클릭/ESC)"), app.quit()))

    def _on_captured(pm):
        out = "_capture_test.png"
        pm.save(out, "PNG")
        print(f"[capture] 캡처됨 {pm.width()}x{pm.height()} px (DPR={pm.devicePixelRatio()}) → {out}")
        app.quit()

    overlay.region_captured.connect(_on_captured)
    overlay.start()
    print("마그네틱 캡처 테스트: 요소 좌클릭=요소 캡처, 좌드래그=자유 사각형, 우클릭/ESC=취소.")
    sys.exit(app.exec())
