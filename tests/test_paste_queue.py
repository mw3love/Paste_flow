"""순차 붙여넣기 큐 테스트"""
import time
import pytest
from pasteflow.paste_queue import PasteQueue
from pasteflow.models import ClipboardItem


def make_item(text: str) -> ClipboardItem:
    return ClipboardItem(content_type="text", text_content=text)


def make_item_id(text: str, item_id: int) -> ClipboardItem:
    return ClipboardItem(content_type="text", text_content=text, id=item_id)


class TestQueueBasic:
    """큐 기본 동작"""

    def test_empty_queue_returns_none(self):
        """빈 큐에서 get_next → None"""
        q = PasteQueue()
        assert q.get_next() is None

    def test_add_and_get_next(self):
        """항목 추가 후 get_next로 순서대로 반환"""
        q = PasteQueue()
        q.add_item(make_item("A"))
        q.add_item(make_item("B"))
        q.add_item(make_item("C"))
        assert q.get_next().text_content == "A"
        assert q.get_next().text_content == "B"
        assert q.get_next().text_content == "C"

    def test_exhausted_queue_returns_none(self):
        """큐 소진 후 get_next → None"""
        q = PasteQueue()
        q.add_item(make_item("A"))
        q.get_next()  # A
        assert q.get_next() is None


class TestPointerReset:
    """포인터 리셋 동작"""

    def test_consecutive_copy_accumulates(self):
        """붙여넣기 전 연속 복사 → 누적"""
        q = PasteQueue()
        q.add_item(make_item("A"))
        q.add_item(make_item("B"))
        q.add_item(make_item("C"))
        assert q.get_next().text_content == "A"
        assert q.get_next().text_content == "B"
        assert q.get_next().text_content == "C"

    def test_copy_after_paste_resets_queue(self):
        """PRD F2-6: 붙여넣기 후 새 복사 → 큐 리셋, 새 항목부터 시작"""
        q = PasteQueue()
        q.add_item(make_item("A"))
        q.add_item(make_item("B"))
        q.get_next()  # A, pointer → 1
        q.add_item(make_item("D"))  # 큐 리셋: [D], pointer=0
        assert q.get_next().text_content == "D"
        assert q.get_next() is None

    def test_copy_after_exhaust_resets_queue(self):
        """PRD F2-6: 큐 소진 후 새 복사 → 큐 리셋, 새 항목부터 시작"""
        q = PasteQueue()
        q.add_item(make_item("A"))
        q.get_next()  # A, pointer → 1
        q.get_next()  # None, pointer 유지
        q.add_item(make_item("X"))  # 큐 리셋: [X], pointer=0
        assert q.get_next().text_content == "X"
        assert q.get_next() is None

    def test_copy_after_paste_then_accumulate(self):
        """붙여넣기 후 연속 복사 → 큐 리셋 + 이후 누적"""
        q = PasteQueue()
        q.add_item(make_item("A"))
        q.get_next()  # pointer → 1
        q.add_item(make_item("X"))  # 큐 리셋: [X], pointer=0
        q.add_item(make_item("Y"))  # 누적: [X, Y], pointer=0
        assert q.get_next().text_content == "X"
        assert q.get_next().text_content == "Y"
        assert q.get_next() is None


class TestQueueStatus:
    """큐 상태 조회"""

    def test_status_empty(self):
        """빈 큐 상태"""
        q = PasteQueue()
        pos, total = q.get_status()
        assert pos == 0
        assert total == 0

    def test_status_during_sequence(self):
        """순차 진행 중 상태"""
        q = PasteQueue()
        q.add_item(make_item("A"))
        q.add_item(make_item("B"))
        q.add_item(make_item("C"))
        pos, total = q.get_status()
        assert pos == 0
        assert total == 3
        q.get_next()  # A
        pos, total = q.get_status()
        assert pos == 1
        assert total == 3

    def test_status_exhausted(self):
        """큐 소진 후 상태"""
        q = PasteQueue()
        q.add_item(make_item("A"))
        q.get_next()
        pos, total = q.get_status()
        assert pos == 1
        assert total == 1

    def test_is_exhausted(self):
        """소진 여부 확인"""
        q = PasteQueue()
        assert q.is_exhausted() is True
        q.add_item(make_item("A"))
        assert q.is_exhausted() is False
        q.get_next()
        assert q.is_exhausted() is True


class TestGetItems:
    """큐 항목 목록 조회"""

    def test_get_items_returns_all(self):
        """모든 큐 항목 반환"""
        q = PasteQueue()
        q.add_item(make_item("A"))
        q.add_item(make_item("B"))
        items = q.get_items()
        assert len(items) == 2

    def test_get_current_pointer(self):
        """현재 포인터 위치 확인"""
        q = PasteQueue()
        q.add_item(make_item("A"))
        q.add_item(make_item("B"))
        assert q.pointer == 0
        q.get_next()
        assert q.pointer == 1


class TestUndoLast:
    """실수 복구: undo_last() 동작"""

    def test_undo_decrements_pointer_and_returns_item(self):
        """undo_last() → 포인터 1 감소, 해당 항목 반환"""
        q = PasteQueue()
        q.add_item(make_item("A"))
        q.add_item(make_item("B"))
        q.get_next()  # A, pointer → 1
        result = q.undo_last()
        assert result.text_content == "A"
        assert q.get_status() == (0, 2)

    def test_undo_at_zero_returns_none(self):
        """pointer가 0일 때 undo_last() → None (되돌릴 것 없음)"""
        q = PasteQueue()
        q.add_item(make_item("A"))
        assert q.undo_last() is None
        assert q.pointer == 0

    def test_undo_then_get_next_replays_item(self):
        """undo 후 get_next → 같은 항목 다시 반환"""
        q = PasteQueue()
        q.add_item(make_item("A"))
        q.add_item(make_item("B"))
        q.get_next()  # A
        q.undo_last()
        result = q.get_next()  # A again
        assert result.text_content == "A"

    def test_undo_multiple_steps(self):
        """여러 번 undo → 포인터 순차적으로 후퇴"""
        q = PasteQueue()
        q.add_item(make_item("A"))
        q.add_item(make_item("B"))
        q.add_item(make_item("C"))
        q.get_next()  # A, ptr=1
        q.get_next()  # B, ptr=2
        q.undo_last()  # ptr=1
        q.undo_last()  # ptr=0
        assert q.pointer == 0
        assert q.undo_last() is None  # 더 이상 되돌릴 수 없음


class TestSetQueue:
    """set_queue() — 패널 큐 선택 시작점 지정"""

    def test_set_queue_replaces_items(self):
        """set_queue 후 get_next가 새 항목 순서대로 반환"""
        q = PasteQueue()
        q.add_item(make_item("OLD"))
        q.set_queue([make_item("C"), make_item("D"), make_item("E")])
        assert q.get_next().text_content == "C"
        assert q.get_next().text_content == "D"
        assert q.get_next().text_content == "E"
        assert q.get_next() is None

    def test_set_queue_resets_pointer(self):
        """set_queue 호출 후 pointer는 0"""
        q = PasteQueue()
        q.add_item(make_item("A"))
        q.get_next()  # pointer → 1
        q.set_queue([make_item("X"), make_item("Y")])
        assert q.pointer == 0
        assert q.get_status() == (0, 2)

    def test_set_queue_with_pointer(self):
        """pointer 지정 시 해당 위치부터 get_next 시작"""
        q = PasteQueue()
        q.set_queue([make_item("A"), make_item("B"), make_item("C")], pointer=1)
        assert q.pointer == 1
        assert q.get_next().text_content == "B"
        assert q.get_next().text_content == "C"
        assert q.get_next() is None

    def test_set_queue_empty_list(self):
        """빈 리스트로 set_queue → is_exhausted True"""
        q = PasteQueue()
        q.add_item(make_item("A"))
        q.set_queue([])
        assert q.is_exhausted() is True
        assert q.get_next() is None


class TestPlainPasteClear:
    """트리거 A — 일반 Ctrl+V 감지 시 즉시 큐 비우기"""

    def test_mark_empties_queue_immediately(self):
        """mark_plain_paste 호출 즉시 큐 비어짐 (다음 복사를 기다리지 않음)"""
        q = PasteQueue()
        q.add_item(make_item("A"))
        q.add_item(make_item("B"))
        q.mark_plain_paste()
        assert q.get_status() == (0, 0)
        assert q.is_exhausted() is True
        assert q.get_next() is None

    def test_mark_then_new_copy_starts_fresh(self):
        """사용자 핵심 시나리오: a,b 복사 → b 일반paste → c,d 복사 → 순차 c,d"""
        q = PasteQueue()
        q.add_item(make_item("A"))
        q.add_item(make_item("B"))
        q.mark_plain_paste()           # 일반 paste → 큐 비움
        q.add_item(make_item("C"))     # 새 큐 시작
        q.add_item(make_item("D"))     # 누적
        assert q.get_status() == (0, 2)
        assert q.get_next().text_content == "C"
        assert q.get_next().text_content == "D"
        assert q.get_next() is None

    def test_mark_during_active_queue(self):
        """순차 진행 중(pointer>0)에 mark → 큐 즉시 비워짐"""
        q = PasteQueue()
        q.add_item(make_item("A"))
        q.add_item(make_item("B"))
        q.get_next()                   # pointer=1
        q.mark_plain_paste()
        assert q.get_status() == (0, 0)

    def test_mark_resets_idle_timer(self):
        """mark는 _last_copy_time도 0으로 → mark 직후 새 복사가 idle 만료로 또 리셋되지 않음"""
        q = PasteQueue(idle_reset_sec=10.0)
        q.add_item(make_item("A"))
        q.mark_plain_paste()
        q.add_item(make_item("B"))     # 시간 무관, 누적도 없음 → [B]
        q.add_item(make_item("C"))     # 짧은 간격 → 누적 → [B, C]
        assert q.get_status() == (0, 2)


class TestRemoveItem:
    """remove_item() — 히스토리 삭제와 큐 동기화 (2026-08-01 사용자 리포트 회귀 방지)"""

    def test_remove_unconsumed_item(self):
        """아직 안 꺼낸 항목 제거 → 남은 항목만 순서대로, pointer 불변"""
        q = PasteQueue()
        q.add_item(make_item_id("A", 1))
        q.add_item(make_item_id("B", 2))
        q.add_item(make_item_id("C", 3))
        assert q.remove_item(2) is True
        assert q.get_status() == (0, 2)
        assert q.get_next().text_content == "A"
        assert q.get_next().text_content == "C"

    def test_remove_already_consumed_item_decrements_pointer(self):
        """이미 소진된(pointer 이전) 항목 제거 → pointer도 함께 1 감소"""
        q = PasteQueue()
        q.add_item(make_item_id("A", 1))
        q.add_item(make_item_id("B", 2))
        q.add_item(make_item_id("C", 3))
        q.get_next()  # A, pointer=1
        assert q.remove_item(1) is True  # 이미 소진된 A 제거
        assert q.get_status() == (0, 2)  # pointer 1→0, total 3→2
        assert q.get_next().text_content == "B"
        assert q.get_next().text_content == "C"

    def test_remove_unknown_id_returns_false(self):
        """큐에 없는 id 제거 시도 → False, 큐 불변"""
        q = PasteQueue()
        q.add_item(make_item_id("A", 1))
        assert q.remove_item(999) is False
        assert q.get_status() == (0, 1)

    def test_remove_all_items_empties_queue(self):
        """캡처 3개 중 남은 1개까지 지우면 큐도 완전히 비워짐"""
        q = PasteQueue()
        q.add_item(make_item_id("A", 1))
        q.add_item(make_item_id("B", 2))
        q.add_item(make_item_id("C", 3))
        assert q.remove_item(1) is True
        assert q.remove_item(2) is True
        assert q.remove_item(3) is True
        assert q.get_status() == (0, 0)
        assert q.is_exhausted() is True


class TestIdleReset:
    """트리거 B — 마지막 복사로부터 idle_reset_sec 경과 시 다음 복사가 큐 첫 항목"""

    def test_idle_expired_drops_previous_queue(self):
        """사용자 핵심 시나리오: a 복사 → 시간 경과 → b,c 복사 → 순차 b,c"""
        q = PasteQueue(idle_reset_sec=0.05)
        q.add_item(make_item("A"))
        time.sleep(0.1)                # idle 만료
        q.add_item(make_item("B"))     # 이전 큐 버림 → [B]
        q.add_item(make_item("C"))     # 짧은 간격 → 누적 → [B, C]
        assert q.get_status() == (0, 2)
        assert q.get_next().text_content == "B"
        assert q.get_next().text_content == "C"
        assert q.get_next() is None

    def test_within_idle_keeps_accumulating(self):
        """idle 시간 내 연속 복사 → 누적 (회귀 방지)"""
        q = PasteQueue(idle_reset_sec=10.0)
        q.add_item(make_item("A"))
        q.add_item(make_item("B"))
        q.add_item(make_item("C"))
        assert q.get_status() == (0, 3)

    def test_idle_timer_resets_on_each_copy(self):
        """idle 타이머는 복사할 때마다 갱신 (간격이 짧으면 영원히 누적)"""
        q = PasteQueue(idle_reset_sec=0.1)
        q.add_item(make_item("A"))
        time.sleep(0.05)               # 아직 안 만료
        q.add_item(make_item("B"))     # 누적 → [A, B], 타이머 갱신
        time.sleep(0.05)               # 직전 복사로부터 0.05초 — 안 만료
        q.add_item(make_item("C"))     # 누적 → [A, B, C]
        assert q.get_status() == (0, 3)

    def test_set_idle_reset_sec_takes_effect(self):
        """set_idle_reset_sec로 런타임 변경 가능"""
        q = PasteQueue(idle_reset_sec=10.0)
        q.add_item(make_item("A"))
        q.set_idle_reset_sec(0.01)
        time.sleep(0.05)
        q.add_item(make_item("B"))     # 새 idle 적용 → 만료 → [B]
        assert q.get_status() == (0, 1)
        assert q.get_next().text_content == "B"

    def test_first_copy_never_resets(self):
        """첫 복사는 idle 검사 대상 아님 (이전 복사 없음)"""
        q = PasteQueue(idle_reset_sec=0.0)
        q.add_item(make_item("A"))     # _last_copy_time=0이라 idle_expired=False
        assert q.get_status() == (0, 1)
        assert q.get_next().text_content == "A"
