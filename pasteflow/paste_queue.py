"""순차 붙여넣기 큐 & 포인터 관리"""
import threading
import time
from typing import Optional
from pasteflow.models import ClipboardItem

# 마지막 복사로부터 이 시간(초) 이상 지난 새 복사는 큐 전체를 버리고 새 큐로 시작.
# 사용자가 잠시 다른 일을 한 뒤 새 작업을 시작할 때 이전 큐가 잔존하는 것을 방지.
DEFAULT_IDLE_RESET_SEC = 10.0


class PasteQueue:
    """FIFO 순차 붙여넣기 큐 (스레드 안전)

    두 가지 큐 초기화 트리거:
      A. mark_plain_paste(): 일반 Ctrl+V 발생 → 즉시 clear()
      B. idle timeout: 마지막 복사로부터 idle_reset_sec 초 이상 경과한 새 복사 →
         이전 큐를 버리고 새 항목 1개로 시작

    공개 메서드:
      - add_item(): 새 항목 추가 (idle 만료 또는 pointer>0이면 리셋 후 추가, 아니면 누적)
      - get_next(): 다음 항목 반환, 포인터 전진. 소진 시 None
      - mark_plain_paste(): 일반 Ctrl+V 알림 (paste_interceptor가 훅에서 호출)
      - set_idle_reset_sec(seconds): idle timeout 값 변경 (설정 다이얼로그에서 호출)
    """

    def __init__(self, idle_reset_sec: float = DEFAULT_IDLE_RESET_SEC):
        self._items: list[ClipboardItem] = []
        self.pointer: int = 0
        self._lock = threading.Lock()
        self._last_copy_time: float = 0.0
        self.idle_reset_sec: float = float(idle_reset_sec)

    def set_idle_reset_sec(self, seconds: float):
        """idle timeout 값 변경 (런타임 설정 변경 시 호출)"""
        with self._lock:
            self.idle_reset_sec = float(seconds)

    def _reset_unlocked(self):
        """lock을 이미 잡은 상태에서 큐를 비운다 (내부 헬퍼)"""
        self._items.clear()
        self.pointer = 0
        self._last_copy_time = 0.0

    def mark_plain_paste(self):
        """일반 Ctrl+V 발생 → 큐 즉시 비우기 (트리거 A)"""
        with self._lock:
            self._reset_unlocked()

    def add_item(self, item: ClipboardItem):
        """새 항목 추가

        리셋 조건:
          1) 순차 붙여넣기 진행 중(pointer>0) — 기존 PRD F2-6
          2) idle timeout 만료 (마지막 복사로부터 idle_reset_sec 초 경과)
        그 외에는 누적. 히스토리(DB)에는 모든 항목이 유지됨.
        """
        with self._lock:
            now = time.monotonic()
            idle_expired = (
                self._last_copy_time > 0.0
                and (now - self._last_copy_time) >= self.idle_reset_sec
            )
            if self.pointer > 0 or idle_expired:
                self._items.clear()
            self._items.append(item)
            self.pointer = 0
            self._last_copy_time = now

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
        """큐 및 포인터 초기화 (사용자 명시적 호출 — 패널 우클릭 큐 해제 등)"""
        with self._lock:
            self._reset_unlocked()

    def remove_item(self, item_id: int) -> bool:
        """id로 큐에서 항목 제거 (히스토리 삭제와 큐 상태 동기화용).

        큐는 캡처 시점의 ClipboardItem 스냅샷을 독립적으로 들고 있어 DB 삭제와
        자동으로 연동되지 않는다 — 이게 없으면 히스토리에서 지운 항목이 큐/진행
        HUD에는 계속 남아 "몇 개가 남았는지"가 어긋난다(2026-08-01 사용자 리포트:
        캡처 3개 중 1개를 히스토리에서 지웠는데 HUD는 여전히 3개로 표시).
        제거된 항목이 pointer 이전(이미 소진)이면 pointer도 함께 1 감소시켜
        정렬을 유지한다. 항목을 못 찾으면 False.
        """
        with self._lock:
            for i, it in enumerate(self._items):
                if it.id == item_id:
                    del self._items[i]
                    if i < self.pointer:
                        self.pointer -= 1
                    return True
            return False
