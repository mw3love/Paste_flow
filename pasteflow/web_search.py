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

from typing import NamedTuple, Optional

from . import gdrive

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

# 드라이브가 연결됐을 때만 지시문에 덧붙인다. 상수에 박아 두면 드라이브를 연결하지 않은
# 사용자에게도 "드라이브를 검색하라"고 말하는 셈이라, 도구가 없는 심부름꾼이 헛돌거나
# "드라이브에 접근할 수 없다"는 잡음을 답에 섞는다.
_DRIVE_NOTE = (
    "\n사용자의 구글 드라이브를 검색하는 도구(gdrive)도 있다. 질문이 '내 드라이브·내 문서·"
    "내 파일·내가 쓴 …'처럼 **사용자 개인 자료**를 가리키면 웹이 아니라 드라이브를 검색해 "
    "찾은 파일명·내용을 그대로 보고한다. 웹과 드라이브 둘 다 필요하면 둘 다 쓴다."
)


def agent_tools(gdrive_token: str = "") -> list[dict]:
    """심부름꾼(nano)·GPT Responses에 실을 도구 목록. 드라이브는 토큰이 있을 때만 붙는다.

    비GPT 모델(claude·gemini…)은 자기가 검색하지 않고 이 심부름꾼에게 시키므로, 여기에
    드라이브를 얹으면 **전 모델이** 드라이브를 쓰게 된다 — 모델별 도구 호환성 지뢰
    (Llama-4-Maverick의 tools→405 등)를 다시 밟지 않는 것이 이 방식의 이득이다.
    """
    tools: list[dict] = [{"type": "web_search"}]
    if gdrive_token:
        tools.append(gdrive.drive_tool(gdrive_token))
    return tools

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


def search_tool_spec(drive_connected: bool = False) -> dict:
    """모델(비GPT)에게 줄 검색 도구 스펙. 드라이브가 연결됐으면 그 사실을 설명에 덧붙인다.

    설명을 손대는 이유: 이 도구는 겉보기에 '웹 검색'이라, 설명을 그대로 두면 모델이
    "내 드라이브에서 X 찾아줘"를 **검색이 필요 없는 질문**으로 보고 도구를 아예 안 부른다
    (그러면 드라이브까지 가 볼 심부름꾼이 출동하지 않는다). 도구 뒤에서 드라이브도 뒤진다는
    것을 모델이 알아야 부른다. 실행부(`_search_via_gpt`)는 토큰이 있을 때만 드라이브를 붙이므로
    이름은 `web_search` 그대로 둔다(모델이 부르는 이름을 바꾸면 `_run_tool_call`과 어긋난다).
    """
    if not drive_connected:
        return SEARCH_TOOL_SPEC
    import copy
    spec = copy.deepcopy(SEARCH_TOOL_SPEC)
    spec["function"]["description"] += (
        " 사용자의 구글 드라이브(내 문서·내 파일·내가 쓴 자료)도 이 도구로 검색된다 — "
        "'내 드라이브에서 …', '내 문서 중에 …'처럼 개인 자료를 묻는 질문에도 호출하고, "
        "검색어에 무엇을 찾는지 그대로 넣는다."
    )
    return spec


def search(query: str, api_key: str = "", base_url: str = "",
           max_results: int = DEFAULT_MAX_RESULTS, gdrive_token: str = "") -> str:
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
        result = _search_via_gpt(query, api_key, base_url, gdrive_token)
        if result is not None:
            return result
        # None = 심부름꾼을 못 썼다(모델 권한 없음·Responses 차단 등) → 안전망으로 내려간다.
        # ⚠ 이 안전망(DDG)에는 드라이브가 없다 — 웹만 검색한다. 커넥터는 Responses 전용이라
        #    심부름꾼을 못 쓰면 드라이브도 함께 빠지는 것이 구조상 불가피하다.

    return _search_via_ddg(query, max_results)


def _search_via_gpt(query: str, api_key: str, base_url: str,
                    gdrive_token: str = "") -> Optional[str]:
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
            instructions=_SEARCH_AGENT_INSTRUCTIONS + (_DRIVE_NOTE if gdrive_token else ""),
            input=query,
            tools=agent_tools(gdrive_token),
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


class Prefetch(NamedTuple):
    """`prefetch()`의 결과.

    `available=False`는 "검색이 필요 없다"가 **아니라** "심부름꾼을 못 썼다"(모델 권한 없음·
    Responses 차단·네트워크)는 뜻이다. 둘을 섞으면 안 된다 — 못 쓴 것을 "검색 불필요"로
    읽으면 실시간 질문에 도구도 없이 답하게 되어 모델이 "저는 인터넷이 없습니다"로 답한다.
    호출자는 `available=False`면 모델별 자체 검색(현행 동작)으로 열화해야 한다.
    """
    available: bool   # 심부름꾼을 실제로 썼는가
    facts: str        # 검색 결과 텍스트. ""이면 "이 질문엔 검색이 불필요"라는 판정


# 게이트키퍼 지시문 — `_SEARCH_AGENT_INSTRUCTIONS`와 다른 점은 **검색 여부 판단까지 맡긴다**는
# 것이다. 도구 호출 판단이 곧 "이 질문에 검색이 필요한가"의 답이므로 판단기를 따로 둘 필요가
# 없다(nano 1콜 = 판단 + 검색). 검색이 불필요하면 도구를 부르지 않고 NO_SEARCH만 답하게 한다.
_GATEKEEPER_INSTRUCTIONS = (
    "너는 웹 검색 심부름꾼이다. 질문에 답하지 말고, 답하는 데 필요한 **최신 사실만** 모아 온다.\n"
    "1) 질문이 시점에 따라 변하는 정보(날씨·뉴스·시세·환율·최신 버전·출시일·근황 등)를 요구하면 "
    "웹을 검색해 찾은 사실만 간결히 정리한다. 수치·날짜·고유명사는 찾은 그대로 옮기고, 각 항목 "
    "끝에 출처 URL을 적는다. 출처를 모르면 URL을 지어내지 말고 비워 둔다.\n"
    "2) 검색이 필요 없는 질문(일반 지식·코드·번역·요약·의견 등)이면 **검색하지 말고** 정확히 "
    "'NO_SEARCH' 한 단어만 답한다.\n"
    "질문에 이미지가 함께 오면 이미지 내용을 근거로 검색어를 만든다.\n"
    "어떤 경우에도 조언·의견·인사를 덧붙이지 마라."
)


def _did_search(resp) -> bool:
    """심부름꾼이 실제로 도구를 썼는가 — 웹 검색(`web_search_call`) 또는 드라이브(`mcp_call`).

    ⚠ 드라이브 호출은 타입이 `mcp_call`이라 "search" 문자열이 들어 있지 않다. 웹 검색만
    찾으면 **드라이브만 뒤진 질문**("내 드라이브에서 X 찾아줘")이 '검색 안 함'으로 오판돼
    애써 찾은 자료가 통째로 버려진다(Prefetch(True, "")). 도구 목록 조회(`mcp_list_tools`)는
    실제 검색이 아니라 커넥터가 늘 먼저 하는 준비 동작이므로 세지 않는다.
    """
    for o in (resp.output or []):
        kind = getattr(o, "type", "") or ""
        if "search" in kind or kind == "mcp_call":
            return True
    return False


def prefetch(question: str, api_key: str = "", base_url: str = "",
             images: list[bytes] | None = None, gdrive_token: str = "") -> Prefetch:
    """질문에 필요한 웹 검색을 **미리 한 번만** 수행해 사실 텍스트를 돌려준다.

    여러 모델 비교(`main._start_compare_query`)가 쓴다. 모델마다 각자 검색하면 같은 질문에
    서로 다른 수치가 나와 비교가 성립하지 않는다("누가 더 잘 정리하나"를 봐야 하는데 "각자
    무엇을 찾았나"가 섞인다). 그래서 검색은 앞단에서 한 번만 하고 같은 자료를 전 모델에 물린다.

    **검색 필요 여부도 여기서 갈린다.** 판단기를 따로 두지 않는다 — 심부름꾼이 도구를
    불렀는지(`web_search_call`)가 곧 그 판단이다. 응답 텍스트('NO_SEARCH')가 아니라 **도구
    호출 유무**로 읽는 이유는 문구에 기대지 않기 위해서다(모델이 말투를 바꿔도 안 흔들린다 —
    `_ask_openai_compat`의 도구 미지원 판별과 같은 기법).

    게이트웨이 자격증명이 없으면(공식 백엔드 등) `available=False`로 즉시 반환한다.
    동기 호출이라 호출자가 워커 스레드에서 실행해야 한다(검색 시 2~5초).
    """
    question = (question or "").strip()
    if not question or not (api_key and base_url):
        return Prefetch(False, "")

    try:
        import base64
        import openai
        from .ocr_engine import _normalize_base_url   # 순환 import 회피 — 함수 안에서
    except ImportError:
        return Prefetch(False, "")

    content: object = question
    if images:
        # 이미지에만 단서가 있는 질문("이 사진 도시의 내일 날씨는?")도 검색어를 만들 수 있다
        # (2026-07-11 실호출 검증: 질문에 없는 'BUSAN'을 이미지에서 읽어 부산 날씨를 찾아옴).
        content = [{"type": "input_text", "text": question}]
        for png in images:
            b64 = base64.standard_b64encode(png).decode()
            content.append({"type": "input_image", "image_url": f"data:image/png;base64,{b64}"})

    try:
        client = openai.OpenAI(api_key=api_key, base_url=_normalize_base_url(base_url))
        resp = client.responses.create(
            model=SEARCH_AGENT_MODEL,
            instructions=_GATEKEEPER_INSTRUCTIONS + (_DRIVE_NOTE if gdrive_token else ""),
            input=[{"role": "user", "content": content}],
            tools=agent_tools(gdrive_token),
        )
    except Exception:
        return Prefetch(False, "")   # 못 썼다 ≠ 검색 불필요 → 호출자가 현행 동작으로 열화

    if not _did_search(resp):
        return Prefetch(True, "")    # 검색 불필요라고 판정함
    return Prefetch(True, (getattr(resp, "output_text", "") or "").strip())


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
