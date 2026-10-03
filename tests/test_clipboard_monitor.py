"""ClipboardMonitor._compute_hash 회귀 테스트

snipaste 등으로 캡처한 이미지에 주석을 덮어 다시 복사하면, 크기가 같아 바이트
길이가 동일하고 변경 부분이 앞 4096바이트 밖(특히 DIB는 픽셀이 아래→위 저장이라
첫 바이트가 맨 아래 줄)이라, 옛 해시(앞 4096바이트 + 길이)가 충돌해 중복으로
오탐되던 버그의 재발 방지.
"""
from pasteflow.clipboard_monitor import ClipboardMonitor
from pasteflow.models import ClipboardItem


class TestComputeHashImage:
    def test_same_length_diff_after_4096_distinct_hash(self):
        """길이 동일 + 4096바이트 이후만 다른 이미지는 서로 다른 해시여야 한다"""
        monitor = ClipboardMonitor()
        head = b"\x00" * 4096
        original = ClipboardItem(content_type="image", image_data=head + b"A" * 1000)
        annotated = ClipboardItem(content_type="image", image_data=head + b"B" * 1000)

        assert len(original.image_data) == len(annotated.image_data)
        assert monitor._compute_hash(original) != monitor._compute_hash(annotated)

    def test_identical_image_same_hash(self):
        """동일 바이트는 같은 해시 (정상 중복 제거 유지)"""
        monitor = ClipboardMonitor()
        data = b"\x89PNG\r\n\x1a\n" + b"x" * 5000
        a = ClipboardItem(content_type="image", image_data=data)
        b = ClipboardItem(content_type="image", image_data=bytes(data))

        assert monitor._compute_hash(a) == monitor._compute_hash(b)


class _FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _monitor_with(texts, clock, monkeypatch):
    """_read_clipboard가 texts의 다음 값을 돌려주는 모니터 + 받은 항목 목록."""
    import pasteflow.clipboard_monitor as cm
    monkeypatch.setattr(cm.time, "monotonic", clock)
    got = []
    m = ClipboardMonitor(on_new_item=lambda it: got.append(it.text_content))
    queue = list(texts)
    m._read_clipboard = lambda: [ClipboardItem(content_type="text", text_content=queue.pop(0))]
    return m, got


class TestDuplicateWindow:
    def test_quick_repeat_is_skipped(self, monkeypatch):
        """같은 내용이 짧은 간격으로 두 번 오면(이벤트 중복·Ctrl+C 연타) 한 번만 받는다"""
        clock = _FakeClock()
        m, got = _monitor_with(["A", "A"], clock, monkeypatch)
        m._on_clipboard_changed()
        clock.t += 0.3
        m._on_clipboard_changed()
        assert got == ["A"]

    def test_recopy_after_window_is_new_item(self, monkeypatch):
        """한참 뒤 같은 내용을 다시 복사하면 새 복사로 받는다(새 순차 묶음의 첫 항목)"""
        clock = _FakeClock()
        m, got = _monitor_with(["A", "A"], clock, monkeypatch)
        m._on_clipboard_changed()
        clock.t += 5
        m._on_clipboard_changed()
        assert got == ["A", "A"]

    def test_recopy_of_self_written_item_is_new_item(self, monkeypatch):
        """PasteFlow가 붙여넣은(자체 쓰기) 내용을 나중에 사용자가 복사하면 받는다"""
        clock = _FakeClock()
        m, got = _monitor_with(["A"], clock, monkeypatch)
        m.mark_self_write(ClipboardItem(content_type="text", text_content="A"))
        clock.t += 5
        m._on_clipboard_changed()
        assert got == ["A"]

    def test_late_self_write_event_is_skipped(self, monkeypatch):
        """자체 쓰기의 늦은 이벤트(0.5초 시간창을 넘김)는 해시로 계속 걸러진다"""
        clock = _FakeClock()
        m, got = _monitor_with(["A"], clock, monkeypatch)
        m.mark_self_write(ClipboardItem(content_type="text", text_content="A"))
        clock.t += 1.0
        m._on_clipboard_changed()
        assert got == []
