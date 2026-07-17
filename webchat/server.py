"""드라이브 근거 웹 챗봇 — 백엔드(FastAPI + SSE).

역할: 게이트웨이 Responses API에 구글 드라이브 커넥터를 붙여 스트리밍 호출하고, 그
스트림을 브라우저로 중계한다. 두 갈래를 SSE 이벤트로 분리해 보낸다:
  event: files  → search/recent_documents 등이 돌려준 관련도순 파일목록 (오른쪽 패널)
  event: token  → 답변 텍스트 토큰 (왼쪽 본문 스트리밍)

⚠ 이 서버는 PasteFlow와 **같은 PC·같은 Windows 계정**에서 실행해야 한다. 크리덴셜을
   PasteFlow DB에서 읽는데 시크릿이 DPAPI로 암호화돼 있어 다른 계정에선 복호화되지 않는다.

재사용 자산(복사 아님, import):
  pasteflow.gdrive   — TokenCache(1시간 토큰 자동 갱신), drive_tool(커넥터 스펙), OAuth
  pasteflow.crypto   — DPAPI 복호화
  pasteflow.ocr_engine._normalize_base_url — 게이트웨이 base_url 정규화

실행: webchat/ 상위(Paste_flow)에서
  python -m uvicorn webchat.server:app --reload --port 8000
그러면 http://127.0.0.1:8000 에서 챗봇이 열린다.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

# Paste_flow 루트를 import 경로에 넣어 pasteflow 패키지를 그대로 쓴다.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from fastapi import FastAPI                        # noqa: E402
from fastapi.responses import HTMLResponse, StreamingResponse  # noqa: E402
from pydantic import BaseModel                      # noqa: E402

from pasteflow import crypto, gdrive                # noqa: E402
from pasteflow.ocr_engine import _normalize_base_url  # noqa: E402

# ── 설정 ─────────────────────────────────────────────────────────────────────
# 답변 모델. 게이트웨이 Responses(내장 web_search + 드라이브 커넥터)를 타므로 GPT 계열이어야
# 한다(supports_responses_api). 기본값 gpt-5.2 — 18종 GPT 모델을 137KB 문서(CLAUDE.md)
# fetch로 실측(2026-07-17)한 결과, nano는 큰 문서에서 컨텍스트가 터졌지만 gpt-5.2/5.1/
# mini는 멀쩡히 읽었고 그중 5.2가 가장 빨랐다(16.8초). nano보다 추론도 낫다.
# ⚠ 단 18MB급 초대형 파일(PDF 매뉴얼 등)은 어떤 모델로도 통째로 못 읽는다 — 그건 fetch가
#   아니라 '파일 안 검색'으로 접근해야 하는 별도 과제다.
# 품질이 더 필요하면 환경변수로 교체하거나(WEBCHAT_MODEL), 계획의 경로 2(Claude MCP)로 승격.
MODEL = os.environ.get("WEBCHAT_MODEL", "gpt-5.2")

# 드라이브 검색을 파일목록 패널로 렌더할 도구들 — output이 {"results":[...]} 형태인 것만.
# fetch(본문 읽기)는 output이 {"content":...}라 목록이 아니므로 제외한다.
_LIST_TOOLS = {"search", "recent_documents", "list_folder", "list_drives"}

_SYSTEM_PROMPT = (
    "당신은 사용자의 구글 드라이브 문서를 근거로 답하는 도우미입니다. "
    "질문에 답하기 위해 gdrive 도구의 search를 **반드시 비어 있지 않은 키워드 query**로 "
    "호출해 관련 문서를 찾고, 필요하면 fetch로 본문을 읽어 근거로 삼으세요. "
    "빈 query로 search를 부르면 오류가 납니다. 찾은 문서의 내용을 지어내지 말고, "
    "근거가 부족하면 솔직히 밝히세요. 한국어로 자연스럽게, 마크다운으로 답하세요."
)


def _db_path() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser(r"~\AppData\Local")
    return os.path.join(base, "PasteFlow", "pasteflow.db")


def _get_setting(key: str, default: str = "") -> str:
    """PasteFlow DB에서 설정값 하나 읽기(읽기 전용, 매번 새 커넥션 — 저빈도 호출)."""
    try:
        conn = sqlite3.connect(f"file:{_db_path()}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        return default
    try:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row[0] if row and row[0] is not None else default
    finally:
        conn.close()


def _gateway_creds() -> tuple[str, str]:
    """(api_key, base_url) — api_key는 DPAPI 복호화."""
    api_key = crypto.unprotect(_get_setting("ocr_gemini_api_key_gateway"))
    base_url = _get_setting("ocr_gemini_base_url")
    return api_key, base_url


def _gdrive_creds() -> tuple[str, str, str]:
    """(client_id, client_secret, refresh_token) — TokenCache에 넘길 콜러블용.

    콜러블로 만드는 이유는 gdrive.TokenCache와 같다: 설정이 바뀌면(재연결) 다음 호출에
    자동 반영되도록 값이 아니라 '그때그때 읽기'를 넘긴다.
    """
    return (
        _get_setting("gdrive_client_id"),
        crypto.unprotect(_get_setting("gdrive_client_secret")),
        crypto.unprotect(_get_setting("gdrive_refresh_token")),
    )


# 토큰 캐시 하나를 앱 전역에서 공유(1시간 토큰을 물고 있다가 만료 전 자동 갱신).
_tokens = gdrive.TokenCache(_gdrive_creds)

app = FastAPI(title="Drive Chatbot")

_INDEX_PATH = Path(__file__).parent / "index.html"


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    # 매 요청마다 읽는다 — 개인용 로컬 서버라 비용이 무의미하고, index.html을 고치면
    # 서버 재시작 없이 새로고침만으로 반영된다(개발 편의).
    return _INDEX_PATH.read_text(encoding="utf-8")


class ChatRequest(BaseModel):
    # 대화 히스토리 전체. 마지막 항목이 방금 던진 user 질문.
    messages: list[dict]


def _sse(event: str, data: dict) -> str:
    """SSE 프레임 하나. data는 항상 JSON — 토큰에 개행이 섞여도 안전하다."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _event_stream(messages: list[dict]):
    """Responses 스트림을 SSE로 중계하는 동기 제너레이터.

    동기 제너레이터라도 FastAPI가 threadpool에서 돌리므로 이벤트 루프를 막지 않는다
    (openai 스트림 자체가 동기라 async로 감싸도 이득이 없다).
    """
    import openai

    api_key, base_url = _gateway_creds()
    if not (api_key and base_url):
        yield _sse("error", {"message": "게이트웨이 API 키/Base URL이 설정되지 않았습니다 "
                                         "(PasteFlow 설정창에서 입력)."})
        return

    token = _tokens.access_token()  # 없으면 "" → 드라이브 도구만 빠짐(우아한 열화)
    tools = [gdrive.drive_tool(token)] if token else []

    client = openai.OpenAI(api_key=api_key, base_url=_normalize_base_url(base_url))
    try:
        stream = client.responses.create(
            model=MODEL,
            instructions=_SYSTEM_PROMPT,
            input=messages,
            tools=tools,
            stream=True,
        )
    except Exception as exc:  # noqa: BLE001 — 사용자에게 사유를 보여줘야 함
        yield _sse("error", {"message": f"{type(exc).__name__}: {exc}"})
        return

    if not token:
        yield _sse("notice", {"message": "구글 드라이브가 연결돼 있지 않아 웹 검색만 사용합니다."})

    try:
        for ev in stream:
            etype = getattr(ev, "type", "")
            if etype == "response.output_text.delta":
                yield _sse("token", {"text": ev.delta})
            elif etype == "response.output_item.done":
                item = getattr(ev, "item", None)
                if item is None or getattr(item, "type", "") != "mcp_call":
                    continue
                if getattr(item, "name", "") not in _LIST_TOOLS:
                    continue
                out = getattr(item, "output", None)
                if not out:
                    continue
                try:
                    results = (json.loads(out) or {}).get("results")
                except (ValueError, TypeError):
                    results = None
                if results:
                    yield _sse("files", {"tool": item.name, "results": results})
            elif etype == "response.error":
                yield _sse("error", {"message": str(getattr(ev, "error", "스트림 오류"))})
    except Exception as exc:  # noqa: BLE001
        yield _sse("error", {"message": f"{type(exc).__name__}: {exc}"})
        return

    yield _sse("done", {})


@app.post("/chat")
def chat(req: ChatRequest) -> StreamingResponse:
    return StreamingResponse(
        _event_stream(req.messages),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
