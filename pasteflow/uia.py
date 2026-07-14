"""커서 아래 UI 요소의 화면 사각형 조회 — MSAA(우선) + UIA(폴백).

커서 위치의 접근성 요소(크롬 탭·북마크·작업표시줄 아이콘 등 — 실제 HWND가 아니라
접근성 트리의 요소)를 짚어 그 화면 사각형을 반환한다. 마그네틱 캡처의 요소 하이라이트용.

**MSAA(oleacc) 우선, UIA 폴백인 이유**: 크롬 최대화 북마크 바 중앙에서 UIA
`ElementFromPoint`는 개별 버튼 대신 거친 컨테이너 pane만 돌려주는 반면, MSAA
`AccessibleObjectFromPoint`는 개별 북마크를 정확한 사각형으로 돌려준다(실측 확정 —
Snipaste도 OLEACC를 사용). MSAA가 요소를 못 주는 창은 UIA로 폴백한다.

- 좌표는 모두 **물리 픽셀**(가상 데스크톱 기준). MSAA `accLocation`·UIA
  `BoundingRectangle` 모두 DPI-aware 프로세스에서 물리 픽셀이고 GetCursorPos도 물리
  픽셀이라 입력·출력 좌표계가 일치한다. Qt 논리 좌표 변환은 호출부(capture_overlay)가 처리.
- comtypes 기반(이미 설치). COM 인스턴스는 첫 호출 시 1회 생성해 재사용한다. 반드시
  동일 스레드(메인 Qt 스레드)에서만 호출할 것(STA 아파트먼트 일관성).
"""
from __future__ import annotations

import ctypes
from ctypes import POINTER, byref

from PyQt6.QtCore import QRect


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


# ── MSAA (oleacc) — 우선 경로 ────────────────────────────────────────────────
_OBJID_WINDOW = 0x00000000  # AccessibleObjectFromWindow 루트(창 전체) 오브젝트 ID
# ChildWindowFromPointEx 플래그: 숨김·비활성·입력투명 자식을 모두 건너뛴다.
# ⚠ SKIPDISABLED|SKIPTRANSPARENT가 없으면 크롬의 Chrome_RenderWidgetHostHWND·
# Intermediate D3D Window(실제 마우스 입력을 안 받는 합성용 자식)를 짚어, 그 창의 별개
# 접근성 트리에서 엉뚱한 사각형이 나온다(실측: 격자 48점 중 12점 불일치, 최악은 창 전체).
# 이 플래그를 주면 크롬은 최상위 창이 루트로 남아 옛 동작과 48/48 일치한다.
_CWP_SKIP = 0x0001 | 0x0002 | 0x0004  # SKIPINVISIBLE | SKIPDISABLED | SKIPTRANSPARENT
_msaa_ready = False
_Acc = None
_oleacc = None

_user32 = ctypes.windll.user32
_user32.ChildWindowFromPointEx.argtypes = [
    ctypes.c_void_p, _POINT, ctypes.c_uint]
_user32.ChildWindowFromPointEx.restype = ctypes.c_void_p
_user32.ScreenToClient.argtypes = [ctypes.c_void_p, POINTER(_POINT)]
_user32.ScreenToClient.restype = ctypes.c_int


def _ensure_msaa():
    """oleacc(MSAA) 준비 — COM 초기화 + AccessibleObjectFromPoint/FromWindow 시그니처 1회 설정."""
    global _msaa_ready, _Acc, _oleacc
    if _msaa_ready:
        return True
    import comtypes
    import comtypes.client
    import comtypes.automation  # noqa: F401  (VARIANT 사용)

    try:
        comtypes.CoInitialize()  # 메인 스레드 STA 보장 (이미 초기화면 무해)
    except Exception:
        pass
    comtypes.client.GetModule("oleacc.dll")
    from comtypes.gen import Accessibility as Acc

    _Acc = Acc
    _oleacc = ctypes.oledll.oleacc
    _oleacc.AccessibleObjectFromPoint.argtypes = [
        _POINT, POINTER(POINTER(Acc.IAccessible)), POINTER(comtypes.automation.VARIANT)]
    # 창-스코프 hit-test용: 점이 아니라 hwnd로 IAccessible 루트를 얻는다(오버레이를 안 짚음)
    _oleacc.AccessibleObjectFromWindow.argtypes = [
        ctypes.c_void_p, ctypes.c_uint,
        POINTER(comtypes.GUID), POINTER(POINTER(Acc.IAccessible))]
    _msaa_ready = True
    return True


def _rect_at_msaa(x: int, y: int) -> QRect | None:
    import comtypes.automation

    pacc = POINTER(_Acc.IAccessible)()
    var = comtypes.automation.VARIANT()
    _oleacc.AccessibleObjectFromPoint(_POINT(x, y), byref(pacc), byref(var))
    if not pacc:
        return None
    left, top, w, h = pacc.accLocation(var)  # 물리 픽셀
    if w <= 0 or h <= 0:
        return None
    return QRect(left, top, w, h)


def _deepest_child_at(hwnd: int, x: int, y: int) -> int:
    """창 hwnd의 자식 트리에서 물리 점 (x,y) 아래 최말단 자식 HWND. 없으면 hwnd 자신.

    hwnd의 *자식만* 뒤지므로 별개 최상위 창(입력 소유 오버레이)을 짚을 위험이 없다.
    """
    cur = hwnd
    for _ in range(20):  # 하강 깊이 가드
        pt = _POINT(x, y)
        _user32.ScreenToClient(ctypes.c_void_p(cur), byref(pt))
        child = _user32.ChildWindowFromPointEx(
            ctypes.c_void_p(cur), pt, _CWP_SKIP)
        if not child or int(child) == int(cur):
            break
        cur = int(child)
    return cur


def _rect_in_window_msaa(hwnd: int, x: int, y: int) -> QRect | None:
    """특정 창(hwnd) 안에서 물리 점 (x,y) 아래 최말단 요소의 사각형(물리). 없으면 None.

    점 기반 AccessibleObjectFromPoint가 최상위 오버레이(입력 소유)를 짚는 문제를 피하려고,
    hwnd로 IAccessible 루트를 얻은 뒤 accHitTest를 반복 하강해 커서 아래 최말단 요소를 찾는다
    (AccessibleObjectFromPoint가 내부적으로 하는 일을 대상 창에 한정해 재현).

    ⚠ 루트는 **점 아래 최말단 자식 HWND**로 잡는다(oleacc가 내부적으로 하는 WindowFromPoint에
    해당). 최상위 창부터 하강하면 리본·주소창 등 껍데기 계층을 전부 걸어 내려가는데 accHitTest
    한 단계가 크로스프로세스 COM 호출(~4ms)이라 탐색기에서 13단계 = 59ms가 걸렸다(실측). 자식
    HWND(DirectUIHWND)에서 시작하면 2단계 = 3ms로 같은 사각형이 나온다. 요소를 못 짚으면
    최상위 창 루트로 폴백해 품질은 그대로 둔다.
    """
    root = _deepest_child_at(hwnd, x, y)
    r = _hit_test_from(root, x, y)
    if r is None and root != hwnd:
        r = _hit_test_from(hwnd, x, y)  # 자식이 접근성 요소를 안 주는 창 → 기존 경로 폴백
    return r


def _hit_test_from(hwnd: int, x: int, y: int) -> QRect | None:
    """hwnd를 IAccessible 루트로 삼아 accHitTest를 반복 하강, 최말단 요소 사각형(물리)."""
    import comtypes.automation

    pacc = POINTER(_Acc.IAccessible)()
    _oleacc.AccessibleObjectFromWindow(
        ctypes.c_void_p(hwnd), _OBJID_WINDOW,
        byref(_Acc.IAccessible._iid_), byref(pacc))
    if not pacc:
        return None
    acc = pacc
    childid = 0  # CHILDID_SELF
    for _ in range(40):  # 하강 깊이 가드
        try:
            res = acc.accHitTest(x, y)
        except Exception:
            break
        if res is None:
            break
        if isinstance(res, int):
            childid = res  # 단순 자식(leaf) 또는 CHILDID_SELF(0) — 하강 종료
            break
        try:
            sub = res.QueryInterface(_Acc.IAccessible)  # VT_DISPATCH 자식 → 더 하강
        except Exception:
            break
        acc = sub
        childid = 0
    try:
        cv = comtypes.automation.VARIANT()
        cv.value = childid
        left, top, w, h = acc.accLocation(cv)  # 물리 픽셀
    except Exception:
        return None
    if w <= 0 or h <= 0:
        return None
    return QRect(left, top, w, h)


# ── UIA (UIAutomationCore) — 폴백 경로 ───────────────────────────────────────
_iuia = None
_C = None


def _ensure_uia():
    """CUIAutomation 인스턴스를 1회 생성해 반환. 실패 시 예외."""
    global _iuia, _C
    if _iuia is not None:
        return _iuia
    import comtypes
    import comtypes.client

    comtypes.client.GetModule("UIAutomationCore.dll")
    from comtypes.gen import UIAutomationClient as C

    _C = C
    _iuia = comtypes.client.CreateObject(C.CUIAutomation, interface=C.IUIAutomation)
    return _iuia


def _rect_at_uia(x: int, y: int) -> QRect | None:
    iuia = _ensure_uia()
    el = iuia.ElementFromPoint(_C.tagPOINT(x, y))
    if not el:
        return None
    r = el.CurrentBoundingRectangle
    w, h = r.right - r.left, r.bottom - r.top
    if w <= 0 or h <= 0:
        return None
    return QRect(r.left, r.top, w, h)


# ── 공개 API ─────────────────────────────────────────────────────────────────
def is_available() -> bool:
    """요소 조회가 가능한지(MSAA 또는 UIA COM 준비 성공) 확인."""
    try:
        if _ensure_msaa():
            return True
    except Exception:
        pass
    try:
        _ensure_uia()
        return True
    except Exception:
        return False


def rect_at(x: int, y: int) -> QRect | None:
    """물리 픽셀 (x, y) 아래 요소의 화면 사각형(물리 픽셀 QRect). 없으면 None.

    MSAA(개별 요소 granularity 우수)를 먼저 시도하고, 요소를 못 주면 UIA로 폴백한다.
    빈 영역(바탕화면 등)·0×0 요소·미노출 창은 None을 반환해 호출부가 자유 드래그
    폴백으로 넘어가게 한다.
    """
    try:
        if _ensure_msaa():
            r = _rect_at_msaa(x, y)
            if r is not None:
                return r
    except Exception:
        pass
    try:
        return _rect_at_uia(x, y)
    except Exception:
        return None


def rect_in_window_at(hwnd: int, x: int, y: int) -> QRect | None:
    """창 hwnd 안에서 물리 (x, y) 아래 최말단 요소 사각형(물리 QRect). 없으면 None.

    입력 소유(비클릭-통과) 오버레이 아래에서 요소 스냅을 하려면 점 기반 hit-test 대신
    창-스코프 hit-test가 필요하다(점 기반은 최상위 오버레이를 짚음). MSAA만 사용 —
    요소를 못 주면 None을 반환해 호출부가 창 전체 사각형으로 폴백하게 한다.
    """
    try:
        if _ensure_msaa():
            return _rect_in_window_msaa(hwnd, x, y)
    except Exception:
        return None
    return None
