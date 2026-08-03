"""AI 팔레트 — 자유질문창(Alt+`)의 질문을 여러 목적지로 라우팅한다.

Raycast/PowerToys Run처럼 입력한 텍스트를 "어디로 보낼지" 여러 타겟(구글 AI 모드·
사용자가 추가한 웹사이트) 중 하나로 보낸다. 목적지 목록은 설정 DB에 JSON으로 저장돼
사용자가 직접 추가·삭제·순서 변경할 수 있다(설정창 "AI 팔레트 타겟" 그룹,
`ui/settings_dialog.py`). 새 외부 의존성 없음 — stdlib `json`/`urllib.parse`만
사용(프로젝트 "새 외부 의존성 0" 원칙).

각 타겟은 `{"label": str, "keyword": str, "kind": str, "url": str}` — `kind`가
`KIND_GOOGLE_AI`면 실제 실행은 main.py의 기존 배관(web_open.py의 구글 AI 검색·
클립보드 주입)이 담당하고 `url`은 쓰이지 않는다. `KIND_URL`만 이 모듈의 `build_url()`로
`{q}` 자리에 질의를 채워 넣는다.

⚠ v1.6x에서 드라이브·ChatGPT/Claude/Gemini(주입)·PasteFlow 자체 답변(API) 타겟을
전부 제거했다 — 실사용 결과 Google AI 텍스트검색만 견고했고(2026-07-14 실측), 나머지는
크롬 즐겨찾기로 충분해 과도했다(사용자 판단, 2026-07-29). API 키가 필요한 질의 기능
자체(우클릭 "AI에게 질문"·비교·기록·드라이브 연동)도 함께 제거돼, 이제 API 키는
OCR 전용이다.
"""
from __future__ import annotations

import json
from urllib.parse import quote_plus

KIND_URL = "url"
KIND_GOOGLE_AI = "google_ai"

# 첫 실행(저장된 값이 없을 때) 기본 타겟 — 순서가 곧 팔레트 번호(질문창 Tab 순환 순서).
DEFAULT_SITES: list[dict] = [
    {"label": "Google AI", "keyword": "g", "kind": KIND_GOOGLE_AI, "url": ""},
]


def load_sites(raw_json: str) -> list[dict]:
    """저장된 JSON을 파싱한다. 비어있거나 깨졌으면 기본 목록으로 폴백한다."""
    if raw_json:
        try:
            data = json.loads(raw_json)
            if isinstance(data, list) and data:
                return [dict(s) for s in data if isinstance(s, dict)]
        except Exception:
            pass
    return [dict(s) for s in DEFAULT_SITES]


def ensure_google_ai(sites: list[dict]) -> list[dict]:
    """정확히 하나의 Google AI 타겟을 보장한다.

    2026-07-29 설정창 개편(종류 선택 드롭다운 제거)으로 Google AI는 항상 정확히 1개인
    **고정 타겟**이 됐다. 옛 설정에 저장된 목록은 이 불변식을 보장하지 않을 수 있어
    (드롭다운이 있던 시절 여러 개 추가했거나, 전부 지웠을 수 있음) 로드 시점에
    정규화한다: 없으면 맨 앞에 기본값을 추가하고, 여럿이면 첫 번째만 남긴다(멱등 —
    이미 정확히 1개면 그대로).
    """
    result = []
    seen_google = False
    for site in sites:
        if site.get("kind") == KIND_GOOGLE_AI:
            if seen_google:
                continue
            seen_google = True
        result.append(dict(site))
    if not seen_google:
        result.insert(0, dict(DEFAULT_SITES[0]))
    return result


def dump_sites(sites: list[dict]) -> str:
    return json.dumps(sites, ensure_ascii=False)


def build_url(url_template: str, query: str) -> str:
    """`{q}` 자리에 URL-인코딩된 질의를 넣는다. 자리표시자가 없으면 쿼리스트링으로 붙인다."""
    q = quote_plus(query.strip())
    if "{q}" in url_template:
        return url_template.replace("{q}", q)
    sep = "&" if "?" in url_template else "?"
    return f"{url_template}{sep}q={q}"


def match_keyword(sites: list[dict], text: str) -> "tuple[int, str] | None":
    """텍스트가 등록된 keyword+공백으로 시작하면 (그 사이트 인덱스, 나머지 질의)를 돌려준다.

    등록 순서대로 첫 매치를 쓴다 — keyword를 짧고 서로 겹치지 않게 정하는 건 사용자 몫
    (Chrome 커스텀 검색엔진의 "키워드" 관례와 동일).
    """
    for i, site in enumerate(sites):
        kw = (site.get("keyword") or "").strip()
        if kw and text.startswith(kw + " "):
            return i, text[len(kw) + 1:]
    return None
