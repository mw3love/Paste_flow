# PasteFlow

**순차 붙여넣기 클립보드 매니저** — Windows 전용

복사한 순서대로 `Ctrl+Shift+V`를 누를 때마다 다음 항목이 자동으로 붙여넣어집니다.  
별도의 모드 전환이나 설정 없이 항상 동작합니다.

---

## 주요 기능

- **순차 붙여넣기** — A → B → C 순서로 복사하면 붙여넣기도 A → B → C 순서로
- **다양한 형식 지원** — 텍스트, 이미지, HTML, RTF(노션 서식 포함)
- **클립보드 패널** — 복사 히스토리 목록, 고정(pin)
- **이미지 미리보기** — 다중 창 동시 표시, 휠 줌, 드래그 이동
- **드래그 붙여넣기** — 패널에서 텍스트/이미지를 앱으로 직접 드래그 (Alt+드래그 시 이미지를 임시 파일 경로로 변환 — Claude CLI 등)
- **이미지 → 경로 단축키** — `Ctrl+Shift+P` 한 번으로 클립보드 이미지를 임시 PNG로 저장 후 절대경로 텍스트로 현재 창에 자동 붙여넣기 (Claude CLI 워크플로용)
- **화면 OCR** — `Ctrl+Shift+S`로 화면 영역 선택 → 텍스트 추출 (Windows WinRT 무료 / AI API — Gemini·Claude·GPT 등 게이트웨이 모델 선택 가능)
- **OCR / AI 모델 분리** — 텍스트 추출은 저렴한 모델로, AI 답변은 강한 모델로 각각 지정 (설정 → AI 연동)
- **AI 웹 검색** — 내일 날씨·최신 뉴스·주가처럼 학습 데이터에 없는 정보를 물으면 AI가 알아서 웹을 검색해 답한다. 평범한 질문에는 검색이 돌지 않아 느려지지 않음 (Mindlogic 게이트웨이·Google AI Studio 양쪽 지원)
- **여러 모델 동시 비교** — 한 질문을 AI 모델 1·2·3에 동시에 던져 답변창을 나란히 띄우기 (설정에서 모델 2·3 지정 시)
- **시스템 트레이** — 백그라운드 상주, 트레이 아이콘으로 패널 열기
- **단축키 커스터마이징** — 패널 토글 / OCR / 이미지→경로 단축키 변경 가능

---

## 요구사항

- **OS**: Windows 10 / 11
- **Python**: 3.10 이상
- **패키지**:
  ```
  PyQt6
  pywin32
  Pillow
  winocr                  # 화면 OCR (Windows WinRT)
  google-generativeai     # Gemini OCR (선택)
  openai                  # OpenAI 호환 게이트웨이 OCR (선택)
  ddgs                    # AI 웹 검색 안전망 (선택, 키 불필요)
  ```

---

## 설치 및 실행

```bash
# 패키지 설치
pip install -r requirements.txt

# 실행
python -m pasteflow.main
```

---

## 단축키

| 단축키 | 동작 |
|--------|------|
| `Ctrl+Shift+V` | 큐에서 다음 항목 붙여넣기 |
| `Ctrl+Space` | 클립보드 패널 열기 / 닫기 (기본값, 설정에서 변경 가능) |
| `Ctrl+Shift+S` | 화면 영역 선택 OCR (기본값, 설정에서 변경 가능) |
| `Ctrl+Shift+P` | 클립보드 이미지를 임시 PNG로 저장 후 경로 텍스트로 자동 붙여넣기 (기본값, 설정에서 변경 가능) |
| 트레이 아이콘 좌클릭 | 클립보드 패널 열기 / 닫기 |

---

## exe 빌드

```bash
python -m PyInstaller PasteFlow.spec --clean
# 결과물: dist/PasteFlow-{버전}.exe
```

> 커밋 시 `pasteflow/` 소스가 변경되었으면 `post-commit` 훅이 백그라운드에서 자동 빌드합니다.  
> 빌드 로그: `build/post-commit-build.log`

---

## 버전

**v1.4.0** — API 키 DPAPI 암호화. OCR Gemini API 키(공식·게이트웨이)를 Windows DPAPI(`CryptProtectData`)로 암호화해 로컬 DB에 저장(`enc:v1:` prefix) — 현재 Windows 계정에만 묶이므로 PC 도난/디스크 복제 시에도 키 노출 차단. 다중 PC `settings.json` 자동 동기화 폐기(DPAPI 바인딩과 양립 불가, 클라우드 평문 키 노출 위험 제거). 모든 설정은 PC별 로컬 DB(`%LOCALAPPDATA%\PasteFlow\pasteflow.db`)에 저장. 시작 시 1회 마이그레이션으로 기존 평문 키 자동 암호화 + 고아 설정 키 4종(`ocr_api_key`·`ocr_base_url`·`hotkey_settings`·`panel_always_on_top`) 정리.

**v1.3.0** — OCR Gemini API 키를 backend별로 분리 (공식 Google AI Studio / 학교 게이트웨이). 설정에 **API 백엔드** 콤보 추가 — 두 backend의 API 키·모델·캐시를 동시 보관하고 자유롭게 전환. base_url 입력란은 게이트웨이일 때만 노출. 기존 단일 키(`ocr_gemini_api_key`)는 `base_url` 유무를 기준으로 1회 자동 마이그레이션 후 정리.

**v1.2.0** — 이미지→경로 단축키 (`Ctrl+Shift+P` 기본, 설정 가능) 추가 — 클립보드 이미지를 임시 PNG로 저장 후 절대경로 텍스트로 자동 붙여넣기. OCR Gemini 게이트웨이 본문 잘림 수정 (`max_tokens` 2048→16384, reasoning 토큰 차감 대응) — 기본 모델을 `gemini-3.1-flash-lite`로 변경. OCR Gemini 모델 선택 개선 — 검증 모델 화이트리스트 기반 콤보 정렬(가격순, 미검증 모델은 회색 + 구분선 아래), 호출 실패 시 `gemini-2.5-flash`로 자동 폴백 + 토스트 알림(게이트웨이 라인업 변경/광고-실제 불일치 대응).

**v1.1.0** — 순차 큐 자동 초기화 트리거 2종 추가 (마지막 복사로부터 idle timeout / 일반 Ctrl+V 시 즉시 비우기), 설정 다이얼로그에 idle 시간 노출, 다크 테마 툴팁 스타일.

**v1.0.0** — 최초 릴리즈
