"""自动演示用的“家长手机拍的作业照片”：按作业题目生成一篇中等水平的作答，渲染成仿手写照片并缓存。

照片里用 PNG iTXt 块内嵌了标准文本（与种子数据相同的做法），所以 mock 识别能“认出”文字。
这是演示数据，不是真实学生作业。
"""
from __future__ import annotations

import threading
from pathlib import Path

from . import corpus
from .handwriting import make_homework_photo, style_for

_lock = threading.Lock()
SAMPLE_ABILITY = 0.6  # 中等水平：有可以改的地方，也有能肯定的地方
SAMPLE_SEED = 4242


def _corpus_key(title: str, subject: str) -> tuple[str, str]:
    for key, p in corpus.ZH_PROMPTS.items():
        if p["title"] == title:
            return "zh", key
    for key, p in corpus.EN_PROMPTS.items():
        if p["title"] == title:
            return "en", key
    for key, m in corpus.MATH_SETS.items():
        if m["title"] == title:
            return "math", key
    if subject == "english":
        return "en", "invite"
    if subject == "math":
        return "math", "equation"
    return "zh", "hero"


def sample_text(title: str, subject: str) -> tuple[str, str]:
    """返回 (文本, 语言)。"""
    kind, key = _corpus_key(title, subject)
    if kind == "en":
        return corpus.gen_english(key, SAMPLE_ABILITY, SAMPLE_SEED), "en"
    if kind == "math":
        return corpus.gen_math(key, SAMPLE_ABILITY, SAMPLE_SEED), "zh"
    return corpus.gen_chinese(key, SAMPLE_ABILITY, SAMPLE_SEED), "zh"


def photo_for(assignment, cache_dir: Path) -> Path:  # noqa: ANN001
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / f"assignment_{assignment.id}.png"
    with _lock:
        if target.exists():
            return target
        text, lang = sample_text(assignment.title, assignment.subject)
        paper = "squared" if assignment.subject == "math" else ("lined" if lang == "en" else "grid")
        style = style_for(0, lang, messiness=0.85, paper=paper)  # 0 号学生（张小明）的笔迹
        return make_homework_photo(text, lang, target.with_suffix(""), seed=SAMPLE_SEED, style=style,
                                   angle=-4, out_width=1100)
