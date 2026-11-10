"""量规（rubric）：YAML 定义 → Pydantic 校验 → 渲染进提示词。

内置量规放在 backend/rubrics/*.yaml；教师自定义量规存数据库（同一 YAML 格式）。
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, ValidationError, model_validator

BUILTIN_DIR = Path(__file__).resolve().parent.parent / "rubrics"

RubricKind = Literal["essay_en", "essay_zh", "short_answer", "paper", "generic"]


class Level(BaseModel):
    name: str = ""
    min: float
    max: float
    text: str = ""


class Criterion(BaseModel):
    id: str
    name: str
    max: float = Field(gt=0)
    dimension: str = "content"
    descriptor: str = ""
    levels: list[Level] = []
    knowledge_point: str = ""


class Band(BaseModel):
    name: str
    min: float
    max: float
    descriptor: str = ""


class Deduction(BaseModel):
    id: str
    name: str
    rule: str
    per_item: float = 1
    max: float | None = None


class ReviewPolicy(BaseModel):
    confidence_below: float = 0.7
    near_band_boundary: float = 0.5  # 距离档次边界不超过多少分时建议复核
    low_score_ratio: float = 0.4  # 低于满分该比例时建议复核（低分要慎重）
    always: bool = False


class Rubric(BaseModel):
    key: str = Field(pattern=r"^[a-z0-9_\-]{2,64}$")
    name: str
    subject: str
    kind: RubricKind = "generic"
    version: str = "1"
    max_score: float | None = None  # short_answer 可为空：由参考答案逐题分值求和
    band_scale: Literal["score", "ratio"] = "score"  # ratio：档次按得分率（0–1）划分
    description: str = ""
    source: str = ""
    scoring_method: str = ""
    bands: list[Band] = []
    criteria: list[Criterion] = []
    deductions: list[Deduction] = []
    review_policy: ReviewPolicy = ReviewPolicy()
    heuristic: dict[str, Any] = {}  # mock/规则评分器的参数（目标词数等）

    @model_validator(mode="after")
    def _check(self) -> Rubric:
        if self.kind != "short_answer":
            if not self.criteria:
                raise ValueError("非简答类量规必须至少有一个评分维度 criteria")
            total = sum(c.max for c in self.criteria)
            if self.max_score is None:
                self.max_score = total
            elif abs(total - self.max_score) > 1e-6:
                raise ValueError(f"各维度满分之和 {total} 与 max_score {self.max_score} 不一致")
        ids = [c.id for c in self.criteria]
        if len(ids) != len(set(ids)):
            raise ValueError("criteria.id 重复")
        for b in self.bands:
            if b.min > b.max:
                raise ValueError(f"档次 {b.name} 的 min > max")
        return self

    def _scaled(self, score: float, max_score: float | None) -> float:
        if self.band_scale == "ratio":
            m = max_score or self.max_score or 1
            return score / m if m else 0.0
        return score

    def band_of(self, score: float, max_score: float | None = None) -> str:
        """档次按下限划分：取下限 ≤ 分数的最高档。整数档次之间的半分（如 16.5，介于 13–16 与 17–20 之间）
        归入较低档——未达到上一档下限即不算上一档。"""
        score = self._scaled(score, max_score)
        if not self.bands:
            return ""
        for b in sorted(self.bands, key=lambda x: -x.min):
            if score >= b.min - 1e-9:
                return b.name
        return min(self.bands, key=lambda x: x.min).name

    def band_index(self, score: float, max_score: float | None = None) -> int:
        """档次序号（0 = 最低档），用于 ±1 档一致率；无档次时按满分 5 等分。"""
        if self.bands:
            order = sorted(self.bands, key=lambda b: b.min)
            name = self.band_of(score, max_score)
            for i, b in enumerate(order):
                if b.name == name:
                    return i
        ms = max_score or self.max_score or 1
        return min(4, int(score / ms * 5))

    def distance_to_boundary(self, score: float, max_score: float | None = None) -> float:
        if not self.bands or self.band_scale == "ratio":
            return float("inf")
        edges = {b.min for b in self.bands if b.min > 0}  # 档次下限即分界线
        return min((abs(score - e) for e in edges), default=float("inf"))

    def effective_criteria(self, reference: dict | None) -> list[Criterion]:
        """简答/数学：每道题就是一个评分维度。"""
        if self.kind == "short_answer":
            qs = (reference or {}).get("questions") or []
            return [
                Criterion(id=str(q.get("id", i + 1)), name=f"第{q.get('id', i + 1)}题", max=float(q.get("points", 1)),
                          dimension="accuracy", knowledge_point=q.get("knowledge_point", ""),
                          descriptor=str(q.get("question", "")))
                for i, q in enumerate(qs)
            ]
        return self.criteria

    def effective_max(self, reference: dict | None) -> float:
        if self.kind == "short_answer":
            return float(sum(c.max for c in self.effective_criteria(reference))) or float(self.max_score or 0)
        return float(self.max_score or 0)

    def render_for_prompt(self, reference: dict | None = None) -> str:
        lines = [f"# 量规：{self.name}（版本 {self.version}）", f"学科：{self.subject}；满分：{self.effective_max(reference):g} 分"]
        if self.description:
            lines.append(self.description.strip())
        if self.scoring_method:
            lines += ["## 评分方法", self.scoring_method.strip()]
        if self.bands:
            lines.append("## 档次")
            for b in sorted(self.bands, key=lambda x: -x.max):
                rng = (f"得分率 {b.min:.0%}–{b.max:.0%}" if self.band_scale == "ratio" else f"{b.min:g}–{b.max:g} 分")
                lines.append(f"- {b.name}（{rng}）：{b.descriptor.strip()}")
        lines.append("## 评分维度（criteria，分别打分，总分=各维度之和−扣分项）")
        for c in self.effective_criteria(reference):
            lines.append(f"- id={c.id}｜{c.name}｜满分 {c.max:g}：{c.descriptor.strip()}")
            for lv in c.levels:
                lines.append(f"    - {lv.name} {lv.min:g}–{lv.max:g}：{lv.text.strip()}")
        if self.deductions:
            lines.append("## 扣分项（deductions）")
            for d in self.deductions:
                cap = f"，最多扣 {d.max:g} 分" if d.max is not None else ""
                lines.append(f"- id={d.id}｜{d.name}：{d.rule.strip()}（每处 {d.per_item:g} 分{cap}）")
        return "\n".join(lines)


class RubricError(ValueError):
    pass


def parse_rubric_yaml(text: str) -> Rubric:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise RubricError(f"YAML 语法错误：{e}") from e
    if not isinstance(data, dict):
        raise RubricError("量规 YAML 顶层必须是映射（key: value）")
    try:
        return Rubric.model_validate(data)
    except ValidationError as e:
        raise RubricError(f"量规字段校验失败：{e}") from e


@lru_cache(maxsize=1)
def builtin_rubrics() -> dict[str, Rubric]:
    out: dict[str, Rubric] = {}
    for p in sorted(BUILTIN_DIR.glob("*.yaml")):
        r = parse_rubric_yaml(p.read_text(encoding="utf-8"))
        out[r.key] = r
    return out


def builtin_yaml(key: str) -> str | None:
    for p in BUILTIN_DIR.glob("*.yaml"):
        if parse_rubric_yaml(p.read_text(encoding="utf-8")).key == key:
            return p.read_text(encoding="utf-8")
    return None


def get_rubric(key: str, db=None) -> Rubric:  # noqa: ANN001
    b = builtin_rubrics()
    if key in b:
        return b[key]
    if db is not None:
        from ..models import Rubric as RubricRow

        row = db.query(RubricRow).filter(RubricRow.key == key).first()
        if row:
            return parse_rubric_yaml(row.yaml_text)
    raise RubricError(f"找不到量规：{key}")
