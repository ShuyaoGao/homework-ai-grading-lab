"""生成 PWA 图标（Pillow 绘制：绿色圆角方块 + 白色对勾与“评”字），输出到 frontend/icons/。"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "frontend" / "icons"
GREEN, WHITE = (47, 111, 94), (255, 255, 255)


def _font(size: int):
    for name in ("msyhbd.ttc", "msyh.ttc", "simhei.ttf"):
        p = Path("C:/Windows/Fonts") / name
        if p.exists():
            return ImageFont.truetype(str(p), size)
    return ImageFont.load_default(size=size)


def icon(size: int, maskable: bool = False) -> Image.Image:
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    if maskable:
        d.rectangle([0, 0, size, size], fill=GREEN)
        pad = int(size * 0.2)
    else:
        d.rounded_rectangle([0, 0, size - 1, size - 1], radius=int(size * 0.22), fill=GREEN)
        pad = int(size * 0.12)
    inner = size - 2 * pad
    f = _font(int(inner * 0.62))
    d.text((size / 2, pad + inner * 0.42), "评", font=f, fill=WHITE, anchor="mm")
    w = max(2, int(inner * 0.07))
    pts = [(pad + inner * 0.22, pad + inner * 0.80), (pad + inner * 0.40, pad + inner * 0.94), (pad + inner * 0.80, pad + inner * 0.70)]
    d.line(pts, fill=(255, 214, 140), width=w, joint="curve")
    return im


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    icon(192).save(OUT / "icon-192.png")
    icon(512).save(OUT / "icon-512.png")
    icon(512, maskable=True).save(OUT / "maskable-512.png")
    print("icons written to", OUT)
