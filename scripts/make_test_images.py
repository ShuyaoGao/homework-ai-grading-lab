"""生成测试用“手写作业照片”到 samples/generated/（已 gitignore，可随时重建）。

    .venv\\Scripts\\python scripts\\make_test_images.py

产出：
- en_essay_photo.png      英语作文，模拟手机斜拍（透视+旋转+阴影），内嵌标准文本（供 mock OCR）
- zh_essay_photo.png      中文作文（行楷/楷体），内嵌标准文本
- math_photo.png          数学解答
- paper_p1.png / p2.png   两页小论文（测多页拼接）
- real_like_photo.jpg     不含内嵌文本的 JPEG（模拟真实照片：mock 无法识别，会提示人工复核）
- manifest.json           每张图对应的标准文本
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.demo import corpus  # noqa: E402
from backend.demo.handwriting import find_font, make_homework_photo, EN_FONTS, ZH_FONTS  # noqa: E402

OUT = ROOT / "samples" / "generated"


def main() -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    items = {
        "en_essay_photo": (corpus.gen_english("invite", 0.55, 11), "en", dict(angle=5)),
        "zh_essay_photo": (corpus.gen_chinese("grownup", 0.7, 12), "zh", dict(angle=-4)),
        "math_photo": (corpus.gen_math("equation", 0.5, 13), "zh", dict(angle=3)),
    }
    paper = corpus.gen_paper(0.9, 14).splitlines()
    half = len(paper) // 2
    items["paper_p1"] = ("\n".join(paper[:half]), "zh", dict(angle=2))
    items["paper_p2"] = ("\n".join(paper[half:]), "zh", dict(angle=-2))
    manifest = {}
    for i, (name, (text, lang, kw)) in enumerate(items.items()):
        p = make_homework_photo(text, lang, OUT / name, seed=100 + i, **kw)
        manifest[p.name] = {"lang": lang, "text": text}
    p = make_homework_photo(corpus.gen_english("weekend", 0.6, 15), "en", OUT / "real_like_photo", seed=15, embed=False)
    manifest[p.name] = {"lang": "en", "text": None, "note": "无内嵌文本"}
    (OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    fonts = {"en": str(find_font(EN_FONTS)), "zh": str(find_font(ZH_FONTS))}
    print(f"已生成 {len(manifest)} 张图片到 {OUT}；使用字体 {fonts}")
    return manifest


if __name__ == "__main__":
    main()
