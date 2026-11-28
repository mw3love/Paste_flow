"""판서를 켤 때 시작 도구 고르기 — 계획: docs/plan/20261125-2226-판서-조작-개선.md."""
import pytest

from pasteflow.app.live_draw import pick_start_tool, pick_toolbar_pos


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


@pytest.mark.parametrize("stored, cur, want", [
    (None, (0, 0, 1920, 1080), "new"),                    # 숨긴 칠판 없음
    ((0, 0, 1920, 1080), (0, 0, 1920, 1080), "reuse"),    # 같은 모니터 그대로
    ((0, 0, 1920, 1080), (0, 0, 2560, 1440), "replace"),  # 해상도가 바뀜
    ((1920, 0, 1920, 1080), (-1920, 0, 1920, 1080), "replace"),  # 배치가 바뀜
])
def test_board_action(stored, cur, want):
    from pasteflow.app.live_draw import board_action
    assert board_action(stored, cur) == want


@pytest.mark.parametrize("visible, want", [
    (True, "hide"),    # 그리기든 앱 쓰기든 보이면 단축키 = 끄기
    (False, "open"),   # 보이는 판서가 없으면 커서 모니터에 켜기
])
def test_hotkey_action(visible, want):
    """계획: docs/plan/20261128-2049-판서-단축키-켜고-끄기.md."""
    from pasteflow.app.live_draw import hotkey_action
    assert hotkey_action(visible) == want


@pytest.mark.parametrize("saved, want", [
    ("", None),                     # 저장값 없음 → 기본 자리(아래 가운데)
    ("not json", None),             # 깨진 값
    ("[1, 2, 3]", None),            # 모양이 다름
    ('["a", 2]', None),
    ("[300, 40]", (300, 40)),       # 정상
    ("[1900, 1050]", (1500, 1000)), # 화면 밖 → 오른쪽·아래 끝 안으로
    ("[-50, -10]", (0, 0)),         # 왼쪽·위 밖
])
def test_pick_toolbar_pos(saved, want):
    # 툴바 400×80, 판서 창 1900×1080
    assert pick_toolbar_pos(saved, (400, 80), (1900, 1080)) == want
