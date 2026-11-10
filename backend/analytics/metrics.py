"""一致性指标：QWK（二次加权 Kappa）、Pearson、MAE、精确一致率、±1 档一致率。纯 numpy 实现。"""
from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np


def quadratic_weighted_kappa(a: Sequence[int], b: Sequence[int], min_rating: int | None = None,
                             max_rating: int | None = None) -> float | None:
    """QWK（与 sklearn.metrics.cohen_kappa_score(weights='quadratic') 定义一致）。

    评分须为整数（连续分数请先四舍五入或映射到档次）。两组完全一致且只有一个取值时返回 1.0；
    方差为 0 导致分母为 0 时返回 None（数学上无定义，不能当作 0 或 -1 处理）。
    """
    a = [int(round(x)) for x in a]
    b = [int(round(x)) for x in b]
    if len(a) != len(b):
        raise ValueError("两组评分长度不一致")
    if not a:
        return None
    lo = min(min(a), min(b)) if min_rating is None else min_rating
    hi = max(max(a), max(b)) if max_rating is None else max_rating
    n = hi - lo + 1
    if n <= 1:
        return 1.0 if a == b else None
    O = np.zeros((n, n))
    for x, y in zip(a, b):
        O[x - lo, y - lo] += 1
    W = np.array([[(i - j) ** 2 for j in range(n)] for i in range(n)], dtype=float) / (n - 1) ** 2
    E = np.outer(O.sum(axis=1), O.sum(axis=0)) / O.sum()
    denom = (W * E).sum()
    if denom == 0:
        return 1.0 if (W * O).sum() == 0 else None
    return float(1 - (W * O).sum() / denom)


def pearson(a: Sequence[float], b: Sequence[float]) -> float | None:
    x, y = np.asarray(a, float), np.asarray(b, float)
    if len(x) < 2 or x.std() == 0 or y.std() == 0:
        return None  # 任一方为常数时相关系数无定义
    return float(np.corrcoef(x, y)[0, 1])


def mae(a: Sequence[float], b: Sequence[float]) -> float | None:
    if not len(a):
        return None
    return float(np.mean(np.abs(np.asarray(a, float) - np.asarray(b, float))))


def exact_agreement(a: Sequence[int], b: Sequence[int]) -> float | None:
    if not len(a):
        return None
    return float(np.mean([int(x) == int(y) for x, y in zip(a, b)]))


def adjacent_agreement(a: Sequence[int], b: Sequence[int], tol: int = 1) -> float | None:
    if not len(a):
        return None
    return float(np.mean([abs(int(x) - int(y)) <= tol for x, y in zip(a, b)]))


def degeneracy(values: Sequence[float]) -> dict:
    v = np.asarray(values, float)
    return {"n": int(len(v)), "unique": int(len(set(v.tolist()))), "std": float(v.std()) if len(v) else 0.0}


def agreement_report(ai: Sequence[float], human: Sequence[float], ai_band: Sequence[int] | None = None,
                     human_band: Sequence[int] | None = None, min_rating: int | None = None,
                     max_rating: int | None = None) -> dict:
    """一组 AI 分与人工分的完整一致性报告。分数用于 Pearson/MAE；整数化分数用于 QWK；档次用于 ±1 档一致率。

    任一方方差为 0（模型给所有样本同一分）时，QWK/Pearson 置为 null 并给出 blocked_reason，
    避免把无定义的数字当成真实结果写进报告。
    """
    ai, human = list(ai), list(human)
    out: dict = {"n": len(ai)}
    if not ai:
        out["blocked_reason"] = "没有样本"
        return out
    da, dh = degeneracy(ai), degeneracy(human)
    out["degeneracy"] = {"ai": da, "human": dh}
    ai_i = [int(round(x)) for x in ai]
    hu_i = [int(round(x)) for x in human]
    if da["std"] == 0 or dh["std"] == 0:
        out["qwk"] = None
        out["pearson"] = None
        out["blocked_reason"] = "AI 或人工评分方差为 0（所有样本同分），QWK/相关系数无定义"
    else:
        q = quadratic_weighted_kappa(ai_i, hu_i, min_rating, max_rating)
        out["qwk"] = None if q is None or math.isnan(q) else round(q, 4)
        p = pearson(ai, human)
        out["pearson"] = None if p is None else round(p, 4)
    out["mae"] = round(mae(ai, human), 4)
    out["exact"] = round(exact_agreement(ai_i, hu_i), 4)
    out["within1_score"] = round(adjacent_agreement(ai_i, hu_i, 1), 4)
    if ai_band is not None and human_band is not None:
        out["band_exact"] = round(exact_agreement(ai_band, human_band), 4)
        out["within1_band"] = round(adjacent_agreement(ai_band, human_band, 1), 4)
    out["mean_ai"] = round(float(np.mean(ai)), 3)
    out["mean_human"] = round(float(np.mean(human)), 3)
    out["bias"] = round(out["mean_ai"] - out["mean_human"], 3)  # 正值 = AI 偏宽松
    return out


# ---------------------------------------------------------------- 识别准确率

def _norm_for_cer(s: str, ignore_space: bool) -> str:
    import re
    import unicodedata

    s = unicodedata.normalize("NFKC", s or "")  # 全角/半角统一（，→, 等）
    s = s.replace("[?]", "⁇")  # 识别不清标记算一个错字符
    return re.sub(r"\s+", "", s) if ignore_space else re.sub(r"\s+", " ", s).strip()


def levenshtein(a: str, b: str) -> int:
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def cer(reference: str, hypothesis: str, ignore_space: bool | None = None) -> float:
    """字符错误率 CER = 编辑距离 / 参考文本长度。中文默认忽略空白；英文把连续空白合并为一个空格。
    可以大于 1（识别出大量多余内容时）。参考为空时返回 0（两者都空）或 1。"""
    if ignore_space is None:
        ignore_space = sum(1 for ch in reference if "一" <= ch <= "鿿") > len(reference) * 0.3
    r, h = _norm_for_cer(reference, ignore_space), _norm_for_cer(hypothesis, ignore_space)
    if not r:
        return 0.0 if not h else 1.0
    return levenshtein(r, h) / len(r)
