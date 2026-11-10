"""作业提交：拍照上传 → 后台流水线 → 查看结果 → 教师复核（修改记录留痕）。"""
from __future__ import annotations

import json
import shutil

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..deps import (
    accessible_student_ids,
    audit,
    ensure_submission_access,
    ensure_teacher_of_submission,
    get_current_user,
    require_roles,
)
from ..models import Assignment, Review, Student, Submission, User, utcnow
from ..pipeline.grading.schema import CriterionScore, DeductionItem, ErrorAnnotation, GradingResult
from ..pipeline.registry import GRADERS, OCR_PROVIDERS
from ..pipeline.rubrics import RubricError, get_rubric
from ..pipeline.runner import has_consent, process_submission, save_pages, submission_dir
from ..schemas import OcrTextIn, PageOut, RetryIn, ReviewIn, ReviewOut, SubmissionBrief, SubmissionDetail

router = APIRouter(prefix="/api/submissions", tags=["作业提交与复核"])

ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp", "image/heic", "image/heif", "image/bmp", "image/gif",
                 "application/octet-stream", ""}


def _hide_scores(user: User, sub: Submission) -> bool:
    return user.role in ("parent", "student") and sub.status != "reviewed" and not get_settings().show_ai_before_review


def brief(sub: Submission, user: User | None = None) -> SubmissionBrief:
    b = SubmissionBrief.model_validate(sub)
    b.assignment_title = sub.assignment.title
    b.subject = sub.assignment.subject
    b.student_name = sub.student.name
    b.band = (sub.final_result or {}).get("band", "")
    if user is not None and _hide_scores(user, sub):
        b.final_total = b.ai_total = None
        b.band = ""
    return b


def _can_review(db: Session, user: User, sub: Submission) -> bool:
    try:
        ensure_teacher_of_submission(db, user, sub)
        return True
    except HTTPException:
        return False


def detail(db: Session, sub: Submission, user: User) -> SubmissionDetail:
    d = SubmissionDetail.model_validate({**brief(sub, user).model_dump(), **{
        k: getattr(sub, k) for k in ("page_count", "ocr_provider", "grader_provider", "ocr_text", "ocr_confidence",
                                     "ocr_meta", "ocr_text_edited", "preprocess_meta")}})
    d.pages = [PageOut(page_no=p.page_no, width=p.width, height=p.height, has_processed=bool(p.processed_path))
               for p in sub.pages]
    d.has_stitched = (submission_dir(sub.id) / "stitched.jpg").exists()
    d.rubric_key = sub.assignment.rubric_key
    d.assignment_prompt = sub.assignment.prompt
    d.can_review = _can_review(db, user, sub)
    if not _hide_scores(user, sub):
        d.ai_result = GradingResult.model_validate(sub.ai_result) if sub.ai_result else None
        d.final_result = GradingResult.model_validate(sub.final_result) if sub.final_result else None
    if user.role in ("parent", "student"):
        d.ai_result = None  # 家长/学生只看最终结果
        d.ocr_meta = {k: v for k, v in (sub.ocr_meta or {}).items() if k != "notes"}
    return d


@router.post("", response_model=SubmissionBrief, status_code=202,
             summary="上传作业照片（可多页），后台自动识别与评分")
async def upload(
    background: BackgroundTasks,
    assignment_id: int = Form(...),
    student_id: int = Form(...),
    files: list[UploadFile] = File(..., description="作业照片，可多张（多页按顺序拼接）"),
    ocr_provider: str | None = Form(None),
    grader_provider: str | None = Form(None),
    edits: str | None = Form(None, description='每页的编辑，JSON 数组，如 [{"rotate":90,"crop":[0.05,0.1,0.95,0.9]}, null]'),
    client_token: str | None = Form(None, max_length=64, description="客户端生成的随机串；网络重试时用同一个值，服务器不会重复建提交"),
    user: User = Depends(require_roles("teacher", "parent", "admin", "student")),
    db: Session = Depends(get_db),
) -> SubmissionBrief:
    s = get_settings()
    if client_token:  # 幂等：同一用户同一令牌已经建过提交，直接返回那一份
        prev = db.execute(select(Submission).where(Submission.uploaded_by == user.id,
                                                   Submission.client_token == client_token)).scalar_one_or_none()
        if prev is not None:
            return brief(prev, user)
    ids = accessible_student_ids(db, user)
    if ids is not None and student_id not in ids:
        raise HTTPException(403, "无权为该学生上传作业")
    st = db.get(Student, student_id)
    a = db.get(Assignment, assignment_id)
    if st is None or a is None:
        raise HTTPException(404, "学生或作业不存在")
    if a.class_id != st.class_id:
        raise HTTPException(400, "该作业不属于学生所在班级")
    if st.is_minor_under(s.minor_age) and not has_consent(db, st.id, "basic"):
        raise HTTPException(403, "该学生未满 14 周岁，需先由监护人在“授权与隐私”页面签署同意后才能上传作业")
    if user.role in ("parent", "student") and (ocr_provider or grader_provider):
        ocr_provider = grader_provider = None  # 家长/学生不能指定模型
    if ocr_provider and ocr_provider not in OCR_PROVIDERS:
        raise HTTPException(400, f"未知 OCR provider：{ocr_provider}")
    if grader_provider and grader_provider not in GRADERS:
        raise HTTPException(400, f"未知评分 provider：{grader_provider}")
    if not files:
        raise HTTPException(400, "请至少上传一张图片")
    if len(files) > s.max_pages:
        raise HTTPException(400, f"最多上传 {s.max_pages} 页")
    edit_list = None
    if edits:
        try:
            edit_list = json.loads(edits)
            if not isinstance(edit_list, list):
                raise ValueError
        except ValueError:
            raise HTTPException(400, "edits 必须是 JSON 数组") from None
    blobs = []
    for f in files:
        if (f.content_type or "") not in ALLOWED_TYPES and not (f.content_type or "").startswith("image/"):
            raise HTTPException(415, f"不支持的文件类型：{f.content_type}")
        data = await f.read()
        if len(data) > s.max_upload_mb * 1024 * 1024:
            raise HTTPException(413, f"单张图片不能超过 {s.max_upload_mb} MB")
        if not data:
            raise HTTPException(400, "空文件")
        blobs.append(data)

    sub = Submission(assignment_id=a.id, student_id=st.id, uploaded_by=user.id, status="queued", stage="uploaded",
                     client_token=client_token or None)
    db.add(sub)
    db.flush()
    try:
        save_pages(db, sub, blobs, edit_list)
    except ValueError as e:
        db.rollback()
        shutil.rmtree(submission_dir(sub.id), ignore_errors=True)
        raise HTTPException(400, str(e)) from e
    audit(db, user, "upload", f"submission:{sub.id}", student=st.id, pages=len(blobs))
    db.commit()
    if s.pipeline_sync:
        process_submission(sub.id, ocr_provider, grader_provider)
        db.refresh(sub)
    else:
        background.add_task(process_submission, sub.id, ocr_provider, grader_provider)
    return brief(sub, user)


@router.get("", response_model=list[SubmissionBrief], summary="提交列表（按权限过滤）")
def list_submissions(status: str | None = None, needs_review: bool | None = None, class_id: int | None = None,
                     assignment_id: int | None = None, student_id: int | None = None, limit: int = 200,
                     user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[SubmissionBrief]:
    q = select(Submission).join(Assignment, Submission.assignment_id == Assignment.id)
    ids = accessible_student_ids(db, user)
    if ids is not None:
        q = q.where(Submission.student_id.in_(ids))
    if status:
        q = q.where(Submission.status.in_(status.split(",")))
    if needs_review is not None:
        q = q.where(Submission.needs_review == needs_review)
    if class_id is not None:
        q = q.where(Assignment.class_id == class_id)
    if assignment_id is not None:
        q = q.where(Submission.assignment_id == assignment_id)
    if student_id is not None:
        q = q.where(Submission.student_id == student_id)
    q = q.order_by(Submission.submitted_at.desc()).limit(min(limit, 1000))
    return [brief(s, user) for s in db.execute(q).scalars()]


@router.get("/{sub_id}", response_model=SubmissionDetail, summary="提交详情（识别文本、评分、批注）")
def get_submission(sub_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> SubmissionDetail:
    sub = ensure_submission_access(db, user, sub_id)
    return detail(db, sub, user)


@router.get("/{sub_id}/pages/{page_no}/image", summary="作业图片（需登录；kind=original|processed）")
def page_image(sub_id: int, page_no: int, kind: str = "processed", user: User = Depends(get_current_user),
               db: Session = Depends(get_db)) -> FileResponse:
    sub = ensure_submission_access(db, user, sub_id)
    pg = next((p for p in sub.pages if p.page_no == page_no), None)
    if pg is None:
        raise HTTPException(404, "页面不存在")
    path = pg.processed_path if kind == "processed" and pg.processed_path else pg.original_path
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, no-store"})


@router.get("/{sub_id}/stitched", summary="多页拼接图")
def stitched(sub_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> FileResponse:
    ensure_submission_access(db, user, sub_id)
    p = submission_dir(sub_id) / "stitched.jpg"
    if not p.exists():
        raise HTTPException(404, "没有拼接图（单页作业）")
    return FileResponse(p, media_type="image/jpeg", headers={"Cache-Control": "private, no-store"})


@router.post("/{sub_id}/retry", response_model=SubmissionBrief, summary="重新识别/评分（教师，可指定 provider）")
def retry(sub_id: int, body: RetryIn, background: BackgroundTasks, user: User = Depends(require_roles("teacher", "admin")),
          db: Session = Depends(get_db)) -> SubmissionBrief:
    sub = ensure_submission_access(db, user, sub_id)
    ensure_teacher_of_submission(db, user, sub)
    if sub.status == "processing":
        raise HTTPException(409, "正在处理中")
    audit(db, user, "retry", f"submission:{sub.id}", **body.model_dump())
    db.commit()
    if get_settings().pipeline_sync:
        process_submission(sub.id, body.ocr_provider, body.grader_provider, body.regrade_only)
        db.refresh(sub)
    else:
        background.add_task(process_submission, sub.id, body.ocr_provider, body.grader_provider, body.regrade_only)
    return brief(sub, user)


@router.put("/{sub_id}/ocr-text", response_model=SubmissionDetail, summary="教师修正识别文字（可选重新评分）")
def fix_ocr_text(sub_id: int, body: OcrTextIn, background: BackgroundTasks,
                 user: User = Depends(require_roles("teacher", "admin")), db: Session = Depends(get_db)) -> SubmissionDetail:
    sub = ensure_submission_access(db, user, sub_id)
    ensure_teacher_of_submission(db, user, sub)
    old_len = len(sub.ocr_text or "")
    sub.ocr_text = body.text
    sub.ocr_text_edited = True
    audit(db, user, "fix_ocr_text", f"submission:{sub.id}", old_len=old_len, new_len=len(body.text))
    db.commit()
    if body.regrade:
        process_submission(sub.id, regrade_only=True)  # 只重新评分，速度快，同步执行
        db.expire_all()
        sub = db.get(Submission, sub_id)
    return detail(db, sub, user)


def _apply_review(rubric, base: GradingResult, body: ReviewIn, reference: dict) -> GradingResult:  # noqa: ANN001
    defs = {c.id: c for c in rubric.effective_criteria(reference)}
    given = {c.id: c for c in body.criteria}
    unknown = set(given) - set(defs)
    if unknown:
        raise HTTPException(400, f"量规中不存在的维度：{', '.join(sorted(unknown))}")
    old = {c.id: c for c in base.criteria}
    criteria = []
    for cid, c in defs.items():
        g = given.get(cid)
        prev = old.get(cid)
        score = g.score if g else (prev.score if prev else 0)
        if score > c.max + 1e-9:
            raise HTTPException(400, f"{c.name} 分数 {score:g} 超过满分 {c.max:g}")
        level = ""
        for lv in c.levels:
            if lv.min - 1e-9 <= score <= lv.max + 1e-9:
                level = lv.name
        criteria.append(CriterionScore(id=cid, name=c.name, score=score, max_score=c.max, level=level,
                                       comment=(g.comment if g and g.comment else (prev.comment if prev else ""))))
    ded_defs = {d.id: d for d in rubric.deductions}
    deductions = []
    for d in body.deductions:
        dd = ded_defs.get(d.id)
        if dd is None:
            raise HTTPException(400, f"量规中不存在的扣分项：{d.id}")
        if dd.max is not None and d.points > dd.max + 1e-9:
            raise HTTPException(400, f"扣分项 {dd.name} 最多扣 {dd.max:g} 分")
        deductions.append(DeductionItem(id=d.id, name=dd.name, points=d.points, reason=d.reason))
    mx = rubric.effective_max(reference)
    total = round(max(0.0, min(mx, sum(c.score for c in criteria) - sum(d.points for d in deductions))), 2)
    errors = base.errors if body.errors is None else body.errors
    return base.model_copy(update={
        "criteria": criteria, "deductions": deductions, "total_score": total, "band": rubric.band_of(total, mx),
        "errors": errors, "comment": base.comment if body.comment is None else body.comment,
        "needs_review": False, "review_reasons": [], "provider": "teacher",
    })


@router.post("/{sub_id}/review", response_model=SubmissionDetail, summary="教师复核：修改分项分/扣分/批注/评语并保存（留痕）")
def review(sub_id: int, body: ReviewIn, user: User = Depends(require_roles("teacher", "admin")),
           db: Session = Depends(get_db)) -> SubmissionDetail:
    sub = ensure_submission_access(db, user, sub_id)
    ensure_teacher_of_submission(db, user, sub)
    if sub.status not in ("ai_done", "reviewed") or not sub.final_result:
        raise HTTPException(409, "该提交还没有评分结果，无法复核")
    a = sub.assignment
    try:
        rubric = get_rubric(a.rubric_key, db)
    except RubricError as e:
        raise HTTPException(400, str(e)) from e
    before = GradingResult.model_validate(sub.final_result)
    after = _apply_review(rubric, before, body, a.reference or {})
    ai = GradingResult.model_validate(sub.ai_result) if sub.ai_result else None
    ai_crit = {c.id: c.score for c in ai.criteria} if ai else {}
    old_err = {(e.type, e.quote, e.message) for e in before.errors}
    new_err = {(e.type, e.quote, e.message) for e in after.errors}
    diff = {
        "total": {"ai": ai.total_score if ai else None, "before": before.total_score, "after": after.total_score},
        "criteria": [{"id": c.id, "name": c.name, "ai": ai_crit.get(c.id), "before": next(
            (x.score for x in before.criteria if x.id == c.id), None), "after": c.score} for c in after.criteria],
        "deductions": [d.model_dump() for d in after.deductions],
        "errors_added": len(new_err - old_err), "errors_removed": len(old_err - new_err),
        "comment_changed": before.comment != after.comment,
    }
    mx = after.max_score
    db.add(Review(submission_id=sub.id, reviewer_id=user.id, rubric_key=a.rubric_key,
                  ai_total=ai.total_score if ai else None, teacher_total=after.total_score, max_score=mx,
                  ai_band=ai.band if ai else "", teacher_band=after.band, before=before.model_dump(),
                  after=after.model_dump(), diff=diff, note=body.note))
    sub.final_result = after.model_dump()
    sub.final_total = after.total_score
    sub.needs_review = False
    sub.status = "reviewed"
    sub.reviewed_by = user.id
    sub.reviewed_at = utcnow()
    audit(db, user, "review", f"submission:{sub.id}", ai=diff["total"]["ai"], teacher=after.total_score)
    db.commit()
    return detail(db, sub, user)


@router.get("/{sub_id}/reviews", response_model=list[ReviewOut], summary="复核历史")
def review_history(sub_id: int, user: User = Depends(require_roles("teacher", "admin")),
                   db: Session = Depends(get_db)) -> list[ReviewOut]:
    sub = ensure_submission_access(db, user, sub_id)
    out = []
    for r in db.execute(select(Review).where(Review.submission_id == sub.id).order_by(Review.id)).scalars():
        o = ReviewOut.model_validate(r)
        u = db.get(User, r.reviewer_id)
        o.reviewer_name = u.display_name if u else ""
        out.append(o)
    return out


@router.delete("/{sub_id}", summary="删除提交及其图片（任课教师/管理员/该学生的家长）")
def delete_submission(sub_id: int, user: User = Depends(require_roles("teacher", "admin", "parent")),
                      db: Session = Depends(get_db)) -> dict:
    sub = ensure_submission_access(db, user, sub_id)
    if user.role == "teacher":
        ensure_teacher_of_submission(db, user, sub)
    d = submission_dir(sub.id)
    audit(db, user, "delete_submission", f"submission:{sub.id}", student=sub.student_id)
    db.delete(sub)
    db.commit()
    shutil.rmtree(d, ignore_errors=True)
    return {"ok": True}

