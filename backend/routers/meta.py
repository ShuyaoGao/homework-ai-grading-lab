"""系统信息、provider 状态、错误类型字典、AI-教师一致性、审计日志、数据保留清理。"""
from __future__ import annotations

import datetime as dt
import shutil

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import __version__
from ..analytics.profile import ai_teacher_agreement
from ..config import get_settings
from ..db import get_db
from ..deps import accessible_class_ids, audit, get_current_user, require_roles
from ..models import AuditLog, Submission, User
from ..pipeline.error_types import DIMENSION_LABELS, ERROR_TYPES
from ..pipeline.registry import describe_providers
from ..pipeline.runner import submission_dir

router = APIRouter(prefix="/api", tags=["系统"])


@router.get("/health", summary="健康检查（无需登录）")
def health() -> dict:
    s = get_settings()
    return {"ok": True, "version": __version__, "ocr": s.resolve_ocr_provider(), "grader": s.resolve_grader_provider(),
            "demo": s.demo_mode}


@router.get("/meta/providers", summary="可用的识别/评分 provider（不返回任何 key）")
def providers(user: User = Depends(get_current_user)) -> dict:
    return describe_providers()


@router.get("/meta/error-types", summary="错误类型字典")
def error_types() -> dict:
    return {"error_types": ERROR_TYPES, "dimensions": DIMENSION_LABELS}


@router.get("/agreement", summary="AI 初评与教师复核的一致性（QWK / Pearson / MAE / ±1 档）")
def agreement(class_id: int | None = None, rubric_key: str | None = None,
              user: User = Depends(require_roles("teacher", "admin")), db: Session = Depends(get_db)) -> dict:
    if user.role == "teacher":
        ids = accessible_class_ids(db, user) or set()
        if class_id is None:
            return {cid: ai_teacher_agreement(db, class_id=cid, rubric_key=rubric_key) for cid in sorted(ids)}
        if class_id not in ids:
            return {}
    return ai_teacher_agreement(db, class_id=class_id, rubric_key=rubric_key)


@router.get("/admin/audit", summary="审计日志（管理员）")
def audit_logs(limit: int = 200, action: str | None = None, user: User = Depends(require_roles("admin")),
               db: Session = Depends(get_db)) -> list[dict]:
    q = select(AuditLog).order_by(AuditLog.id.desc()).limit(min(limit, 2000))
    if action:
        q = q.where(AuditLog.action == action)
    return [{"id": r.id, "user_id": r.user_id, "action": r.action, "target": r.target, "detail": r.detail,
             "created_at": r.created_at.isoformat()} for r in db.execute(q).scalars()]


@router.post("/admin/retention/purge", summary="按保留期限清理作业图片（管理员；dry_run=true 只统计）")
def purge(dry_run: bool = True, days: int | None = None, user: User = Depends(require_roles("admin")),
          db: Session = Depends(get_db)) -> dict:
    keep = days if days is not None else get_settings().retention_days
    cutoff = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) - dt.timedelta(days=keep)
    subs = list(db.execute(select(Submission).where(Submission.submitted_at < cutoff)).scalars())
    removed = 0
    if not dry_run:
        for s in subs:
            d = submission_dir(s.id)
            if d.exists():
                shutil.rmtree(d, ignore_errors=True)
                removed += 1
            for p in s.pages:
                p.processed_path = ""
            s.ocr_meta = {**(s.ocr_meta or {}), "images_purged_at": dt.datetime.now().isoformat()}
        audit(db, user, "retention_purge", "", days=keep, submissions=len(subs))
        db.commit()
    return {"retention_days": keep, "cutoff": cutoff.isoformat(), "matched_submissions": len(subs),
            "image_dirs_removed": removed, "dry_run": dry_run,
            "note": "只删除图片文件；成绩与识别文本保留用于学情分析，如需一并删除请使用删除提交接口。"}
