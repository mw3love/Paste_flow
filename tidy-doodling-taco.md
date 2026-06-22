# 3단계 — 마그네틱 영역 캡처 (Snipaste식 요소 스냅)

## Context

PasteFlow에 Snipaste식 캡처를 단계적으로 추가 중이다. 1단계(Alt+F3 핀)·2단계(Alt+F2 드래그 캡처)는 완료·검증됨. 3단계는 캡처의 하이라이트: **마우스를 올리면 커서 아래 UI 요소(크롬 탭·북마크·작업표시줄 아이콘 등)가 자동으로 네모 박스로 잡히고, 클릭하면 그 영역이 캡처**되는 마그네틱 기능이다.

**타당성은 스파이크로 실증됨** (`_spike_uia.py`): Windows UI Automation `ElementFromPoint`가 작업표시줄 버튼·트레이·**크롬 탭**(예: `size=72x41 name='…YouTube…'`)까지 요소 단위로 스냅하고, 호출당 **3.9ms**로 라이브 hover에 충분. 의존성 `comtypes`는 이미 설치됨.

## 핵심 구조 결정 — 접근법 (A): 클릭-통과 오버레이 + 라이브 UIA 조회

**문제**: 캡처 오버레이가 화면을 덮으면 `ElementFromPoint`가 *오버레이 자신*을 짚는다.
**해법**: 오버레이를 **클릭-통과**(`Qt.WindowType.WindowTransparentForInput`)로 만들어 ElementFromPoint가 아래 실제 창을 짚게 한다. 오버레이는 얼린 스크린샷+딤+하이라이트만 그린다. 입력은:
- **hover**: QTimer(~30ms)가 `GetCursorPos` → UIA `ElementFromPoint` → 하이라이트 rect 갱신. (훅 불필요)
- **클릭**: 오버레이가 클릭-통과라 클릭이 아래 앱으로 새므로(탭 전환 등), **WH_MOUSE_LL 마우스 훅**으로 좌클릭을 suppress하고 그 시점 하이라이트 요소를 캡처. 우클릭=취소.
- **ESC**: QTimer가 `GetAsyncKeyState(VK_ESCAPE)` 폴링(또는 기존 키보드 훅 경로).

근거 패턴(코드에 이미 존재):
- 클릭-통과 Qt 창: `image_annotator.py:683` `_ColorLoupe` (`WindowTransparentForInput` + `WA_ShowWithoutActivating`).
- LL 훅 ctypes 셋업: `paste_interceptor.py:104-166`(`SetWindowsHookExW`·`HOOKPROC`·`CallNextHookEx`) — WH_MOUSE_LL에 그대로 복제.
- 스크린샷 freeze·DPR crop: `ocr_overlay.py`의 `_ScreenOverlay.prepare()`(grabWindow)·`crop_selection()`(205줄, DPR 물리픽셀 crop).
- UIA 조회 로직: `_spike_uia.py`(CUIAutomation 단일 인스턴스 + `ElementFromPoint(tagPOINT)` + `CurrentBoundingRectangle`).

**Alt+F2에 통합**: 별도 단축키 없이 기존 캡처에 흡수 — hover=요소 스냅(신규), 드래그=자유 사각형(2단계 동작을 폴백으로 보존). 2단계의 plain `OcrOverlay` 인스턴스를 신규 마그네틱 오버레이로 교체한다.

## 하위 단계 (3a → 3b → 3c, 각자 검증)

### 3a — 요소 모드 오버레이 (하이라이트만)
- 신규 `pasteflow/ui/capture_overlay.py`: 모니터별 클릭-통과 프레임리스 토픽맵 창. `prepare()`로 자기 화면 grabWindow → 딤 마스크 + 커서 아래 요소 하이라이트(밝게 un-dim + teal 테두리). QTimer로 커서 추적·UIA 조회·repaint, ESC 폴링.
- 신규 `pasteflow/uia.py`(또는 capture_overlay 내부): `UiaHitTester.rect_at(x,y) -> QRect|None` — CUIAutomation 단일 인스턴스 재사용, 0×0/빈 영역은 None.
- **캡처·클릭 없음**. 검증: 하이라이트가 크롬 탭·북마크·작업표시줄에서 요소 단위로 착착 따라오고 ESC로 닫힘.

### 3b — 클릭 캡처 (마우스 훅)
- WH_MOUSE_LL 훅을 캡처 시작 시 설치·종료 시 해제(transient). 콜백은 **trivial 유지**(이동=좌표 저장만, ElementFromPoint는 QTimer가 담당 — LL 훅 콜백이 무거우면 시스템 마우스가 끊김). 좌클릭 down/up suppress→현재 하이라이트 요소 rect 확정; 우클릭 suppress→취소.
- 확정 rect를 `_ScreenOverlay.crop_selection` 패턴으로 얼린 스크린샷에서 crop → `region_captured(QPixmap)` emit.
- **main.py 변경 최소**: `self._capture_overlay`를 신규 오버레이로 교체, `region_captured`→기존 `_on_capture_region`(이미 DIB 클립보드+파일+토스트 처리, 무수정) 그대로 연결.
- 검증: Alt+F2 → 크롬 탭 클릭 → 그 탭 영역이 클립보드+파일로 캡처(그림판/카톡 붙여넣기)되고, **클릭이 탭 전환을 일으키지 않음**(suppress 확인). 우클릭 취소.

### 3c — 폴백·확장·엣지
- 빈 영역에서 좌버튼 누른 채 이동>임계 → 자유 사각형(2단계 드래그 동작). 마우스 훅이 down→move→up을 추적해 드래그/클릭 구분.
- 휠/방향키 → 부모 요소로 확장(선택 영역 키우기, Snipaste식). UIA `TreeWalker`로 부모 rect. *(선택 — 가치 보고 후 결정)*
- 엣지: 빈 바탕화면(요소 0×0) → 자유 드래그만, UIA 미노출 창(관리자 권한 등) → 폴백 드래그, 멀티모니터 rect 매핑.

## 수정/생성 파일
- **신규** `pasteflow/ui/capture_overlay.py` — 마그네틱 오버레이(클릭-통과 per-monitor + 하이라이트 + QTimer 추적 + 3b 마우스 훅 + 3c 폴백). `ocr_overlay.py`의 screenshot/crop 패턴 차용.
- **신규** `pasteflow/uia.py` — 얇은 UIA hit-test 헬퍼(comtypes, 스파이크 코드 기반).
- **수정** `pasteflow/main.py` — `_capture_overlay`를 신규 오버레이로 교체(연결·`_on_capture_region` 무수정).
- **수정** `requirements.txt` — `comtypes` 명시(이미 설치됨, 선언만).
- **삭제** `_spike_uia.py`(throwaway, 3단계 진입 시 정리).

## 주요 리스크 (3a에서 우선 검증)
- **멀티-DPI 좌표 매핑**: UIA BoundingRectangle(가상 데스크톱 좌표) → 올바른 모니터 오버레이 → DPR crop. 스파이크가 멀티모니터(좌표 3841,-101)에서 정상 rect를 줬으므로 가능하나, 실기기에서 crop 정합 확인 필요.
- **WH_MOUSE_LL 지연**: 콜백이 무거우면 전체 시스템 마우스가 끊김 → 콜백은 좌표 저장+클릭 suppress만, UIA 조회는 QTimer로 분리.
- **클릭-통과 부작용**: hover 시 아래 앱의 hover 상태가 실제로 바뀜(탭 닫기버튼 노출 등) — 얼린 스크린샷이 가려 무해. 막아야 할 건 클릭(탭 전환)뿐 → 훅 suppress.

## 검증 (수동·실조건 — 헤드리스 불가)
- 3a: Alt+F2 → 크롬 탭/북마크/작업표시줄 hover → 요소별 하이라이트 스냅, ESC 닫힘.
- 3b: Alt+F2 → 탭 클릭 → 해당 요소 클립보드+파일 캡처(그림판·카톡 붙여넣기), 탭 전환 안 됨, 우클릭 취소.
- 3c: 빈 영역 드래그=자유 사각형, (구현 시) 휠=부모 확장.
- 회귀: TDD 모듈 무수정 — `pytest tests/` 92개 유지. 2단계 드래그 캡처가 폴백으로 계속 동작하는지.
