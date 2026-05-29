"""PasteFlow 앱 아이콘(.ico) 생성기.

트레이 런타임 아이콘(`pasteflow/ui/tray.py:_create_default_icon`)과 동일한 시각
정체성 — Catppuccin TEAL 라운드 사각형 + 어두운 "P" 글자 — 을 고해상도로 렌더해
다중 해상도 .ico(16~256px)로 저장한다.

재생성:  python tools/make_icon.py
출력:    pasteflow/assets/pasteflow.ico
"""
from __future__ import annotations

import pathlib

from PIL import Image, ImageDraw, ImageFont

# pasteflow/ui/theme.py 와 동일한 값
TEAL = "#94e2d5"
BASE = "#1e1e1e"

ICON_SIZES = [16, 24, 32, 48, 64, 128, 256]
MASTER = 256  # 마스터 렌더 해상도 (다운스케일로 각 크기 생성)


def _load_bold_font(px: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """굵은 산세리프 폰트 로드. 실패 시 PIL 기본 폰트."""
    for name in ("arialbd.ttf", "segoeuib.ttf", "Arial Bold.ttf"):
        try:
            return ImageFont.truetype(name, px)
        except OSError:
            continue
    return ImageFont.load_default()


def render_master(size: int = MASTER) -> Image.Image:
    """투명 배경 위에 teal 라운드 사각형 + 중앙 'P'."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # 캔버스의 ~88%를 차지하는 라운드 사각형 (가장자리 여백 확보)
    margin = round(size * 0.06)
    radius = round(size * 0.18)
    box = (margin, margin, size - margin - 1, size - margin - 1)
    border = max(1, round(size * 0.02))
    draw.rounded_rectangle(box, radius=radius, fill=TEAL, outline=BASE, width=border)

    # 중앙 'P'
    font = _load_bold_font(round(size * 0.62))
    bbox = draw.textbbox((0, 0), "P", font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    tx = (size - tw) / 2 - bbox[0]
    ty = (size - th) / 2 - bbox[1]
    draw.text((tx, ty), "P", font=font, fill=BASE)

    return img


def main() -> None:
    repo_root = pathlib.Path(__file__).resolve().parent.parent
    out_dir = repo_root / "pasteflow" / "assets"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "pasteflow.ico"

    master = render_master(MASTER)
    master.save(out_path, format="ICO", sizes=[(s, s) for s in ICON_SIZES])
    print(f"wrote {out_path} (sizes: {ICON_SIZES})")


if __name__ == "__main__":
    main()
