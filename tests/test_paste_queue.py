"""순차 붙여넣기 큐 테스트"""
import pytest
from pasteflow.paste_queue import PasteQueue
from pasteflow.models import ClipboardItem


def make_item(text: str) -> ClipboardItem:
    return ClipboardItem(content_type="text", text_content=text)


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

    def test_copy_after_paste_clears_queue(self):
        """붙여넣기 후 새 복사 → 큐 초기화, 새 항목만"""
        q = PasteQueue()
        q.add_item(make_item("A"))
        q.add_item(make_item("B"))
        q.get_next()  # A, pointer → 1
        q.add_item(make_item("D"))  # pointer>0 → 큐 초기화 → [D]
        assert q.get_next().text_content == "D"
        assert q.get_next() is None

    def test_copy_after_exhaust_clears_queue(self):
        """큐 소진 후 새 복사 → 큐 초기화, 새 항목만"""
        q = PasteQueue()
        q.add_item(make_item("A"))
        q.get_next()  # A, pointer → 1
        q.get_next()  # None, pointer 유지
        q.add_item(make_item("X"))  # pointer>0 → 큐 초기화 → [X]
        assert q.get_next().text_content == "X"

    def test_copy_after_clear_then_accumulate(self):
        """초기화 후 다시 연속 복사 가능"""
        q = PasteQueue()
        q.add_item(make_item("A"))
        q.get_next()  # pointer → 1
        q.add_item(make_item("X"))  # 초기화 → [X], pointer=0
        q.add_item(make_item("Y"))  # pointer==0 → 누적 → [X, Y]
        assert q.get_next().text_content == "X"
        assert q.get_next().text_content == "Y"


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
