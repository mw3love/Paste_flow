# webchat — 구글 드라이브 근거 웹 챗봇

질문하면 **AI 답변(스트리밍)** + **근거가 된 드라이브 파일 목록(관련도순)** 을 함께 보여주는
로컬 웹 챗봇. PasteFlow가 이미 만든 게이트웨이·드라이브 배관을 그대로 재사용한다(복사 아님, import).

## 구조

```
webchat/
├─ server.py       # FastAPI + SSE — Responses 스트림을 브라우저로 중계
├─ index.html      # 순수 HTML/JS 단일 파일 (빌드 툴 없음)
├─ requirements.txt
└─ run_local.bat   # 로컬 실행 런처 (개인 API 키 담김 → .gitignore, 커밋 안 됨)
```

- 답변 모델이 게이트웨이 **Responses API**에서 드라이브 커넥터(`connector_googledrive`)를
  직접 호출한다(PasteFlow 앱의 nano 심부름꾼 병목 없음).
- SSE 두 갈래: `event: files`(파일목록 패널) / `event: token`(답변 스트리밍).
  파일목록은 답변 텍스트보다 **먼저** 도착한다(실측).

## 실행

가장 간단: `webchat/run_local.bat` 더블클릭(개인 게이트웨이 계정으로 실행되도록 env가 세팅돼 있다).
수동으로 하려면:

```bash
# 1) 의존성 (최초 1회)
pip install -r webchat/requirements.txt

# 2) 서버 (Paste_flow 루트에서)
python -m uvicorn webchat.server:app --port 8000

# 3) 브라우저에서 http://127.0.0.1:8000
```

## 전제

- **구글 드라이브 연결**은 PasteFlow 설정창에서 돼 있어야 한다(OAuth refresh token을 DB에서
  읽는다). 안 붙어 있으면 도구만 빠지고 웹 검색으로 답한다(우아한 열화).
- 게이트웨이 자격증명(API 키·Base URL)은 두 경로 중 하나:
  - **env override**(`WEBCHAT_API_KEY`/`WEBCHAT_BASE_URL`) — `run_local.bat`이 쓰는 방식.
    기존 PasteFlow 앱과 **다른 게이트웨이 계정**으로 webchat만 돌릴 수 있다.
  - **없으면 PasteFlow DB 폴백** — `%LOCALAPPDATA%\PasteFlow\pasteflow.db`의 게이트웨이 키.
    이 경우 시크릿이 DPAPI 암호화라 **같은 PC·같은 Windows 계정**이어야 복호화된다.
- 드라이브 토큰(구글 OAuth)은 게이트웨이 키와 무관하므로, env로 게이트웨이 계정만 바꿔도
  드라이브는 그대로 붙는다.

## 설정

- 답변 모델: 기본 `gpt-5.6-sol`. `WEBCHAT_MODEL` 환경변수로 교체(`run_local.bat` 안에 예시 주석).
  ```bash
  WEBCHAT_MODEL=gpt-5.6-terra python -m uvicorn webchat.server:app --port 8000
  ```
  **모델 실측(2026-07-17, 137KB 문서 fetch 요약):** `gpt-5.6-sol/terra/luna`·`5.5`·`5.4`가
  큰 문서를 8~14초에 소화(terra 최속 8.6초). `gpt-5.4-nano`는 큰 문서에서 컨텍스트 초과,
  `gpt-5-mini`도 작음. 품질이 더 필요하면 계획 문서의 **경로 2(Claude MCP 커넥터,
  `/v1/gateway/claude` 엔드포인트)** 로 승격 검토.

⚠ **크레딧(종량제):** 게이트웨이 계정은 월 크레딧 한도가 있고 **매달 1일 리셋**된다. 큰 문서
  읽기는 1회에 수만 토큰을 먹어(문서를 통째로 모델에 넣음) 몇 번이면 한도가 소진돼 `402`가
  뜬다. 대형 파일(수 MB PDF 등) 통째 요약은 컨텍스트·크레딧 이중으로 막히므로, 그런 문서는
  '부분 검색'(필요한 부분만 읽기) 방식이 필요하다(미구현). 수십~수백 KB 문서는 문제없다.

## 확정된 드라이브 검색 스키마 (참고)

`search` mcp_call의 `output`(JSON) → `{"results": [...]}`, **순서 = 관련도순**. 각 항목:
`title`(파일명) · `url`(webViewLink, 직접 옴) · `id` · `mime_type` · `file_or_folder` ·
`size` · `created_at`/`updated_at`/`viewedByMeTime` · `parent_ids`.
⚠ `search`는 **빈 query면 500** — 반드시 비어 있지 않은 키워드가 필요하다.
