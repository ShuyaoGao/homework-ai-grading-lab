"""把文字渲染成“手写风格 + 手机拍照”效果的演示图片（Pillow）。

真实感来源：
- **每个学生一种笔迹**（HandStyle）：字体（英文 6 种手写体、中文 4 种行楷/舒体/楷体）、字号、倾斜、潦草程度、
  墨色（黑/蓝黑/蓝）、笔画粗细；
- **纸张**：英语用横线本，中文作文用 **作文格**（方格稿纸，段首空两格），数学用方格本；
- 逐字绘制：随机旋转、缩放、基线抖动、行内漂移；偶尔有**涂改**（写错后划掉，标准文本里不含被划掉的字）；
- 再模拟手机拍照：桌面背景、透视、旋转、光照不均/阴影、噪声、轻微模糊。

标准文本写入 PNG iTXt 块 `edu_eval_text`，只供 mock OCR 读取（真实照片没有这个块）。
所有图片都是程序合成的演示数据，不是学生真实作业。
"""
from __future__ import annotations

import os
import random
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from PIL.PngImagePlugin import PngInfo

EMBED_KEY = "edu_eval_text"
FONT_DIRS = [Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts", Path("/usr/share/fonts"),
             Path("/usr/share/fonts/truetype"), Path("/System/Library/Fonts"), Path.home() / ".fonts"]
EN_FONTS = ["Inkfree.ttf", "segoepr.ttf", "BRADHITC.TTF", "mvboli.ttf", "ITCKRIST.TTF", "LHANDW.TTF", "comic.ttf",
            "DejaVuSans.ttf"]
ZH_FONTS = ["STXINGKA.TTF", "FZSTK.TTF", "simkai.ttf", "STKAITI.TTF", "NotoSansCJK-Regular.ttc", "msyh.ttc", "simsun.ttc"]
INKS = [(22, 24, 30), (28, 38, 88), (24, 52, 140), (40, 40, 52)]
PAPER = (250, 249, 244)


def find_font(names: list[str]) -> Path | None:
    for d in FONT_DIRS:
        for n in names:
            for p in (d / n, d / n.lower()):
                if p.exists():
                    return p
    return None


def available_fonts(lang: str) -> list[Path]:
    names = ZH_FONTS[:4] if lang == "zh" else EN_FONTS[:6]
    out = [p for n in names if (p := find_font([n])) is not None]
    if not out:
        p = find_font(ZH_FONTS if lang == "zh" else EN_FONTS)
        out = [p] if p else []
    return out


@dataclass
class HandStyle:
    font: Path | None
    size: int
    messiness: float = 1.0  # 0 工整 … 1.5 潦草
    slant: float = 0.0  # 正值右倾
    ink: tuple[int, int, int] = INKS[0]
    bold: int = 0  # 0/1：笔画加粗（模拟用力或粗笔）
    paper: str = "lined"  # lined | grid | squared
    scribble_rate: float = 0.0  # 每个词出现涂改的概率


def style_for(seed: int, lang: str, messiness: float = 1.0, paper: str | None = None) -> HandStyle:
    """按学生/样本 seed 确定一种笔迹（同一 seed 总是同一种，便于“同一个学生的字迹一致”）。"""
    rng = random.Random(seed * 7919 + (1 if lang == "zh" else 0))
    fonts = available_fonts(lang)
    font = fonts[seed % len(fonts)] if fonts else None
    size = rng.randint(40, 46) if lang == "zh" else rng.randint(38, 46)
    return HandStyle(
        font=font, size=size, messiness=messiness, slant=rng.uniform(-0.06, 0.16) if lang == "en" else rng.uniform(-0.04, 0.08),
        ink=INKS[rng.randrange(len(INKS))], bold=1 if rng.random() < 0.35 else 0,
        paper=paper or ("grid" if lang == "zh" else "lined"), scribble_rate=0.008 + 0.015 * max(0.0, messiness - 0.6),
    )


def _font(style: HandStyle, size: int | None = None):  # noqa: ANN202
    size = size or style.size
    if style.font is None:
        return ImageFont.load_default(size=size)
    return ImageFont.truetype(str(style.font), size=size)


def _glyph(ch: str, font, style: HandStyle, rng: random.Random) -> Image.Image:  # noqa: ANN001
    """单个字形：L 模式蒙版，带旋转与倾斜。"""
    w = int(font.getlength(ch)) + 14
    h = int(font.size * 1.6)
    g = Image.new("L", (max(w, 8), h), 0)
    d = ImageDraw.Draw(g)
    d.text((7, 4), ch, font=font, fill=255)
    if style.bold:
        d.text((8, 4), ch, font=font, fill=230)
    sh = style.slant + rng.uniform(-0.03, 0.03) * style.messiness
    if abs(sh) > 1e-3:
        g = g.transform(g.size, Image.AFFINE, (1, sh, -sh * h * 0.6, 0, 1, 0), resample=Image.BICUBIC)
    g = g.rotate(rng.uniform(-4, 4) * style.messiness, resample=Image.BICUBIC, expand=False)
    if rng.random() < 0.3:  # 墨迹深浅不均
        g = g.point(lambda v: int(v * rng.uniform(0.82, 1.0)))
    return g


def _ink(style: HandStyle, rng: random.Random) -> tuple[int, int, int]:
    return tuple(max(0, min(255, c + rng.randint(-10, 10))) for c in style.ink)


def _scribble(page: Image.Image, box: tuple[int, int, int, int], style: HandStyle, rng: random.Random) -> None:
    """涂改：在 box 上画两三道来回的划线。"""
    d = ImageDraw.Draw(page)
    x0, y0, x1, y1 = box
    for k in range(rng.randint(2, 3)):
        y = y0 + (y1 - y0) * (0.35 + 0.15 * k) + rng.uniform(-3, 3)
        pts = [(x0 + (x1 - x0) * t / 6, y + rng.uniform(-5, 5)) for t in range(7)]
        d.line(pts, fill=_ink(style, rng), width=3 + style.bold, joint="curve")


WRONG_EN = ["becaus", "freind", "beautifull", "wich", "goed", "tomorow"]
WRONG_ZH = "的地得在再做作那哪"


# ------------------------------------------------------------------ 横线本（英文/数学）

def _wrap_words(text: str, font, max_w: int) -> list[str]:  # noqa: ANN001
    out: list[str] = []
    for para in text.split("\n"):
        if not para.strip():
            out.append("")
            continue
        line = ""
        for word in para.split(" "):
            cand = (line + " " + word) if line else word
            if font.getlength(cand) > max_w and line:
                out.append(line)
                line = word
            else:
                line = cand
        out.append(line)
    return out


def _wrap_chars(text: str, font, max_w: int) -> list[str]:  # noqa: ANN001
    out = []
    for para in text.split("\n"):
        line = ""
        for ch in para:
            if font.getlength(line + ch) > max_w:
                out.append(line)
                line = ""
            line += ch
        out.append(line)
    return out


def render_lined(text: str, lang: str, style: HandStyle, seed: int, width: int = 1240) -> Image.Image:
    rng = random.Random(seed)
    font = _font(style)
    margin_l, margin_r, top = 110, 70, 120
    line_h = int(style.size * 1.75)
    wrap = _wrap_words if (lang == "en" or " " in text) else _wrap_chars
    # 逐字绘制有字距抖动、倾斜和涂改占位，按 85% 的行宽折行，避免写出纸边
    lines = wrap(text, font, int((width - margin_l - margin_r) * 0.85))
    height = max(int(width * 1.414), top + line_h * (len(lines) + 2))
    page = Image.new("RGB", (width, height), PAPER)
    d = ImageDraw.Draw(page)
    if style.paper == "squared":
        step = line_h // 2
        for y in range(top - step, height - 30, step):
            d.line([(40, y + 8), (width - 40, y + 8)], fill=(200, 222, 210), width=1)
        for x in range(40, width - 30, step):
            d.line([(x, top - step + 8), (x, height - 30)], fill=(200, 222, 210), width=1)
    else:
        for y in range(top + line_h, height - 40, line_h):
            d.line([(40, y + 8), (width - 40, y + 8)], fill=(170, 195, 225), width=2)
        d.line([(margin_l - 20, 40), (margin_l - 20, height - 40)], fill=(230, 150, 150), width=2)
    y = top
    for line in lines:
        x = margin_l + rng.uniform(-4, 4) * style.messiness
        drift = rng.uniform(-0.04, 0.04) * style.messiness
        tokens = line.split(" ") if lang == "en" else [line]
        for ti, tok in enumerate(tokens):
            if lang == "en" and tok and rng.random() < style.scribble_rate:  # 写错一个词再划掉
                bad = rng.choice(WRONG_EN)
                x0 = x
                for ch in bad:
                    g = _glyph(ch, font, style, rng)
                    page.paste(Image.new("RGB", g.size, _ink(style, rng)), (int(x) - 7, int(y)), g)
                    x += font.getlength(ch)
                _scribble(page, (int(x0) - 2, int(y + style.size * 0.3), int(x) + 2, int(y + style.size * 1.1)), style, rng)
                x += font.getlength(" ") * 1.2
            for i, ch in enumerate(tok):
                s = style.size * rng.uniform(0.93, 1.07) if style.messiness else style.size
                f = font if abs(s - style.size) < 1 else _font(style, int(s))
                g = _glyph(ch, f, style, rng)
                dy = rng.uniform(-3, 3) * style.messiness + drift * i * 2
                page.paste(Image.new("RGB", g.size, _ink(style, rng)), (int(x) - 7, int(y + dy)), g)
                x += f.getlength(ch) * rng.uniform(0.95, 1.05)
            if ti < len(tokens) - 1:
                x += font.getlength(" ") * rng.uniform(0.9, 1.35)
        y += line_h
    return page


# ------------------------------------------------------------------ 作文格（中文）

def render_grid(text: str, style: HandStyle, seed: int, width: int = 1240, cols: int = 20) -> Image.Image:
    """中文作文稿纸：每格一个字（标点也占一格），段首空两格，首行标题居中。"""
    rng = random.Random(seed)
    margin = 80
    cell = (width - 2 * margin) // cols
    gap = int(cell * 0.32)  # 行间空白
    font = _font(style, int(cell * 0.78))
    paras = [p for p in text.split("\n")]
    title, body = (paras[0], paras[1:]) if paras and len(paras[0]) <= 16 and len(paras) > 1 else ("", paras)
    rows: list[list[str]] = []
    if title:
        pad = max(0, (cols - len(title)) // 2)
        rows.append([""] * pad + list(title))
    for p in body:
        if not p.strip():
            continue
        cells = ["", ""] + list(p.strip())
        for i in range(0, len(cells), cols):
            rows.append(cells[i:i + cols])
    n_rows = max(len(rows) + 2, 14)
    top = 110
    height = top + n_rows * (cell + gap) + 80
    page = Image.new("RGB", (width, height), PAPER)
    d = ImageDraw.Draw(page)
    grid_c = (214, 120, 110) if seed % 2 else (90, 160, 110)  # 红格或绿格稿纸
    for r in range(n_rows):
        y0 = top + r * (cell + gap)
        d.rectangle([margin, y0, margin + cols * cell, y0 + cell], outline=grid_c, width=2)
        for c in range(1, cols):
            d.line([(margin + c * cell, y0), (margin + c * cell, y0 + cell)], fill=grid_c, width=1)
    footer_font = find_font(["simsun.ttc", "msyh.ttc"])
    d.text((width - margin - 220, height - 60), f"20×{n_rows}={20 * n_rows}",
           fill=grid_c, font=ImageFont.truetype(str(footer_font), 22) if footer_font else ImageFont.load_default(size=22))
    for r, row in enumerate(rows):
        y0 = top + r * (cell + gap)
        for c, ch in enumerate(row):
            if not ch:
                continue
            if ch not in "，。、；：！？“”‘’（）《》…—" and rng.random() < style.scribble_rate * 0.6:
                # 涂改：本格写了错字被划掉，正字挤在格子右上角（模拟学生在格内改字）
                g = _glyph(rng.choice(WRONG_ZH), font, style, rng)
                gx, gy = margin + c * cell + (cell - g.size[0]) // 2, y0 + (cell - g.size[1]) // 2 + 4
                page.paste(Image.new("RGB", g.size, _ink(style, rng)), (gx, gy), g)
                _scribble(page, (margin + c * cell + 6, y0 + 4, margin + (c + 1) * cell - 6, y0 + cell - 4), style, rng)
                small = _font(style, int(cell * 0.5))
                g2 = _glyph(ch, small, style, rng)
                page.paste(Image.new("RGB", g2.size, _ink(style, rng)), (margin + c * cell + cell // 2, y0 - gap // 2 - 6), g2)
                continue
            g = _glyph(ch, font, style, rng)
            jx = rng.uniform(-3, 3) * style.messiness
            jy = rng.uniform(-3, 3) * style.messiness
            # 中文字体里句读本身就在字身左下，居中贴入即落在格子左下角
            gx = margin + c * cell + (cell - g.size[0]) / 2 + jx
            gy = y0 + (cell - g.size[1]) / 2 + 4 + jy
            page.paste(Image.new("RGB", g.size, _ink(style, rng)), (int(gx), int(gy)), g)
    return page


# ------------------------------------------------------------------ 兼容旧接口

def render_page(text: str, lang: str = "en", seed: int = 0, width: int = 1240, font_size: int | None = None,
                messiness: float = 1.0, style: HandStyle | None = None) -> Image.Image:
    style = style or style_for(seed, lang, messiness, paper="lined")
    if font_size:
        style.size = font_size
    if style.paper == "grid":
        return render_grid(text, style, seed, width)
    return render_lined(text, lang, style, seed, width)


# ------------------------------------------------------------------ 拍照模拟

DESKS = [(118, 98, 82), (150, 128, 100), (92, 96, 104), (196, 190, 180), (70, 74, 60)]


def simulate_photo(page: Image.Image, seed: int = 0, angle: float | None = None, perspective: float = 0.04,
                   out_width: int = 1400, shadow: bool | None = None) -> Image.Image:
    """把一页作业纸“拍成照片”：桌面背景、透视、旋转、光照不均、手机阴影、噪声。"""
    rng = random.Random(seed)
    nrng = np.random.default_rng(seed)
    pw, ph = page.size
    pad = int(pw * 0.12)
    W, H = pw + 2 * pad, ph + 2 * pad
    base = np.array(DESKS[seed % len(DESKS)], dtype=np.float32)
    tex = nrng.normal(0, 1, (H // 8 + 1, W // 8 + 1)) * 10
    tex = np.asarray(Image.fromarray(tex.astype(np.float32)).resize((W, H), Image.BICUBIC))
    desk = np.clip(base[None, None, :] + tex[:, :, None], 0, 255).astype(np.uint8)
    canvas = Image.fromarray(desk).filter(ImageFilter.GaussianBlur(2))
    canvas.paste(page, (pad, pad))
    j = perspective * pw
    src = [(pad, pad), (pad + pw, pad), (pad + pw, pad + ph), (pad, pad + ph)]
    dst = [(x + rng.uniform(-j, j), y + rng.uniform(-j, j)) for x, y in src]
    fill = tuple(int(c) for c in base)
    canvas = canvas.transform((W, H), Image.PERSPECTIVE, _perspective_coeffs(dst, src), Image.BICUBIC, fillcolor=fill)
    ang = angle if angle is not None else rng.uniform(-6, 6)
    canvas = canvas.rotate(ang, resample=Image.BICUBIC, expand=False, fillcolor=fill)
    arr = np.asarray(canvas).astype(np.float32)
    gx = np.linspace(rng.uniform(0.74, 0.92), rng.uniform(1.0, 1.08), W)[None, :, None]
    gy = np.linspace(1.03, rng.uniform(0.8, 0.95), H)[:, None, None]
    arr = arr * gx * gy
    if shadow if shadow is not None else rng.random() < 0.5:  # 手机/手的阴影：一角的柔和暗斑
        yy, xx = np.mgrid[0:H, 0:W]
        cx, cy = rng.choice([(0, H), (W, H), (W, 0)])
        r = 0.55 * max(W, H)
        dist = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2) / r
        arr = arr * (1 - 0.28 * np.clip(1 - dist, 0, 1) ** 2)[:, :, None]
    arr = arr + nrng.normal(0, 4, arr.shape)
    img = Image.fromarray(arr.clip(0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(0.6))
    if out_width and img.width > out_width:
        img = img.resize((out_width, int(img.height * out_width / img.width)), Image.LANCZOS)
    return img


def _perspective_coeffs(src, dst):  # noqa: ANN001, ANN202
    """求 PIL PERSPECTIVE 变换系数：把输出坐标 src 映射到输入坐标 dst。"""
    A, b = [], []
    for (x, y), (u, v) in zip(src, dst):
        A.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        A.append([0, 0, 0, x, y, 1, -v * x, -v * y])
        b += [u, v]
    return np.linalg.solve(np.array(A, dtype=float), np.array(b, dtype=float)).tolist()


def save_with_text(img: Image.Image, path: Path, text: str) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    info = PngInfo()
    info.add_itxt(EMBED_KEY, text)
    img.save(path.with_suffix(".png"), "PNG", pnginfo=info, optimize=False, compress_level=6)
    return path.with_suffix(".png")


def make_homework_photo(text: str, lang: str, path: Path, seed: int = 0, photo: bool = True, angle: float | None = None,
                        messiness: float = 1.0, embed: bool = True, out_width: int = 1400, paper: str | None = None,
                        style: HandStyle | None = None) -> Path:
    """生成一张作业照片。paper：lined（横线）/ grid（作文格）/ squared（方格）；默认英文横线、中文作文格。"""
    if style is None:
        style = style_for(seed, lang, messiness, paper=paper)
    page = render_page(text, lang=lang, seed=seed, style=style)
    img = simulate_photo(page, seed=seed, angle=angle, out_width=out_width) if photo else page
    if embed:
        return save_with_text(img, path, text)
    p = Path(path).with_suffix(".jpg")
    p.parent.mkdir(parents=True, exist_ok=True)
    img.save(p, "JPEG", quality=88)
    return p
