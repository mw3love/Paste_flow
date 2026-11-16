"""시작할 때 한 번 도는 DB 이전 6개 — pasteflow/main.py에서 글자 그대로 옮김(2026-11-16 폴더 구조 2편)."""

from pasteflow.app.secret_keys import _ORPHAN_KEYS, _SECRET_KEYS


def _migrate_secrets(db):
    """1회 마이그레이션 — _SECRET_KEYS 평문 → DPAPI 암호화 + 고아 키 purge. Idempotent.

    - 이미 암호화된 값(`enc:v1:`)은 `crypto.protect`가 그대로 두므로 재실행 안전.
    - 빈 값/없는 키는 no-op.
    - 고아 키는 SELECT 없이 DELETE — 없으면 0행 영향, 있으면 1행 정리.
    """
    from pasteflow.crypto import protect, is_protected

    for key in _SECRET_KEYS:
        cur = db.get_setting(key, "") or ""
        if cur and not is_protected(cur):
            db.set_setting(key, protect(cur))

    with db._lock:
        placeholders = ",".join("?" * len(_ORPHAN_KEYS))
        db.conn.execute(
            f"DELETE FROM settings WHERE key IN ({placeholders})",
            _ORPHAN_KEYS,
        )
        db.conn.commit()


def _migrate_split_ocr_ai_model(db):
    """1회 마이그레이션: OCR 모델 슬롯(`ocr_model_gateway`)을 기존 AI 모델값으로 초기화.

    v1.39.0에서 OCR과 AI 질의가 모델 설정을 나눠 가지게 됐다. 기존 사용자는 모델 하나만
    갖고 있으므로 그 값을 OCR 슬롯에도 복사해 **동작 변화 0에서 시작**한다(둘 다 예전 모델).
    이후 사용자가 설정창에서 OCR 모델만 싼 것으로 바꾸면 그때부터 갈린다.

    옛 키(`ocr_gemini_model_gateway`)는 AI 질의가 계속 쓰므로 삭제하지 않는다.
    새 키가 이미 있으면 건드리지 않아 idempotent.
    """
    src = db.get_setting("ocr_gemini_model_gateway", "") or ""
    if src and not db.get_setting("ocr_model_gateway", ""):
        db.set_setting("ocr_model_gateway", src)
        print(f"[Migrate] OCR 모델 슬롯 초기화: {src}")


# v1.6x에서 제거된 AI 질의 기능(우클릭 "AI에게 질문"·여러 모델 비교·구글 드라이브 연동)이
# 남긴 키들. ai_history 테이블의 실제 대화 데이터는 사용자 콘텐츠라 여기서 지우지 않는다
# (기능만 끈다 — 테이블은 그대로 남지만 어떤 코드도 더는 읽거나 쓰지 않는다).
_AI_QUERY_FEATURE_KEYS = (
    "ocr_gemini_model_gateway",   # 옛 "AI 모델 1" 슬롯 — ocr_model_gateway로 이미 승계됨
    "ai_compare_model_a",
    "ai_compare_model_b",
    "ai_system_prompt",
    "gdrive_client_id",
    "gdrive_client_secret",
    "gdrive_refresh_token",
)


def _migrate_drop_ai_query_feature(db):
    """1회 마이그레이션: AI 질의 기능 제거가 남긴 키 purge. Idempotent.

    ⚠ 순서 주의: `_migrate_split_ocr_ai_model`(ocr_gemini_model_gateway → ocr_model_gateway
    복사)보다 반드시 뒤에 와야 한다 — 먼저 지우면 아직 마이그레이션 안 된 사용자의 OCR
    모델이 통째로 빈칸이 된다.
    """
    with db._lock:
        placeholders = ",".join("?" * len(_AI_QUERY_FEATURE_KEYS))
        db.conn.execute(
            f"DELETE FROM settings WHERE key IN ({placeholders})",
            _AI_QUERY_FEATURE_KEYS,
        )
        db.conn.commit()


# v1.50.0에서 제거된 official(Google AI Studio) 백엔드가 DB에 남긴 키들.
# 크리덴셜·모델·캐시 + backend 선택값 전부. 한 번 지우면 어느 코드도 다시 쓰지 않는다.
_OFFICIAL_KEYS = (
    "ocr_gemini_api_key_official",
    "ocr_gemini_model_official",
    "ocr_gemini_model_cache_official",
    "ocr_model_official",
    "ocr_gemini_backend",
    "ocr_backend",
    "ai_compare_backend_a",
    "ai_compare_backend_b",
)


def _migrate_regen_thumbnails(db, make_thumbnail):
    """1회 마이그레이션(2026-11-14): 썸네일 상자를 80×60 → 512×144로 키우면서, 이미 쌓인
    이미지 항목의 썸네일을 원본에서 다시 만든다.

    패널이 그림을 비율대로 더 크게(높이 72px) 보여 주게 되어, 옛 80×60 썸네일을 그대로 두면
    늘어나 흐리다. 원본 이미지를 한꺼번에 메모리에 올리지 않게 항목을 하나씩 읽는다.
    끝나면 `thumbnail_regen_v2` 표시를 남겨 다시 돌지 않는다(idempotent).
    """
    if db.get_setting("thumbnail_regen_v2", "") == "1":
        return
    for summary in db.get_pinned_items_summary() + db.get_recent_items_summary():
        if summary.content_type != "image":
            continue
        full = db.get_item(summary.id)
        if full is None or not full.image_data:
            continue
        thumb = make_thumbnail(full.image_data)
        if thumb:
            db.update_thumbnail(summary.id, thumb)
    db.set_setting("thumbnail_regen_v2", "1")


def _migrate_path_paste_hotkeys(db):
    """1회 마이그레이션(2026-09-25): 한 번짜리 경로 붙여넣기 제거 + 경로 단축키 재배치.

    옛 배치: 한 번짜리 Ctrl+Shift+P / 순차 경로 Ctrl+Shift+[ / 전체 경로 Ctrl+Shift+].
    새 배치: 순차 경로(하나씩) Ctrl+Shift+P / 전체 경로 Ctrl+Shift+[ (] 없음) — 2×2 표에서
    「경로」 열이 P(Path)·[ 두 키가 된다(사용자 요청).
    설정창 [저장]은 모든 키를 DB에 쓰므로 기본값만 바꾸면 옛 키가 남는다 → 여기서 옮긴다.
    **판정 재료 = `hotkey_image_to_path` 행이 남아 있는가**(옛 배치로 저장된 적 있음). 옮긴 뒤
    그 행을 지우므로 두 번째 실행부터는 아무것도 안 한다(idempotent). 사용자가 바꿔 둔 키는
    옛 기본값과 같을 때만 옮기고 나머지는 존중한다.
    """
    with db._lock:
        row = db.conn.execute(
            "SELECT 1 FROM settings WHERE key='hotkey_image_to_path'").fetchone()
        if row is None:
            return
        cur = dict(db.conn.execute(
            "SELECT key, value FROM settings WHERE key IN "
            "('hotkey_seq_image_to_path','hotkey_bulk_path_paste')").fetchall())
        if cur.get("hotkey_seq_image_to_path", "ctrl+shift+[") == "ctrl+shift+[":
            db.conn.execute("INSERT OR REPLACE INTO settings(key, value) VALUES(?, ?)",
                            ("hotkey_seq_image_to_path", "ctrl+shift+p"))
        if cur.get("hotkey_bulk_path_paste", "ctrl+shift+]") == "ctrl+shift+]":
            db.conn.execute("INSERT OR REPLACE INTO settings(key, value) VALUES(?, ?)",
                            ("hotkey_bulk_path_paste", "ctrl+shift+["))
        db.conn.execute("DELETE FROM settings WHERE key='hotkey_image_to_path'")
        db.conn.commit()


def _migrate_drop_official_backend(db):
    """1회 마이그레이션: official(Google AI Studio) 백엔드 잔재 제거. Idempotent.

    v1.50.0에서 backend 개념 자체를 없앴다(게이트웨이 단일). 남은 official 키는 어느
    코드도 읽지 않으므로 지운다.

    ⚠ **비교 모델(2·3)이 official을 가리키고 있었다면 그 모델명도 함께 비운다.** 그냥 두면
    official 전용 모델명이 게이트웨이로 날아가 404가 나는데, 사용자는 "왜 안 되지"만 보게
    된다. 비우면 그 슬롯이 '(사용 안 함)'이 되어 설정창에서 다시 고르면 된다(조용한 실패
    대신 눈에 보이는 빈칸).

    backend 키를 지우고 나면 다음 실행에선 `slot_backend`가 "" 라 아무것도 안 지운다.
    """
    cleared = []
    for slot in ("a", "b"):
        slot_backend = db.get_setting(f"ai_compare_backend_{slot}", "") or ""
        model = (db.get_setting(f"ai_compare_model_{slot}", "") or "").strip()
        if slot_backend == "official" and model:
            db.set_setting(f"ai_compare_model_{slot}", "")
            cleared.append(f"{slot}={model}")

    with db._lock:
        placeholders = ",".join("?" * len(_OFFICIAL_KEYS))
        cur = db.conn.execute(
            f"DELETE FROM settings WHERE key IN ({placeholders})",
            _OFFICIAL_KEYS,
        )
        db.conn.commit()
        removed = cur.rowcount

    if removed > 0 or cleared:
        msg = f"[Migrate] official 백엔드 제거: 키 {removed}개 삭제"
        if cleared:
            msg += f", 비교 모델 초기화({', '.join(cleared)})"
        print(msg)
