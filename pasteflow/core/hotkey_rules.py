"""키 판정 규칙 — 「이 키와 수식키 상태면 어떤 동작인가」만 정한다(Windows·Qt를 모른다).

저수준 키보드 훅(pasteflow/paste_interceptor.py `_low_level_keyboard_proc`)의 keydown 조건 사슬을
그대로 떼어 냈다 — 검사 순서가 곧 우선순위다. 실제로 하는 일(콜백·키 삼키기·가상키 주입·
전경 잠금 풀기), Alt·Win 추적, 연타 방지 시각 계산, keyup 처리는 훅에 남아 있다.
계획: docs/plan/done/20261116-폴더-구조-1편.md
"""
from dataclasses import dataclass
from typing import Mapping, Optional

VK_V = 0x56
VK_SNAPSHOT = 0x2C  # PrintScreen

# 사용자가 바꿀 수 있는 단축키 이름 — 검사 순서대로(Ctrl+Shift+V는 고정이라 따로 본다)
ORDER_BEFORE_PRINTSCREEN = (
    "panel", "ocr", "seq_image_to_path", "bulk_paste", "bulk_path_paste", "pin", "memo", "capture",
)
ORDER_AFTER_PRINTSCREEN = ("record", "stt")


@dataclass(frozen=True)
class Combo:
    """단축키 하나 — 가상키 번호와 Ctrl·Shift·Alt가 필요한가. vk 0은 「설정 안 됨」."""
    vk: int = 0
    ctrl: bool = False
    shift: bool = False
    alt: bool = False

    def matches(self, vk: int, ctrl: bool, shift: bool, alt: bool) -> bool:
        # 지금 훅과 같다: Ctrl·Shift·Alt 각각 필요 여부와 눌림이 같아야 하고, Win은 보지 않는다
        return bool(self.vk) and vk == self.vk and ctrl == self.ctrl and shift == self.shift and alt == self.alt


@dataclass(frozen=True)
class KeyDecision:
    """plain_paste: 평범한 Ctrl+V를 봤다(관찰만 — 키는 통과). action: 실행할 동작 이름 또는 None."""
    plain_paste: bool = False
    action: Optional[str] = None


def classify_keydown(vk: int, ctrl: bool, shift: bool, alt: bool, win: bool, injected: bool,
                     combos: Mapping[str, Combo], capture_via_printscreen: bool,
                     stt_active: bool, seq_paste_ready: bool) -> KeyDecision:
    """keydown 하나를 판정한다.

    - Ctrl+Shift+V(Alt 없음, Win 무관): seq_paste_ready면 "seq_paste"로 끝. 아니면(연타) 아래 규칙을 계속 본다.
    - 평범한 Ctrl+V(Shift·Alt 없음): 주입 키가 아니면 plain_paste=True로 표시하고 **아래 규칙을 계속** 본다.
    - 사용자 단축키는 검사 순서대로 첫 번째로 맞는 것. 주입 키 여부는 보지 않는다(지금 훅과 같다).
    - PrintScreen 대체 캡처: 설정이 켜져 있고 Ctrl·Shift·Alt·Win이 하나도 안 눌렸을 때만.
    - 음성 입력: 이미 녹음 중이면 "stt_repeat"(키 반복 — 삼키기만), 아니면 "stt_start".
    """
    if ctrl and shift and not alt and vk == VK_V and seq_paste_ready:
        return KeyDecision(action="seq_paste")

    plain = vk == VK_V and ctrl and not shift and not alt and not injected

    def first(names):
        for name in names:
            combo = combos.get(name)
            if combo is not None and combo.matches(vk, ctrl, shift, alt):
                return name
        return None

    action = first(ORDER_BEFORE_PRINTSCREEN)
    if action is None and capture_via_printscreen and vk == VK_SNAPSHOT \
            and not ctrl and not shift and not alt and not win:
        action = "capture_printscreen"
    if action is None:
        action = first(ORDER_AFTER_PRINTSCREEN)
        if action == "stt":
            action = "stt_repeat" if stt_active else "stt_start"
    return KeyDecision(plain_paste=plain, action=action)
