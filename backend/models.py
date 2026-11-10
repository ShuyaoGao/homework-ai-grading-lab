"""ORM 模型。

数据最小化原则：学生只存姓名（可用化名）、学号、出生年份（仅用于判断是否不满 14 周岁），
不存身份证号、住址、电话、照片以外的生物特征。
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


ROLES = ("admin", "teacher", "parent", "student")


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    display_name: Mapped[str] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(String(16), index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # 学生账号关联到学生档案
    student_id: Mapped[int | None] = mapped_column(ForeignKey("students.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class ClassRoom(Base):
    __tablename__ = "classes"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    grade: Mapped[str] = mapped_column(String(32), default="")
    school_year: Mapped[str] = mapped_column(String(16), default="")
    teacher_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)

    students: Mapped[list[Student]] = relationship(back_populates="classroom")


class Student(Base):
    __tablename__ = "students"
    id: Mapped[int] = mapped_column(primary_key=True)
    class_id: Mapped[int] = mapped_column(ForeignKey("classes.id"), index=True)
    name: Mapped[str] = mapped_column(String(64))
    student_no: Mapped[str] = mapped_column(String(32), default="")
    birth_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)

    classroom: Mapped[ClassRoom] = relationship(back_populates="students")

    def is_minor_under(self, age: int, today: dt.date | None = None) -> bool:
        """出生年份未知时按“不满 14 周岁”从严处理。"""
        if self.birth_year is None:
            return True
        today = today or dt.date.today()
        return today.year - self.birth_year < age + 1  # 只有年份，取保守判断


class GuardianLink(Base):
    __tablename__ = "guardian_links"
    __table_args__ = (UniqueConstraint("parent_user_id", "student_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    parent_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id"), index=True)
    relation: Mapped[str] = mapped_column(String(16), default="监护人")


class Consent(Base):
    """监护人同意记录（append-only：撤回时写 revoked_at，不删除）。

    scope:
      basic    —— 在本系统内处理作业图片与成绩（本地处理）
      cloud_ai —— 允许把作业图片/文字发送给第三方云端模型（可能涉及数据出境）
    """
    __tablename__ = "consents"
    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id"), index=True)
    guardian_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    scope: Mapped[str] = mapped_column(String(16))
    policy_version: Mapped[str] = mapped_column(String(16), default="v1")
    granted_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    revoked_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)


class Rubric(Base):
    """教师自定义量规（YAML 原文 + 解析后的 key）。内置量规在 backend/rubrics/*.yaml。"""
    __tablename__ = "rubrics"
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    yaml_text: Mapped[str] = mapped_column(Text)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class Assignment(Base):
    __tablename__ = "assignments"
    id: Mapped[int] = mapped_column(primary_key=True)
    class_id: Mapped[int] = mapped_column(ForeignKey("classes.id"), index=True)
    title: Mapped[str] = mapped_column(String(128))
    subject: Mapped[str] = mapped_column(String(16))  # english | chinese | math | paper | other
    rubric_key: Mapped[str] = mapped_column(String(64))
    prompt: Mapped[str] = mapped_column(Text, default="")  # 题目/写作要求
    # 参考答案/要点：{"questions":[...]} 或 {"key_points":[...], "min_words":80}
    reference: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    due_date: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


SUBMISSION_STATUSES = ("queued", "processing", "ai_done", "reviewed", "failed")


class Submission(Base):
    __tablename__ = "submissions"
    id: Mapped[int] = mapped_column(primary_key=True)
    assignment_id: Mapped[int] = mapped_column(ForeignKey("assignments.id"), index=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id"), index=True)
    uploaded_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    stage: Mapped[str] = mapped_column(String(32), default="")
    error: Mapped[str] = mapped_column(Text, default="")
    page_count: Mapped[int] = mapped_column(Integer, default=0)

    ocr_provider: Mapped[str] = mapped_column(String(64), default="")
    grader_provider: Mapped[str] = mapped_column(String(64), default="")
    preprocess_meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    ocr_text: Mapped[str] = mapped_column(Text, default="")
    ocr_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    ocr_meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    ocr_text_edited: Mapped[bool] = mapped_column(Boolean, default=False)

    ai_result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    ai_total: Mapped[float | None] = mapped_column(Float, nullable=True)
    final_result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    final_total: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=True)
    # 演示数据（程序合成的图片 + 模拟评分/模拟复核），界面上醒目标注，统计时可区分
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    # 客户端生成的上传令牌：弱网重试时同一次上传不会重复建提交（幂等）
    client_token: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    reviewed_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    submitted_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    pages: Mapped[list[SubmissionPage]] = relationship(
        back_populates="submission", cascade="all, delete-orphan", order_by="SubmissionPage.page_no"
    )
    assignment: Mapped[Assignment] = relationship()
    student: Mapped[Student] = relationship()


class SubmissionPage(Base):
    __tablename__ = "submission_pages"
    id: Mapped[int] = mapped_column(primary_key=True)
    submission_id: Mapped[int] = mapped_column(ForeignKey("submissions.id", ondelete="CASCADE"), index=True)
    page_no: Mapped[int] = mapped_column(Integer)
    original_path: Mapped[str] = mapped_column(String(512))
    processed_path: Mapped[str] = mapped_column(String(512), default="")
    width: Mapped[int] = mapped_column(Integer, default=0)
    height: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str] = mapped_column(String(64), default="")
    # 上传时从图片元数据里读到的“演示用标准文本”（仅 mock OCR 使用；真实照片为空）
    embedded_text: Mapped[str] = mapped_column(Text, default="")

    submission: Mapped[Submission] = relationship(back_populates="pages")


class Review(Base):
    """教师复核记录（append-only），每次保存一条，用于评估 AI 与教师一致性。"""
    __tablename__ = "reviews"
    id: Mapped[int] = mapped_column(primary_key=True)
    submission_id: Mapped[int] = mapped_column(ForeignKey("submissions.id", ondelete="CASCADE"), index=True)
    reviewer_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    rubric_key: Mapped[str] = mapped_column(String(64))
    ai_total: Mapped[float | None] = mapped_column(Float, nullable=True)
    teacher_total: Mapped[float] = mapped_column(Float)
    max_score: Mapped[float] = mapped_column(Float)
    ai_band: Mapped[str] = mapped_column(String(32), default="")
    teacher_band: Mapped[str] = mapped_column(String(32), default="")
    before: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    after: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    diff: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class AuditLog(Base):
    """访问未成年人数据与关键操作留痕。"""
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    target: Mapped[str] = mapped_column(String(128), default="")
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, index=True)
