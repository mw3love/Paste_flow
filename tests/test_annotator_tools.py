"""주석 편집기 도구 표 시험 — 도구 번호·곧게(Shift·곧게 잠금)·직선 자석·도형 모양 기억·형광펜.

계획: docs/plan/done/20261125-2040-라이브-화면-판서.md(형광펜·사라지는 펜),
docs/plan/20261125-2225-주석-편집기-도구-정리.md(번호·곧게·도형). QApplication 없이
클래스 값과 순수 함수·메서드만 본다.
"""
import copy
import json
import math
from types import SimpleNamespace

import pytest
from PyQt6.QtCore import QLineF, QPointF, Qt
from PyQt6.QtGui import QColor, QPen

from pasteflow.ui.image_annotator import (
    _MARKER_ALPHA, _AnnotatorView, _EditorMixin, _LineItem, _TOOLS,
    _magnet_point, _straight_active,
)


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


def test_tool_numbers():
    # 1 선택 · 2 형광펜 · 3 펜 · 4 도형 · 5 화살표 · 6 텍스트 · 7 번호 · 8 사라지는 펜. 선 도구는 없다
    want = ["select", "marker", "pen", "shape", "arrow", "text", "badge", "fade"]
    assert [k for k, _n, _s in _TOOLS] == want
    assert [s for _k, _n, s in _TOOLS] == [str(i) for i in range(1, 9)]
    keys = [Qt.Key.Key_1, Qt.Key.Key_2, Qt.Key.Key_3, Qt.Key.Key_4,
            Qt.Key.Key_5, Qt.Key.Key_6, Qt.Key.Key_7, Qt.Key.Key_8]
    assert [_AnnotatorView._SHORTCUTS[k] for k in keys] == want
    assert Qt.Key.Key_9 not in _AnnotatorView._SHORTCUTS
    assert Qt.Key.Key_0 not in _AnnotatorView._SHORTCUTS


def test_straight_is_shift_xor_lock():
    assert _straight_active(False, False) is False
    assert _straight_active(True, False) is True
    assert _straight_active(False, True) is True
    assert _straight_active(True, True) is False   # 잠금 중 Shift = 그 순간만 자유


def _angle(a: QPointF, b: QPointF) -> float:
    return math.degrees(math.atan2(b.y() - a.y(), b.x() - a.x()))


def test_magnet_snaps_within_5_degrees():
    s = QPointF(0, 0)
    # 가로에서 4도 → 0도로 붙음, 길이는 유지
    cur = QPointF(100 * math.cos(math.radians(4)), 100 * math.sin(math.radians(4)))
    p = _magnet_point(s, cur)
    assert abs(_angle(s, p)) < 1e-6
    assert abs(math.hypot(p.x(), p.y()) - 100) < 1e-6
    # 41도 → 45도
    cur = QPointF(100 * math.cos(math.radians(41)), 100 * math.sin(math.radians(41)))
    assert abs(_angle(s, _magnet_point(s, cur)) - 45) < 1e-6
    # -92도 → -90도
    cur = QPointF(100 * math.cos(math.radians(-92)), 100 * math.sin(math.radians(-92)))
    assert abs(_angle(s, _magnet_point(s, cur)) + 90) < 1e-6


def test_magnet_keeps_free_angle_outside_5_degrees():
    s = QPointF(10, 10)
    cur = QPointF(10 + 100 * math.cos(math.radians(20)), 10 + 100 * math.sin(math.radians(20)))
    p = _magnet_point(s, cur)
    assert abs(p.x() - cur.x()) < 1e-9 and abs(p.y() - cur.y()) < 1e-9


@pytest.mark.parametrize("saved, want", [
    (None, "rect"),                                          # 저장값 없음
    ("{broken", "rect"),                                     # 깨진 JSON
    (json.dumps({"shape": {"kind": "triangle"}}), "rect"),   # 모르는 모양
    (json.dumps({"shape": {"kind": "ellipse"}}), "ellipse"),
])
def test_shape_kind_loaded(restore_tool_defaults, saved, want):
    _EditorMixin.load_last_values(saved)
    assert _EditorMixin._tool_defaults["shape"]["kind"] == want


def test_fade_pen_is_off_unless_host_allows():
    from pasteflow.ui.live_draw import LiveDrawOverlay
    assert _EditorMixin._allow_fade_pen is False
    assert LiveDrawOverlay._allow_fade_pen is True


def test_set_tool_fade_ignored_in_preview_host():
    # 미리보기 편집기(허용 안 함)에서 키 8 → 도구가 그대로여야 한다
    host = SimpleNamespace(_allow_fade_pen=False, current_tool="pen")
    _EditorMixin.set_tool(host, "fade")
    assert host.current_tool == "pen"


def test_marker_pen_is_translucent_pen_is_not():
    host = SimpleNamespace(tool_defaults={"marker": {"color": "#FFCC00", "width": 18},
                                          "pen": {"color": "#FFCC00", "width": 4}})
    assert _EditorMixin.make_pen(host, "marker").color().alpha() == _MARKER_ALPHA
    assert _EditorMixin.make_pen(host, "pen").color().alpha() == 255


def test_marker_line_keeps_alpha_and_kind():
    # 형광펜 직선 = 선 그림. 색을 바꿔도 반투명, 복제해도 어느 도구에서 왔는지 기억
    c = QColor("#FFCC00")
    c.setAlpha(_MARKER_ALPHA)
    it = _LineItem(QLineF(0, 0, 50, 0))
    it.setPen(QPen(c, 18))
    it.tool_kind = "marker"
    it.apply_color(QColor("#34C759"))
    assert it.pen().color().alpha() == _MARKER_ALPHA
    assert it.pen().color().name() == "#34c759"
    assert it.clone().tool_kind == "marker"
    assert _EditorMixin._tool_key_for_item(None, it) == "marker"
    plain = _LineItem(QLineF(0, 0, 5, 5))
    assert _EditorMixin._tool_key_for_item(None, plain) == "pen"   # 기억이 없으면 펜


def test_magnet_boundary_5_degrees():
    s = QPointF(0, 0)
    at5 = QPointF(100 * math.cos(math.radians(4.99)), 100 * math.sin(math.radians(4.99)))
    at6 = QPointF(100 * math.cos(math.radians(6)), 100 * math.sin(math.radians(6)))
    assert abs(_angle(s, _magnet_point(s, at5))) < 1e-6
    assert abs(_angle(s, _magnet_point(s, at6)) - 6) < 1e-6


class _FakeItem:
    def __init__(self, parent=None):
        self._parent = parent

    def parentItem(self):
        return self._parent


class _FakeScene:
    def __init__(self, items):
        self._items = list(items)

    def items(self):
        return list(self._items)

    def removeItem(self, it):
        self._items.remove(it)

    def addItem(self, it):
        self._items.append(it)


def _board_host(items):
    host = SimpleNamespace(_scene=_FakeScene(items), _undo=[], _last_move_key=None)
    host.push_undo_delete = lambda its: _EditorMixin.push_undo_delete(host, its)
    return host


def test_clear_board_removes_all_and_undo_restores():
    a, b = _FakeItem(), _FakeItem()
    child = _FakeItem(parent=a)           # 손잡이 같은 자식은 부모와 함께 움직이므로 따로 빼지 않는다
    host = _board_host([a, b, child])
    _EditorMixin.clear_board(host)
    assert host._scene.items() == [child]  # 가짜 씬이라 자식이 남지만, 최상위 둘만 뺐는지 본다
    assert host._undo == [("delete", [a, b])]
    _EditorMixin.undo(host)
    assert set(host._scene.items()) == {a, b, child}


def test_clear_board_on_empty_scene_records_nothing():
    host = _board_host([])
    _EditorMixin.clear_board(host)
    assert host._undo == []


def test_new_board_only_in_live_draw():
    from pasteflow.ui.live_draw import LiveDrawOverlay
    assert _EditorMixin._allow_new_board is False
    assert LiveDrawOverlay._allow_new_board is True


def test_clear_board_drops_fade_strokes_from_undo():
    keep, fade = _FakeItem(), _FakeItem()
    fade.is_fade = True                    # 사라지는 펜 획 — 되살리면 영구 획이 되므로 기록에서 뺀다
    host = _board_host([keep, fade])
    _EditorMixin.clear_board(host)
    assert host._scene.items() == []
    assert host._undo == [("delete", [keep])]


# ---- 도구 옵션창 다시 그리기 — docs/plan/20261125-2302-도구-옵션창-다시-그리기.md ----

from pasteflow.ui import image_annotator as _ia  # noqa: E402


@pytest.mark.parametrize("value, want", [
    (8, 14), (14, 14), (17, 14), (18, 22), (22, 22), (26, 22), (27, 32), (32, 32), (60, 32),
])
def test_nearest_head_step(value, want):
    assert _ia._HEAD_STEPS == (14, 22, 32)
    assert _ia._nearest_head_step(value) == want


@pytest.mark.parametrize("tool, field, label", [
    ("pen", "width", "두께"), ("marker", "width", "두께"), ("rect", "width", "두께"),
    ("ellipse", "width", "두께"), ("arrow", "width", "두께"), ("fade", "width", "두께"),
    ("text", "font", "크기"), ("badge", "size", "크기"),
])
def test_slider_spec_one_slider_per_tool(tool, field, label):
    f, lab, lo, hi = _ia._slider_spec(tool)
    assert (f, lab) == (field, label)
    assert lo < hi


@pytest.mark.parametrize("tool, cur, steps, want", [
    ("pen", 6, 1, 7), ("pen", 6, -1, 5), ("pen", 40, 1, 40), ("pen", 1, -1, 1),
    ("text", 200, 2, 200), ("text", 16, 1, 17), ("badge", 12, -1, 12), ("badge", 30, 3, 33),
])
def test_wheel_value_one_per_notch_clamped(tool, cur, steps, want):
    assert _ia._wheel_value(tool, cur, steps) == want


@pytest.mark.parametrize("key, active, want", [
    ("select", True, "none"), ("select", False, "tool"),
    ("pen", False, "tool"), ("pen", True, "popup"),
    ("shape", True, "popup"), ("arrow", True, "popup"), ("marker", False, "tool"),
])
def test_tool_click_action(key, active, want):
    assert _ia._tool_click_action(key, active) == want


@pytest.mark.parametrize("center, box_x, box_w, want", [
    (150, 100, 100, 50),    # 가운데
    (101, 100, 100, 14),    # 왼쪽 끝 — 안쪽 14px까지
    (199, 100, 100, 86),    # 오른쪽 끝
    (50, 100, 100, 14),     # 상자가 화면 끝에 밀려 버튼이 상자 밖
])
def test_tail_offset_points_at_button(center, box_x, box_w, want):
    assert _ia._tail_offset(center, box_x, box_w) == want


def test_old_width_dots_and_stroke_preview_removed():
    for name in ("_WIDTH_DOTS", "_MARKER_WIDTH_DOTS", "_StrokePreview", "_width_dot_icon"):
        assert not hasattr(_ia, name), name


class _FakeWheel:
    def __init__(self, dy):
        self._dy = dy

    def type(self):
        from PyQt6.QtCore import QEvent
        return QEvent.Type.Wheel

    def angleDelta(self):
        return SimpleNamespace(y=lambda: self._dy)


def test_wheel_filter_counts_120_per_notch_and_swallows():
    got = []
    f = _ia._ToolWheelFilter(lambda obj, steps: got.append(steps))
    assert f.eventFilter("btn", _FakeWheel(120)) is True          # 한 칸 → +1, 휠은 삼킨다
    assert [f.eventFilter("btn", _FakeWheel(40)) for _ in range(2)] == [True, True]
    assert got == [1]                                               # 터치패드 80 — 아직 한 칸 안 됨
    f.eventFilter("btn", _FakeWheel(40))                            # 모아서 120 → +1
    f.eventFilter("btn", _FakeWheel(-240))                          # 아래로 두 칸 → -2
    assert got == [1, 1, -2]


# ---- 툴바 다듬기(docs/plan/20261126-0030-주석-편집기-툴바-다듬기.md) ----------------

def _taps(*events):
    """("p"|"r"|"k", 시각) 사건열 → 두 번 누르기가 잡힌 횟수."""
    from pasteflow.ui.image_annotator import _ShiftDoubleTap
    d = _ShiftDoubleTap()
    hits = 0
    for kind, t in events:
        if kind == "p":
            d.press(t)
        elif kind == "r":
            hits += d.release(t)
        else:
            d.other_key()
    return hits


def test_shift_double_tap_quick():
    assert _taps(("p", 0.0), ("r", 0.1), ("p", 0.3), ("r", 0.4)) == 1


def test_shift_double_tap_too_slow_between():
    # 첫 번째를 뗀 뒤 0.4초가 지나 두 번째를 누름
    assert _taps(("p", 0.0), ("r", 0.1), ("p", 0.6), ("r", 0.7)) == 0


def test_shift_double_tap_long_hold_is_not_tap():
    # Shift+끌기처럼 오래 누름(0.3초 이상)은 두 번 누르기가 아니다
    assert _taps(("p", 0.0), ("r", 0.5), ("p", 0.6), ("r", 0.7)) == 0
    assert _taps(("p", 0.0), ("r", 0.1), ("p", 0.2), ("r", 0.6)) == 0


def test_shift_double_tap_other_key_between_breaks():
    assert _taps(("p", 0.0), ("r", 0.1), ("k", 0.15), ("p", 0.2), ("r", 0.3)) == 0
    # Shift를 누른 채 다른 키(한글 ㄲ 등)
    assert _taps(("p", 0.0), ("k", 0.05), ("r", 0.1), ("p", 0.2), ("r", 0.3)) == 0


def test_shift_double_tap_three_taps_count_once():
    # 두 번에 한 번 잡히고 셋째는 새로 시작 — 네 번이면 두 번 잡힌다(켜고 끄기)
    ev = [("p", 0.0), ("r", 0.05), ("p", 0.1), ("r", 0.15), ("p", 0.2), ("r", 0.25)]
    assert _taps(*ev) == 1
    assert _taps(*ev, ("p", 0.3), ("r", 0.35)) == 2


@pytest.mark.parametrize("shift, lock, want", [
    (False, False, False), (True, False, True), (False, True, True), (True, True, False),
])
def test_pen_icon_straight_follows_drawing_rule(shift, lock, want):
    from pasteflow.ui.image_annotator import _pen_icon_straight
    assert _pen_icon_straight(shift, lock) is want


@pytest.mark.parametrize("saved, want", [
    (None, False),
    (json.dumps({"arrow": {"head_at_end": False}}), False),      # 옛 저장값 — 칸 없음
    (json.dumps({"arrow": {"head_both": True}}), True),
    (json.dumps({"arrow": {"head_both": 0}}), False),
])
def test_arrow_head_both_loaded(restore_tool_defaults, saved, want):
    _EditorMixin.load_last_values(saved)
    assert _EditorMixin._tool_defaults["arrow"]["head_both"] is want


def test_arrow_clone_keeps_head_both():
    from pasteflow.ui.image_annotator import _ArrowItem
    a = _ArrowItem(QColor("#FF0000"), 4, True, 22, head_both=True)
    a.set_points(QPointF(0, 0), QPointF(100, 0))
    assert a.clone()._head_both is True
    a.set_head_both(False)
    assert a.clone()._head_both is False


def test_arrow_head_both_draws_two_heads():
    from pasteflow.ui.image_annotator import _ArrowItem
    a = _ArrowItem(QColor("#FF0000"), 4, True, 22)
    a.set_points(QPointF(0, 0), QPointF(100, 0))
    assert len(a._all_head_points()) == 1
    a.set_head_both(True)
    heads = a._all_head_points()
    assert len(heads) == 2
    tips = sorted((h[0].x(), h[0].y()) for h in heads)
    assert tips == [(0.0, 0.0), (100.0, 0.0)]
    # 머리 뒤쪽 꼭짓점은 선 안쪽(0과 100 사이)으로 — 바깥을 가리킨다
    for h in heads:
        for p in h[1:]:
            assert 0 < p.x() < 100


def test_only_pen_family_icons_turn_straight():
    from pasteflow.ui.image_annotator import _STRAIGHT_ICON_TOOLS
    assert set(_STRAIGHT_ICON_TOOLS) == {"pen", "marker", "fade"}   # 도형·화살표 아이콘은 그대로


@pytest.mark.parametrize("a_top, a_bot, pop_h, top, bot, want", [
    (900, 930, 200, 0, 1040, (698, False)),    # 위에 자리 있음 → 위, 꼬리 아래
    (40, 70, 200, 0, 1040, (72, True)),        # 위에 자리 없음 → 아래로 뒤집기, 꼬리 위
    (202, 230, 200, 0, 1040, (0, False)),      # 위에 딱 맞음 → 위
    (100, 900, 200, 0, 1040, (0, False)),      # 위·아래 둘 다 모자람 → 지금처럼 화면 안으로(위 기준)
])
def test_popup_y_flips_below_when_no_room_above(a_top, a_bot, pop_h, top, bot, want):
    assert _ia._popup_y(a_top, a_bot, pop_h, top, bot) == want
