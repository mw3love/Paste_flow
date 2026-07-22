"""중립 차콜 다크 테마 — 전체 UI 공유 상수"""

import os

BASE     = "#121212"
MANTLE   = "#0a0a0a"
CRUST    = "#050505"
SURFACE0 = "#2d2d2d"
SURFACE1 = "#3c3c3c"
SURFACE2 = "#505050"
OVERLAY0 = "#6b6b6b"
SUBTEXT0 = "#ababab"
TEXT     = "#d4d4d4"

BLUE  = "#89b4fa"
GREEN = "#a6e3a1"
PEACH = "#fab387"
RED   = "#f38ba8"

PEACH_HOVER = "#f39e62"
RED_HOVER  = "#e07898"

COLORS = {
    "base":     BASE,
    "mantle":   MANTLE,
    "crust":    CRUST,
    "surface0": SURFACE0,
    "surface1": SURFACE1,
    "surface2": SURFACE2,
    "overlay0": OVERLAY0,
    "subtext0": SUBTEXT0,
    "text":     TEXT,
    "blue":     BLUE,
    "green":    GREEN,
    "peach":    PEACH,
    "red":      RED,
}


def check_icon_url() -> str:
    """켜진 체크박스에 그릴 코랄 체크마크(✓) SVG를 로컬 assets에 쓰고,
    QSS `image: url(...)`에 넣을 경로(슬래시)를 돌려준다. 실패 시 "".

    Qt QSS는 `data:` URI를 파일 경로로 오인해 데이터 URI 체크마크가 렌더되지
    않는다(실측). 그래서 실제 SVG 파일을 만들어 경로로 참조한다. 아웃라인
    체크박스(테두리만 코랄 + 코랄 ✓, 채우지 않음)에서 켜짐을 표시하는 마크.
    """
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~\\AppData\\Local")
    d = os.path.join(base, "PasteFlow", "assets")
    try:
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, "check_peach.svg")
        with open(path, "w", encoding="utf-8") as f:
            f.write(
                "<svg xmlns='http://www.w3.org/2000/svg' width='16' height='16' "
                "viewBox='0 0 24 24'>"
                f"<path d='M4 12l6 6L20 5' fill='none' stroke='{PEACH}' "
                "stroke-width='3.2' stroke-linecap='round' "
                "stroke-linejoin='round'/></svg>"
            )
    except OSError:
        return ""
    return path.replace("\\", "/")
