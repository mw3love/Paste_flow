"""키 판정 규칙(pasteflow/core/hotkey_rules.py) 시험.

① 표 꼴 예제 — 동작마다 걸리는 경우·안 걸리는 경우.
② 옛 규칙과 대조 — 바꾸기 전 훅의 조건 사슬(paste_interceptor.py, 커밋 64e06bc 709~887줄)을
   시험 안에 그대로 옮겨 적고(_old_chain), 무작위 입력으로 새 함수와 결과가 같은지 본다.
"""
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from pasteflow.core.hotkey_rules import Combo, KeyDecision, VK_SNAPSHOT, VK_V, classify_keydown

VK_SPACE, VK_F2, VK_F3, VK_S, VK_P, VK_R, VK_G, VK_OEM3 = 0x20, 0x71, 0x72, 0x53, 0x50, 0x52, 0x47, 0xC0

COMBOS = {
    "panel": Combo(VK_SPACE, ctrl=True),
    "ocr": Combo(VK_S, ctrl=True, shift=True),
    "seq_image_to_path": Combo(VK_P, ctrl=True, shift=True),
    "bulk_paste": Combo(0x41, ctrl=True, shift=True),          # Ctrl+Shift+A
    "bulk_path_paste": Combo(0xDB, ctrl=True, shift=True),     # Ctrl+Shift+[
    "pin": Combo(VK_F3, alt=True),
    "memo": Combo(VK_OEM3, alt=True),
    "capture": Combo(VK_F2, alt=True),
    "record": Combo(VK_R, ctrl=True, shift=True),
    "stt": Combo(VK_G, alt=True),
}


def decide(vk, ctrl=False, shift=False, alt=False, win=False, injected=False, combos=COMBOS,
           printscreen=False, stt_active=False, ready=True):
    return classify_keydown(vk, ctrl, shift, alt, win, injected, combos, printscreen, stt_active, ready)


@pytest.mark.parametrize("kwargs, want", [
    (dict(vk=VK_V, ctrl=True, shift=True), KeyDecision(action="seq_paste")),
    (dict(vk=VK_V, ctrl=True, shift=True, win=True), KeyDecision(action="seq_paste")),   # Win 무관
    (dict(vk=VK_V, ctrl=True, shift=True, injected=True), KeyDecision(action="seq_paste")),  # 주입도(지금 훅과 같음)
    (dict(vk=VK_V, ctrl=True, shift=True, alt=True), KeyDecision()),                      # Alt면 아님
    (dict(vk=VK_V, ctrl=True, shift=True, ready=False), KeyDecision()),                   # 연타 → 아래 규칙(없음)
    (dict(vk=VK_V, ctrl=True), KeyDecision(plain_paste=True)),
    (dict(vk=VK_V, ctrl=True, injected=True), KeyDecision()),                             # 자기 주입 Ctrl+V는 관찰 안 함
    (dict(vk=VK_V, ctrl=True, alt=True), KeyDecision()),
    (dict(vk=VK_V), KeyDecision()),
    (dict(vk=VK_SPACE, ctrl=True), KeyDecision(action="panel")),
    (dict(vk=VK_SPACE, ctrl=True, win=True), KeyDecision(action="panel")),                # Win은 안 봄
    (dict(vk=VK_SPACE, ctrl=True, shift=True), KeyDecision()),                            # 여분 Shift면 아님
    (dict(vk=VK_SPACE), KeyDecision()),                                                   # Ctrl 빠지면 아님
    (dict(vk=VK_S, ctrl=True, shift=True), KeyDecision(action="ocr")),
    (dict(vk=VK_P, ctrl=True, shift=True), KeyDecision(action="seq_image_to_path")),
    (dict(vk=0x41, ctrl=True, shift=True), KeyDecision(action="bulk_paste")),
    (dict(vk=0xDB, ctrl=True, shift=True), KeyDecision(action="bulk_path_paste")),
    (dict(vk=VK_F3, alt=True), KeyDecision(action="pin")),
    (dict(vk=VK_OEM3, alt=True), KeyDecision(action="memo")),
    (dict(vk=VK_F2, alt=True), KeyDecision(action="capture")),
    (dict(vk=VK_F2, alt=True, injected=True), KeyDecision(action="capture")),
    (dict(vk=VK_R, ctrl=True, shift=True), KeyDecision(action="record")),
    (dict(vk=VK_G, alt=True), KeyDecision(action="stt_start")),
    (dict(vk=VK_G, alt=True, stt_active=True), KeyDecision(action="stt_repeat")),
    (dict(vk=VK_SNAPSHOT, printscreen=True), KeyDecision(action="capture_printscreen")),
    (dict(vk=VK_SNAPSHOT), KeyDecision()),                                                # 설정 꺼짐
    (dict(vk=VK_SNAPSHOT, printscreen=True, win=True), KeyDecision()),                    # Win+PrtScn은 OS 몫
    (dict(vk=VK_SNAPSHOT, printscreen=True, alt=True), KeyDecision()),
])
def test_table(kwargs, want):
    assert decide(**kwargs) == want


def test_unset_combo_never_matches():
    combos = dict(COMBOS, panel=Combo(0, ctrl=True))
    assert decide(0, ctrl=True, combos=combos) == KeyDecision()


def test_seq_paste_wins_over_user_combo_on_same_keys():
    combos = dict(COMBOS, panel=Combo(VK_V, ctrl=True, shift=True))
    assert decide(VK_V, ctrl=True, shift=True, combos=combos).action == "seq_paste"
    # 연타면 Ctrl+Shift+V 규칙을 건너뛰고 아래(패널)로 간다 — 지금 훅과 같다
    assert decide(VK_V, ctrl=True, shift=True, combos=combos, ready=False).action == "panel"


def test_plain_paste_observed_and_other_rule_also_runs():
    combos = dict(COMBOS, panel=Combo(VK_V, ctrl=True))
    assert decide(VK_V, ctrl=True, combos=combos) == KeyDecision(plain_paste=True, action="panel")


def test_earlier_rule_wins_when_two_combos_collide():
    combos = dict(COMBOS, capture=Combo(VK_F3, alt=True))  # 핀과 같은 조합
    assert decide(VK_F3, alt=True, combos=combos).action == "pin"


# ── ② 옛 규칙과 대조 ─────────────────────────────────────────────────────────

_OLD_FIELDS = [("panel", "panel"), ("ocr", "ocr"), ("seqimg2path", "seq_image_to_path"),
               ("bulk", "bulk_paste"), ("bulkpath", "bulk_path_paste"), ("pin", "pin"),
               ("memo", "memo"), ("capture", "capture")]


def _old_chain(vk, ctrl, shift, alt, win, injected, combos, printscreen, stt_active, ready):
    """바꾸기 전 훅의 keydown 조건 사슬을 순서 그대로 옮긴 것(부작용 대신 결과만 기록)."""
    plain = False
    if ctrl and shift and not alt:
        if vk == VK_V and ready:
            return KeyDecision(action="seq_paste")
    if vk == VK_V and ctrl and not shift and not alt:
        if not injected:
            plain = True
    for _field, name in _OLD_FIELDS:
        c = combos[name]
        if c.vk and vk == c.vk and ctrl == c.ctrl and shift == c.shift and alt == c.alt:
            return KeyDecision(plain_paste=plain, action=name)
    if printscreen and vk == VK_SNAPSHOT and not ctrl and not shift and not alt and not win:
        return KeyDecision(plain_paste=plain, action="capture_printscreen")
    c = combos["record"]
    if c.vk and vk == c.vk and ctrl == c.ctrl and shift == c.shift and alt == c.alt:
        return KeyDecision(plain_paste=plain, action="record")
    c = combos["stt"]
    if c.vk and vk == c.vk and ctrl == c.ctrl and shift == c.shift and alt == c.alt:
        return KeyDecision(plain_paste=plain, action="stt_repeat" if stt_active else "stt_start")
    return KeyDecision(plain_paste=plain)


_KEYS = st.sampled_from([VK_V, VK_SPACE, VK_F2, VK_F3, VK_S, VK_P, VK_R, VK_G, VK_OEM3, VK_SNAPSHOT, 0x41, 0xDB, 0])
_combo = st.builds(Combo, _KEYS, st.booleans(), st.booleans(), st.booleans())
_combos = st.fixed_dictionaries({name: _combo for name in COMBOS})


@settings(max_examples=1000, deadline=None)  # 커밋 직전 검사에서 돌므로 1,000가지
@given(vk=_KEYS, ctrl=st.booleans(), shift=st.booleans(), alt=st.booleans(), win=st.booleans(),
       injected=st.booleans(), combos=_combos, printscreen=st.booleans(), stt_active=st.booleans(),
       ready=st.booleans())
def test_same_as_old_chain(vk, ctrl, shift, alt, win, injected, combos, printscreen, stt_active, ready):
    args = (vk, ctrl, shift, alt, win, injected, combos, printscreen, stt_active, ready)
    assert classify_keydown(*args) == _old_chain(*args)


# ── 라이브 화면 판서(계획 docs/plan/done/20261125-2040-라이브-화면-판서.md) ───────────

def test_live_draw_combo():
    combos = dict(COMBOS, live_draw=Combo(0x44, ctrl=True, shift=True))  # Ctrl+Shift+D
    assert decide(0x44, ctrl=True, shift=True, combos=combos).action == "live_draw"
    assert decide(0x44, ctrl=True, combos=combos).action is None          # Shift 빠지면 아님
    assert decide(0x44, ctrl=True, shift=True).action is None             # 설정 표에 없으면 아님
