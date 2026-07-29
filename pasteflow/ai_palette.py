"""AI 팔레트 — 자유질문창(Alt+`)의 질문을 여러 목적지로 라우팅한다.

Raycast/PowerToys Run처럼 입력한 텍스트를 "어디로 보낼지" 여러 타겟(구글 AI 모드·
구글 드라이브·ChatGPT/Claude/Gemini·PasteFlow 자체 답변·사용자가 추가한 웹사이트) 중
하나로 보낸다. 목적지 목록은 설정 DB에 JSON으로 저장돼 사용자가 직접 추가·삭제·순서
변경할 수 있다(설정창 "AI 팔레트 타겟" 그룹, `ui/settings_dialog.py`). 새 외부 의존성
없음 — stdlib `json`/`urllib.parse`만 사용(프로젝트 "새 외부 의존성 0" 원칙).

각 타겟은 `{"label": str, "keyword": str, "kind": str, "url": str}` — `kind`가 특수
경로(`KIND_GOOGLE_AI`/`KIND_DRIVE`/`KIND_API`/`INJECT_KINDS`)면 실제 실행은 main.py의
기존 배관(web_open.py의 구글 AI·드라이브 검색, AI 워커, 클립보드 주입)이 그대로 담당하고
`url`은 쓰이지 않는다. `KIND_URL`만 이 모듈의 `build_url()`로 `{q}` 자리에 질의를 채워
넣는다. `INJECT_KINDS`(ChatGPT·Claude·Gemini)는 URL의 `q=`가 안 먹혀 텍스트조차 URL로
못 넣으므로, 이미지 유무와 무관하게 항상 클립보드 붙여넣기 주입 경로를 탄다(구글 AI 모드는
텍스트만이면 URL, 이미지가 있을 때만 주입 — 차이는 web_open.py 참고).
"""
from __future__ import annotations

import json
from urllib.parse import quote_plus

KIND_URL = "url"
KIND_GOOGLE_AI = "google_ai"
KIND_DRIVE = "drive"
KIND_API = "api"
KIND_CHATGPT = "chatgpt"
KIND_CLAUDE = "claude"
KIND_GEMINI = "gemini"

# 클립보드 주입(이미지+텍스트를 Ctrl+V로 순서대로 붙여넣고 Enter)이 필요한 kind들.
# 구글 AI 모드와 달리 이 세 곳은 URL의 q= 파라미터를 아예 무시해(2026-07-29 실측 —
# web_open.py 모듈 docstring 참고) 텍스트조차 URL만으론 못 넣는다. 그래서 이미지 유무와
# 무관하게 **항상** 주입 경로를 탄다(구글은 이미지가 있을 때만 주입, 텍스트만이면 URL).
INJECT_KINDS: frozenset[str] = frozenset({KIND_CHATGPT, KIND_CLAUDE, KIND_GEMINI})

# 설정창 종류 드롭다운에 쓸 표시 라벨(순서=드롭다운 순서).
KIND_LABELS: dict[str, str] = {
    KIND_URL: "웹사이트 URL",
    KIND_GOOGLE_AI: "Google AI 모드",
    KIND_DRIVE: "구글 드라이브 검색",
    KIND_CHATGPT: "ChatGPT (주입)",
    KIND_CLAUDE: "Claude (주입)",
    KIND_GEMINI: "Gemini (주입)",
    KIND_API: "PasteFlow 답변(API)",
}

# 첫 실행(저장된 값이 없을 때) 기본 타겟 — 순서가 곧 팔레트 번호(Alt+1~9).
# Gemini 웹앱은 URL의 q 파라미터를 무시해(2026-07-14 실측, web_open.py 참고) 검색 URL
# 방식(KIND_URL) 목록에서는 뺐었다 — 대신 아래 KIND_GEMINI(주입 경로)로 들어간다.
DEFAULT_SITES: list[dict] = [
    {"label": "Google AI", "keyword": "g", "kind": KIND_GOOGLE_AI, "url": ""},
    {"label": "드라이브", "keyword": "dr", "kind": KIND_DRIVE, "url": ""},
    {"label": "유튜브", "keyword": "yt", "kind": KIND_URL,
     "url": "https://www.youtube.com/results?search_query={q}"},
    {"label": "다나와", "keyword": "dw", "kind": KIND_URL,
     "url": "https://search.danawa.com/dsearch.php?query={q}"},
    {"label": "네이버쇼핑", "keyword": "ns", "kind": KIND_URL,
     "url": "https://search.shopping.naver.com/search/all?query={q}"},
    {"label": "ChatGPT", "keyword": "gpt", "kind": KIND_CHATGPT, "url": ""},
    {"label": "Claude", "keyword": "cl", "kind": KIND_CLAUDE, "url": ""},
    {"label": "Gemini", "keyword": "gm", "kind": KIND_GEMINI, "url": ""},
    {"label": "질문(API)", "keyword": "q", "kind": KIND_API, "url": ""},
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
