"""클립보드 포맷 진단 — 노션에서 이미지 복사 후 실행"""
import win32clipboard
import win32con

FORMAT_NAMES = {
    1: "CF_TEXT",
    2: "CF_BITMAP",
    3: "CF_METAFILEPICT",
    7: "CF_OEMTEXT",
    8: "CF_DIB",
    13: "CF_UNICODETEXT",
    14: "CF_ENHMETAFILE",
    15: "CF_HDROP",
    16: "CF_LOCALE",
    17: "CF_DIBV5",
}

def main():
    win32clipboard.OpenClipboard()
    try:
        fmt = 0
        print("=== 클립보드 포맷 목록 ===")
        while True:
            fmt = win32clipboard.EnumClipboardFormats(fmt)
            if fmt == 0:
                break
            name = FORMAT_NAMES.get(fmt)
            if name is None:
                try:
                    name = win32clipboard.GetClipboardFormatName(fmt)
                except:
                    name = f"(unknown #{fmt})"
            print(f"  {fmt:5d}  {name}")

            # 데이터 크기 확인
            try:
                data = win32clipboard.GetClipboardData(fmt)
                if isinstance(data, bytes):
                    print(f"         → {len(data)} bytes, header: {data[:20]}")
                elif isinstance(data, str):
                    print(f"         → str len={len(data)}, preview: {data[:80]!r}")
                else:
                    print(f"         → type={type(data).__name__}")
            except Exception as e:
                print(f"         → 읽기 실패: {e}")
    finally:
        win32clipboard.CloseClipboard()

if __name__ == "__main__":
    print("노션에서 이미지(+텍스트)를 복사한 후 이 스크립트를 실행하세요.")
    print()
    main()
