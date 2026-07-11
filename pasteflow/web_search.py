"""웹 검색 — 모델이 요청한 검색어를 실제로 수행해 결과 텍스트를 돌려준다.

왜 필요한가
----------
모델은 학습이 끝난 시점에 얼려진 백과사전이라 인터넷에 직접 닿지 못한다. 웹 챗봇이
내일 날씨를 아는 건 모델이 똑똑해서가 아니라, 제품이 모델 대신 검색을 해서 그 결과를
프롬프트에 끼워 넣어 주기 때문이다. Mindlogic 게이트웨이는 OpenAI 호환
chat.completions만 중계하고 제공사 내장 검색(Google grounding 등)은 실어 주지 않으므로,
그 '심부름꾼' 역할을 PasteFlow가 직접 맡는다. (사용자가 고른 모델이 GPT면 애초에 이
모듈이 필요 없다 — Responses API의 내장 검색을 직접 탄다. ocr_engine의 경로 분기 참조.)

검색 수단 두 가지 — 좋은 것 먼저, 안 되면 안전망
-----------------------------------------------
1. **GPT 검색 심부름꾼**(기본): 게이트웨이의 Responses API로 `gpt-5.4-nano`에게 내장
   web_search를 시켜 "사실만 정리"하게 한다. 검색·본문 독해·인용을 OpenAI가 다 해준다.
2. **DuckDuckGo**(폴백): 키 없이 되지만 **검색 결과 스니펫만** 준다.

왜 DuckDuckGo가 기본이 아닌가 — 2026-07-11 실측이 갈랐다. "내일 서울 날씨"에 DDG 경로는
claude가 두 번 검색하고도 "정확한 수치를 못 찾았다"고 포기했다(수치는 스니펫이 아니라
페이지 본문에 있다). 같은 질문에 nano 경로는 **2.9초에 시간대별 기온**까지 가져왔다 —
DDG와 속도는 같은데 품질이 비교가 안 된다. 그래서 순서를 뒤집었다.
`gpt-5-mini`가 아니라 `gpt-5.4-nano`인 이유도 속도다(같은 검색에 mini는 51~83초 —
추론 모델이라 생각이 길다. 검색은 사실 수집이지 사고가 아니므로 가벼운 모델이 맞다).
DDG는 계정에 nano 권한이 없거나 Responses가 막혔을 때를 위한 안전망으로 남긴다.
"""
from __future__ import annotations

from typing import Optional

# 검색 심부름꾼 모델. 가벼울수록 좋다 — 검색은 사실 수집이지 사고가 아니다.
# (실측: nano 2.9초 vs gpt-5-mini 51~83초. 답변 품질은 어차피 본 모델이 책임진다.)
SEARCH_AGENT_MODEL = "gpt-5.4-nano"

# 검색 심부름꾼에게 주는 역할. 의견·조언을 금지하는 이유: 답하는 건 사용자가 고른 모델의
# 몫이다. 심부름꾼이 자기 해석을 섞으면 그게 본 모델의 답에 그대로 흘러든다.
_SEARCH_AGENT_INSTRUCTIONS = (
    "너는 웹 검색 도구다. 웹을 검색해 찾은 **사실만** 간결히 정리해 보고한다. "
    "조언·인사·의견·추측을 덧붙이지 마라. 수치·날짜·고유명사는 찾은 그대로 옮기고, "
    "각 항목 끝에 출처 URL을 적어라. 못 찾았으면 '검색 결과 없음'이라고만 답한다."
)

# 모델에 돌려줄 검색 결과 개수. 늘릴수록 근거는 풍부해지지만 프롬프트 토큰을 먹는다.
DEFAULT_MAX_RESULTS = 5

# 스니펫 1건당 잘라 넣는 길이. 통째로 넣으면 5건만 해도 프롬프트가 크게 부푼다.
_SNIPPET_LIMIT = 400

# 모델이 이 도구를 언제 쓸지 판단하는 근거가 되는 스펙. OpenAI 호환 function calling 형식.
SEARCH_TOOL_SPEC = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": (
            "웹을 검색해 최신 정보를 가져온다. 학습 데이터에 없거나 시점에 따라 바뀌는 정보"
            "(오늘·내일 날씨, 최근 뉴스·사건, 주가·환율·시세, 최신 버전·출시일, 특정 인물의"
            "현재 근황 등)를 물었을 때 사용한다. 검색 결과가 부족하면 검색어를 바꿔 다시"
            "호출해도 된다."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "검색어. 사용자의 질문을 검색에 적합한 키워드로 바꿔 넣는다.",
                },
            },
            "required": ["query"],
        },
    },
}


def search(query: str, api_key: str = "", base_url: str = "",
           max_results: int = DEFAULT_MAX_RESULTS) -> str:
    """`query`를 웹에서 검색해 모델이 읽을 결과 텍스트를 반환한다(동기).

    게이트웨이 자격증명(`api_key`+`base_url`)이 있으면 GPT 검색 심부름꾼을 먼저 쓰고,
    실패하면 DuckDuckGo로 내려간다. 자격증명이 없으면 곧장 DuckDuckGo.

    **실패해도 예외를 올리지 않고** "검색 실패: ..." 문자열을 돌려준다. 이 반환값은 도구
    실행 결과로 모델에게 그대로 전달되는데, 예외로 대화를 끊어 버리면 사용자는 답을
    아예 못 받는다. 실패를 알려 주면 모델이 최소한 학습 지식으로라도 답하고 "검색이
    안 돼 최신 정보는 확인하지 못했다"고 밝힐 수 있다.
    호출자가 워커 스레드에서 실행해야 UI가 멈추지 않는다(네트워크 3~5초).
    """
    query = (query or "").strip()
    if not query:
        return "검색 실패: 검색어가 비어 있습니다."

    if api_key and base_url:
        result = _search_via_gpt(query, api_key, base_url)
        if result is not None:
            return result
        # None = 심부름꾼을 못 썼다(모델 권한 없음·Responses 차단 등) → 안전망으로 내려간다.

    return _search_via_ddg(query, max_results)


def _search_via_gpt(query: str, api_key: str, base_url: str) -> Optional[str]:
    """GPT 검색 심부름꾼 — 성공 시 결과 텍스트, **못 쓰면 None**(폴백 신호).

    빈 답도 None으로 본다(검색은 됐는데 아무 말도 안 한 것 = 쓸모없음 → DDG가 낫다).
    """
    try:
        import openai
        # 함수 안에서 import — 모듈 최상단에 두면 ocr_engine ↔ web_search 순환 import가 된다
        # (ocr_engine이 이 모듈을 import한다). 호출 시점엔 ocr_engine이 이미 로드돼 있어 안전.
        from .ocr_engine import _normalize_base_url
    except ImportError:
        return None

    try:
        client = openai.OpenAI(api_key=api_key, base_url=_normalize_base_url(base_url))
        resp = client.responses.create(
            model=SEARCH_AGENT_MODEL,
            instructions=_SEARCH_AGENT_INSTRUCTIONS,
            input=query,
            tools=[{"type": "web_search"}],
        )
        text = (getattr(resp, "output_text", "") or "").strip()
        return text or None
    except Exception:
        # 모델 권한 없음(403)·Responses 미지원(400)·네트워크 등 — 조용히 안전망으로.
        return None


def _search_via_ddg(query: str, max_results: int = DEFAULT_MAX_RESULTS) -> str:
    """DuckDuckGo 안전망 — 키 없이 되지만 검색 결과 스니펫만 준다(본문은 못 읽는다)."""
    try:
        from ddgs import DDGS
    except ImportError:
        return "검색 실패: ddgs 패키지가 설치되지 않았습니다 (pip install ddgs)."

    try:
        # region="kr-kr" — 한국어 질문이 대부분이라 한국 결과를 우선한다. 영어 질문에도
        # DuckDuckGo는 질의어 언어를 함께 보므로 영어 결과가 밀리지 않는다.
        rows = DDGS().text(query, region="kr-kr", max_results=max_results)
    except Exception as exc:  # 네트워크 단절·rate limit·DDG 내부 오류 전부
        return f"검색 실패: {type(exc).__name__}: {exc}"

    return format_results(query, rows or [])


def format_results(query: str, rows: list[dict]) -> str:
    """검색 결과 dict 목록을 모델이 읽기 좋은 텍스트로 정리한다(순수 함수 — 테스트 대상).

    출처 URL을 함께 넣는 이유: 모델이 답변에 근거를 밝힐 수 있고, 사용자가 원문을
    확인할 수 있다(AI 답변창은 링크를 클릭 가능하게 렌더한다).
    """
    if not rows:
        return f"'{query}' 검색 결과가 없습니다."

    parts = [f"'{query}' 웹 검색 결과 {len(rows)}건:"]
    for i, row in enumerate(rows, 1):
        title = (row.get("title") or "").strip()
        body = (row.get("body") or "").strip().replace("\n", " ")
        url = (row.get("href") or row.get("url") or "").strip()
        if len(body) > _SNIPPET_LIMIT:
            body = body[:_SNIPPET_LIMIT] + "…"
        parts.append(f"\n[{i}] {title}\n{body}\n출처: {url}")
    return "\n".join(parts)
