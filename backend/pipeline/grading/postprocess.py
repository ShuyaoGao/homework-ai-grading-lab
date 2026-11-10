"""评分后处理：所有 provider（含 mock）的输出都经过这里，保证结果合法、可解释。

做的事：
1. 分项分截断到 [0, 满分]，未知/缺失的维度补 0 并记 warning；
2. 扣分项截断到量规规定的上限；总分 = 分项和 − 扣分，截断到 [0, 满分]；
3. 根据 quote 在识别文本里定位错误的字符位置（模型给的偏移量不可信）；找不到的保留但不高亮；
4. 定档；
5. 判断是否“建议人工复核”（置信度低 / 识别质量差 / 贴近档次边界 / 低分 / 模型自报 / 有校正）。
"""
from __future__ import annotations

import re

from ..error_types import normalize_error_type
from ..rubrics import Rubric
from .schema import (
    CriterionScore,
    DeductionItem,
    ErrorAnnotation,
    GradingResult,
    KnowledgePointScore,
    LLMGradingOutput,
)


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _norm_ws(s: str) -> str:
    return re.sub(r"\s+", "", s)


def locate_quote(text: str, quote: str, used: set[tuple[int, int]] | None = None) -> tuple[int, int] | None:
    """在原文中定位引用片段：先精确匹配（跳过已用位置），再忽略空白匹配。"""
    if not quote:
        return None
    used = used or set()
    start = 0
    while True:
        i = text.find(quote, start)
        if i < 0:
            break
        span = (i, i + len(quote))
        if span not in used:
            return span
        start = i + 1
    # 忽略空白/换行的模糊匹配（OCR 换行常把短语切开）
    q = _norm_ws(quote)
    if not q:
        return None
    idx_map = [i for i, ch in enumerate(text) if not ch.isspace()]
    compact = "".join(text[i] for i in idx_map)
    j = compact.find(q)
    if j < 0:
        j = compact.lower().find(q.lower())
    if j < 0:
        return None
    return idx_map[j], idx_map[j + len(q) - 1] + 1


def finalize(
    raw: LLMGradingOutput,
    rubric: Rubric,
    text: str,
    reference: dict | None,
    provider: str,
    model: str = "",
    ocr_confidence: float | None = None,
    extra_warnings: list[str] | None = None,
) -> GradingResult:
    warnings: list[str] = list(extra_warnings or [])
    reasons: list[str] = [r for r in raw.review_reasons if r]
    crit_defs = rubric.effective_criteria(reference)
    by_id = {c.id: c for c in raw.criteria}

    criteria: list[CriterionScore] = []
    corrected = False
    for c in crit_defs:
        got = by_id.get(c.id)
        if got is None:
            warnings.append(f"模型未给出维度 {c.name}（{c.id}）的分数，按 0 分处理")
            corrected = True
            criteria.append(CriterionScore(id=c.id, name=c.name, score=0, max_score=c.max))
            continue
        s = _clamp(float(got.score), 0, c.max)
        if abs(s - got.score) > 1e-9:
            warnings.append(f"维度 {c.name} 的分数 {got.score:g} 超出 [0,{c.max:g}]，已截断为 {s:g}")
            corrected = True
        level = got.level
        if not level and c.levels:
            for lv in c.levels:
                if lv.min - 1e-9 <= s <= lv.max + 1e-9:
                    level = lv.name
                    break
        criteria.append(CriterionScore(id=c.id, name=c.name, score=round(s, 2), max_score=c.max,
                                       level=level, comment=got.comment))
    unknown = set(by_id) - {c.id for c in crit_defs}
    if unknown:
        warnings.append(f"忽略了量规中不存在的维度：{', '.join(sorted(unknown))}")

    # 扣分项
    ded_defs = {d.id: d for d in rubric.deductions}
    deductions: list[DeductionItem] = []
    for d in raw.deductions:
        if d.points <= 0:
            continue
        dd = ded_defs.get(d.id)
        if dd is None:
            warnings.append(f"忽略了量规中不存在的扣分项：{d.id}")
            continue
        pts = d.points if dd.max is None else min(d.points, dd.max)
        deductions.append(DeductionItem(id=d.id, name=dd.name, points=round(pts, 2), reason=d.reason))

    max_score = rubric.effective_max(reference)
    subtotal = sum(c.score for c in criteria)
    total = round(_clamp(subtotal - sum(d.points for d in deductions), 0, max_score), 2)

    # 错误定位
    errors: list[ErrorAnnotation] = []
    used: set[tuple[int, int]] = set()
    missing_quotes = 0
    for e in raw.errors:
        span = locate_quote(text, e.quote, used)
        if span:
            used.add(span)
        elif e.quote.strip():
            missing_quotes += 1
        sev = e.severity if e.severity in ("minor", "major", "critical") else "minor"
        errors.append(ErrorAnnotation(
            type=normalize_error_type(e.type), quote=e.quote, start=span[0] if span else None,
            end=span[1] if span else None, message=e.message, suggestion=e.suggestion, severity=sev,
            criterion_id=e.criterion_id or None, source="ai" if provider != "mock" else "rule",
        ))
    if missing_quotes:
        warnings.append(f"{missing_quotes} 条错误批注的引文在识别文本中找不到，未能高亮")
    errors.sort(key=lambda e: (e.start is None, e.start or 0))

    # 同名知识点取平均
    kp_acc: dict[str, list[float]] = {}
    for k in raw.knowledge_points:
        if k.name:
            kp_acc.setdefault(k.name.strip(), []).append(_clamp(float(k.score_ratio), 0, 1))
    kps = [KnowledgePointScore(name=k, score_ratio=round(sum(v) / len(v), 3)) for k, v in kp_acc.items()]
    # 量规维度上标注的知识点也计入（用于画像）
    have = {k.name for k in kps}
    for c, cs in zip(crit_defs, criteria):
        if c.knowledge_point and c.knowledge_point not in have and cs.max_score > 0:
            kps.append(KnowledgePointScore(name=c.knowledge_point, score_ratio=round(cs.score / cs.max_score, 3)))
            have.add(c.knowledge_point)

    confidence = _clamp(float(raw.confidence), 0, 1)
    band = rubric.band_of(total, max_score)

    # 复核判断
    pol = rubric.review_policy
    if pol.always:
        reasons.append("该量规设置为全部人工复核")
    if confidence < pol.confidence_below:
        reasons.append(f"模型置信度较低（{confidence:.2f}）")
    if ocr_confidence is not None and ocr_confidence < 0.75:
        reasons.append(f"手写识别置信度较低（{ocr_confidence:.2f}），可能有识别错误")
    if pol.near_band_boundary > 0 and rubric.distance_to_boundary(total, max_score) <= pol.near_band_boundary:
        reasons.append(f"总分 {total:g} 贴近档次边界，定档需人工确认")
    if max_score and total / max_score < pol.low_score_ratio:
        reasons.append(f"得分率较低（{total / max_score:.0%}），低分建议人工确认")
    if corrected:
        reasons.append("模型输出存在越界或缺项，已自动校正")
    if not text.strip():
        reasons.append("识别文本为空")
    reasons = list(dict.fromkeys(reasons))

    return GradingResult(
        rubric_key=rubric.key, rubric_version=rubric.version, total_score=total, max_score=max_score,
        band=band, criteria=criteria, deductions=deductions, errors=errors, comment=raw.comment.strip(),
        strengths=[s for s in raw.strengths if s], suggestions=[s for s in raw.suggestions if s],
        knowledge_points=kps, confidence=round(confidence, 3), needs_review=bool(reasons),
        review_reasons=reasons, provider=provider, model=model, warnings=warnings,
    )


def parse_llm_json(text: str) -> LLMGradingOutput:
    """从模型文本中稳健地取出 JSON（兼容 ```json 代码块、前后有说明文字的情况）。"""
    import json

    t = text.strip()
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", t, re.S)
    if m:
        t = m.group(1)
    else:
        i, j = t.find("{"), t.rfind("}")
        if i >= 0 and j > i:
            t = t[i : j + 1]
    return LLMGradingOutput.model_validate(json.loads(t))
