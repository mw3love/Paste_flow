# webchat — 구글 드라이브 근거 웹 챗봇

질문하면 **AI 답변(스트리밍)** + **근거가 된 드라이브 파일 목록(관련도순)** 을 함께 보여주는
로컬 웹 챗봇. PasteFlow가 이미 만든 게이트웨이·드라이브 배관을 그대로 재사용한다(복사 아님, import).

## 구조

```
webchat/
├─ server.py     # FastAPI + SSE — Responses 스트림을 브라우저로 중계
├─ index.html    # 순수 HTML/JS 단일 파일 (빌드 툴 없음)
└─ requirements.txt
```

- 답변 모델이 게이트웨이 **Responses API**에서 드라이브 커넥터(`connector_googledrive`)를
  직접 호출한다(PasteFlow 앱의 nano 심부름꾼 병목 없음).
- SSE 두 갈래: `event: files`(파일목록 패널) / `event: token`(답변 스트리밍).
  파일목록은 답변 텍스트보다 **먼저** 도착한다(실측).

## 실행

```bash
# 1) 의존성 (최초 1회)
pip install -r webchat/requirements.txt

# 2) 서버 (Paste_flow 루트에서)
python -m uvicorn webchat.server:app --port 8000

# 3) 브라우저에서 http://127.0.0.1:8000
```

## 전제

- **PasteFlow와 같은 PC·같은 Windows 계정**에서 실행해야 한다. 크리덴셜을 PasteFlow DB
  (`%LOCALAPPDATA%\PasteFlow\pasteflow.db`)에서 읽는데, 시크릿이 DPAPI로 암호화돼 있어
  다른 계정에선 복호화되지 않는다.
- PasteFlow 설정창에서 **게이트웨이 API 키 + 구글 드라이브 연결**이 돼 있어야 한다.
  드라이브가 안 붙어 있으면 도구만 빠지고 웹 검색으로 답한다(우아한 열화).

## 설정

- 답변 모델: 기본 `gpt-5.4-nano`(커넥터+스트리밍 실호출 검증된 모델). 환경변수로 교체:
  ```bash
  WEBCHAT_MODEL=gpt-5-mini python -m uvicorn webchat.server:app --port 8000
  ```
  ⚠ 이 게이트웨이에서 `gpt-5-mini`는 컨텍스트 윈도가 작아 큰 문서를 fetch하면 깨진다(실측).
  품질이 부족하면 계획 문서의 **경로 2(Claude MCP 커넥터)** 로 승격을 검토.

## 확정된 드라이브 검색 스키마 (참고)

`search` mcp_call의 `output`(JSON) → `{"results": [...]}`, **순서 = 관련도순**. 각 항목:
`title`(파일명) · `url`(webViewLink, 직접 옴) · `id` · `mime_type` · `file_or_folder` ·
`size` · `created_at`/`updated_at`/`viewedByMeTime` · `parent_ids`.
⚠ `search`는 **빈 query면 500** — 반드시 비어 있지 않은 키워드가 필요하다.
