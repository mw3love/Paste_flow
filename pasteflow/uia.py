"""Windows UI Automation hit-test 헬퍼 — 커서 아래 UI 요소의 화면 사각형 조회.

`ElementFromPoint`로 커서 위치의 접근성 요소(크롬 탭·북마크·작업표시줄 아이콘 등 —
실제 HWND가 아니라 접근성 트리의 요소)를 짚어 그 BoundingRectangle을 반환한다.
마그네틱 캡처의 요소 하이라이트에 사용한다.

- 좌표는 모두 **물리 픽셀**(가상 데스크톱 기준)이다. UIA는 DPI-aware 프로세스에서
  물리 픽셀을 돌려주고, GetCursorPos도 물리 픽셀이라 입력·출력 좌표계가 일치한다.
  Qt 논리 좌표로의 변환은 호출부(capture_overlay)가 모니터 DPR로 처리한다.
- comtypes 기반(이미 설치). COM 초기화·CUIAutomation 인스턴스는 첫 호출 시 1회 생성해
  재사용한다. 반드시 동일 스레드(메인 Qt 스레드)에서만 호출할 것(STA 아파트먼트 일관성).
"""
from __future__ import annotations

import ctypes

from PyQt6.QtCore import QRect

_iuia = None
_C = None


def _ensure():
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


def is_available() -> bool:
    """UIA 조회가 가능한지(COM 생성 성공) 확인. 1회 시도 후 결과 캐시."""
    try:
        _ensure()
        return True
    except Exception:
        return False


def rect_at(x: int, y: int) -> QRect | None:
    """물리 픽셀 (x, y) 아래 UIA 요소의 화면 사각형(물리 픽셀 QRect). 없으면 None.

    빈 영역(바탕화면 등)이나 0×0 요소, UIA 미노출 창은 None을 반환해
    호출부가 자유 드래그 폴백으로 넘어가게 한다.
    """
    try:
        iuia = _ensure()
        el = iuia.ElementFromPoint(_C.tagPOINT(x, y))
        if not el:
            return None
        r = el.CurrentBoundingRectangle
        w, h = r.right - r.left, r.bottom - r.top
        if w <= 0 or h <= 0:
            return None
        return QRect(r.left, r.top, w, h)
    except Exception:
        return None
