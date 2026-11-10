"""评分结果的结构化 schema。

两层：
- LLMGradingOutput：要求模型输出的 JSON（只含模型该判断的东西；字符位置由服务端根据 quote 计算，
  因为模型给的偏移量不可靠）。
- GradingResult：经过后处理校验后的最终结果（分数被截断到合法范围、总分重算、档次、复核标记）。
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

Severity = Literal["minor", "major", "critical"]


class CriterionScore(BaseModel):
    id: str
    name: str = ""
    score: float
    max_score: float
    level: str = ""
    comment: str = ""


class ErrorAnnotation(BaseModel):
    type: str = "other"
    quote: str = ""
    start: int | None = None  # 在识别文本中的字符偏移（服务端计算）
    end: int | None = None
    message: str = ""
    suggestion: str = ""
    severity: Severity = "minor"
    criterion_id: str | None = None
    source: Literal["ai", "rule", "teacher"] = "ai"


class DeductionItem(BaseModel):
    id: str
    name: str = ""
    points: float = Field(ge=0)
    reason: str = ""


class KnowledgePointScore(BaseModel):
    name: str
    score_ratio: float = Field(ge=0, le=1)


class GradingResult(BaseModel):
    rubric_key: str
    rubric_version: str = ""
    total_score: float
    max_score: float
    band: str = ""
    criteria: list[CriterionScore]
    deductions: list[DeductionItem] = []
    errors: list[ErrorAnnotation] = []
    comment: str = ""
    strengths: list[str] = []
    suggestions: list[str] = []
    knowledge_points: list[KnowledgePointScore] = []
    confidence: float = Field(ge=0, le=1)
    needs_review: bool = True
    review_reasons: list[str] = []
    provider: str = ""
    model: str = ""
    warnings: list[str] = []

    @field_validator("total_score")
    @classmethod
    def _non_negative(cls, v: float) -> float:
        if v < 0:
            raise ValueError("total_score 不能为负")
        return v


# ---------- 模型输出（LLM）----------

class LLMCriterion(BaseModel):
    id: str
    score: float
    level: str = ""
    comment: str = ""


class LLMError(BaseModel):
    type: str
    quote: str
    message: str
    suggestion: str = ""
    severity: str = "minor"
    criterion_id: str = ""


class LLMDeduction(BaseModel):
    id: str
    points: float
    reason: str = ""


class LLMKnowledgePoint(BaseModel):
    name: str
    score_ratio: float


class LLMGradingOutput(BaseModel):
    criteria: list[LLMCriterion]
    deductions: list[LLMDeduction] = []
    errors: list[LLMError] = []
    comment: str = ""
    strengths: list[str] = []
    suggestions: list[str] = []
    knowledge_points: list[LLMKnowledgePoint] = []
    confidence: float = 0.5
    review_reasons: list[str] = []


def llm_output_json_schema() -> dict[str, Any]:
    """给 Claude structured outputs / vLLM guided decoding 用的 JSON Schema（全字段 required、禁止多余字段）。"""
    def obj(props: dict[str, Any]) -> dict[str, Any]:
        return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}

    s = {"type": "string"}
    n = {"type": "number"}
    return obj({
        "criteria": {"type": "array", "items": obj({"id": s, "score": n, "level": s, "comment": s})},
        "deductions": {"type": "array", "items": obj({"id": s, "points": n, "reason": s})},
        "errors": {"type": "array", "items": obj({
            "type": s, "quote": s, "message": s, "suggestion": s,
            "severity": {"type": "string", "enum": ["minor", "major", "critical"]}, "criterion_id": s,
        })},
        "comment": s,
        "strengths": {"type": "array", "items": s},
        "suggestions": {"type": "array", "items": s},
        "knowledge_points": {"type": "array", "items": obj({"name": s, "score_ratio": n})},
        "confidence": n,
        "review_reasons": {"type": "array", "items": s},
    })
