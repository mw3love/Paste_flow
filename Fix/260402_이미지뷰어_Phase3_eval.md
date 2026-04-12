# 이미지뷰어 Phase 3 평가 결과

- 평가 일시: 2026-04-02
- 커밋: `709a67e` — `Feat: 이미지 미리보기 다중 창 동시 표시 지원`
- 평가 대상 파일: `pasteflow/ui/image_preview.py`, `pasteflow/main.py`, `pasteflow/ui/panel.py`

---

## 1. 계획서 Phase 3 항목 완료 여부

| # | 계획서 항목 | 완료 | 비고 |
|---|-------------|------|------|
| 1 | `_instance` → `_instances: list[ImagePreviewPopup]` 클래스 변수 교체 | ✅ | L25 |
| 2 | `open_new(cls, image_data, global_pos)` 클래스메서드 추가 | ✅ | L32~37 |
| 3 | `closeEvent` 오버라이드 — `_instances`에서 자신 제거 | ✅ | L228~230 |
| 4 | `close_all(cls)` 클래스메서드 추가 | ✅ | L39~43 |
| 5 | 기존 `instance()`, `toggle_preview()`, `hide_preview()` 제거 | ✅ | diff 확인 |
| 6 | `main.py` `_on_preview_image()` → `open_new()` 호출로 변경 | ✅ | L571 |
| 7 | `panel.py` L253 `hide_preview()` → `close_all()` 교체 | ✅ | L253 |

**결과: 계획서 7개 항목 모두 완료.**

---

## 2. 다중 인스턴스 관리

### 등록 타이밍
`open_new()` 내에서 `cls._instances.append(popup)` 후 `popup.show_preview()`를 호출하는 순서는 올바르다.
`show_preview()` 실패(png 변환 오류 등) 시 인스턴스가 목록에 남을 수 있으나, 이 경우 창이 숨겨진 채(`hide()` 상태) 목록에 잔류한다.

- **심각도: Low** — `show_preview()` 실패 시 창은 숨김 상태지만 `_instances`에 남아 `close_all()` 호출에는 정상 동작하므로 실제 누수는 없다.

### 제거 타이밍 (`closeEvent`)
`closeEvent`에서 리스트 컴프리헨션으로 새 목록을 할당한다. 순회 중 목록 변경 문제 없음.
`type(self)._instances` 사용으로 서브클래스가 생길 경우 클래스 변수가 혼용될 수 있으나, 현재 서브클래스 없음. 허용 가능.

- **결론: 이상 없음**

---

## 3. `close_all()` 안전성

```python
for popup in list(cls._instances):
    popup.close()
```

`list(cls._instances)` 복사본으로 순회하고, `close()` → `closeEvent` → `_instances` 목록 재할당이 발생해도 원본 순회에 영향 없다.

- **결론: 안전하게 구현됨. 이상 없음.**

---

## 4. `main.py` 변경

`_on_preview_image()`:
```python
# 변경 전
ImagePreviewPopup.instance().toggle_preview(item.image_data, pos)
# 변경 후
ImagePreviewPopup.open_new(item.image_data, pos)
```

- 싱글톤 `instance()` 및 `toggle_preview()` 완전히 제거됨.
- 다중 창 열기 방식으로 올바르게 변경됨.
- 기존 토글(보이면 숨기기) 동작은 의도적으로 제거됨 — 계획서와 일치.

- **결론: 이상 없음.**

---

## 5. `panel.py` 변경

`PanelItemWidget.mouseMoveEvent()` L253:
```python
# 변경 전
ImagePreviewPopup.instance().hide_preview()
# 변경 후
ImagePreviewPopup.close_all()
```

- `TextPreviewPopup.instance().hide_preview()` (L254)는 변경되지 않음 — `TextPreviewPopup`은 여전히 싱글톤 방식이므로 올바름.
- 드래그 시작 시점에 열린 모든 이미지 미리보기 창이 닫히는 동작으로 교체됨.

### 패널 닫힐 때 `close_all()` 미호출 여부 — Medium 발견

계획서 Phase 3 항목에는 명시되어 있지 않으나, 계획서 표(수정 대상 파일 표)에
`panel.py L253` 만 명시되어 있고, **패널 자체가 닫히거나 숨겨질 때 열린 이미지 미리보기를 닫는 로직이 없음.**

- `panel.py` `toggle()` → `hide()` 호출 시 `hideEvent`에서 `ImagePreviewPopup.close_all()`을 호출하지 않는다.
- 자동 닫기 경로(`changeEvent` → L1307 `self.hide()`) 역시 마찬가지.
- 결과: 패널이 숨겨져도 이미지 미리보기 창들이 화면에 계속 남아 있게 된다.
- Phase 4 수동 확인 항목 `"패널 닫을 때 모든 미리보기 창 같이 닫힘"` 이 실패할 가능성이 높다.

**심각도: Medium** (Phase 4 수동 확인 단계에서 발견 예정이나, 계획서 취지에 반하므로 선제 보고)

---

## 6. `hide_preview()` 완전 제거 여부

전체 코드베이스 검색 결과:

```
pasteflow/main.py         — hide_preview 사용 없음
pasteflow/ui/image_preview.py — hide_preview 완전 제거됨
pasteflow/ui/panel.py     — hide_preview(ImagePreview 관련) 완전 제거됨
pasteflow/ui/text_preview.py  — hide_preview 잔존 (TextPreviewPopup 전용, 올바름)
```

`ImagePreviewPopup.hide_preview()`의 모든 호출부가 제거됨. `TextPreviewPopup.hide_preview()`는 별개 클래스이므로 영향 없음.

- **결론: 이상 없음.**

---

## 종합 발견 사항

| 심각도 | 건수 | 내용 |
|--------|------|------|
| Critical | 0 | — |
| High | 0 | — |
| Medium | 1 | 패널 hide 시 `ImagePreviewPopup.close_all()` 미호출 → 패널 닫혀도 이미지 미리보기 창 잔존 |
| Low | 1 | `show_preview()` 실패 시 숨김 인스턴스가 `_instances`에 잔류 (기능 영향 없음) |

---

## 권장 조치

### Medium — 패널 hide 시 close_all 미호출

`pasteflow/ui/panel.py` `hideEvent`에 `ImagePreviewPopup.close_all()` 추가:

```python
def hideEvent(self, event):
    self._cursor_timer.stop()
    self.unsetCursor()
    if self._search_expanded:
        self._collapse_search()
    ImagePreviewPopup.close_all()          # 추가
    super().hideEvent(event)
```

이 수정은 Phase 4 검증 전에 처리하는 것이 권장된다.
