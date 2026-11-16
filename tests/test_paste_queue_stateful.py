"""순차 붙여넣기 조합 시험 — 사건을 무작위 순서로 섞어 PasteQueue와 기준 모형을 같이 움직인다.

예제 시험(tests/test_paste_queue.py)이 못 본 조합을 찾는 것이 목적이다.
진짜 PasteQueue의 버그를 찾으면 고치지 않고 반례를 기록한다(계획: docs/plan/done/20261116-시험과-검사.md).
"""
from hypothesis import settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule

import pasteflow.paste_queue as pq
from pasteflow.models import ClipboardItem

IDLE = 10.0


class FakeClock:
    """pasteflow.paste_queue 모듈이 보는 `time` 이름만 바꾼다 — 전역 time은 그대로."""

    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now


class Model:
    """PasteQueue 규칙의 짧은 기준 모형(목록 + 포인터 + 마지막 복사 시각)."""

    def __init__(self):
        self.ids = []
        self.pointer = 0
        self.last = 0.0

    def add(self, item_id, now):
        idle = self.last > 0.0 and (now - self.last) >= IDLE
        if self.pointer > 0 or idle:
            self.ids = []
        self.ids.append(item_id)
        self.pointer = 0
        self.last = now

    def next(self):
        if self.pointer < len(self.ids):
            self.pointer += 1
            return self.ids[self.pointer - 1]
        return None

    def reset(self):
        self.ids, self.pointer, self.last = [], 0, 0.0

    def undo(self):
        if self.pointer > 0:
            self.pointer -= 1
            return self.ids[self.pointer]
        return None

    def set_queue(self, ids, pointer):
        self.ids, self.pointer = list(ids), pointer

    def remove(self, item_id):
        if item_id in self.ids:
            i = self.ids.index(item_id)
            del self.ids[i]
            if i < self.pointer:
                self.pointer -= 1
            return True
        return False


class PasteQueueMachine(RuleBasedStateMachine):
    def __init__(self):
        super().__init__()
        self.clock = FakeClock()
        self._real_time = pq.time
        pq.time = self.clock
        self.q = pq.PasteQueue(idle_reset_sec=IDLE)
        self.m = Model()
        self.next_id = 1

    def teardown(self):
        pq.time = self._real_time

    def _item(self, item_id):
        return ClipboardItem(content_type="text", text_content=f"t{item_id}", id=item_id)

    @rule()
    def copy(self):
        i = self.next_id
        self.next_id += 1
        self.q.add_item(self._item(i))
        self.m.add(i, self.clock.now)

    @rule()
    def seq_paste(self):
        got = self.q.get_next()
        want = self.m.next()
        assert (got.id if got else None) == want

    @rule()
    def plain_paste(self):
        self.q.mark_plain_paste()
        self.m.reset()

    @rule()
    def clear(self):
        self.q.clear()
        self.m.reset()

    @rule()
    def undo(self):
        got = self.q.undo_last()
        want = self.m.undo()
        assert (got.id if got else None) == want

    @rule(data=st.data())
    def set_queue(self, data):
        n = data.draw(st.integers(min_value=0, max_value=4))
        ids = []
        for _ in range(n):
            ids.append(self.next_id)
            self.next_id += 1
        pointer = data.draw(st.integers(min_value=0, max_value=n))
        self.q.set_queue([self._item(i) for i in ids], pointer)
        self.m.set_queue(ids, pointer)

    @rule(data=st.data())
    def remove(self, data):
        pool = list(self.m.ids) + [999999]  # 줄에 있는 id와 없는 id
        item_id = data.draw(st.sampled_from(pool))
        assert self.q.remove_item(item_id) == self.m.remove(item_id)

    @rule(dt=st.sampled_from([0.0, 5.0, 10.0, 11.0]))
    def time_passes(self, dt):
        self.clock.now += dt

    @invariant()
    def same_as_model(self):
        pointer, total = self.q.get_status()
        assert pointer == self.m.pointer
        assert [it.id for it in self.q.get_items()] == self.m.ids
        assert 0 <= pointer <= total
        assert self.q.is_exhausted() == (pointer >= total)


# 커밋 직전 검사에서 돌므로 짧게(100가지 → 50가지, 약 0.5초)
PasteQueueMachine.TestCase.settings = settings(deadline=None, max_examples=50)
TestPasteQueueStateful = PasteQueueMachine.TestCase
