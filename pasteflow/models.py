"""ClipboardItem 데이터 모델"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

VALID_CONTENT_TYPES = {"text", "image", "richtext", "html"}


@dataclass
class ClipboardItem:
    """클립보드 항목 데이터 모델"""

    content_type: str
    text_content: Optional[str] = None
    image_data: Optional[bytes] = None
    html_content: Optional[str] = None
    rtf_content: Optional[str] = None
    preview_text: Optional[str] = None
    thumbnail: Optional[bytes] = None
    id: Optional[int] = None
    created_at: datetime = field(default_factory=datetime.now)
    is_pinned: bool = False
    pin_order: int = 0
    extra_formats: Optional[dict] = None  # {format_id: bytes} — 앱 전용 포맷 보존

    def __post_init__(self):
        if self.content_type not in VALID_CONTENT_TYPES:
            raise ValueError(
                f"Invalid content_type: '{self.content_type}'. "
                f"Must be one of {VALID_CONTENT_TYPES}"
            )
        if self.preview_text is None:
            self.preview_text = self._generate_preview()

    def _generate_preview(self) -> str:
        """preview_text 자동 생성"""
        if self.content_type == "image":
            return "[이미지]"

        if self.text_content:
            text = self.text_content.replace("\n", " ")
            if len(text) > 200:
                return text[:200] + "..."
            return text

        return ""
