# PasteFlow

**순차 붙여넣기 클립보드 매니저** — Windows 전용

복사한 순서대로 `Ctrl+V`를 누를 때마다 다음 항목이 자동으로 붙여넣어집니다.  
별도의 모드 전환이나 설정 없이 항상 동작합니다.

---

## 주요 기능

- **순차 붙여넣기** — A → B → C 순서로 복사하면 붙여넣기도 A → B → C 순서로
- **다양한 형식 지원** — 텍스트, 이미지, HTML, RTF(노션 서식 포함)
- **클립보드 패널** — 복사 히스토리 목록, 고정(pin), 검색
- **이미지 미리보기** — 다중 창 동시 표시, 휠 줌, 드래그 이동
- **드래그 붙여넣기** — 패널에서 텍스트/이미지를 앱으로 직접 드래그
- **시스템 트레이** — 백그라운드 상주, 트레이 아이콘으로 패널 열기
- **단축키 커스터마이징** — 패널 토글 단축키 변경 가능

---

## 요구사항

- **OS**: Windows 10 / 11
- **Python**: 3.10 이상
- **패키지**:
  ```
  PyQt6
  pywin32
  keyboard
  Pillow
  ```

---

## 설치 및 실행

```bash
# 패키지 설치
pip install PyQt6 pywin32 keyboard Pillow

# 실행
python -m pasteflow.main
```

---

## 단축키

| 단축키 | 동작 |
|--------|------|
| `Ctrl+Shift+V` | 큐에서 다음 항목 붙여넣기 |
| `Ctrl+Space` | 클립보드 패널 열기 / 닫기 (기본값, 설정에서 변경 가능) |
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

**v1.0.0** — 최초 릴리즈
