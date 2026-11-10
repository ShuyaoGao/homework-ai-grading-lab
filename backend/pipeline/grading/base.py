"""评分 provider 接口与提示词。"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from ..rubrics import Rubric
from .schema import GradingResult


@dataclass
class GradingContext:
    rubric: Rubric
    text: str  # 识别出的作答文本
    prompt: str = ""  # 题目 / 写作要求
    reference: dict = field(default_factory=dict)  # 参考答案、要点、字数要求等
    ocr_confidence: float | None = None
    grade_level: str = ""  # 年级（如“初二”），帮助模型把握尺度


class GradingProvider(ABC):
    name: str = "base"
    is_cloud: bool = False
    model: str = ""

    @abstractmethod
    def grade(self, ctx: GradingContext) -> GradingResult: ...

    def available(self) -> tuple[bool, str]:
        return True, ""


SYSTEM_PROMPT = """你是一位经验丰富、严谨公正的中学/大学教师，负责按给定量规批改学生作业。
要求：
1. 严格依据量规的档次与各评分维度打分，先整体定档，再给各维度分数；各维度分数之和减去扣分项应落在所定档次区间内。
2. 学生文本来自手写识别（OCR），可能有识别错误。对明显是识别造成的问题（如个别乱码、[?] 标记）不要扣分，
   但要在 review_reasons 里说明“识别存疑”，并用 type=ocr_uncertain 标注。
3. errors 中每条的 quote 必须是学生原文中逐字存在的连续片段（不要改写、不要加省略号），尽量短（定位到出错的词或短句）。
4. error type 只能使用这些代码：spelling, grammar.tense, grammar.agreement, grammar.article, grammar.preposition,
   grammar.comparative, grammar.other, capitalization, word_choice, repetition, typo, punctuation, sentence,
   off_topic, length, title, structure, coherence, logic, citation, calculation, concept, missing_step, unit,
   incomplete, ocr_uncertain, other。
5. comment 是写给学生和家长看的评语：先肯定优点，再指出 1–3 个最重要的改进方向，语气鼓励、具体、可操作，
   中文作业与评语用中文；英语作文的评语也用中文，引用英文原句时保留英文。
6. confidence 是你对本次评分与资深教师一致的把握（0–1）。文本残缺、识别质量差、题意不明或你拿不准时调低。
7. knowledge_points：列出本次作业反映出的知识点掌握情况（score_ratio 0–1），简答/数学题按题目对应的知识点给出。
8. 学生身份信息已匿名化，不要猜测或输出任何个人信息。只输出符合 schema 的 JSON。"""


def build_user_prompt(ctx: GradingContext) -> str:
    parts = [ctx.rubric.render_for_prompt(ctx.reference)]
    if ctx.grade_level:
        parts.append(f"# 学段/年级\n{ctx.grade_level}")
    if ctx.prompt:
        parts.append(f"# 题目与写作要求\n{ctx.prompt}")
    if ctx.reference:
        parts.append("# 参考答案 / 要点 / 要求（JSON）\n" + json.dumps(ctx.reference, ensure_ascii=False, indent=1))
    crit_ids = ", ".join(c.id for c in ctx.rubric.effective_criteria(ctx.reference))
    ded_ids = ", ".join(d.id for d in ctx.rubric.deductions) or "（无）"
    parts.append(f"# 输出约束\ncriteria 必须且只能包含这些 id：{crit_ids}\ndeductions 的 id 只能取：{ded_ids}（未扣分则返回空数组）")
    if ctx.ocr_confidence is not None:
        parts.append(f"# 识别质量\n手写识别置信度约 {ctx.ocr_confidence:.2f}")
    parts.append("# 学生作答（手写识别文本，原样提供，不要纠正后再评）\n<<<\n" + ctx.text + "\n>>>")
    return "\n\n".join(parts)
