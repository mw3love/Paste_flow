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
