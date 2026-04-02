"""순차 붙여넣기 큐 & 포인터 관리"""
import threading
from typing import Optional
from pasteflow.models import ClipboardItem


class PasteQueue:
    """FIFO 순차 붙여넣기 큐 (스레드 안전)

    - add_item(): 새 항목 추가, 포인터 0으로 리셋
    - get_next(): 다음 항목 반환, 포인터 전진. 소진 시 None
    """

    def __init__(self):
        self._items: list[ClipboardItem] = []
        self.pointer: int = 0
        self._lock = threading.Lock()

    def add_item(self, item: ClipboardItem):
        """새 항목 추가

        PRD F2-6: 붙여넣기 진행 전(pointer==0)이면 누적,
        붙여넣기 진행 중(pointer>0)이면 큐 리셋 후 새 항목부터 시작.
        히스토리(DB)에는 모든 항목이 유지됨.
        """
        with self._lock:
            if self.pointer > 0:
                self._items.clear()
            self._items.append(item)
            self.pointer = 0

    def get_next(self) -> Optional[ClipboardItem]:
        """다음 붙여넣기 항목 반환. 소진 시 None"""
        with self._lock:
            if self.pointer < len(self._items):
                item = self._items[self.pointer]
                self.pointer += 1
                return item
            return None

    def get_status(self) -> tuple[int, int]:
        """(현재 포인터, 전체 개수) 반환"""
        with self._lock:
            return self.pointer, len(self._items)

    def is_exhausted(self) -> bool:
        """큐가 소진되었는지 여부"""
        with self._lock:
            return self.pointer >= len(self._items)

    def get_items(self) -> list[ClipboardItem]:
        """큐의 모든 항목 반환"""
        with self._lock:
            return list(self._items)

    def undo_last(self) -> Optional[ClipboardItem]:
        """순차 붙여넣기 1단계 되돌리기

        pointer를 1 감소시키고 해당 항목을 반환한다.
        pointer가 0이면 (되돌릴 것 없음) None 반환.
        """
        with self._lock:
            if self.pointer > 0:
                self.pointer -= 1
                return self._items[self.pointer]
            return None

    def set_queue(self, items: list[ClipboardItem], pointer: int = 0):
        """큐를 직접 설정 (패널에서 특정 항목부터 시작할 때 사용)"""
        with self._lock:
            self._items = list(items)
            self.pointer = pointer

    def clear(self):
        """큐 및 포인터 초기화"""
        with self._lock:
            self._items.clear()
            self.pointer = 0
