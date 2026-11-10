"""流水线编排：保存上传 → 预处理 → OCR → 评分 → 落库。

合规闸门（choose_providers）：
- 管理员关闭云端（ALLOW_CLOUD_PROVIDERS=false）时，云端 provider 一律降级为本地；
- 学生的监护人没有勾选“允许云端 AI 处理（cloud_ai）”时，同样降级为本地 provider；
- 降级原因写进 ocr_meta / ai_result.warnings，界面上可见。
"""
from __future__ import annotations

import hashlib
import logging
import traceback
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import SessionLocal
from ..models import Assignment, ClassRoom, Consent, Student, Submission, SubmissionPage, utcnow
from .errors import ProviderError
from .grading.base import GradingContext
from .ocr.base import OCRContext, PageInput
from .preprocess import apply_edits, load_upload, preprocess, save_image, stitch_vertical
from .registry import get_grader, get_ocr
from .rubrics import get_rubric

log = logging.getLogger("edu_eval.pipeline")

SUBJECT_LANG = {"english": "en", "chinese": "zh", "math": "mixed", "paper": "zh"}


def submission_dir(sub_id: int) -> Path:
    return get_settings().upload_dir / f"sub_{sub_id:06d}"


def has_consent(db: Session, student_id: int, scope: str) -> bool:
    row = db.execute(
        select(Consent).where(Consent.student_id == student_id, Consent.scope == scope, Consent.revoked_at.is_(None))
    ).scalars().first()
    return row is not None


def save_pages(db: Session, sub: Submission, files: list[bytes], edits: list[dict | None] | None = None) -> list[str]:
    """保存上传图片（已去除 EXIF/GPS；如有手机端的旋转/裁剪编辑，在这里应用），返回警告。"""
    d = submission_dir(sub.id)
    d.mkdir(parents=True, exist_ok=True)
    warnings = []
    for i, data in enumerate(files, start=1):
        li = load_upload(data)
        edit = (edits or [None] * len(files))[i - 1] if edits and i - 1 < len(edits) else None
        img = apply_edits(li.image, edit)
        p = d / f"page_{i}.jpg"
        save_image(img, p, quality=92)
        db.add(SubmissionPage(submission_id=sub.id, page_no=i, original_path=str(p), width=img.width,
                              height=img.height, sha256=hashlib.sha256(data).hexdigest(),
                              embedded_text=li.embedded_text))
    sub.page_count = len(files)
    return warnings


def choose_providers(db: Session, student: Student, ocr_name: str | None, grader_name: str | None) -> tuple[str, str, list[str]]:
    s = get_settings()
    ocr_name = ocr_name or s.resolve_ocr_provider()
    grader_name = grader_name or s.resolve_grader_provider()
    notes: list[str] = []
    cloud_ok = s.allow_cloud_providers and has_consent(db, student.id, "cloud_ai")
    why = "管理员已关闭云端模型" if not s.allow_cloud_providers else "监护人未授权云端 AI 处理（cloud_ai）"

    def local_alt(kind: str) -> str:
        base, _, is_local = s.openai_compat_resolved(kind)
        if base and is_local:
            return "openai_compat"
        return "mock"

    o = get_ocr(ocr_name)
    if o.is_cloud and not cloud_ok:
        new = local_alt("ocr")
        notes.append(f"OCR 由 {ocr_name} 降级为 {new}：{why}")
        ocr_name = new
    g = get_grader(grader_name)
    if g.is_cloud and not cloud_ok:
        new = local_alt("grader")
        notes.append(f"评分由 {grader_name} 降级为 {new}：{why}")
        grader_name = new
    return ocr_name, grader_name, notes


def _grade_level(db: Session, a: Assignment) -> str:
    c = db.get(ClassRoom, a.class_id)
    return c.grade if c else ""


def run_preprocess_and_ocr(db: Session, sub: Submission, ocr_name: str, notes: list[str]) -> None:
    a = sub.assignment
    d = submission_dir(sub.id)
    processed, metas, inputs = [], [], []
    sub.stage = "preprocess"
    db.commit()
    from PIL import Image

    for pg in sub.pages:
        im = Image.open(pg.original_path)
        out, meta = preprocess(im)
        pp = d / f"page_{pg.page_no}_proc.jpg"
        save_image(out, pp)
        pg.processed_path = str(pp)
        processed.append(out)
        metas.append(meta.as_dict())
        inputs.append(PageInput(pg.page_no, pp, pg.embedded_text))
    if len(processed) > 1:
        save_image(stitch_vertical(processed), d / "stitched.jpg")
    sub.preprocess_meta = {"pages": metas, "stitched": len(processed) > 1}

    sub.stage = "ocr"
    db.commit()
    ocr = get_ocr(ocr_name)
    res = ocr.recognize(inputs, OCRContext(subject=a.subject, language_hint=SUBJECT_LANG.get(a.subject, "")))
    sub.ocr_provider = f"{res.provider}:{res.model}" if res.model else res.provider
    sub.ocr_text = res.text
    sub.ocr_confidence = res.confidence
    sub.ocr_text_edited = False
    warn = list(res.warnings) + [w for m in metas for w in m.get("warnings", [])]
    sub.ocr_meta = {"warnings": warn, "notes": notes, "illegible_count": res.illegible_count,
                    "pages": [{"page_no": p.page_no, "confidence": p.confidence, "chars": len(p.text)} for p in res.pages]}


def run_grading(db: Session, sub: Submission, grader_name: str, notes: list[str]) -> None:
    a = sub.assignment
    sub.stage = "grading"
    db.commit()
    rubric = get_rubric(a.rubric_key, db)
    grader = get_grader(grader_name)
    ctx = GradingContext(rubric=rubric, text=sub.ocr_text or "", prompt=a.prompt or "", reference=a.reference or {},
                         ocr_confidence=sub.ocr_confidence if not sub.ocr_text_edited else None,
                         grade_level=_grade_level(db, a))
    result = grader.grade(ctx)
    result.warnings = notes + result.warnings
    if sub.ocr_meta and sub.ocr_meta.get("warnings"):
        mock_warn = [w for w in sub.ocr_meta["warnings"] if "mock" in w]
        if mock_warn:
            result.review_reasons.append("识别结果不是真实识别（mock 示例文字）")
            result.needs_review = True
    data = result.model_dump()
    sub.grader_provider = f"{result.provider}:{result.model}" if result.model else result.provider
    sub.ai_result = data
    sub.ai_total = result.total_score
    sub.max_score = result.max_score
    if sub.status != "reviewed":
        sub.final_result = data
        sub.final_total = result.total_score
        sub.needs_review = result.needs_review


def process_submission(sub_id: int, ocr_name: str | None = None, grader_name: str | None = None,
                       regrade_only: bool = False, db: Session | None = None) -> None:
    own = db is None
    db = db or SessionLocal()
    sub = db.get(Submission, sub_id)
    if sub is None:
        return
    prev_status = sub.status
    try:
        sub.status = "processing" if prev_status != "reviewed" else "reviewed"
        sub.error = ""
        db.commit()
        student = db.get(Student, sub.student_id)
        o, g, notes = choose_providers(db, student, ocr_name, grader_name)
        if not regrade_only:
            run_preprocess_and_ocr(db, sub, o, notes)
        run_grading(db, sub, g, notes)
        sub.stage = "done"
        if prev_status != "reviewed":
            sub.status = "ai_done"
        sub.updated_at = utcnow()
        db.commit()
        log.info("submission %s processed: ocr=%s grader=%s total=%s", sub_id, sub.ocr_provider, sub.grader_provider,
                 sub.ai_total)
    except ProviderError as e:
        db.rollback()
        sub = db.get(Submission, sub_id)
        sub.status, sub.error = ("failed" if prev_status != "reviewed" else prev_status), f"模型调用失败：{e}"
        db.commit()
        log.warning("submission %s provider error: %s", sub_id, e)
    except Exception as e:  # noqa: BLE001
        db.rollback()
        sub = db.get(Submission, sub_id)
        sub.status, sub.error = ("failed" if prev_status != "reviewed" else prev_status), f"处理失败：{e}"
        db.commit()
        log.error("submission %s failed: %s\n%s", sub_id, e, traceback.format_exc())
    finally:
        if own:
            db.close()
