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
from PyQt6.QtGui import QPainter, QPixmap, QImage, QColor, QPen, QScreen, QCursor

from pasteflow.ui.theme import PEACH, TEXT
from pasteflow import uia

_MASK_ALPHA = 100  # 어두운 마스크 알파
_BORDER_W = 2
_POLL_MS = 16          # 커서 추적/드래그 repaint 주기 (~60fps)
_INVAL_MARGIN = _BORDER_W + 2  # 부분 repaint 무효화 영역 여유(테두리 잔상 방지)
_DRAG_THRESHOLD = 4  # 클릭(창) vs 드래그(자유 사각형) 구분 임계(논리 px)

_VK_ESCAPE = 0x1B
# 커서 포함 토글(정지 캡처에서만 — select_only=GIF/영상 녹화는 gif_show_cursor 설정을 그대로 씀).
# 얼려둔 '진짜' 커서(오버레이가 뜨기 전에 sample_cursor()로 뜬 값)를 보여줄지만 결정한다 —
# 위치·아이콘 자체는 이미 확정돼 있어 Space는 순수 표시 여부 스위치.
_VK_SPACE = 0x20
_HINT_RECT = QRect(16, 16, 260, 30)  # 좌상단 고정 힌트 배지(얼려둔 커서가 있을 때만 표시)
_CURSOR_PATCH_PX = 96  # 커서 미리보기 패치 한 변(물리픽셀) — 표준~고DPI 커서가 넉넉히 들어가는 여유
_MONITOR_DEFAULTTONEAREST = 2

_user32 = ctypes.windll.user32
_dwmapi = ctypes.windll.dwmapi
_kernel32 = ctypes.windll.kernel32

_HWND_TOPMOST = wintypes.HWND(-1)
_SWP_NOMOVE = 0x0002
_SWP_NOSIZE = 0x0001
_SWP_NOACTIVATE = 0x0010

# ── 키 억제 훅(Space·ESC가 배경 앱까지 새는 것 방지) ────────────────────────────
# 오버레이는 마우스는 소유(입력 소유 모델)하지만 키보드 포커스는 원래 앱에 그대로 남겨둔다
# (WA_ShowWithoutActivating — 활성화를 뺏지 않아야 어떤 앱 위에서든 뜰 수 있으므로). 그래서
# Space·ESC를 GetAsyncKeyState로 '폴링'만 하고 실제로 삼키지 않으면, 그 키의 진짜 WM_KEYDOWN은
# 여전히 원래 포커스(예: 크롬)로 그대로 전달돼 "스페이스 눌렀더니 스크롤됨/버튼이 클릭됨" 현상이
# 난다(2026-08-04 사용자 리포트). paste_interceptor.py의 상시 전역 훅(앱 단축키 전용, 이미
# IME 마스킹·Alt 레이스 등 세밀하게 튜닝돼 있어 건드리면 회귀 위험이 큼)과는 별개로, 캡처
# 세션 동안만 사는 전용 저수준 훅을 이 모듈에 자체적으로 둔다(설치는 start(), 해제는
# _close_all()). Qt의 메인 스레드 이벤트루프가 이미 네이티브 메시지를 펌프하므로(내부적으로
# PeekMessage/DispatchMessage) paste_interceptor처럼 별도 스레드+GetMessageW 루프가 필요
# 없다 — 훅 설치 스레드가 메시지를 계속 펌프하기만 하면 콜백이 호출된다.
_WH_KEYBOARD_LL = 13
_WM_KEYDOWN = 0x0100
_WM_SYSKEYDOWN = 0x0104
_WM_KEYUP = 0x0101
_WM_SYSKEYUP = 0x0105
_LLKHF_INJECTED = 0x10  # KBDLLHOOKSTRUCT.flags 비트 — SendInput 등으로 주입된 키(억제 대상 아님)
_LRESULT = ctypes.c_ssize_t
_HOOKPROC = ctypes.CFUNCTYPE(_LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)


class _KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD),
                ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_void_p)]


_user32.SetWindowsHookExW.argtypes = [ctypes.c_int, _HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
_user32.SetWindowsHookExW.restype = ctypes.c_void_p
_user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
_user32.CallNextHookEx.restype = _LRESULT
_user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
_user32.UnhookWindowsHookEx.restype = wintypes.BOOL
_kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
_kernel32.GetModuleHandleW.restype = wintypes.HMODULE

# ── 커서 '숨김' 상태 폴백(표준 화살표) ────────────────────────────────────────
# Windows "입력 중 포인터 숨기기"(마우스 설정 기본 ON)가 걸리면 물리 마우스를 움직이기 전까진
# GetCursorInfo().flags에 CURSOR_SHOWING이 꺼진 채로 남는다 — 키보드만 쓰다가(Alt+F2도
# 키보드) 캡처하면 흔히 걸리는 상태(2026-08-04 실측 재현: SendInput 키 입력만 반복하니
# 물리 마우스를 안 건드리는 한 계속 꺼져 있었음). GIF/영상 녹화(sample_cursor, 라이브 매
# 프레임)는 실제로 안 보이던 걸 그대로 정확히 반영해야 하니 이 폴백을 안 쓰지만, 정지 캡처의
# '커서 포함'은 사용자가 Space로 명시 요청한 스탬프라 숨김 상태 하나로 통째로 안 되는 게 더
# 나쁘다 — 표준 화살표로 대체해서라도 계속 동작하게 한다.
_IDC_ARROW = 32512
_user32.LoadCursorW.argtypes = [wintypes.HINSTANCE, ctypes.c_void_p]
_user32.LoadCursorW.restype = wintypes.HANDLE


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
_user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(_RECT)]
_user32.GetClientRect.restype = wintypes.BOOL
_user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(_POINT)]
_user32.ClientToScreen.restype = wintypes.BOOL
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


def _client_rect_screen(hwnd) -> _RECT | None:
    """hwnd 클라이언트 영역의 화면 사각형(물리 픽셀 _RECT). 실패 시 None.

    GetClientRect(원점 0,0 기준 크기) + ClientToScreen(원점을 화면 좌표로)로 조립한다.
    DPI-aware 프로세스에서 둘 다 물리 픽셀이라 커서(GetCursorPos)·창(DWM) 좌표계와 일치.
    이 사각형 '밖'이면 커서가 비클라이언트(제목표시줄·창 테두리) 위 = 창 전체 스냅 신호.
    """
    rc = _RECT()
    if not _user32.GetClientRect(hwnd, ctypes.byref(rc)):
        return None
    org = _POINT(0, 0)
    if not _user32.ClientToScreen(hwnd, ctypes.byref(org)):
        return None
    w, h = rc.right - rc.left, rc.bottom - rc.top
    if w <= 0 or h <= 0:
        return None
    return _RECT(org.x, org.y, org.x + w, org.y + h)


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

    def _claim_topmost(self):
        """topmost 밴드의 맨 앞으로 못박는다 — panel.py의 _set_always_on_top와 동일 호출.

        ⚠ Qt의 raise_()는 TOPMOST 창끼리의 순서까지는 못 뒤집는다 — 패널처럼 이미
        SetWindowPos(HWND_TOPMOST)로 떠 있는 창이 있으면, 이 오버레이가 더 늦게 떠도
        패널이 그 위에 남아 클릭/드래그를 오버레이 대신 패널이 받는다(패널이 열려 있을 때
        Alt+F2 캡처가 안 되던 원인 — 2026-07-28 사용자 보고, 실측: `WindowFromPoint`로 클릭
        좌표의 실제 소유 창을 확인하니 패널이었다). 이 명시적 Win32 호출로 오버레이를
        topmost 밴드 맨 앞에 세운다. `CaptureOverlay.start()`가 표시 직후와 짧은 지연 후
        두 번 부른다 — 실측 결과 두 TOPMOST 창이 거의 동시에 자기 자리를 주장하면 어느 쪽이
        이길지 5회 중 1회꼴로 뒤집히는 레이스가 있어(패널의 재확인 타이밍과 겹칠 때), 한 번의
        호출만으로는 신뢰할 수 없었다.
        """
        hwnd = wintypes.HWND(int(self.winId()))
        _user32.SetWindowPos(
            hwnd, _HWND_TOPMOST, 0, 0, 0, 0,
            _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOACTIVATE,
        )

    def show_overlay(self):
        self.show()
        self.raise_()
        self._claim_topmost()
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
        # 3) 얼려둔 커서 미리보기 마커 — Space가 ON이고 이 화면이 그 마커를 담은 화면일 때만.
        # 오버레이가 뜨기 '전'에 sample_cursor()로 떠둔 진짜 커서를 그 자리에 실제 아이콘
        # 픽셀 그대로 미리 합성해둔 패치(_build_cursor_preview)를 그대로 그린다.
        preview = self._mgr._cursor_preview
        if self._mgr._include_cursor and preview is not None:
            img, marker_rect, marker_ov = preview
            if marker_ov is self and marker_rect.intersects(er):
                p.drawImage(marker_rect, img)
        # 4) 커서 포함 토글 힌트(좌상단 고정 배지) — 얼려둔 커서가 있을 때만(없으면 토글 무의미),
        # select_only(GIF/영상 녹화)는 아예 표시하지 않는다(그쪽은 gif_show_cursor 설정이 담당).
        if not self._mgr._select_only and self._mgr._frozen_cursor is not None \
                and _HINT_RECT.intersects(er):
            on = self._mgr._include_cursor
            p.fillRect(_HINT_RECT, QColor(0, 0, 0, 160))
            p.setPen(QColor(PEACH) if on else QColor(TEXT))
            p.drawText(
                _HINT_RECT, Qt.AlignmentFlag.AlignCenter,
                f"Space: 실제 커서 표시 {'ON' if on else 'OFF'}")
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
    region_selected = pyqtSignal(QRect)  # select_only 모드: 사각형만(GIF 녹화가 라이브로 채운다)


class CaptureOverlay:
    """모니터별 _CaptureScreen을 관리하고 커서 아래 최상위 창을 하이라이트하는 매니저."""

    def __init__(self):
        self._bridge = _Bridge()
        self.cancelled = self._bridge.cancelled
        self.region_captured = self._bridge.region_captured
        self.region_selected = self._bridge.region_selected
        self._select_only = False  # True면 자르지 않고 사각형만 emit(GIF 녹화용)
        self._include_cursor = False  # Space로 토글 — 얼려둔 커서를 보여줄지(정지 캡처 전용)
        self._space_toggle_pending = False  # 훅이 본 Space keydown → _tick이 소비
        self._space_down = False  # auto-repeat keydown이 연타 토글되지 않게(누른 동안 1회)
        self._frozen_cursor: tuple[int, int, int] | None = None  # (hCursor, 물리x, 물리y)
        self._frozen_marker_screen: QScreen | None = None  # 위 좌표가 속한 화면
        self._cursor_preview: tuple[QImage, QRect, "_CaptureScreen"] | None = None  # 지연 생성 캐시
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
        self._kb_hook = None  # Space·ESC 억제용 저수준 훅 핸들(세션 동안만 삶)
        self._kb_hook_proc = None  # HOOKPROC 콜백 — GC 방지를 위해 인스턴스에 붙잡아둠

    def start(self, select_only: bool = False):
        """select_only=True면 선택 영역을 자르지 않고 논리 전역 사각형만 emit한다
        (GIF 녹화가 그 자리를 라이브로 연속 캡처한다 — capture_overlay는 '얼린' 스냅이라 부적합)."""
        self._select_only = select_only
        # 오버레이가 자기 십자선으로 커서를 가로채기 '전'에 진짜 커서를 얼려둔다 — 이후로는
        # 다시 관측할 수 없으므로 세션에서 가장 먼저(창 생성보다도 전에) 해야 한다.
        self._frozen_cursor = None
        self._frozen_marker_screen = None
        self._cursor_preview = None
        if not select_only:
            self._sample_and_locate_cursor()
        self._close_all()
        self._install_key_suppression_hook()
        self._pending = None
        self._cancel_pending = False
        self._lbtn_down = False
        self._dragging = False
        self._drag_start = None
        self._frozen_windows = []
        self._last_cursor = None
        self._include_cursor = False  # 매 캡처 세션마다 기본 OFF(가끔만 필요 — 예측 가능하게)
        self._space_toggle_pending = False
        self._space_down = False
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
        # 표시 직후 한 번 더 topmost를 재확인(_CaptureScreen._claim_topmost docstring의
        # 레이스 참고) — 패널의 재확인과 겹쳐 이번 라운드를 놓쳤어도 이 지연 호출이 만회한다.
        QTimer.singleShot(60, self._reclaim_topmost)

    def _reclaim_topmost(self):
        for ov in self._overlays:
            ov._claim_topmost()

    # ── 커서 포함(정지 캡처 전용) ────────────────────────────────────────────────

    def _sample_and_locate_cursor(self):
        """오버레이가 커서를 가로채기 전, 지금 화면에 떠 있는 '진짜' 커서(아이콘+물리좌표)를
        1회 얼려두고 그게 속한 화면만 찾아둔다(정확한 화면-로컬 좌표 계산은 실제로 미리보기를
        그릴 때 _build_cursor_preview가 스크린샷 버퍼 기준으로 직접 한다). Space로 미리보기를
        켤 때만 실제 렌더한다(지연 생성 — 안 켜면 만들 필요가 없으므로).

        sample_cursor()가 '숨김'(None)을 주면 표준 화살표 + 마지막 위치(GetCursorPos)로
        대체한다(위 _IDC_ARROW 선언부 설명 참고) — Windows "입력 중 포인터 숨기기"에 걸린
        채(키보드만 쓰다 Alt+F2를 누른 흔한 경우) 기능 전체가 조용히 먹통 되는 것을 막기
        위함. 이 대체가 완전히 불가능한 경우(LoadCursorW 자체 실패)에만 진짜로 아무것도
        안 남긴다.
        """
        from pasteflow.gif_recorder import sample_cursor
        cur = sample_cursor()
        if cur is None:
            arrow = _user32.LoadCursorW(None, _IDC_ARROW)
            if not arrow:
                return
            cur = (arrow,) + _cursor_phys()
        self._frozen_cursor = cur
        # 이 시점엔 아직 오버레이가 안 떴으므로 QCursor.pos()가 진짜 마우스 위치와 일치한다
        # (창 생성/grabWindow보다 앞서 호출되는 게 보장돼야 함 — start()가 그렇게 부른다).
        self._frozen_marker_screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()

    def _build_cursor_preview(self):
        """얼려둔 커서를 실제 배경 위에 합성한 작은 미리보기 — (QImage, 화면-로컬 논리
        사각형, 그 화면의 _CaptureScreen) 또는 None. composite_cursor와 같은 GDI 블릿
        (blit_icon_at)을 작은 패치에 직접 적용한다(최선 노력형 — 실패하면 마커 없이 진행)."""
        if self._frozen_cursor is None or self._frozen_marker_screen is None:
            return None
        ov = next((o for o in self._overlays if o._screen is self._frozen_marker_screen), None)
        if ov is None or ov._screenshot is None:
            return None
        try:
            from pasteflow.gif_recorder import blit_icon_at, cursor_hotspot
            hcursor, sx, sy = self._frozen_cursor
            hs = cursor_hotspot(hcursor)
            if hs is None:
                return None
            hx, hy = hs
            mon_x, mon_y = _monitor_phys_origin(sx, sy)
            # 이 화면 스크린샷 버퍼(물리픽셀, 화면 자기 원점 기준) 안에서 커서의 물리좌표
            cur_px, cur_py = sx - mon_x, sy - mon_y
            half = _CURSOR_PATCH_PX // 2
            buf_rect = QRect(0, 0, ov._screenshot.width(), ov._screenshot.height())
            clipped = QRect(cur_px - half, cur_py - half,
                             _CURSOR_PATCH_PX, _CURSOR_PATCH_PX).intersected(buf_rect)
            if clipped.isEmpty():
                return None
            patch = ov._screenshot.copy(clipped)
            patch.setDevicePixelRatio(1.0)
            img = patch.toImage().convertToFormat(QImage.Format.Format_RGB32)
            cx = (cur_px - clipped.x()) - hx
            cy = (cur_py - clipped.y()) - hy
            out = blit_icon_at(img, hcursor, cx, cy)
            dpr = ov._screenshot.devicePixelRatio()
            local_rect = QRect(
                round(clipped.x() / dpr), round(clipped.y() / dpr),
                max(1, round(clipped.width() / dpr)), max(1, round(clipped.height() / dpr)))
            return (out, local_rect, ov)
        except Exception:
            return None

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
        # 우클릭 또는 ESC → 취소.
        # ⚠ ESC는 _on_kb_hook이 _cancel_pending을 세워 준다 — GetAsyncKeyState 폴링으로
        # 판정하지 않는다. 훅이 ESC를 suppress(return 1)하면 그 키는 비동기 키 상태 테이블에
        # 반영되지 않아 폴링이 영원히 False를 보기 때문(2026-08-04 실측 — 아래 _on_kb_hook 참고).
        # 훅 설치가 실패한 경우에만 폴링으로 열화 폴백(그땐 억제도 없어 폴링이 정상 동작).
        if self._cancel_pending or (
                self._kb_hook is None and (_user32.GetAsyncKeyState(_VK_ESCAPE) & 0x8000)):
            self._cancel()
            return
        # 커서 포함 토글(Space) — 훅이 keydown에서 세워 둔 플래그를 여기서 소비한다(위와 같은 이유).
        # 얼려둔 커서가 없으면(캡처 시작 시 커서가 숨겨져 있었던 경우) 토글할 게 없으므로 무시.
        # select_only(GIF/영상 녹화)는 별개 설정(gif_show_cursor)이 이미 담당하므로 건드리지 않는다.
        if not self._select_only and self._frozen_cursor is not None:
            if self._kb_hook is None:  # 훅 없음 → 폴링 폴백(엣지 검출은 _space_down이 담당)
                down = bool(_user32.GetAsyncKeyState(_VK_SPACE) & 0x8000)
                if down and not self._space_down:
                    self._space_toggle_pending = True
                self._space_down = down
            if self._space_toggle_pending:
                self._space_toggle_pending = False
                self._include_cursor = not self._include_cursor
                if self._include_cursor and self._cursor_preview is None:
                    self._cursor_preview = self._build_cursor_preview()
                for ov in self._overlays:
                    ov.update(_HINT_RECT)
                if self._cursor_preview is not None:
                    _, marker_rect, marker_ov = self._cursor_preview
                    marker_ov.update(marker_rect.adjusted(-4, -4, 4, 4))
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
        # 커서가 비클라이언트(제목표시줄·창 테두리) 위면 요소 하강 없이 창 전체로 스냅한다
        # → Snipaste처럼 "상단바 위 = 제목표시줄 포함 창 통째로". 이 판정은 MSAA가 그 자리에서
        #   무엇을 짚든(캡션 띠·시스템 버튼·창 루트) 무관하게 구성상 항상 옳다. 요소 스냅은
        #   클라이언트 영역 안에서만(내용 위) 일어난다.
        cr = _client_rect_screen(_hwnd)
        in_client = cr is not None and cr.left <= px < cr.right and cr.top <= py < cr.bottom
        if not in_client:
            rect_phys = win_rect
        else:
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
        if self._select_only:
            # 자르지 않고 사각형만 넘긴다 — 오버레이를 먼저 닫아 녹화 프레임에 안 잡히게.
            self._close_all()
            self.region_selected.emit(QRect(gr))
            return
        pm = self._crop_global(gr, self._include_cursor)
        self._close_all()
        if pm is not None and not pm.isNull():
            # gr은 캡처한 위치(논리 전역) — 핀(Alt+F3)이 그 자리에 그대로 덮을 수 있게 함께 emit
            self.region_captured.emit(pm, QRect(gr))

    def _composite_cursor_best_effort(self, pm: QPixmap, screen: QScreen, local_rect: QRect) -> QPixmap:
        """pm(local_rect를 crop한 정지 캡처)에 얼려둔 진짜 커서를 합성. 실패하면 원본 pm 반환.

        gif_recorder.composite_cursor를 그대로 재사용(GDI DrawIconEx 합성, 모노크롬·ARGB 커서
        모두 정확)하되 self._frozen_cursor(오버레이가 뜨기 전에 sample_cursor()로 떠둔 실제
        커서)를 명시적으로 넘긴다 — 지금 이 순간을 라이브로 다시 재는 게 아니다(그러면 오버레이
        자신의 십자선이 찍힌다). 지연 임포트인 이유는 gif_recorder가 이미 이 모듈의
        _monitor_phys_origin을 가져다 쓰고 있어(video_recorder도 gif_recorder 경유), 모듈
        최상단에서 맞임포트하면 순환 임포트가 된다. 앱 시작 시 gif_recorder가 이미 로드돼
        있으므로 호출 시점 지연 임포트는 안전하다.
        """
        try:
            from pasteflow.gif_recorder import composite_cursor
            dpr = pm.devicePixelRatio()
            out = composite_cursor(pm.toImage(), screen, local_rect, cursor=self._frozen_cursor)
            result = QPixmap.fromImage(out)
            result.setDevicePixelRatio(dpr)
            return result
        except Exception:
            return pm

    def _crop_global(self, gr: QRect, include_cursor: bool = False) -> QPixmap | None:
        """논리 가상좌표 사각형을 캡처. 여러 모니터에 걸치면 각 화면 조각을 합성한다.

        단일 모니터면 그 화면 스크린샷에서 바로 crop. 다중 모니터면 가장 높은 DPR을
        타깃으로 빈 캔버스를 만들고 각 화면 조각을 제 위치에 그려 넣는다(배율 다른 화면의
        조각은 타깃 DPR로 스케일 — 기하학은 정확, 저DPI 조각은 약간 소프트).

        include_cursor=True면 실제 커서를 합성한다 — 단 **단일 모니터 캡처에 한정**
        (composite_cursor가 단일 screen·DPR 기준 좌표계라, 서로 다른 DPR의 모니터를
        섞어 만드는 크로스 모니터 캔버스에는 좌표계가 안 맞는다 — GIF/영상 녹화가 이미
        '선택이 시작된 단일 모니터 한정'인 것과 같은 한계를 그대로 물려받음).
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
            pm = ov.crop_local(local)
            if include_cursor and pm is not None and not pm.isNull():
                pm = self._composite_cursor_best_effort(pm, ov._screen, local)
            return pm

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
        self._uninstall_key_suppression_hook()
        for ov in self._overlays:
            try:
                ov.close()
                ov.deleteLater()
            except Exception:
                pass
        self._overlays = []

    # ── Space·ESC 훅 (억제 + 판정을 겸한다) ──────────────────────────────────────

    def _install_key_suppression_hook(self):
        """Space·ESC를 삼켜 배경 앱(크롬 등)에 안 새게 하고, **동시에 그 키의 판정도 여기서
        한다**(_on_kb_hook이 플래그를 세우고 _tick이 소비).

        ⚠ 억제와 GetAsyncKeyState 폴링은 **양립 불가**라 판정을 훅으로 옮긴 것이다 —
        훅이 삼킨 키는 비동기 키 상태 테이블에 반영되지 않아, 예전처럼 폴링으로 판정하면
        Space 토글·ESC 취소가 영영 안 걸린다(2026-08-04 실측: 훅은 keydown을 정상 수신했는데
        같은 순간 GetAsyncKeyState는 훅 콜백 안에서조차 False. 이 억제 훅을 넣은 직후
        "Space를 눌러도 OFF에서 안 바뀐다"는 사용자 리포트의 정체가 이것이었다).
        훅 설치가 실패하면 억제가 없으므로 그때만 _tick의 폴링 폴백이 유효해진다.
        """
        self._kb_hook_proc = _HOOKPROC(self._on_kb_hook)
        h_mod = _kernel32.GetModuleHandleW(None)
        self._kb_hook = _user32.SetWindowsHookExW(_WH_KEYBOARD_LL, self._kb_hook_proc, h_mod, 0)
        if not self._kb_hook:
            self._kb_hook = None  # 설치 실패 — 억제 없이 _tick 폴링 폴백으로 계속 동작(열화)

    def _uninstall_key_suppression_hook(self):
        """훅을 뗀다. **콜백 객체(_kb_hook_proc) 참조는 일부러 놓지 않는다**(방어적) — 언훅
        직후에도 이미 디스패치된 키 이벤트가 콜백을 한 번 더 부를 수 있고, 그 사이 파이썬이
        콜백을 수거하면 C가 해제된 함수를 호출하게 된다. 콜백 하나는 다음 캡처에서 새것으로
        대체될 뿐이라 남겨 둬도 누수가 아니다(이 레이스를 실제로 재현해 본 것은 아니며,
        ctypes 콜백 수명 관리의 표준 관행을 따른 것)."""
        if self._kb_hook:
            _user32.UnhookWindowsHookEx(self._kb_hook)
        self._kb_hook = None
        # self._kb_hook_proc는 위 이유로 클리어하지 않는다.

    def _on_kb_hook(self, nCode, wParam, lParam):
        """저수준 키보드 훅 콜백 — Space·ESC의 물리 keydown/keyup을 삼키고 그 판정도 남긴다.

        Space keydown → _space_toggle_pending(누른 동안 1회 — auto-repeat keydown이 연타
        토글하지 않게 _space_down으로 가드), ESC keydown → _cancel_pending.
        실제 처리는 _tick이 한다(훅 콜백 안에서 UI를 만지지 않는다 — 콜백은 최대한 짧게).

        ⚠ ctypes 콜백 안 예외가 C 레벨로 새면 프로세스가 죽으므로 반드시 전부 잡는다
        (paste_interceptor._low_level_keyboard_proc과 동일한 이유).
        주입된(SendInput 등) 키는 통과시킨다 — 우리가 만든 게 아닌 다른 자동화가 이 두 키를
        주입하는 경우까지 막을 이유는 없다.
        """
        try:
            if nCode == 0 and wParam in (_WM_KEYDOWN, _WM_SYSKEYDOWN, _WM_KEYUP, _WM_SYSKEYUP):
                kb = ctypes.cast(lParam, ctypes.POINTER(_KBDLLHOOKSTRUCT)).contents
                if not (kb.flags & _LLKHF_INJECTED) and kb.vkCode in (_VK_SPACE, _VK_ESCAPE):
                    is_down = wParam in (_WM_KEYDOWN, _WM_SYSKEYDOWN)
                    if kb.vkCode == _VK_ESCAPE:
                        if is_down:
                            self._cancel_pending = True
                    else:  # Space
                        if is_down:
                            if not self._space_down:
                                self._space_down = True
                                self._space_toggle_pending = True
                        else:
                            self._space_down = False
                    return 1
        except Exception:
            pass
        return _user32.CallNextHookEx(self._kb_hook, nCode, wParam, lParam)


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
