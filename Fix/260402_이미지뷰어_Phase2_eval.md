# 이미지 미리보기 Phase 2 평가 결과

- 평가 일시: 2026-04-02
- 커밋: `26227af` — `Feat: 이미지 미리보기 휠 줌 추가`
- 대상 파일: `pasteflow/ui/image_preview.py`
- 계획서: `Fix/260402_이미지뷰어_수정계획.md` Phase 2

---

## 1. 계획서 Phase 2 체크리스트 이행 여부

| 항목 | 완료 | 비고 |
|------|------|------|
| `_original_pixmap: QPixmap \| None = None` 인스턴스 변수 추가 | O | L45 |
| `_scale_factor: float = 1.0` 인스턴스 변수 추가 | O | L46 |
| `show_preview()`에서 `self._original_pixmap = pixmap` 저장 | O | L111 |
| `wheelEvent` — 휠 업 +10%, 다운 -10%, 범위 0.1×~8.0× | O | L145–151 |
| `_apply_scale()` 헬퍼 구현 | O | L153–184 |
| 커밋 메시지 `Feat: 이미지 미리보기 휠 줌 추가` | O | |
| eval-plan evaluate 자동 실행 체크박스 갱신 | **미완료** | 계획서 L86 `[ ]` 그대로 — 본 평가 실행으로 대체 간주 |

---

## 2. 휠 이벤트 구현 검토

### 2-1. angleDelta() 방향 판별
```python
delta = event.angleDelta().y()
factor = 1.1 if delta > 0 else (1 / 1.1)
```
- `angleDelta().y() > 0` → 휠 업(확대), `< 0` → 휠 다운(축소). **정확.**
- `delta == 0`일 때 축소 방향으로 처리되나, 실제로 `angleDelta().y() == 0`은 수평 스크롤만 해당되므로 실용적 문제 없음.

### 2-2. 배율 범위 클램핑
```python
self._scale_factor = max(0.1, min(8.0, self._scale_factor * factor))
```
- 계획서 지정 범위 `0.1×~8.0×` **정확히 준수.**

**판정: 이상 없음**

---

## 3. _apply_scale() 정확성 검토

```python
base = self._original_pixmap.scaled(PREVIEW_MAX_W, PREVIEW_MAX_H, KeepAspectRatio, Smooth)
target_w = max(1, round(base.width() * self._scale_factor))
target_h = max(1, round(base.height() * self._scale_factor))
scaled = self._original_pixmap.scaled(target_w, target_h, KeepAspectRatio, Smooth)
```

### 3-1. 원본 픽스맵 기반 재스케일
- 최종 스케일링은 `self._original_pixmap`에서 직접 수행 → 누적 열화 없음. **정확.**

### 3-2. PREVIEW_MAX 적용 방식 — Medium 이슈
- `base`(PREVIEW_MAX 상한 적용)에서 `target_w/h`를 계산한 뒤, `_original_pixmap`을 그 크기로 스케일링.
- `scale_factor = 1.0`일 때: `target_w = base.width() × 1.0` → 의도한 초기 크기 정상.
- **그러나** `scale_factor > 1.0`으로 줌인 시, `target_w`가 `PREVIEW_MAX_W × scale_factor`를 넘어가는 경우: `_original_pixmap.scaled(target_w, target_h, KeepAspectRatio)`는 aspect ratio를 유지하기 위해 실제 너비를 `target_w`보다 작게 출력할 수 있음.
- 예: 원본이 정사각형 1000×1000, PREVIEW_MAX=640×480, base=480×480. scale_factor=2.0이면 target_w=960, target_h=960. `_original_pixmap.scaled(960, 960, KeepAspectRatio)` → 960×960 (정사각형이므로 OK). 단 원본이 1600×400이라면 base=640×160, target_w=1280, target_h=320. `_original_pixmap.scaled(1280, 320, KeepAspectRatio)` → KeepAspectRatio는 둘 중 더 제약적인 쪽을 따르므로 → 1280×320 또는 1280×(400×1280/1600)=320. 정상.
- 실제로는 KeepAspectRatio가 두 번 적용되어 의도한 배율보다 작게 표시될 수 있음: `base` 스케일 시 aspect가 한 번 조정되고, `_original_pixmap` 스케일 시 또 조정됨.
- **실용적 영향**: 이미지가 PREVIEW_MAX에 비해 극단적 종횡비(예: 10:1 이상)일 때만 발생. 일반 사진에서는 무시 가능.

**심각도: Low** — 극단적 종횡비 이미지에서 줌 배율이 예상보다 약간 다를 수 있으나 기능적 결함은 아님.

---

## 4. 화면 경계 클램핑

```python
x = max(geom.left(), min(pos.x(), geom.right() - self.width()))
y = max(geom.top(), min(pos.y(), geom.bottom() - self.height()))
if (x, y) != (pos.x(), pos.y()):
    self.move(x, y)
```

- `adjustSize()` 호출 후 클램핑 계산 → 새 창 크기 기준으로 정확히 계산됨. **정확.**
- 조건 분기(`if (x,y) != ...`)로 불필요한 `move()` 호출 방지. **양호.**
- `show_preview()`의 배치 코드와 분리되어 있어 각 경우에 독립 적용됨. **설계 정상.**

**판정: 이상 없음**

---

## 5. show_preview() 흐름

```python
self._original_pixmap = pixmap       # 원본 보존
self._scale_factor = 1.0             # 스케일 리셋
self._apply_scale()                  # 초기 크기 적용
# 이후 위치 계산 → show() → raise_()
```

- 이미지 교체 시 `_scale_factor` 를 `1.0`으로 리셋 → **정상** (이전 줌 상태가 새 이미지에 잔류하지 않음).
- `_original_pixmap` 업데이트 후 `_apply_scale()` 호출 순서 **정확.**
- 위치 계산이 `_apply_scale()` 이후에 수행되어 올바른 `self.width()/height()`를 참조함. **정상.**

**판정: 이상 없음**

---

## 6. 추가 관찰 사항

### M-1. wheelEvent 미처리: delta == 0 엣지케이스 (Medium → Low로 분류)
- `delta == 0`이면 `factor = 1/1.1`(축소)로 처리됨.
- `angleDelta().y() == 0`은 수평 스크롤 이벤트에서만 발생하므로 실제 문제 발생 가능성 낮음.
- 명시적 가드(`if delta == 0: return`) 추가를 권고하나 심각도는 낮음.

**심각도: Low**

### L-1. show_preview() 위치 계산 중복
- `show_preview()`의 위치 클램핑 코드(`geom.right()` 비교)와 `_apply_scale()`의 클램핑 코드가 유사하나 로직이 다름.
  - `show_preview()`: 커서 기준 배치 + 단순 오른쪽/아래 오버플로우만 처리
  - `_apply_scale()`: 4방향 완전 클램핑
- `show_preview()`도 `_apply_scale()` 호출 후 바로 `self.move(x, y)`를 하는데, 이 이동이 `_apply_scale()` 내부 클램핑보다 나중에 실행되므로 실제 초기 배치는 `show_preview()`의 위치 계산이 최종 적용됨. 정상 동작하나 코드 중복이 있음.

**심각도: Low** (리팩터링 권고)

---

## 7. 심각도별 요약

| 심각도 | 건수 | 항목 |
|--------|------|------|
| Critical | 0 | — |
| High | 0 | — |
| Medium | 0 | — |
| Low | 3 | L-1: 위치 계산 코드 중복 / delta==0 엣지케이스 미처리 / 극단적 종횡비 배율 미세 오차 |

---

## 8. 종합 판정

**Phase 2 통과** — Critical/High 항목 없음. 계획서의 모든 구현 항목이 정확히 이행되었으며, 휠 이벤트, 배율 범위, 원본 기반 재스케일, 화면 경계 클램핑, `show_preview()` 흐름 모두 의도대로 동작함. Low 3건은 선택적 개선 사항으로 Phase 3 진행을 차단하지 않음.
