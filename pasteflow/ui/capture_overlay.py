"""마그네틱 영역 캡처 오버레이 (Snipaste식 창 스냅).

hover=커서 아래 최상위 창 하이라이트, 좌클릭=창 캡처, 좌드래그=자유 사각형,
우클릭/ESC=취소.

흐름
----
1. CaptureOverlay.start() → 스크린샷 grab + 최상위 창 사각형을 1회 캡처(얼림) →
   각 QScreen마다 _CaptureScreen(입력 소유) 위젯 생성·표시
2. 매니저의 QTimer(~60fps)가:
   - GetCursorPos(물리 픽셀) → 얼려둔 창 목록에서 커서를 품는 최상위 창 사각형 조회
   - 커서가 있는 모니터의 DPR·원점으로 물리→논리 변환
   - 각 오버레이에 하이라이트(논리 가상좌표) 주입 → 해당 모니터 오버레이만 표시
   - GetAsyncKeyState(ESC) 폴링 → 취소
3. 클릭/우클릭은 오버레이 위젯의 Qt 마우스 이벤트로 매니저에 전달

입력 소유(비클릭-통과)인 이유
----------------------------
오버레이가 입력을 통과시키면(WindowTransparentForInput) 커서 아래 실제 창이 WM_SETCURSOR를
받아 자기 커서(HWP의 커스텀 I-beam 등)를 세우므로 십자가 벗겨진다. 오버레이가 입력을
소유하면 오버레이 자신이 커서를 정하므로 어떤 앱 위에서든 십자가 100% 유지된다(Snipaste 모델).
대신 커서 아래 요소를 라이브 hit-test할 수 없으므로, 캡처 시작 시점에 최상위 창 좌표를
얼려두고(EnumWindows/Z-order) 그 캐시에 hit-test 한다. 창 단위 스냅(요소 단위 아님)이 트레이드오프.

좌표계
------
GetCursorPos·GetWindowRect = 물리 픽셀(DPI-aware 프로세스). Qt 위젯 geometry·페인트 = 논리 좌표.
변환: 커서가 있는 모니터의 물리 원점(Win32 GetMonitorInfo)과 DPR(QScreen)로
물리 가상좌표 → 논리 가상좌표를 계산한다.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes

from PyQt6.QtWidgets import QWidget, QApplication
from PyQt6.QtCore import Qt, QRect, QPoint, QObject, pyqtSignal, QTimer
from PyQt6.QtGui import QPainter, QPixmap, QColor, QPen, QScreen, QCursor

from pasteflow.ui.theme import PEACH
from pasteflow import uia

_MASK_ALPHA = 100  # 어두운 마스크 알파
_BORDER_W = 2
_POLL_MS = 16          # 커서 추적/드래그 repaint 주기 (~60fps)
_INVAL_MARGIN = _BORDER_W + 2  # 부분 repaint 무효화 영역 여유(테두리 잔상 방지)
_DRAG_THRESHOLD = 4  # 클릭(창) vs 드래그(자유 사각형) 구분 임계(논리 px)

_VK_ESCAPE = 0x1B
_MONITOR_DEFAULTTONEAREST = 2

_user32 = ctypes.windll.user32
_dwmapi = ctypes.windll.dwmapi


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", _RECT),
                ("rcWork", _RECT), ("dwFlags", wintypes.DWORD)]


# ── 최상위 창 열거(얼린 스냅용) ────────────────────────────────────────────────
_GW_HWNDNEXT = 2
_DWMWA_CLOAKED = 14  # DWM 클로킹(가상 데스크톱 밖·UWP 유령 창) 판별
_DWMWA_EXTENDED_FRAME_BOUNDS = 9  # 비가시 리사이즈 테두리 제외한 '보이는' 창 경계(물리 픽셀)

_user32.GetCursorPos.argtypes = [ctypes.POINTER(_POINT)]
_user32.GetTopWindow.argtypes = [wintypes.HWND]
_user32.GetTopWindow.restype = wintypes.HWND
_user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
_user32.GetWindow.restype = wintypes.HWND
_user32.IsWindowVisible.argtypes = [wintypes.HWND]
_user32.IsWindowVisible.restype = wintypes.BOOL
_user32.IsIconic.argtypes = [wintypes.HWND]
_user32.IsIconic.restype = wintypes.BOOL
_user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(_RECT)]
_user32.GetWindowRect.restype = wintypes.BOOL
_dwmapi.DwmGetWindowAttribute.argtypes = [
    wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
_dwmapi.DwmGetWindowAttribute.restype = ctypes.c_long  # HRESULT


def _is_cloaked(hwnd) -> bool:
    val = wintypes.DWORD(0)
    res = _dwmapi.DwmGetWindowAttribute(
        hwnd, _DWMWA_CLOAKED, ctypes.byref(val), ctypes.sizeof(val))
    return res == 0 and val.value != 0


def _visible_rect(hwnd) -> _RECT | None:
    """창의 '보이는' 물리 사각형. DWM 확장 프레임 경계를 우선 써 GetWindowRect가 포함하는
    비가시 리사이즈 테두리(~8px)를 제외한다 — 이 테두리가 남으면 창 스냅이 옆 모니터로
    삐져나가거나(코랄 넘침) 캡처 시 모니터 밖 빈공간이 검게 잡힌다. DWM 실패 시 GetWindowRect 폴백."""
    r = _RECT()
    hr = _dwmapi.DwmGetWindowAttribute(
        hwnd, _DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(r), ctypes.sizeof(r))
    if hr == 0 and r.right > r.left and r.bottom > r.top:
        return r
    r2 = _RECT()
    if _user32.GetWindowRect(hwnd, ctypes.byref(r2)) \
            and r2.right > r2.left and r2.bottom > r2.top:
        return r2
    return None


def _enum_top_windows(exclude: set[int]) -> list[tuple[int, int, int, int, int]]:
    """보이는 최상위 창을 (hwnd, l, t, r, b) 물리 사각형으로 Z-order(위→아래) 순 반환.

    GetTopWindow+GW_HWNDNEXT로 Z-order를 명시적으로 보장한다(EnumWindows는 순서 미보장).
    최소화·클로킹·크기 0·자기 오버레이(exclude) 창은 건너뛴다. 목록 앞쪽이 더 위 창이므로
    커서를 품는 첫 항목이 커서 바로 아래 최상위 창. hwnd는 요소 스냅(창-스코프 hit-test)에 쓴다.
    """
    out: list[tuple[int, int, int, int, int]] = []
    hwnd = _user32.GetTopWindow(None)
    guard = 0
    while hwnd and guard < 10000:
        guard += 1
        try:
            if int(hwnd) not in exclude \
                    and _user32.IsWindowVisible(hwnd) \
                    and not _user32.IsIconic(hwnd) \
                    and not _is_cloaked(hwnd):
                r = _visible_rect(hwnd)  # DWM 확장 프레임 우선(비가시 테두리 제외)
                if r is not None:
                    out.append((int(hwnd), r.left, r.top, r.right, r.bottom))
        except Exception:
            pass
        hwnd = _user32.GetWindow(hwnd, _GW_HWNDNEXT)
    return out


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
    """단일 QScreen을 덮는 입력 소유 오버레이. 얼린 스크린샷+딤+하이라이트만 그린다.

    입력을 소유(비클릭-통과)하므로 커서가 항상 이 위젯 위에 있어 십자 커서가 유지되고,
    클릭/우클릭은 Qt 마우스 이벤트로 매니저(_CaptureOverlay)에 전달한다.
    """

    def __init__(self, screen: QScreen, manager: "CaptureOverlay"):
        super().__init__(None)
        self._screen = screen
        self._mgr = manager
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        # 딤 스크린샷으로 전 픽셀을 직접 칠하므로 반투명 불필요. 반투명(레이어드) 창은
        # 첫 표시 때 페인트 전 '검은 프레임'이 한 번 뜨는(=화면 깜빡) 증상이 있어 끈다.
        # WA_OpaquePaintEvent: 배경 지우기 생략(전 픽셀 우리가 칠함) → 깜빡임 추가 억제.
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.setCursor(Qt.CursorShape.CrossCursor)  # 어떤 앱 위에서든 십자 유지(입력 소유의 핵심)
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
        self.repaint()  # 매핑 직후 동기 페인트 강제 → 첫 프레임 빈 화면(깜빡임) 방지

    # ── Qt 마우스 이벤트 → 매니저 위임 ─────────────────────────────────────────
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            # 드래그 시작점은 press 이벤트의 전역 좌표로 즉시 확정 — 다음 tick에서 샘플하면
            # Explorer 위 UIA hit-test로 tick이 늦어질 때 커서가 이미 이동해 시작점이 밀린다.
            self._mgr._on_left_down(e.globalPosition().toPoint())
        elif e.button() == Qt.MouseButton.RightButton:
            self._mgr._on_right_down()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._mgr._on_left_up()

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
        # 2) 하이라이트: 마스크 없는 원본 복원 + coral 테두리 (클립 덕에 dirty 밖은 안 그려짐)
        hl = self._hl_local
        if hl is not None and not hl.isEmpty():
            hsrc = QRect(round(hl.x() * dpr), round(hl.y() * dpr),
                         round(hl.width() * dpr), round(hl.height() * dpr))
            p.drawPixmap(hl, self._screenshot, hsrc)
            pen = QPen(QColor(PEACH), _BORDER_W)
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
    region_captured = pyqtSignal(QPixmap, QRect)  # (잘린 이미지, 캡처한 논리 전역 사각형)


class CaptureOverlay:
    """모니터별 _CaptureScreen을 관리하고 커서 아래 최상위 창을 하이라이트하는 매니저."""

    def __init__(self):
        self._bridge = _Bridge()
        self.cancelled = self._bridge.cancelled
        self.region_captured = self._bridge.region_captured
        self._overlays: list[_CaptureScreen] = []
        self._timer = QTimer()
        self._timer.setInterval(_POLL_MS)
        self._timer.timeout.connect(self._tick)
        self._cancel_pending = False
        # 클릭(창 캡처) vs 드래그(자유 사각형) 구분 상태 — Qt 마우스 이벤트에서 세팅
        self._pending: str | None = None  # None | "click" | "drag" — 마우스 up에서 세팅
        self._lbtn_down = False
        self._dragging = False
        self._drag_start: QPoint | None = None  # 드래그 시작 논리 좌표(press 시점에 확정)
        # 얼린 최상위 창 (hwnd, l, t, r, b) 물리 — hwnd로 요소 스냅(창-스코프 hit-test)
        self._frozen_windows: list[tuple[int, int, int, int, int]] = []
        self._last_cursor: tuple[int, int] | None = None  # 커서가 안 움직이면 hit-test 스킵

    def start(self):
        self._close_all()
        self._pending = None
        self._cancel_pending = False
        self._lbtn_down = False
        self._dragging = False
        self._drag_start = None
        self._frozen_windows = []
        self._last_cursor = None
        for screen in QApplication.screens():
            ov = _CaptureScreen(screen, self)
            ov.prepare()
            self._overlays.append(ov)
        # 오버레이를 보이기 '전에' 최상위 창 좌표를 얼린다(우리 오버레이가 목록에 안 섞이게).
        # winId()로 네이티브 핸들을 생성해 명시적으로 제외.
        exclude = {int(ov.winId()) for ov in self._overlays}
        self._frozen_windows = _enum_top_windows(exclude)
        for ov in self._overlays:
            ov.show_overlay()
        self._timer.start()

    # ── Qt 마우스 이벤트 (오버레이 위젯에서 위임) ──────────────────────────────

    def _on_left_down(self, start_pos: QPoint):
        self._lbtn_down = True
        self._dragging = False
        self._drag_start = start_pos   # press 시점 좌표로 고정(tick 지연과 무관)
        self._pending = None

    def _on_left_up(self):
        self._lbtn_down = False
        self._pending = "drag" if self._dragging else "click"

    def _on_right_down(self):
        self._cancel_pending = True

    # ── internal ──────────────────────────────────────────────────────────────

    def _tick(self):
        # 우클릭 또는 ESC → 취소
        if self._cancel_pending or (_user32.GetAsyncKeyState(_VK_ESCAPE) & 0x8000):
            self._cancel()
            return
        # 마우스 up에서 확정된 동작 처리 (드래그=자유 사각형 / 클릭=창)
        if self._pending == "drag":
            self._pending = None
            self._capture(self._drag_rect_current())
            return
        if self._pending == "click":
            self._pending = None
            self._capture(self._target_rect_logical())
            return
        # 좌버튼을 누른 채 임계 이상 이동 중이면 자유 사각형, 아니면 창 하이라이트.
        # 버튼이 눌린 동안은 hover hit-test(느린 UIA)를 스킵한다 — 창은 press 직전 하이라이트가
        # 유지되고(그게 클릭 시 캡처 대상), 드래그면 자유 사각형만 그린다(Explorer 위 버벅임 제거).
        if self._lbtn_down:
            cur = QCursor.pos()
            if self._drag_start is not None and (
                    abs(cur.x() - self._drag_start.x()) > _DRAG_THRESHOLD
                    or abs(cur.y() - self._drag_start.y()) > _DRAG_THRESHOLD):
                self._dragging = True
            if self._dragging:
                gr = self._drag_rect_current()
                for ov in self._overlays:
                    ov.set_highlight_global(gr)
            return
        # hover 요소 hit-test: 커서가 움직였을 때만 부른다(안 움직이면 결과가 같으므로 유휴 비용 0).
        # 옛 30ms 스로틀은 hit-test 한 번이 59ms이던 시절 과호출을 막으려던 것인데, 루트를 최말단
        # 자식 HWND로 바꿔 5ms로 떨어진 지금은 하이라이트를 33Hz로 묶어 '건너뛰며 따라오는' 체감만
        # 남긴다 → 제거하고 매 tick(60fps) 갱신.
        cur = _cursor_phys()
        if cur == self._last_cursor:
            return
        self._last_cursor = cur
        gr = self._target_rect_logical()
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

    def _window_at_phys(self, px: int, py: int):
        """얼려둔 목록에서 물리 점 (px,py)를 품는 최상위 (hwnd, l, t, r, b). 없으면 None."""
        for rec in self._frozen_windows:
            _hwnd, l, t, r, b = rec
            if l <= px < r and t <= py < b:
                return rec
        return None

    def _target_rect_logical(self) -> QRect | None:
        """커서 아래 요소(가능하면) 또는 창 사각형을 논리 가상좌표로 반환. 없으면 None.

        얼려둔 최상위 창(hwnd)을 찾고, 그 창에 한정해 MSAA 창-스코프 hit-test로 커서 아래
        최말단 요소를 지연 하강해 짚는다(점 기반이 아니라 오버레이를 안 짚음). 요소를 못
        짚으면 창 전체 사각형으로 폴백(B1 동작).
        """
        px, py = _cursor_phys()
        hit = self._window_at_phys(px, py)
        if hit is None:
            return None
        _hwnd, wl, wt, wr, wb = hit
        win_rect = QRect(wl, wt, wr - wl, wb - wt)  # 얼려둔 창 사각형은 이미 DWM 확장프레임(비가시 테두리 제외)
        try:
            rect_phys = uia.rect_in_window_at(_hwnd, px, py)
        except Exception:
            rect_phys = None
        if rect_phys is None:
            rect_phys = win_rect  # 크롬 웹 본문 등 MSAA가 요소를 안 주는 영역 → 창 전체 스냅
        else:
            # 요소는 담긴 창보다 클 수 없다 — 오버사이즈 rect가 창 밖으로 삐져나가지 않게 클램프.
            rect_phys = rect_phys.intersected(win_rect)
            if rect_phys.isEmpty():
                rect_phys = win_rect
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
        """하이라이트 영역(창 또는 자유 사각형, gr)을 얼린 스크린샷에서 잘라 emit.

        gr이 None(빈 영역을 드래그 없이 클릭)이면 무시한다.
        """
        if gr is None or gr.isEmpty():
            return
        pm = self._crop_global(gr)
        self._close_all()
        if pm is not None and not pm.isNull():
            # gr은 캡처한 위치(논리 전역) — 핀(Alt+F3)이 그 자리에 그대로 덮을 수 있게 함께 emit
            self.region_captured.emit(pm, QRect(gr))

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

    def _cancel(self):
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


# ── 단독 검증 ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys

    app = QApplication(sys.argv)

    overlay = CaptureOverlay()
    overlay.cancelled.connect(lambda: (print("[capture] 취소(우클릭/ESC)"), app.quit()))

    def _on_captured(pm, rect):
        out = "_capture_test.png"
        pm.save(out, "PNG")
        print(f"[capture] 캡처됨 {pm.width()}x{pm.height()} px (DPR={pm.devicePixelRatio()}) @ {rect} → {out}")
        app.quit()

    overlay.region_captured.connect(_on_captured)
    overlay.start()
    print("마그네틱 캡처 테스트: 창 좌클릭=창 캡처, 좌드래그=자유 사각형, 우클릭/ESC=취소.")
    sys.exit(app.exec())
