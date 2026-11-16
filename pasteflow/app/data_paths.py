"""로컬 데이터 경로(DB·로그 위치, 레거시 DB 이전) — pasteflow/main.py에서 글자 그대로 옮김(2026-11-16 폴더 구조 2편)."""

import os


# ── 로컬 데이터 경로 (DB·로그) ────────────────────────────────────────────────


def _get_local_data_dir() -> str:
    """%LOCALAPPDATA%\\PasteFlow 폴더 경로. 없으면 생성.
    Drive에 있는 코드와 별개로 PC별 로컬 데이터(DB·로그·설정 캐시)를 보관한다.
    """
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~\\AppData\\Local")
    path = os.path.join(base, "PasteFlow")
    os.makedirs(path, exist_ok=True)
    return path


def _resolve_db_path() -> str:
    """DB 경로 결정. 로컬에 없고 Drive에 기존 DB가 있으면 1회 마이그레이션."""
    local_db = os.path.join(_get_local_data_dir(), "pasteflow.db")
    if not os.path.exists(local_db):
        legacy_db = os.path.join(os.path.dirname(__file__), "..", "pasteflow.db")
        if os.path.exists(legacy_db):
            try:
                import shutil
                shutil.copy2(legacy_db, local_db)
            except Exception as e:
                print(f"[DB] 레거시 DB 마이그레이션 실패: {e}")
    return local_db
