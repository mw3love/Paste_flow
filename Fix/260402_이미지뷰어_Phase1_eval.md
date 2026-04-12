# 이미지 미리보기 Phase 1 평가 보고서

- **평가 대상 커밋**: `e387bac` — `Feat: 이미지 미리보기 드래그 이동·닫기 버튼 추가`
- **평가 파일**: `pasteflow/ui/image_preview.py`
- **평가 일시**: 2026-04-02

---

## 1. 계획서 Phase 1 체크리스트 완료 여부

| # | 항목 | 상태 | 비고 |
|---|------|------|------|
| 1 | 윈도우 플래그 `ToolTip` → `Tool \| FramelessWindowHint \| WindowStaysOnTopHint` | ✅ 완료 | L35~39 |
| 2 | `WA_ShowWithoutActivating` 유지 | ✅ 완료 | L41 |
| 3 | `mousePressEvent` — LeftButton 이면 드래그 시작점 저장 | ✅ 완료 | L146~151 |
| 4 | `mouseMoveEvent` — 델타만큼 창 이동 | ✅ 완료 | L153~156 |
| 5 | `keyPressEvent` — ESC 시 `self.close()` | ✅ 완료 | L166~170 |
| 6 | 우상단 × 닫기 버튼 (`QToolButton`) | ✅ 완료 | L78~83 |
| 7 | 최대 표시 크기 상한 320×240 → 640×480 | ✅ 완료 | L18~19 |
| 8 | 커밋 메시지 일치 | ✅ 완료 | 메시지 동일 |

**체크리스트 완료율: 8/8 (100%)**

---

## 2. 기술적 정확성

### 2-1. 윈도우 플래그 및 어트리뷰트

- `Qt.WindowType.Tool | FramelessWindowHint | WindowStaysOnTopHint` 조합 — CLAUDE.md 설계 규칙("Qt.WindowType.Tool | WindowStaysOnTopHint")과 일치. ✅
- `WA_ShowWithoutActivating` — 패널 포커스 보호 목적 유지. ✅
- `WA_TranslucentBackground` — 기존 속성 유지. ✅

### 2-2. 드래그 구현

- `mousePressEvent`: `event.globalPosition().toPoint() - self.frameGeometry().topLeft()` — PyQt6 표준 패턴. ✅
- `mouseMoveEvent`: `Qt.MouseButton.LeftButton` 비트마스크 체크 (`&`) — 올바름. ✅
- `mouseReleaseEvent`: `_drag_pos = None` 초기화 — 올바름. ✅

### 2-3. 레이아웃 구조

- `QLabel`(기존) → `QWidget` + `QVBoxLayout` 기반 재구성으로 top_bar + image_label 분리. ✅
- `close_btn.setFixedSize(18, 18)` 고정 크기. ✅
- `_image_label.adjustSize()` + `self.adjustSize()` 순서 — 내부 레이블 크기 먼저 조정 후 창 크기 조정. ✅

### 2-4. PIL import 위치 변경

- 기존: 모듈 최상단 `from PIL import Image` (import 실패 시 모듈 로드 불가)
- 변경 후: `_to_png()` 내부 lazy import — PIL 미설치 환경에서도 모듈 로드 가능. 긍정적 변경. ✅

---

## 3. 회귀 위험

### 3-1. 기존 인터페이스 유지 여부

| 메서드 | 이전 | 현재 | 상태 |
|--------|------|------|------|
| `show_preview(image_data, global_pos)` | ✅ | ✅ | 유지 |
| `toggle_preview(image_data, global_pos)` | ✅ | ✅ | 유지 |
| `hide_preview()` | ✅ | ✅ | 유지 |
| `instance()` classmethod | ✅ | ✅ | 유지 |
| `_instance` 클래스 변수 | ✅ | ✅ | 유지 |

**회귀 없음 — `main.py`, `panel.py` 호출부 변경 불필요.** ✅

---

## 4. CLAUDE.md 원칙 준수

| 항목 | 상태 | 비고 |
|------|------|------|
| Catppuccin Mocha 색상 | ✅ | `_BG="#1e1e2e"`, `_BORDER="#45475a"`, `_TEXT="#cdd6f4"`, `_SURFACE1="#313244"` |
| 프레임리스 드래그 패턴 | ✅ | `panel.py`의 드래그 패턴과 동일 구현 |
| 요청하지 않은 기능 추가 없음 | ✅ | Phase 1 범위 내 구현만 포함 |

---

## 5. 엣지 케이스 분석

### [Medium] M-1 — `close_btn` 클릭 시 `_drag_pos` 미초기화 가능성

- **위치**: `mousePressEvent` L146~151
- **현상**: 닫기 버튼(QToolButton)을 클릭할 때 버튼 위젯이 이벤트를 먼저 소비하므로 부모(`ImagePreviewPopup`)의 `mousePressEvent`에 LeftButton 이벤트가 도달하지 않음.
- **영향**: 실제로는 버튼 클릭 시 `_drag_pos`가 설정되지 않으므로 드래그가 시작되지 않는다. 즉, 정상 동작.
- **위험도**: Low — 현재는 문제없음. 단, 향후 `close_btn` 위에서 드래그를 시도할 경우 이벤트 전파 경로 확인 필요.

### [Medium] M-2 — `toggle_preview` 재호출 시 기존 드래그 위치로 이동 재설정

- **위치**: `show_preview()` L117~127
- **현상**: 창이 열려 있는 상태에서 `toggle_preview` → `isVisible() True` → `self.hide()` 로 숨겨짐(올바름). 다시 클릭 시 `show_preview` 재호출 → `self.move(x, y)`로 커서 기준 재배치. ✅
- **위험도**: Low — 정상 동작.

### [Medium] M-3 — `WA_ShowWithoutActivating` + `close_btn` 클릭 조합에서 포커스 미획득

- **위치**: L41, L82
- **현상**: `WA_ShowWithoutActivating` 때문에 창이 열려 있어도 포커스를 자동으로 받지 않음. 닫기 버튼은 마우스 클릭으로 동작하므로 영향 없음. 단 **ESC는 포커스 획득 후에만 동작** — 계획서에 명시된 제한 사항이므로 의도된 동작.
- **위험도**: Low — 계획 내 허용 범위.

### [High] H-1 — `mouseReleaseEvent`에서 `_drag_pos = None` 설정 후 `super()` 미호출

- **위치**: `mouseReleaseEvent` L158~160
- **현상**: `super().mouseReleaseEvent(event)` 호출이 있음. ✅ 검토 결과 실제로는 `super()` 호출이 존재하여 문제 없음.
- **위험도**: Low — 오탐. 이하 재분류.

### [High] H-1 — `mouseMoveEvent`: 드래그 중 화면 경계 클램핑 없음

- **위치**: `mouseMoveEvent` L153~156
- **현상**: 드래그로 창을 이동할 때 화면 밖으로 끌어낼 수 있음. `show_preview()` 내 초기 배치 시에는 경계 클램핑이 있지만, 드래그 이동 중에는 없음.
- **영향**: 사용자가 창을 화면 밖으로 드래그하면 닫기 버튼이 보이지 않게 될 수 있음. ESC로 닫을 수 있으나 포커스 획득이 전제(M-3 참조).
- **완화 요인**: Phase 2 계획서에 `_apply_scale()` 내 화면 경계 클램핑이 포함되어 있으며, 계획서 Phase 1 요구사항에 드래그 중 클램핑은 명시되지 않음.
- **위험도**: Medium — Phase 1 범위 외이지만 사용성 문제.

### [Low] L-1 — `_image_label.adjustSize()` 중복 호출 가능성

- **위치**: L114, L115
- **현상**: `self._image_label.adjustSize()` 후 `self.adjustSize()` — QWidget 레이아웃에서는 `adjustSize()`가 레이아웃을 통해 내부 위젯 크기를 반영하므로 `_image_label.adjustSize()`는 불필요할 수 있음. 그러나 부작용은 없음.
- **위험도**: Low — 기능 영향 없음.

### [Low] L-2 — `setPixmap` 후 `QLabel` `setAlignment` 재적용 불필요

- **위치**: L89, L113
- **현상**: `__init__`에서 `AlignCenter` 설정 후 `setPixmap`은 얼라인먼트를 유지함. 추가 호출 없음. ✅

---

## 6. 종합 심각도 요약

| 심각도 | 건수 | 항목 |
|--------|------|------|
| Critical | 0 | — |
| High | 0 | — |
| Medium | 1 | M-2: 드래그 중 화면 경계 클램핑 없음 (Phase 1 범위 외, Phase 2에서 해소 예정) |
| Low | 2 | L-1: `adjustSize` 중복 호출, L-3: `WA_ShowWithoutActivating` + ESC 제한(계획 내) |

> **Phase 1 품질 판정: 통과**
> Critical/High 없음. Medium 1건은 Phase 2 `_apply_scale()` 클램핑으로 해소 예정이며 Phase 1 계획서 요구사항 외 사항임.

---

## 7. Phase 2 진행 권고

Phase 1 구현은 계획서 요구사항을 100% 충족하고 기존 인터페이스를 완전히 유지하므로 **Phase 2(휠 줌) 진행을 권장**한다.

Phase 2 구현 시 `_apply_scale()` 내 화면 경계 클램핑을 포함하면 위 Medium 항목(M-2)도 자동으로 해소된다.
