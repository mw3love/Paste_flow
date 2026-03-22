"""ClipboardItem 모델 테스트"""
import pytest
from datetime import datetime
from pasteflow.models import ClipboardItem


class TestClipboardItemCreation:
    """ClipboardItem 생성 테스트"""

    def test_create_text_item(self):
        """텍스트 항목 생성"""
        item = ClipboardItem(
            content_type="text",
            text_content="안녕하세요",
        )
        assert item.content_type == "text"
        assert item.text_content == "안녕하세요"
        assert item.id is None
        assert item.image_data is None
        assert item.html_content is None
        assert item.rtf_content is None
        assert item.is_pinned is False
        assert item.pin_order == 0

    def test_create_image_item(self):
        """이미지 항목 생성"""
        fake_image = b"\x89PNG\r\n\x1a\n"
        item = ClipboardItem(
            content_type="image",
            image_data=fake_image,
        )
        assert item.content_type == "image"
        assert item.image_data == fake_image
        assert item.text_content is None

    def test_create_html_item(self):
        """HTML 항목 생성 (노션 등)"""
        item = ClipboardItem(
            content_type="html",
            text_content="안녕하세요",
            html_content="<p>안녕하세요</p>",
        )
        assert item.content_type == "html"
        assert item.html_content == "<p>안녕하세요</p>"
        assert item.text_content == "안녕하세요"

    def test_create_richtext_item(self):
        """RTF 항목 생성"""
        item = ClipboardItem(
            content_type="richtext",
            text_content="Bold text",
            rtf_content=r"{\rtf1 Bold text}",
        )
        assert item.content_type == "richtext"
        assert item.rtf_content == r"{\rtf1 Bold text}"

    def test_created_at_auto_set(self):
        """created_at이 자동으로 현재 시간 설정"""
        before = datetime.now()
        item = ClipboardItem(content_type="text", text_content="test")
        after = datetime.now()
        assert before <= item.created_at <= after


class TestPreviewText:
    """preview_text 자동 생성 테스트"""

    def test_preview_from_text_content(self):
        """text_content에서 preview_text 자동 생성"""
        item = ClipboardItem(
            content_type="text",
            text_content="이것은 미리보기 텍스트입니다",
        )
        assert item.preview_text == "이것은 미리보기 텍스트입니다"

    def test_preview_truncated_at_200_chars(self):
        """200자 초과 시 잘림"""
        long_text = "가" * 250
        item = ClipboardItem(content_type="text", text_content=long_text)
        assert len(item.preview_text) == 203  # 200 + "..."
        assert item.preview_text.endswith("...")

    def test_preview_for_image_item(self):
        """이미지 항목은 '[이미지]'"""
        item = ClipboardItem(content_type="image", image_data=b"\x89PNG")
        assert item.preview_text == "[이미지]"

    def test_preview_explicit_override(self):
        """명시적으로 preview_text 지정 시 그대로 사용"""
        item = ClipboardItem(
            content_type="text",
            text_content="원본 텍스트",
            preview_text="커스텀 미리보기",
        )
        assert item.preview_text == "커스텀 미리보기"

    def test_preview_strips_newlines(self):
        """줄바꿈은 공백으로 대체"""
        item = ClipboardItem(
            content_type="text",
            text_content="첫째줄\n둘째줄\n셋째줄",
        )
        assert "\n" not in item.preview_text
        assert item.preview_text == "첫째줄 둘째줄 셋째줄"


class TestContentTypeValidation:
    """content_type 유효성 검증"""

    def test_invalid_content_type_raises(self):
        """잘못된 content_type은 ValueError"""
        with pytest.raises(ValueError):
            ClipboardItem(content_type="video", text_content="test")

    def test_valid_content_types(self):
        """유효한 content_type 목록"""
        for ct in ["text", "image", "richtext", "html"]:
            item = ClipboardItem(content_type=ct, text_content="t" if ct != "image" else None, image_data=b"x" if ct == "image" else None)
            assert item.content_type == ct
