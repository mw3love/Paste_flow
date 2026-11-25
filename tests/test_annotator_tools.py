"""주석 편집기 도구 표 시험 — 형광펜(9)·사라지는 펜(0)이 기존 도구 설정과 함께 산다.

계획: docs/plan/done/20261125-2040-라이브-화면-판서.md. QApplication 없이 클래스 값과 순수 메서드만 본다.
"""
import copy
import json
from types import SimpleNamespace

import pytest
from PyQt6.QtCore import Qt

from pasteflow.ui.image_annotator import _MARKER_ALPHA, _AnnotatorView, _EditorMixin, _TOOLS


@pytest.fixture
def restore_tool_defaults():
    # load_last_values가 클래스 값을 바꾸므로 다른 시험에 새지 않게 되돌린다
    saved = copy.deepcopy(_EditorMixin._tool_defaults)
    yield
    _EditorMixin._tool_defaults = saved


def test_old_saved_defaults_get_marker_and_fade(restore_tool_defaults):
    # 형광펜이 생기기 전 DB에 저장된 JSON(펜만 바꿔 둠) — 새 도구는 기본값으로 채워져야 한다
    old = json.dumps({"pen": {"color": "#007AFF", "width": 9}})
    _EditorMixin.load_last_values(old)
    d = _EditorMixin._tool_defaults
    assert d["pen"] == {"color": "#007AFF", "width": 9}
    assert d["marker"]["width"] == 18
    assert "color" in d["marker"] and "color" in d["fade"] and "width" in d["fade"]


def test_number_keys_9_and_0_are_new_tools():
    assert _AnnotatorView._SHORTCUTS[Qt.Key.Key_9] == "marker"
    assert _AnnotatorView._SHORTCUTS[Qt.Key.Key_0] == "fade"
    assert [k for k, _n, _s in _TOOLS][-2:] == ["marker", "fade"]


def test_fade_pen_is_off_unless_host_allows():
    from pasteflow.ui.live_draw import LiveDrawOverlay
    assert _EditorMixin._allow_fade_pen is False
    assert LiveDrawOverlay._allow_fade_pen is True


def test_set_tool_fade_ignored_in_preview_host():
    # 미리보기 편집기(허용 안 함)에서 키 0 → 도구가 그대로여야 한다
    host = SimpleNamespace(_allow_fade_pen=False, current_tool="pen")
    _EditorMixin.set_tool(host, "fade")
    assert host.current_tool == "pen"


def test_marker_pen_is_translucent_pen_is_not():
    host = SimpleNamespace(tool_defaults={"marker": {"color": "#FFCC00", "width": 18},
                                          "pen": {"color": "#FFCC00", "width": 4}})
    assert _EditorMixin.make_pen(host, "marker").color().alpha() == _MARKER_ALPHA
    assert _EditorMixin.make_pen(host, "pen").color().alpha() == 255
