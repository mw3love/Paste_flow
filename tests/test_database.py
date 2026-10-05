"""Database CRUD 테스트 — 인메모리 SQLite 사용"""
import threading

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


class TestBumpToTop:
    """옛 히스토리 항목 복사 시 최상단 이동"""

    def test_bump_moves_item_to_top(self, db):
        """중간 항목을 bump하면 히스토리 최상단으로 이동"""
        db.save_item(ClipboardItem(content_type="text", text_content="첫째"))
        second = db.save_item(ClipboardItem(content_type="text", text_content="둘째"))
        db.save_item(ClipboardItem(content_type="text", text_content="셋째"))
        # 현재 최상단은 "셋째"
        db.bump_history_to_top(second.id)
        items = db.get_recent_items(limit=10)
        assert items[0].text_content == "둘째"
        assert len(items) == 3  # 중복 생성 없음

    def test_bump_top_item_is_noop(self, db):
        """이미 최상단인 항목을 bump해도 순서 불변"""
        db.save_item(ClipboardItem(content_type="text", text_content="첫째"))
        top = db.save_item(ClipboardItem(content_type="text", text_content="둘째"))
        db.bump_history_to_top(top.id)
        items = db.get_recent_items(limit=10)
        assert items[0].text_content == "둘째"

    def test_bump_ignores_pinned(self, db):
        """고정 항목은 bump 대상이 아님 (히스토리 순서 미변경)"""
        first = db.save_item(ClipboardItem(content_type="text", text_content="일반1"))
        pinned = db.save_item(ClipboardItem(content_type="text", text_content="고정"))
        db.save_item(ClipboardItem(content_type="text", text_content="일반2"))
        db.pin_item(pinned.id)
        db.bump_history_to_top(pinned.id)  # 고정 항목 — 무효여야 함
        items = db.get_recent_items(limit=10)
        texts = [it.text_content for it in items]
        assert texts == ["일반2", "일반1"]  # 고정 제외, 순서 그대로


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


class TestThreadSafety:
    """동시 읽기/쓰기 스레드 안전성 — 단일 커넥션을 여러 스레드가 공유한다.

    클립보드 모니터 스레드(save_item)와 메인 스레드(get_*) 가 동시에 같은
    sqlite3 커넥션을 쓰면, 읽기 경로가 잠금 없이 커서를 사용할 경우
    'bad parameter or other API misuse' / 'Recursive use of cursors' 류
    예외가 간헐적으로 발생한다. 잠금으로 직렬화되면 예외가 없어야 한다.
    """

    def test_concurrent_read_write_no_error(self):
        db = Database(":memory:")
        try:
            for i in range(20):
                db.save_item(ClipboardItem(content_type="text", text_content=f"seed{i}"))

            errors: list = []
            stop = threading.Event()

            def writer():
                try:
                    for i in range(400):
                        if stop.is_set():
                            return
                        db.save_item(
                            ClipboardItem(content_type="text", text_content=f"w{i}")
                        )
                except Exception as e:  # noqa: BLE001
                    errors.append(repr(e))
                    stop.set()

            def reader():
                try:
                    for _ in range(400):
                        if stop.is_set():
                            return
                        db.get_recent_items(limit=50)
                        db.get_recent_items_summary(limit=50)
                        db.get_pinned_items()
                        db.get_setting("history_max", "50")
                except Exception as e:  # noqa: BLE001
                    errors.append(repr(e))
                    stop.set()

            threads = (
                [threading.Thread(target=writer) for _ in range(2)]
                + [threading.Thread(target=reader) for _ in range(3)]
            )
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=30)

            assert not errors, f"동시 접근 중 예외 발생: {errors[:3]}"
        finally:
            db.close()


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


class TestCompactOnOpen:
    """지운 항목이 남긴 빈 공간을 열 때 되돌려받는다 (실측: 264MB 중 99%가 빈 공간)"""

    def test_reopen_shrinks_file_after_big_deletes(self, tmp_path):
        import os
        path = str(tmp_path / "t.db")
        d = Database(path)
        ids = [d.save_item(ClipboardItem(content_type="image",
                                         image_data=os.urandom(2_000_000))).id
               for _ in range(5)]
        for i in ids:
            d.delete_item(i)
        d.close()
        before = os.path.getsize(path)
        Database(path).close()
        assert os.path.getsize(path) < before / 2

    def test_small_free_space_is_left_alone(self, tmp_path):
        """빈 공간이 작으면 매번 압축하지 않는다(시작 시간 보호)"""
        import os
        path = str(tmp_path / "t.db")
        d = Database(path)
        item = d.save_item(ClipboardItem(content_type="text", text_content="x" * 50_000))
        d.delete_item(item.id)
        d.close()
        before = os.path.getsize(path)
        Database(path).close()
        assert os.path.getsize(path) == before


class TestMemo:
    """메모장(고정 섹션) — 새 메모 만들기·잠금·비우기"""

    def test_create_memo_is_pinned_text(self, db):
        """새 메모는 고정된 텍스트 항목으로 만들어진다"""
        memo = db.create_memo("첫 줄")
        loaded = db.get_item(memo.id)
        assert loaded.is_pinned
        assert loaded.content_type == "text"
        assert loaded.text_content == "첫 줄"

    def test_create_memo_goes_to_top(self, db):
        """새 메모는 메모장 맨 위에 생긴다"""
        old = db.create_memo("예전")
        new = db.create_memo("새것")
        ids = [it.id for it in db.get_pinned_items_summary()]
        assert ids == [new.id, old.id]

    def test_create_memo_does_not_touch_history(self, db):
        """메모를 만들어도 히스토리는 그대로"""
        db.save_item(ClipboardItem(content_type="text", text_content="복사한 것"))
        db.create_memo("메모")
        assert [it.text_content for it in db.get_recent_items()] == ["복사한 것"]

    def test_lock_and_unlock(self, db):
        """잠금을 켜고 끄면 요약 조회에도 반영된다"""
        memo = db.create_memo("x")
        db.set_locked(memo.id, True)
        assert db.get_pinned_items_summary()[0].is_locked
        db.set_locked(memo.id, False)
        assert not db.get_pinned_items_summary()[0].is_locked

    def test_lock_ignored_for_history_item(self, db):
        """히스토리 항목은 잠글 수 없다"""
        saved = db.save_item(ClipboardItem(content_type="text", text_content="h"))
        db.set_locked(saved.id, True)
        assert not db.get_item(saved.id).is_locked

    def test_locked_item_cannot_be_deleted(self, db):
        """잠긴 항목은 단일 삭제로 지워지지 않는다"""
        memo = db.create_memo("x")
        db.set_locked(memo.id, True)
        assert db.delete_item(memo.id) is False
        assert db.get_item(memo.id) is not None

    def test_unlocked_item_delete_returns_true(self, db):
        memo = db.create_memo("x")
        assert db.delete_item(memo.id) is True
        assert db.get_item(memo.id) is None

    def test_locked_item_cannot_be_unpinned(self, db):
        """잠긴 항목은 고정 해제(히스토리로 내리기)도 막힌다"""
        memo = db.create_memo("x")
        db.set_locked(memo.id, True)
        assert db.unpin_item(memo.id) is False
        assert db.get_item(memo.id).is_pinned

    def test_clear_memos_keeps_locked(self, db):
        """메모장 비우기는 잠긴 것만 남기고, 지운 id를 돌려준다"""
        a = db.create_memo("a")
        b = db.create_memo("b")
        db.set_locked(b.id, True)
        hist = db.save_item(ClipboardItem(content_type="text", text_content="h"))
        deleted = db.clear_memos()
        assert deleted == [a.id]
        assert [it.id for it in db.get_pinned_items_summary()] == [b.id]
        assert db.get_item(hist.id) is not None

    def test_count_unlocked_memos(self, db):
        db.create_memo("a")
        b = db.create_memo("b")
        db.set_locked(b.id, True)
        assert db.count_memos() == (1, 1)  # (지워질 것, 잠긴 것)

    def test_existing_pinned_items_locked_on_upgrade(self, tmp_path):
        """잠금 칸이 없던 옛 DB를 열면, 기존 고정 항목은 자동으로 잠긴다"""
        import sqlite3
        path = str(tmp_path / "old.db")
        conn = sqlite3.connect(path)
        conn.execute("""CREATE TABLE clipboard_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT, content_type TEXT NOT NULL,
            text_content TEXT, image_data BLOB, html_content TEXT, rtf_content TEXT,
            preview_text TEXT, thumbnail BLOB, created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            is_pinned BOOLEAN DEFAULT 0, pin_order INTEGER DEFAULT 0)""")
        conn.execute("INSERT INTO clipboard_items (content_type, text_content, is_pinned, pin_order)"
                     " VALUES ('text', '고정', 1, 1)")
        conn.execute("INSERT INTO clipboard_items (content_type, text_content) VALUES ('text', '히스토리')")
        conn.commit()
        conn.close()

        db = Database(path)
        try:
            pinned = db.get_pinned_items()
            assert len(pinned) == 1 and pinned[0].is_locked
            assert not db.get_recent_items()[0].is_locked
            # 두 번째로 열 때는 다시 잠그지 않는다(사용자가 푼 잠금 유지)
            db.set_locked(pinned[0].id, False)
        finally:
            db.close()
        db = Database(path)
        try:
            assert not db.get_pinned_items()[0].is_locked
        finally:
            db.close()
