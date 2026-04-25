"""Catppuccin Mocha 색상 팔레트 — 전체 UI 공유 상수"""

BASE     = "#1e1e2e"
MANTLE   = "#181825"
CRUST    = "#11111b"
SURFACE0 = "#313244"
SURFACE1 = "#45475a"
SURFACE2 = "#585b70"
OVERLAY0 = "#6c7086"
SUBTEXT0 = "#a6adc8"
TEXT     = "#cdd6f4"

BLUE  = "#89b4fa"
TEAL  = "#94e2d5"
GREEN = "#a6e3a1"
PEACH = "#fab387"
RED   = "#f38ba8"

# hover 변형 (각 accent 색에서 명도를 낮춘 값)
TEAL_HOVER = "#7ed5c8"
RED_HOVER  = "#e07898"

# dict 형태 — 기존 COLORS['key'] 패턴과 호환
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
    "teal":     TEAL,
    "green":    GREEN,
    "peach":    PEACH,
    "red":      RED,
}
