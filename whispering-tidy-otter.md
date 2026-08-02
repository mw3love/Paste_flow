# 음성 입력 (mindlogic 게이트웨이 STT) — 계획

## Context

사용자가 마이크로 말하면 mindlogic 게이트웨이 AI가 텍스트로 변환해 현재 포커스된 입력창에
자동으로 써주길 원한다("어디에 붙여넣기"가 아니라 "말하면 타이핑됨"). 새 프로젝트 대비
PasteFlow 통합을 선택함 — 단축키 감지·게이트웨이 API 클라이언트·API 키 프로필 관리·
클립보드 교체 후 자동 붙여넣기 인프라를 이미 갖추고 있어 OCR 기능과 거의 동형이기 때문.

**타당성은 스파이크로 실증됨** (2026-08-02, 합성 음성 왕복 테스트): 게이트웨이 TTS로 만든
"오늘 회의는 오후 세시에 시작합니다" wav를 `chat.completions`에 `input_audio` 오디오 파트로
넣어 게이트웨이 41개 모델 중 **18종을 계열별로 골라 실호출**한 결과:

| 계열 | 시도 모델 수 | 결과 |
|---|---|---|
| Gemini | 8종(2.5-flash/pro, 3-flash-preview, 3.1-flash-lite/pro-preview, 3.5-flash/flash-lite, 3.6-flash) | **8/8 성공** — 전부 "오늘 회의는 오후 3시(세 시)에 시작합니다" 정확 인식 |
| GPT | 7종(5.3-chat-latest, 5.4, 5.4-mini, 5.4-nano, 5.5, 5.6-luna/sol/terra) | **0/7** — 전부 400 "Content blocks are expected to be either text or image_url type" |
| Claude | 1종(sonnet-5) | 0/1 — 400 "Audio content is not supported" |
| Grok | 2종(grok-4, grok-4.5) | 0/2 — 400 "Empty content block" |
| Perplexity | 1종(sonar-pro) | 0/1 — 400 invalid content type |

`audio_url` content type은 별도로 시도했으나 Gemini에서도 400(Invalid content part type) —
오디오는 `input_audio` 포맷 하나만 통한다.

**결론**: 오디오 입력 지원은 **모델 단위가 아니라 계열(family) 단위로 갈린다** — Gemini는
8/8 전부 되고 GPT/Claude/Grok/Perplexity는 시도한 것 전부 안 됐다(mini만의 문제가 아님,
2026-08-02 사용자 확인 요청으로 재검증). 그래서 화이트리스트는 개별 모델명 나열이 아니라
**`ocr_engine.family_of(name) == "Gemini"`로 계열 단위 필터**가 안전하다 — 신규 Gemini
모델이 추가돼도 자동으로 커버되고, 반대로 GPT 계열에 오디오가 나중에 열려도(현재는 전부
막힘) 자동 오인 허용이 안 된다(그때는 재검증 후 화이트리스트 갱신).

⚠ **미검증**: 실제 마이크 육성 음성(잡음·발음·거리), 긴 발화(현재는 3초 합성음성만),
오디오 길이별 토큰 비용. 프록시검증(합성 음성) 단계이며 실조건검증은 구현 후 진행.

## UX 결정 (사용자 확인 완료, 2026-08-02)

- **녹음 방식**: 푸시투토크 — 단축키를 누르고 있는 동안 녹음, 떼면 즉시 종료·전송.
- **결과 처리**: OCR과 동일 — 클립보드 교체 + 히스토리 저장(`_persist_clipboard_item`) 후
  자동 Ctrl+V. 히스토리에 남아 나중에 다시 붙여넣기 가능.

## 핵심 구조 결정

- **오디오 캡처**: `sounddevice`(신규 의존성, PortAudio 번들 wheel이라 추가 설치 불필요·
  이미 설치·동작 확인됨) + stdlib `wave`로 16kHz mono 16bit wav 인코딩. `ocr_engine.py`가
  PIL 이미지를 다루듯, 새 모듈이 오디오 bytes를 다룬다.
- **API 호출**: 신규 `pasteflow/stt_engine.py` — `ocr_engine.py`의 `_get_client`(캐시된
  openai 클라이언트)·`_normalize_base_url`·`_call_with_fallback` 패턴을 그대로 재사용.
  크리덴셜(`base_url`/`api_key`)도 기존 AI 프로필을 공유 — 새 시크릿 키 불필요.
  **모델은 Gemini 계열로 화이트리스트**(신규 설정 키 `stt_model_gateway`, 콤보에
  `ocr_engine.family_of(name) == "Gemini"`인 모델만 표시 — 개별 모델명 나열이 아니라 계열
  단위 필터, 18종 실호출로 "모델이 아니라 계열 단위로 갈린다"를 확인했기 때문. `group_models`가
  이미 계열별로 묶어 주므로 필터 조건만 추가하면 됨).
- **단축키 = 누름/뗌 둘 다 추적**(기존 단축키는 keydown 1회 감지뿐이라 신규 패턴):
  `paste_interceptor.py`에 keydown→`on_stt_start()`, keyup→`on_stt_stop()` 콜백 추가.
  기존 "suppress한 단축키의 keyup도 함께 막는다" 로직(`_suppress` 셋)이 이미 keyup을
  추적하는 구조라 그 위에 얹는다.
- **진행 표시**: `_start_cursor_progress`(OCR과 공유하는 커서 모니터 정중앙 칩) —
  누르는 동안 "🎤 녹음 중…", 떼면 "🎤 인식 중…" → 완료 시 `_finish_cursor_progress`로
  인식 텍스트 앞부분 미리보기(OCR의 `✓ 인식 앞부분…`과 동일 패턴).
- **길이 상한**: 무한 녹음 방지로 최대 30초 자동 컷(토큰 비용·응답 지연 억제) — 넘기면
  자동 종료+전송, 토스트로 "30초 제한" 안내.

## 하위 단계

1. **`stt_engine.py`** — 녹음 관리(`sounddevice.InputStream` 시작/정지, PCM→wav bytes)
   + `transcribe(wav_bytes, api_key, base_url, model) -> str`(게이트웨이 호출, 폴백 없음 —
   Gemini 화이트리스트라 model_not_found 폴백 체인이 OCR과 의미가 다름, 필요성 재검토).
   검증: 독립 스크립트로 실마이크 녹음 → 인식 텍스트 확인(오늘 스파이크의 실마이크 버전).
2. **`paste_interceptor.py`** — push-to-talk 훅(keydown 시작/keyup 종료), `set_stt_hotkey()`.
   검증: 로그로 keydown/keyup 타이밍 확인, 다른 단축키(Ctrl+Shift+V 등) 간섭 없음.
3. **`main.py` 배선** — 시작 콜백에서 녹음 시작, 종료 콜백에서 정지+워커스레드로
   `stt_engine.transcribe()` 호출 → 결과를 OCR과 동일 경로(`_persist_clipboard_item` +
   `_send_clean_key`)로 처리. 진행 칩 연결.
   검증: 실제 입력창(메모장·브라우저)에 육성 발화가 텍스트로 자동 입력됨.
4. **`settings_dialog.py`** — 단축키 행(`AI` 탭 기능 단축키 그룹에 추가) + STT 모델 콤보
   (Gemini 전용 필터) + 연결 테스트에 STT 프로브 추가(OCR 프로브와 나란히).
   검증: 단축키 재지정 가능, 모델 콤보에 gpt/claude 안 뜸.
5. **길이 상한·에러 처리** — 30초 컷, 무음 녹음(빈 wav) 시 API 호출 생략+토스트,
   네트워크 실패 시 에러 토스트(OCR과 동일 안내 패턴).

## 리스크

- **실마이크 음질 미검증**: 잡음·거리·발음별 정확도는 합성음성 테스트로 알 수 없다 —
  1단계에서 가장 먼저 확인.
- **오디오 길이·토큰 비용**: 긴 발화일수록 base64 크기·토큰이 커짐 — 30초 상한으로 완화하나
  실제 과금 단가는 미확인(`costs.json`에 chat 오디오 입력 단가 없음, 실사용 후 관찰 필요).
- **게이트웨이가 Gemini 오디오 지원을 나중에 바꿀 수 있음**: OCR과 동일한 구조적 리스크,
  화이트리스트 방식이라 최소한 "안 되는 모델을 골라서 400" 사고는 방지됨.
- **keydown/keyup 훅은 새 패턴**이라 기존 suppress 단축키보다 구현 복잡도가 약간 높음 —
  훅 콜백에서 무거운 작업(녹음 시작 자체는 가벼움) 금지 원칙은 동일하게 지킨다.

## 수정/생성 파일

- **신규** `pasteflow/stt_engine.py`
- **수정** `pasteflow/paste_interceptor.py` — push-to-talk keydown/keyup 훅
- **수정** `pasteflow/main.py` — 녹음 시작/종료 배선, STT 워커, 결과 처리
- **수정** `pasteflow/ui/settings_dialog.py` — 단축키 행 + STT 모델 콤보 + 연결 테스트
- **수정** `requirements.txt` — `sounddevice` 추가

## 검증 (수동·실조건)

- 스파이크: ✓ 완료(합성 음성 왕복 성공, 위 표).
- 각 하위 단계: 위 명시된 개별 검증.
- 최종: 실제 마이크로 한국어 문장을 말하고 푸시투토크로 메모장/브라우저 입력창에
  자동으로 텍스트가 써지는지 확인 + 히스토리에 남는지 확인.
- 회귀: `pytest tests/` 기존 테스트 유지(신규 모듈은 TDD 대상 표에 없어 수동확인 대상으로
  분류 — `models.py`/`database.py`/`paste_queue.py`만 TDD 필수).

## 구현 상태 (2026-08-02)

전 단계 구현 완료. 검증 상태는 항목별로 다르다(규칙 11-c 정직표기):

| 항목 | 상태 | 근거 |
|---|---|---|
| `stt_engine.py`(녹음+게이트웨이 호출) | 실조건검증 | 실마이크 무음 녹음으로 wav 생성 확인 + 실제 게이트웨이 호출로 합성음성 재확인 |
| `paste_interceptor.py` 푸시투토크 훅 | 실조건검증(부분) | keydown은 SendInput 실주입으로 확인. keyup은 SendInput이 항상 LLKHF_INJECTED가 서서 흉내낼 수 없어(자기충돌 방지 설계), 실제 콜백을 비주입 구조체로 직접 호출해 분기 로직 확인 — 진짜 물리 keyup 자체는 미검증 |
| `main.py` 배선 | 프록시검증 | 문법·구조 확인, `pytest tests/` 129개 회귀 없음. 전체 앱 기동 후 실통합은 미검증(사용자의 기존 PasteFlow 프로세스가 이미 실행 중이라 새 인스턴스가 그걸 대체하지 못함) |
| `settings_dialog.py` UI(STT 단축키·모델 콤보·크레딧 확인) | 실조건검증 | 오프스크린 렌더 스크린샷 확인 + 크레딧 확인 버튼 실클릭·실호출("✓ 잔여 9,088.5 / 10,000 크레딧") |
| 크레딧 확인 API(`ocr_engine.get_credit_balance`) | 실조건검증 | 실제 게이트웨이 `/credits/` 호출 성공 |
| **실제 음성으로 끝까지(Alt+R 눌러 말하기 → 입력창에 자동 입력)** | **미검증** | 합성 음성이 아닌 사용자 육성은 자체적으로 재현 불가 — PasteFlow 재시작 후 사용자가 직접 확인 필요 |

**다음 필수 단계**: 실행 중인 PasteFlow 인스턴스를 재시작(트레이 종료 → 재실행)해 새 코드를
반영한 뒤, Alt+R을 눌러 말하고 떼서 실제 입력창에 텍스트가 자동으로 들어가는지 확인.

## 실사용 피드백 반영 (2026-08-02, Wispr Flow 비교)

사용자가 Wispr Flow와 나란히 써보고 두 가지를 지적:

1. **말 끝나고 바로 떼면 끝자락이 잘림** — `sd.InputStream` 기본(high) 버퍼링이 마지막
   발화를 아직 못 넘긴 상태에서 `stop()`이 호출돼 잘렸을 가능성 + 키를 떼는 사람의 반응
   시간. 두 갈래로 완화: ① `Recorder.start()`에 `latency="low"` 지정(근본 지연 축소),
   ② `main.py`가 keyup 즉시 멈추지 않고 `_STT_TAIL_PAD_MS`(400ms) 여유시간 뒤 실제로
   멈추도록 `_on_stt_stop`→`_finish_stt_recording` 2단계로 분리. 실조건검증은 미완료(사용자
   재확인 필요) — 메커니즘은 합리적이나 "얼마나 잘렸었는지" 정량 측정은 못 했음.
2. **Wispr 대비 체감 느림** — 실측(3초 한국어 음성, 동일 문장 5회 호출)으로 원인을 좁힘:
   기본 모델이던 `gemini-2.5-flash`가 3.1~6.0s(평균 ~4.4s)였고 `gemini-3.1-flash-lite`는
   2.2~3.0s(평균 ~2.6s) — 인식 결과는 완전히 동일. **모델 선택이 주 병목**이었다.
   `STT_FALLBACK_DEFAULT`(ocr_engine.py)를 flash-lite로 바꾸고 설정창 콤보 기본 선택도
   맞췄다(`_init_stt_model_slot`). max_tokens 축소(4096→512)는 0.2~0.4s 개선에 그쳐(노이즈
   수준) 채택 안 함 — OCR 쪽 교훈(낮은 max_tokens가 thinking 모델에서 응답을 자름)을 감안해
   그대로 유지. 남은 지연(2~3초)은 네트워크 RTT+모델 처리 시간으로, Wispr 같은 전용 STT
   서비스 대비 근본적으로 더 빠르게 만들 여지는 제한적(범용 LLM에 오디오를 실어 보내는
   구조 자체의 한계) — 프록시검증(반복 호출 시간 측정)이며 사용자 체감 재확인 필요.

**미착수 — 사용자 확인 필요**: Wispr Flow의 상시 플로팅 마이크 pill(마우스 근처 호버 시
등장)·음량 반응 이퀄라이저 UI는 별도 기능 추가 스코프라 착수하지 않음("나중에 별도로"로
사용자 확인, 2026-08-02).

## 후속 버그: Alt가 메뉴바 포커스를 훔쳐감 (2026-08-02, 사용자 실사용 중 발견)

메모장에서 `Alt+R`을 쓰면 R을 통째로 suppress해 앱이 "Alt만 눌렸다 떼짐"을 본다 —
Windows가 이를 메뉴바 포커스 진입으로 해석해(표준 동작), 직후 우리가 주입하는 Ctrl+V가
메뉴에 먹혀 정작 입력창엔 아무것도 안 들어갔다. `_send_clean_key`의 Ctrl+Shift IME 전환
마스킹과 동일 기법(미할당 키 `VK_MASK`로 조합을 더럽힘)을 `_stt_need_alt`일 때 keydown
시점에 적용해 해결. **실조건검증 완료**(2026-08-02): 실제 메모장 창을 띄우고 우리 훅으로
Alt+R(R은 SendInput 주입, 실제 suppress 경로를 그대로 탐) → 실제 Ctrl+V 주입 → 메모장
Edit 컨트롤 텍스트를 WM_GETTEXT로 직접 읽어 `"PASTEFLOW_ALT_MASK_TEST_12345"`가 정확히
들어간 것 확인(수정 전에는 메모장 텍스트칸이 빈 채로 남았을 것 — 재현 자체는 하지 않고
수정 후 상태만 확인).
