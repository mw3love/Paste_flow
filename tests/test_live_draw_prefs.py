"""판서를 켤 때 시작 도구 고르기 — 계획: docs/plan/20261125-2226-판서-조작-개선.md."""
import pytest

from pasteflow.app.live_draw import pick_start_tool


@pytest.mark.parametrize("saved, want", [
    ("marker", "marker"),
    ("ellipse", "ellipse"),     # 도형은 실제 모양 이름으로 기억
    ("fade", "fade"),
    (None, "pen"),              # 처음 실행 — 저장값 없음
    ("", "pen"),                # 도구 없음 상태로 닫음
    ("line", "pen"),            # 없어진 도구(선은 펜에 합침)
    ("shape", "pen"),           # 버튼 키는 도구 이름이 아님
])
def test_pick_start_tool(saved, want):
    assert pick_start_tool(saved) == want


def test_tool_names_match_editor():
    from pasteflow.app.live_draw import _LIVE_DRAW_TOOLS
    from pasteflow.ui.image_annotator import _SHAPE_KINDS, _TOOLS
    want = set()
    for k, _n, _s in _TOOLS:
        want |= set(_SHAPE_KINDS) if k == "shape" else {k}
    assert set(_LIVE_DRAW_TOOLS) == want
