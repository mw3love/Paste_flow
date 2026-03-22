"""SQLite 데이터베이스 CRUD"""
import sqlite3
from typing import Optional
from pasteflow.models import ClipboardItem

DEFAULT_HISTORY_MAX = 50


class Database:
    """clipboard_items, settings 테이블 관리"""

    def __init__(self, db_path: str = "pasteflow.db"):
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self._create_tables()

    def _create_tables(self):
        cur = self.conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS clipboard_items (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                content_type TEXT NOT NULL,
                text_content TEXT,
                image_data   BLOB,
                html_content TEXT,
                rtf_content  TEXT,
                preview_text TEXT,
                thumbnail    BLOB,
                created_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
                is_pinned    BOOLEAN DEFAULT 0,
                pin_order    INTEGER DEFAULT 0
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        self.conn.commit()

    def save_item(self, item: ClipboardItem) -> ClipboardItem:
        """항목 저장 후 ID가 설정된 항목 반환"""
        cur = self.conn.cursor()
        cur.execute(
            """INSERT INTO clipboard_items
               (content_type, text_content, image_data, html_content,
                rtf_content, preview_text, thumbnail, is_pinned, pin_order)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                item.content_type,
                item.text_content,
                item.image_data,
                item.html_content,
                item.rtf_content,
                item.preview_text,
                item.thumbnail,
                item.is_pinned,
                item.pin_order,
            ),
        )
        self.conn.commit()
        item.id = cur.lastrowid
        self._enforce_fifo_limit()
        return item

    def get_item(self, item_id: int) -> Optional[ClipboardItem]:
        """ID로 항목 조회"""
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM clipboard_items WHERE id = ?", (item_id,))
        row = cur.fetchone()
        if row is None:
            return None
        return self._row_to_item(row)

    def get_recent_items(self, limit: int = 50) -> list[ClipboardItem]:
        """최근 비고정 항목 목록 (최신순)"""
        cur = self.conn.cursor()
        cur.execute(
            """SELECT * FROM clipboard_items
               WHERE is_pinned = 0
               ORDER BY id DESC
               LIMIT ?""",
            (limit,),
        )
        return [self._row_to_item(row) for row in cur.fetchall()]

    def get_pinned_items(self) -> list[ClipboardItem]:
        """고정 항목 목록 (pin_order순)"""
        cur = self.conn.cursor()
        cur.execute(
            """SELECT * FROM clipboard_items
               WHERE is_pinned = 1
               ORDER BY pin_order ASC"""
        )
        return [self._row_to_item(row) for row in cur.fetchall()]

    def delete_item(self, item_id: int):
        """항목 삭제"""
        cur = self.conn.cursor()
        cur.execute("DELETE FROM clipboard_items WHERE id = ?", (item_id,))
        self.conn.commit()

    def pin_item(self, item_id: int):
        """항목 고정 — pin_order는 현재 최대값+1"""
        cur = self.conn.cursor()
        cur.execute("SELECT COALESCE(MAX(pin_order), 0) FROM clipboard_items WHERE is_pinned = 1")
        max_order = cur.fetchone()[0]
        cur.execute(
            "UPDATE clipboard_items SET is_pinned = 1, pin_order = ? WHERE id = ?",
            (max_order + 1, item_id),
        )
        self.conn.commit()

    def unpin_item(self, item_id: int):
        """항목 고정 해제"""
        cur = self.conn.cursor()
        cur.execute(
            "UPDATE clipboard_items SET is_pinned = 0, pin_order = 0 WHERE id = ?",
            (item_id,),
        )
        self.conn.commit()

    def update_pin_orders(self, id_order_list: list[tuple[int, int]]):
        """고정 항목 순서 일괄 업데이트 — [(item_id, new_order), ...]"""
        cur = self.conn.cursor()
        cur.executemany(
            "UPDATE clipboard_items SET pin_order = ? WHERE id = ?",
            [(order, item_id) for item_id, order in id_order_list],
        )
        self.conn.commit()

    def _enforce_fifo_limit(self):
        """비고정 항목이 50개 초과 시 가장 오래된 것부터 삭제"""
        max_count = int(self.get_setting("history_max", str(DEFAULT_HISTORY_MAX)))
        cur = self.conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM clipboard_items WHERE is_pinned = 0"
        )
        count = cur.fetchone()[0]
        if count > max_count:
            excess = count - max_count
            cur.execute(
                """DELETE FROM clipboard_items
                   WHERE id IN (
                       SELECT id FROM clipboard_items
                       WHERE is_pinned = 0
                       ORDER BY created_at ASC
                       LIMIT ?
                   )""",
                (excess,),
            )
            self.conn.commit()

    # --- Settings ---

    def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """설정 값 조회"""
        cur = self.conn.cursor()
        cur.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = cur.fetchone()
        return row[0] if row else default

    def set_setting(self, key: str, value: str):
        """설정 값 저장/업데이트"""
        cur = self.conn.cursor()
        cur.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, value),
        )
        self.conn.commit()

    # --- Helpers ---

    def _row_to_item(self, row: sqlite3.Row) -> ClipboardItem:
        """DB Row → ClipboardItem 변환"""
        return ClipboardItem(
            id=row["id"],
            content_type=row["content_type"],
            text_content=row["text_content"],
            image_data=row["image_data"],
            html_content=row["html_content"],
            rtf_content=row["rtf_content"],
            preview_text=row["preview_text"],
            thumbnail=row["thumbnail"],
            created_at=row["created_at"],
            is_pinned=bool(row["is_pinned"]),
            pin_order=row["pin_order"],
        )

    def close(self):
        """DB 연결 종료"""
        self.conn.close()
