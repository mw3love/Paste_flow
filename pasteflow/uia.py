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
_msaa_ready = False
_Acc = None
_oleacc = None


def _ensure_msaa():
    """oleacc(MSAA) 준비 — COM 초기화 + AccessibleObjectFromPoint 시그니처 1회 설정."""
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
