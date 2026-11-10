"""图像预处理：EXIF 方向校正 → 缩放 → 纸张检测与透视矫正（裁边）→ 纠偏 → 光照均衡增强 → 多页拼接。

全部用 OpenCV + Pillow，在 CPU 上运行；每一步失败都会安全回退（宁可不处理，也不把图弄坏）。
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

MAX_SIDE = 2200

try:  # 可选：HEIC/HEIF（部分安卓浏览器直接上传相机原图）。未安装 pillow-heif 时这类图片会被拒绝并提示
    import pillow_heif  # type: ignore[import-not-found]

    pillow_heif.register_heif_opener()
except ImportError:
    pass
EMBED_KEY = "edu_eval_text"


@dataclass
class LoadedImage:
    image: Image.Image  # 已按 EXIF 转正、去除元数据的 RGB 图
    embedded_text: str = ""


@dataclass
class PreprocessMeta:
    original_size: tuple[int, int] = (0, 0)
    output_size: tuple[int, int] = (0, 0)
    paper_detected: bool = False
    paper_area_ratio: float = 0.0
    deskew_angle: float = 0.0
    cropped: bool = False
    enhanced: bool = False
    blur_score: float = 0.0  # 拉普拉斯方差，越小越模糊
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {k: (list(v) if isinstance(v, tuple) else v) for k, v in self.__dict__.items()}


def load_upload(data: bytes) -> LoadedImage:
    """读取上传的图片字节：校验是图片、按 EXIF 方向转正、读取演示文本，然后丢弃全部 EXIF（含 GPS 定位）。"""
    try:
        probe = Image.open(io.BytesIO(data))
        probe.verify()
    except Exception as e:  # noqa: BLE001
        raise ValueError("文件不是有效的图片（如果是 HEIC 格式，请在手机相机设置里改为“兼容性最佳/JPEG”，"
                         "或由管理员安装 pillow-heif）") from e
    im = Image.open(io.BytesIO(data))
    embedded = ""
    info = getattr(im, "text", None) or im.info
    if isinstance(info, dict) and EMBED_KEY in info:
        embedded = str(info[EMBED_KEY])
    im = ImageOps.exif_transpose(im)
    if im.mode not in ("RGB", "L"):
        bg = Image.new("RGB", im.size, (255, 255, 255))
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGBA")
            bg.paste(im, mask=im.split()[-1])
            im = bg
        else:
            im = im.convert("RGB")
    im = im.convert("RGB")
    clean = Image.fromarray(np.asarray(im))  # 只复制像素，不带任何元数据（EXIF/GPS 被丢弃）
    return LoadedImage(clean, embedded)


def apply_edits(im: Image.Image, edit: dict | None) -> Image.Image:
    """应用手机端的编辑：先旋转（顺时针 0/90/180/270 度），再按归一化坐标裁剪 [x0, y0, x1, y1]（相对旋转后的图）。
    在服务器上做，保留原图分辨率，也不用在手机上重新编码大图。非法参数直接忽略（不让一次编辑毁掉上传）。"""
    if not edit:
        return im
    rot = int(edit.get("rotate", 0) or 0) % 360
    if rot in (90, 180, 270):
        im = im.rotate(-rot, expand=True)  # PIL 正角度为逆时针
    crop = edit.get("crop")
    if isinstance(crop, (list, tuple)) and len(crop) == 4:
        try:
            x0, y0, x1, y1 = (min(1.0, max(0.0, float(v))) for v in crop)
        except (TypeError, ValueError):
            return im
        if x1 - x0 >= 0.05 and y1 - y0 >= 0.05 and (x0, y0, x1, y1) != (0, 0, 1, 1):
            W, H = im.size
            im = im.crop((int(x0 * W), int(y0 * H), int(x1 * W), int(y1 * H)))
    return im


def _to_cv(im: Image.Image) -> np.ndarray:
    return cv2.cvtColor(np.asarray(im.convert("RGB")), cv2.COLOR_RGB2BGR)


def _to_pil(arr: np.ndarray) -> Image.Image:
    if arr.ndim == 2:
        return Image.fromarray(arr)
    return Image.fromarray(cv2.cvtColor(arr, cv2.COLOR_BGR2RGB))


def _order_corners(pts: np.ndarray) -> np.ndarray:
    pts = pts.reshape(4, 2).astype("float32")
    s = pts.sum(axis=1)
    d = np.diff(pts, axis=1).ravel()
    return np.array([pts[np.argmin(s)], pts[np.argmin(d)], pts[np.argmax(s)], pts[np.argmax(d)]], dtype="float32")


def detect_paper(bgr: np.ndarray) -> tuple[np.ndarray | None, float]:
    """找最大的近似四边形（作业纸）。返回 4 个角点与其面积占比。"""
    h, w = bgr.shape[:2]
    scale = 800 / max(h, w)
    small = cv2.resize(bgr, (int(w * scale), int(h * scale))) if scale < 1 else bgr.copy()
    s = scale if scale < 1 else 1.0
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (5, 5), 0)
    best, best_area = None, 0.0
    total = gray.shape[0] * gray.shape[1]
    # 两种二值化思路：边缘 + 亮区（纸通常比桌面亮）
    candidates = []
    edges = cv2.Canny(gray, 40, 120)
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=2)
    candidates.append(edges)
    _, bright = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    bright = cv2.morphologyEx(bright, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    candidates.append(bright)
    for mask in candidates:
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for c in sorted(contours, key=cv2.contourArea, reverse=True)[:5]:
            area = cv2.contourArea(c)
            if area < 0.25 * total:
                continue
            peri = cv2.arcLength(c, True)
            approx = cv2.approxPolyDP(c, 0.02 * peri, True)
            if len(approx) == 4 and cv2.isContourConvex(approx) and area > best_area:
                best, best_area = approx, area
    if best is None:
        return None, 0.0
    return _order_corners(best / s), best_area / total


def warp_paper(bgr: np.ndarray, corners: np.ndarray) -> np.ndarray:
    tl, tr, br, bl = corners
    wa, wb = np.linalg.norm(br - bl), np.linalg.norm(tr - tl)
    ha, hb = np.linalg.norm(tr - br), np.linalg.norm(tl - bl)
    W, H = int(max(wa, wb)), int(max(ha, hb))
    dst = np.array([[0, 0], [W - 1, 0], [W - 1, H - 1], [0, H - 1]], dtype="float32")
    M = cv2.getPerspectiveTransform(corners, dst)
    return cv2.warpPerspective(bgr, M, (W, H), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)


def estimate_skew(gray: np.ndarray, max_angle: float = 15.0) -> float:
    """估计文字行的倾斜角（度，正值=逆时针）。投影法：把二值图旋转一系列候选角度，
    取“逐行墨迹和”方差最大的角度（文字行/横线与水平对齐时投影最尖锐）。先 1° 粗搜，再 0.1° 细搜。"""
    h, w = gray.shape[:2]
    s = 900 / max(h, w)
    small = cv2.resize(gray, (max(1, int(w * s)), max(1, int(h * s))), interpolation=cv2.INTER_AREA) if s < 1 else gray
    bw = cv2.adaptiveThreshold(cv2.GaussianBlur(small, (3, 3), 0), 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                               cv2.THRESH_BINARY_INV, 31, 15)
    if cv2.countNonZero(bw) < 0.002 * bw.size:
        return 0.0
    sh, sw = bw.shape
    center = (sw / 2, sh / 2)

    def score(a: float) -> float:
        M = cv2.getRotationMatrix2D(center, a, 1.0)
        r = cv2.warpAffine(bw, M, (sw, sh), flags=cv2.INTER_NEAREST, borderValue=0)
        proj = r.sum(axis=1, dtype=np.float64)
        return float(np.var(proj))

    coarse = np.arange(-max_angle, max_angle + 1e-6, 1.0)
    best = max(coarse, key=score)
    fine = np.arange(best - 1.0, best + 1.0 + 1e-6, 0.1)
    best = max(fine, key=score)
    base = score(0.0)
    if score(best) < base * 1.02:  # 改善不明显时不旋转，避免误纠
        return 0.0
    return float(-best)


def rotate(bgr: np.ndarray, angle: float) -> np.ndarray:
    h, w = bgr.shape[:2]
    M = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    cos, sin = abs(M[0, 0]), abs(M[0, 1])
    nw, nh = int(h * sin + w * cos), int(h * cos + w * sin)
    M[0, 2] += nw / 2 - w / 2
    M[1, 2] += nh / 2 - h / 2
    return cv2.warpAffine(bgr, M, (nw, nh), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)


def enhance(bgr: np.ndarray) -> np.ndarray:
    """去阴影 + 对比度增强：用大核闭运算估计背景（纸面光照），原图除以背景后归一化，再做轻度 CLAHE。
    输出灰度图，保留笔画浓淡（不做硬二值化，避免细笔画断裂影响识别）。"""
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    k = max(15, (min(gray.shape) // 30) | 1)
    bg = cv2.morphologyEx(gray, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    bg = cv2.medianBlur(bg, 21)
    norm = cv2.divide(gray, bg, scale=255)
    clahe = cv2.createCLAHE(clipLimit=1.5, tileGridSize=(8, 8))
    return clahe.apply(norm)


def crop_margins(gray: np.ndarray, pad: int = 20) -> tuple[np.ndarray, bool]:
    """裁掉四周空白：按墨迹像素的外接框裁剪。"""
    _, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    bw = cv2.morphologyEx(bw, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    pts = cv2.findNonZero(bw)
    if pts is None:
        return gray, False
    x, y, w, h = cv2.boundingRect(pts)
    H, W = gray.shape[:2]
    if w * h > 0.97 * W * H:
        return gray, False
    x0, y0 = max(0, x - pad), max(0, y - pad)
    x1, y1 = min(W, x + w + pad), min(H, y + h + pad)
    return gray[y0:y1, x0:x1], True


def preprocess(im: Image.Image, do_warp: bool = True, do_deskew: bool = True, do_enhance: bool = True) -> tuple[Image.Image, PreprocessMeta]:
    meta = PreprocessMeta(original_size=im.size)
    bgr = _to_cv(im)
    h, w = bgr.shape[:2]
    if max(h, w) > MAX_SIDE:
        s = MAX_SIDE / max(h, w)
        bgr = cv2.resize(bgr, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
    try:
        gray0 = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        meta.blur_score = round(float(cv2.Laplacian(gray0, cv2.CV_64F).var()), 1)
    except cv2.error:
        meta.blur_score = -1.0  # 未能评估
    if 0 <= meta.blur_score < 30:
        meta.warnings.append("图片可能比较模糊，建议重新拍摄（对焦、光线充足、手机拿稳）")

    if do_warp:
        try:
            corners, ratio = detect_paper(bgr)
            if corners is not None and ratio < 0.98:
                warped = warp_paper(bgr, corners)
                if min(warped.shape[:2]) > 200:
                    bgr = warped
                    meta.paper_detected, meta.paper_area_ratio = True, round(ratio, 3)
        except cv2.error as e:
            meta.warnings.append(f"纸张检测失败，已跳过：{e}")

    if do_deskew:
        try:
            ang = estimate_skew(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY))
            if 0.3 <= abs(ang) <= 20:
                bgr = rotate(bgr, -ang)
                meta.deskew_angle = round(ang, 2)
        except cv2.error as e:
            meta.warnings.append(f"纠偏失败，已跳过：{e}")

    out = None
    if do_enhance:
        try:
            out = enhance(bgr)
            meta.enhanced = True
        except cv2.error as e:  # 例如内存紧张时 OpenCV 分配失败：退回未增强的灰度图，不让整份作业失败
            meta.warnings.append(f"增强失败，已使用原始灰度图：{str(e)[:80]}")
    if out is None:
        out = np.asarray(Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)).convert("L"))
    try:
        out, meta.cropped = crop_margins(out)
    except cv2.error as e:
        meta.warnings.append(f"裁边失败，已跳过：{str(e)[:80]}")
    pil = _to_pil(out)
    meta.output_size = pil.size
    return pil, meta


def stitch_vertical(images: list[Image.Image], gap: int = 24, width: int | None = None) -> Image.Image:
    """多页拼接：统一宽度后竖向拼成一张长图（用于结果页展示与整体识别）。"""
    if not images:
        raise ValueError("没有可拼接的页面")
    width = width or min(1400, max(i.width for i in images))
    scaled = [i.convert("L").resize((width, max(1, int(i.height * width / i.width)))) for i in images]
    total_h = sum(i.height for i in scaled) + gap * (len(scaled) - 1)
    canvas = Image.new("L", (width, total_h), 255)
    y = 0
    for i in scaled:
        canvas.paste(i, (0, y))
        y += i.height + gap
    return canvas


def save_image(im: Image.Image, path: Path, quality: int = 90) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path, "JPEG", quality=quality, optimize=True)
