"""SQLite 데이터베이스 CRUD"""
import json
import base64
import sqlite3
import threading
from datetime import datetime
from typing import Optional
from pasteflow.models import ClipboardItem

DEFAULT_HISTORY_MAX = 50


class Database:
    """clipboard_items, settings 테이블 관리"""

    def __init__(self, db_path: str = "pasteflow.db"):
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
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
                pin_order    INTEGER DEFAULT 0,
                extra_formats TEXT
            )
        """)
        # 기존 DB에 extra_formats 컬럼이 없으면 추가
        try:
            cur.execute("ALTER TABLE clipboard_items ADD COLUMN extra_formats TEXT")
        except sqlite3.OperationalError:
            pass  # 이미 존재
        # 기존 DB에 history_order 컬럼이 없으면 추가 + 기존 항목 마이그레이션
        try:
            cur.execute("ALTER TABLE clipboard_items ADD COLUMN history_order INTEGER")
            self._migrate_history_order(cur)
        except sqlite3.OperationalError:
            pass  # 이미 존재
        cur.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        self.conn.commit()

    def save_item(self, item: ClipboardItem) -> ClipboardItem:
        """항목 저장 후 ID가 설정된 항목 반환"""
        extra_json = self._serialize_extra_formats(item.extra_formats)
        with self._lock:
            cur = self.conn.cursor()
            # 비고정 항목은 history_order = MIN - 1 로 맨 위에 배치
            if not item.is_pinned:
                cur.execute("SELECT MIN(history_order) FROM clipboard_items WHERE is_pinned = 0")
                min_order = cur.fetchone()[0]
                hist_order = 0 if min_order is None else min_order - 1
            else:
                hist_order = None
            cur.execute(
                """INSERT INTO clipboard_items
                   (content_type, text_content, image_data, html_content,
                    rtf_content, preview_text, thumbnail, is_pinned, pin_order,
                    extra_formats, history_order)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                    extra_json,
                    hist_order,
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
        """비고정 항목 목록 (history_order순, 전체 데이터)"""
        cur = self.conn.cursor()
        cur.execute(
            """SELECT * FROM clipboard_items
               WHERE is_pinned = 0
               ORDER BY history_order ASC
               LIMIT ?""",
            (limit,),
        )
        return [self._row_to_item(row) for row in cur.fetchall()]

    def get_recent_items_summary(self, limit: int = 50) -> list[ClipboardItem]:
        """비고정 항목 목록 (표시용 — image_data/extra_formats 제외)"""
        cur = self.conn.cursor()
        cur.execute(
            """SELECT id, content_type, text_content, NULL AS image_data,
                      html_content, rtf_content, preview_text, thumbnail,
                      created_at, is_pinned, pin_order, NULL AS extra_formats
               FROM clipboard_items
               WHERE is_pinned = 0
               ORDER BY history_order ASC
               LIMIT ?""",
            (limit,),
        )
        return [self._row_to_item(row) for row in cur.fetchall()]

    def get_pinned_items(self) -> list[ClipboardItem]:
        """고정 항목 목록 (pin_order순, 전체 데이터)"""
        cur = self.conn.cursor()
        cur.execute(
            """SELECT * FROM clipboard_items
               WHERE is_pinned = 1
               ORDER BY pin_order ASC"""
        )
        return [self._row_to_item(row) for row in cur.fetchall()]

    def get_pinned_items_summary(self) -> list[ClipboardItem]:
        """고정 항목 목록 (표시용 — image_data/extra_formats 제외)"""
        cur = self.conn.cursor()
        cur.execute(
            """SELECT id, content_type, text_content, NULL AS image_data,
                      html_content, rtf_content, preview_text, thumbnail,
                      created_at, is_pinned, pin_order, NULL AS extra_formats
               FROM clipboard_items
               WHERE is_pinned = 1
               ORDER BY pin_order ASC"""
        )
        return [self._row_to_item(row) for row in cur.fetchall()]

    def delete_item(self, item_id: int):
        """항목 삭제"""
        with self._lock:
            cur = self.conn.cursor()
            cur.execute("DELETE FROM clipboard_items WHERE id = ?", (item_id,))
            self.conn.commit()

    def pin_item(self, item_id: int):
        """항목 고정 — pin_order는 현재 최대값+1"""
        with self._lock:
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
        with self._lock:
            cur = self.conn.cursor()
            cur.execute(
                "UPDATE clipboard_items SET is_pinned = 0, pin_order = 0 WHERE id = ?",
                (item_id,),
            )
            self.conn.commit()

    def update_pin_orders(self, id_order_list: list[tuple[int, int]]):
        """고정 항목 순서 일괄 업데이트 — [(item_id, new_order), ...]"""
        with self._lock:
            cur = self.conn.cursor()
            cur.executemany(
                "UPDATE clipboard_items SET pin_order = ? WHERE id = ?",
                [(order, item_id) for item_id, order in id_order_list],
            )
            self.conn.commit()

    def update_history_orders(self, id_order_list: list[tuple[int, int]]):
        """비고정 항목 순서 일괄 업데이트 — [(item_id, new_order), ...]"""
        with self._lock:
            cur = self.conn.cursor()
            cur.executemany(
                "UPDATE clipboard_items SET history_order = ? WHERE id = ?",
                [(order, item_id) for item_id, order in id_order_list],
            )
            self.conn.commit()

    def update_item_text(self, item_id: int, new_text: str):
        """텍스트 내용 수정 — html/rtf/extra_formats 초기화"""
        preview = new_text.replace("\n", " ")
        if len(preview) > 200:
            preview = preview[:200] + "..."
        with self._lock:
            cur = self.conn.cursor()
            cur.execute(
                """UPDATE clipboard_items
                   SET text_content = ?, preview_text = ?,
                       html_content = NULL, rtf_content = NULL,
                       extra_formats = NULL
                   WHERE id = ?""",
                (new_text, preview, item_id),
            )
            self.conn.commit()

    def clear_history(self):
        """비고정 항목 전체 삭제"""
        with self._lock:
            cur = self.conn.cursor()
            cur.execute("DELETE FROM clipboard_items WHERE is_pinned = 0")
            self.conn.commit()

    def _enforce_fifo_limit(self):
        """비고정 항목이 50개 초과 시 가장 오래된 것부터 삭제"""
        max_count = int(self.get_setting("history_max", str(DEFAULT_HISTORY_MAX)))
        with self._lock:
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
                           ORDER BY id ASC
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
        with self._lock:
            cur = self.conn.cursor()
            cur.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                (key, value),
            )
            self.conn.commit()

    def _migrate_history_order(self, cur):
        """기존 비고정 항목에 history_order 할당 (id DESC → 0, 1, 2, ...)"""
        cur.execute(
            "SELECT id FROM clipboard_items WHERE is_pinned = 0 ORDER BY id DESC"
        )
        rows = cur.fetchall()
        for i, row in enumerate(rows):
            cur.execute(
                "UPDATE clipboard_items SET history_order = ? WHERE id = ?",
                (i, row[0]),
            )

    # --- Helpers ---

    def _row_to_item(self, row: sqlite3.Row) -> ClipboardItem:
        """DB Row → ClipboardItem 변환"""
        extra_json = row["extra_formats"] if "extra_formats" in row.keys() else None

        # created_at: DB 문자열 → datetime 변환
        created_at_raw = row["created_at"]
        if isinstance(created_at_raw, str):
            try:
                created_at = datetime.fromisoformat(created_at_raw)
            except (ValueError, TypeError):
                created_at = datetime.now()
        elif isinstance(created_at_raw, datetime):
            created_at = created_at_raw
        else:
            created_at = datetime.now()

        return ClipboardItem(
            id=row["id"],
            content_type=row["content_type"],
            text_content=row["text_content"],
            image_data=row["image_data"],
            html_content=row["html_content"],
            rtf_content=row["rtf_content"],
            preview_text=row["preview_text"],
            thumbnail=row["thumbnail"],
            created_at=created_at,
            is_pinned=bool(row["is_pinned"]),
            pin_order=row["pin_order"],
            extra_formats=self._deserialize_extra_formats(extra_json),
        )

    @staticmethod
    def _serialize_extra_formats(extra: Optional[dict]) -> Optional[str]:
        """extra_formats dict → JSON 문자열 (base64 인코딩)"""
        if not extra:
            return None
        data = {str(k): base64.b64encode(v).decode("ascii") for k, v in extra.items()}
        return json.dumps(data)

    @staticmethod
    def _deserialize_extra_formats(raw: Optional[str]) -> Optional[dict]:
        """JSON 문자열 → extra_formats dict"""
        if not raw:
            return None
        try:
            data = json.loads(raw)
            return {int(k): base64.b64decode(v) for k, v in data.items()}
        except Exception:
            return None

    def close(self):
        """DB 연결 종료"""
        self.conn.close()
