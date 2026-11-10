"""API 请求/响应 schema（Pydantic），同时生成 OpenAPI 文档。"""
from __future__ import annotations

import datetime as dt
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .pipeline.grading.schema import CriterionScore, DeductionItem, ErrorAnnotation, GradingResult

Role = Literal["admin", "teacher", "parent", "student"]


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class UserOut(ORM):
    id: int
    username: str
    display_name: str
    role: Role
    is_active: bool
    student_id: int | None = None


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.\-]+$")
    password: str = Field(min_length=8, max_length=128)
    display_name: str = Field(min_length=1, max_length=64)
    role: Role
    student_id: int | None = None


class UserPatch(BaseModel):
    display_name: str | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=8, max_length=128)


class PasswordChange(BaseModel):
    old_password: str
    new_password: str = Field(min_length=8, max_length=128)


class ClassCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    grade: str = ""
    school_year: str = ""
    teacher_id: int | None = None  # 管理员可指定；教师创建时默认自己


class ClassOut(ORM):
    id: int
    name: str
    grade: str
    school_year: str
    teacher_id: int
    student_count: int = 0


class StudentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    student_no: str = ""
    birth_year: int | None = Field(default=None, ge=1990, le=2030)


class StudentOut(ORM):
    id: int
    class_id: int
    name: str
    student_no: str
    birth_year: int | None = None
    is_minor_under_14: bool = True
    consents: dict[str, bool] = {}


class GuardianLinkIn(BaseModel):
    parent_user_id: int
    relation: str = "监护人"


class ConsentIn(BaseModel):
    scope: Literal["basic", "cloud_ai"]
    granted: bool
    policy_version: str = "v1"


class ConsentOut(ORM):
    id: int
    student_id: int
    guardian_user_id: int
    scope: str
    policy_version: str
    granted_at: dt.datetime
    revoked_at: dt.datetime | None = None


class AssignmentCreate(BaseModel):
    class_id: int
    title: str = Field(min_length=1, max_length=128)
    subject: Literal["english", "chinese", "math", "paper", "other"]
    rubric_key: str
    prompt: str = ""
    reference: dict[str, Any] = {}
    due_date: dt.datetime | None = None


class AssignmentOut(ORM):
    id: int
    class_id: int
    title: str
    subject: str
    rubric_key: str
    prompt: str
    reference: dict[str, Any]
    due_date: dt.datetime | None = None
    created_at: dt.datetime
    max_score: float | None = None
    rubric_name: str = ""


class PageOut(ORM):
    page_no: int
    width: int
    height: int
    has_processed: bool = False


class SubmissionBrief(ORM):
    id: int
    assignment_id: int
    assignment_title: str = ""
    subject: str = ""
    student_id: int
    student_name: str = ""
    status: str
    stage: str
    error: str
    final_total: float | None = None
    ai_total: float | None = None
    max_score: float | None = None
    band: str = ""
    needs_review: bool
    is_demo: bool = False
    submitted_at: dt.datetime
    reviewed_at: dt.datetime | None = None


class SubmissionDetail(SubmissionBrief):
    page_count: int
    pages: list[PageOut] = []
    has_stitched: bool = False
    ocr_provider: str
    grader_provider: str
    ocr_text: str
    ocr_confidence: float | None = None
    ocr_meta: dict[str, Any] = {}
    ocr_text_edited: bool = False
    preprocess_meta: dict[str, Any] = {}
    ai_result: GradingResult | None = None
    final_result: GradingResult | None = None
    rubric_key: str = ""
    assignment_prompt: str = ""
    can_review: bool = False
    disclaimer: str = "AI 评分仅供参考，以教师复核结果为准。"


class RetryIn(BaseModel):
    ocr_provider: str | None = None
    grader_provider: str | None = None
    regrade_only: bool = False


class OcrTextIn(BaseModel):
    text: str = Field(max_length=50000)
    regrade: bool = True


class ReviewCriterionIn(BaseModel):
    id: str
    score: float = Field(ge=0)
    comment: str = ""


class ReviewIn(BaseModel):
    criteria: list[ReviewCriterionIn]
    deductions: list[DeductionItem] = []
    errors: list[ErrorAnnotation] | None = None  # None = 沿用当前批注
    comment: str | None = None
    note: str = ""  # 教师内部备注（修改理由）


class ReviewOut(ORM):
    id: int
    submission_id: int
    reviewer_id: int
    reviewer_name: str = ""
    ai_total: float | None
    teacher_total: float
    max_score: float
    ai_band: str
    teacher_band: str
    diff: dict[str, Any]
    note: str
    created_at: dt.datetime


class RubricIn(BaseModel):
    yaml_text: str = Field(max_length=100000)


class RubricBrief(BaseModel):
    key: str
    name: str
    subject: str
    kind: str
    max_score: float | None
    builtin: bool
    version: str


__all__ = ["CriterionScore", "ErrorAnnotation", "GradingResult"]
