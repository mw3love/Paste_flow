"""Database CRUD 테스트 — 인메모리 SQLite 사용"""
import pytest
from pasteflow.database import Database
from pasteflow.models import ClipboardItem


@pytest.fixture
def db():
    """인메모리 DB 인스턴스"""
    database = Database(":memory:")
    yield database
    database.close()


class TestSaveAndLoad:
    """항목 저장/조회 테스트"""

    def test_save_text_item(self, db):
        """텍스트 항목 저장 후 ID 반환"""
        item = ClipboardItem(content_type="text", text_content="안녕")
        saved = db.save_item(item)
        assert saved.id is not None
        assert saved.id > 0

    def test_load_item_by_id(self, db):
        """ID로 항목 조회"""
        item = ClipboardItem(content_type="text", text_content="테스트")
        saved = db.save_item(item)
        loaded = db.get_item(saved.id)
        assert loaded is not None
        assert loaded.text_content == "테스트"
        assert loaded.content_type == "text"

    def test_save_image_item(self, db):
        """이미지 항목 저장/조회"""
        img_data = b"\x89PNG_FAKE_DATA"
        item = ClipboardItem(content_type="image", image_data=img_data)
        saved = db.save_item(item)
        loaded = db.get_item(saved.id)
        assert loaded.image_data == img_data
        assert loaded.content_type == "image"

    def test_save_html_item(self, db):
        """HTML 항목 저장 (텍스트 + HTML + RTF 모두 보존)"""
        item = ClipboardItem(
            content_type="html",
            text_content="Bold",
            html_content="<b>Bold</b>",
            rtf_content=r"{\rtf1 Bold}",
        )
        saved = db.save_item(item)
        loaded = db.get_item(saved.id)
        assert loaded.text_content == "Bold"
        assert loaded.html_content == "<b>Bold</b>"
        assert loaded.rtf_content == r"{\rtf1 Bold}"

    def test_save_preserves_preview_and_thumbnail(self, db):
        """preview_text, thumbnail 저장 보존"""
        item = ClipboardItem(
            content_type="image",
            image_data=b"IMG",
            thumbnail=b"THUMB",
            preview_text="커스텀",
        )
        saved = db.save_item(item)
        loaded = db.get_item(saved.id)
        assert loaded.preview_text == "커스텀"
        assert loaded.thumbnail == b"THUMB"


class TestGetRecent:
    """최근 항목 목록 조회"""

    def test_get_recent_items_order(self, db):
        """최근 항목이 먼저 (최신순)"""
        db.save_item(ClipboardItem(content_type="text", text_content="첫째"))
        db.save_item(ClipboardItem(content_type="text", text_content="둘째"))
        db.save_item(ClipboardItem(content_type="text", text_content="셋째"))
        items = db.get_recent_items(limit=10)
        assert len(items) == 3
        assert items[0].text_content == "셋째"
        assert items[2].text_content == "첫째"

    def test_get_recent_items_limit(self, db):
        """limit 파라미터 동작"""
        for i in range(10):
            db.save_item(ClipboardItem(content_type="text", text_content=f"항목{i}"))
        items = db.get_recent_items(limit=5)
        assert len(items) == 5

    def test_get_recent_excludes_pinned(self, db):
        """고정 항목은 히스토리에서 제외"""
        db.save_item(ClipboardItem(content_type="text", text_content="일반"))
        pinned = db.save_item(ClipboardItem(content_type="text", text_content="고정"))
        db.pin_item(pinned.id)
        items = db.get_recent_items(limit=10)
        assert len(items) == 1
        assert items[0].text_content == "일반"


class TestFIFOLimit:
    """50개 FIFO 히스토리 제한"""

    def test_fifo_removes_oldest_non_pinned(self, db):
        """50개 초과 시 가장 오래된 비고정 항목 삭제"""
        for i in range(52):
            db.save_item(ClipboardItem(content_type="text", text_content=f"항목{i}"))
        items = db.get_recent_items(limit=100)
        assert len(items) == 50
        # 가장 오래된 항목0, 항목1이 삭제되었어야 함
        texts = [it.text_content for it in items]
        assert "항목0" not in texts
        assert "항목1" not in texts

    def test_fifo_does_not_remove_pinned(self, db):
        """고정 항목은 FIFO 삭제 대상에서 제외"""
        pinned = db.save_item(ClipboardItem(content_type="text", text_content="고정항목"))
        db.pin_item(pinned.id)
        for i in range(52):
            db.save_item(ClipboardItem(content_type="text", text_content=f"항목{i}"))
        # 고정 항목은 살아있어야 함
        loaded = db.get_item(pinned.id)
        assert loaded is not None
        assert loaded.text_content == "고정항목"


class TestPinFeature:
    """고정(Pin) 기능"""

    def test_pin_item(self, db):
        """항목 고정"""
        item = db.save_item(ClipboardItem(content_type="text", text_content="테스트"))
        db.pin_item(item.id)
        loaded = db.get_item(item.id)
        assert loaded.is_pinned is True

    def test_unpin_item(self, db):
        """항목 고정 해제"""
        item = db.save_item(ClipboardItem(content_type="text", text_content="테스트"))
        db.pin_item(item.id)
        db.unpin_item(item.id)
        loaded = db.get_item(item.id)
        assert loaded.is_pinned is False

    def test_get_pinned_items(self, db):
        """고정 항목 목록 조회"""
        db.save_item(ClipboardItem(content_type="text", text_content="일반"))
        p1 = db.save_item(ClipboardItem(content_type="text", text_content="고정1"))
        p2 = db.save_item(ClipboardItem(content_type="text", text_content="고정2"))
        db.pin_item(p1.id)
        db.pin_item(p2.id)
        pinned = db.get_pinned_items()
        assert len(pinned) == 2


class TestDeleteItem:
    """항목 삭제"""

    def test_delete_item(self, db):
        """항목 삭제 후 조회 시 None"""
        item = db.save_item(ClipboardItem(content_type="text", text_content="삭제대상"))
        db.delete_item(item.id)
        assert db.get_item(item.id) is None


class TestSettings:
    """설정 테이블 CRUD"""

    def test_save_and_get_setting(self, db):
        """설정 저장 및 조회"""
        db.set_setting("history_max", "50")
        assert db.get_setting("history_max") == "50"

    def test_get_setting_default(self, db):
        """존재하지 않는 설정은 기본값 반환"""
        assert db.get_setting("nonexistent", "default") == "default"

    def test_update_setting(self, db):
        """설정 값 업데이트"""
        db.set_setting("key", "old")
        db.set_setting("key", "new")
        assert db.get_setting("key") == "new"
