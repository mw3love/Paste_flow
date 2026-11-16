"""DPAPI로 암호화해 저장하는 시크릿 키 목록과 고아 키 목록 — pasteflow/main.py에서 글자 그대로 옮김(2026-11-16 폴더 구조 2편)."""


# ── 시크릿 처리 ──────────────────────────────────────────────────────────────
# DPAPI(crypto.py)로 암호화해 로컬 DB에 저장하는 키 화이트리스트.
# 읽기/쓰기 시 자동 복호화/암호화. 다른 PC 복호화 불가(설계상 — 동기화 폐지됨).
_SECRET_KEYS = frozenset({
    "ocr_gemini_api_key_gateway",
})

# 과거 빌드의 잔재로 DB에 남았으나 현재 코드 어디서도 참조하지 않는 고아 키.
# ocr_api_key·ocr_gemini_api_key는 평문 시크릿이라 P0(이미 노출), 나머지는 cruft 정리.
#
# ⚠ 아래 `ocr_gemini_*`(접미사 없는 옛 단일 키) 3종과 `ai_compare_model_*_{backend}` 4종은
# v1.50.0에서 삭제된 두 마이그레이션(`_migrate_split_gemini_keys`·`_migrate_compare_backend`)이
# 이전 후 지우던 것들이다. 그 마이그레이션이 사라진 지금 **아무도 안 지우므로** 여기서 purge한다.
# 특히 `ocr_gemini_api_key`는 암호화 이전 시절의 **평문 API 키**라 그냥 두면 DB에 영구히 남는다.
# (이미 마이그레이션된 DB에는 없는 키들이라 DELETE가 0행 — 무해.)
_ORPHAN_KEYS = (
    "ocr_api_key",
    "ocr_base_url",
    "hotkey_settings",
    "panel_always_on_top",
    # v1.39.0 이전의 단일 Gemini 키/모델/캐시 (backend별 슬롯으로 이전됐던 것)
    "ocr_gemini_api_key",
    "ocr_gemini_model",
    "ocr_gemini_model_cache",
    # v1.47.0 이전의 backend별 비교 모델 키 (평면 키로 이전됐던 것)
    "ai_compare_model_a_official",
    "ai_compare_model_a_gateway",
    "ai_compare_model_b_official",
    "ai_compare_model_b_gateway",
    # 2026-07-31 이전 주석 편집기 '전역' 마지막 값 3종 (annot_tool_defaults JSON 하나로 대체)
    "annot_last_width",
    "annot_last_font_size",
    "annot_last_badge_size",
    # 2026-09-25 제거된 Gemini 호출(Alt+1)·Gemini(캡처)(Alt+2) 단축키와 빠른 검색 타겟 목록
    "hotkey_ask_ai",
    "hotkey_capture_ask",
    "ai_palette_sites",
    # 2026-09-25 제거된 API 프로필(여러 크리덴셜 전환). ai_profiles는 JSON 안에 각 프로필의
    # api_key를 품은 DPAPI 암호문이라, 기능이 사라진 지금 남겨둘 이유가 없다.
    "ai_profiles",
    "ai_active_profile",
    # 2026-09-25 순차 핀(Alt+Shift+F3)을 핀(Alt+F3) 하나로 합치며 제거된 단축키
    "hotkey_seq_pin",
    # 2026-09-25 GIF(Ctrl+Shift+G)·영상(Ctrl+Shift+R) 녹화 단축키를 hotkey_record 하나로 합침
    "hotkey_record_gif",
    "hotkey_record_video",
)
