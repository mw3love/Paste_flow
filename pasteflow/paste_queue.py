"""순차 붙여넣기 큐 & 포인터 관리"""
from typing import Optional
from pasteflow.models import ClipboardItem


class PasteQueue:
    """FIFO 순차 붙여넣기 큐

    - add_item(): 새 항목 추가, 포인터 0으로 리셋
    - get_next(): 다음 항목 반환, 포인터 전진. 소진 시 None
    """

    def __init__(self):
        self._items: list[ClipboardItem] = []
        self.pointer: int = 0

    def add_item(self, item: ClipboardItem):
        """새 항목 추가

        - 아직 붙여넣기 안 했으면(pointer==0): 누적 (연속 복사 모드)
        - 붙여넣기 시작했으면(pointer>0): 큐 초기화 후 새 항목만 (새 세션)
        """
        if self.pointer > 0:
            self._items.clear()
        self._items.append(item)
        self.pointer = 0

    def get_next(self) -> Optional[ClipboardItem]:
        """다음 붙여넣기 항목 반환. 소진 시 None"""
        if self.pointer < len(self._items):
            item = self._items[self.pointer]
            self.pointer += 1
            return item
        return None

    def get_status(self) -> tuple[int, int]:
        """(현재 포인터, 전체 개수) 반환"""
        return self.pointer, len(self._items)

    def is_exhausted(self) -> bool:
        """큐가 소진되었는지 여부"""
        return self.pointer >= len(self._items)

    def get_items(self) -> list[ClipboardItem]:
        """큐의 모든 항목 반환"""
        return list(self._items)
