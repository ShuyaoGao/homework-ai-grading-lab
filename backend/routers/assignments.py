"""作业与量规。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import accessible_class_ids, audit, ensure_class_access, get_current_user, require_roles
from ..models import Assignment, User
from ..models import Rubric as RubricRow
from ..pipeline.rubrics import RubricError, builtin_rubrics, builtin_yaml, get_rubric, parse_rubric_yaml
from ..schemas import AssignmentCreate, AssignmentOut, RubricBrief, RubricIn

router = APIRouter(prefix="/api", tags=["作业与量规"])


def assignment_out(db: Session, a: Assignment) -> AssignmentOut:
    o = AssignmentOut.model_validate(a)
    try:
        r = get_rubric(a.rubric_key, db)
        o.max_score = r.effective_max(a.reference)
        o.rubric_name = r.name
    except RubricError:
        o.rubric_name = f"（量规 {a.rubric_key} 不存在）"
    return o


@router.get("/assignments", response_model=list[AssignmentOut], summary="作业列表")
def list_assignments(class_id: int | None = None, user: User = Depends(get_current_user),
                     db: Session = Depends(get_db)) -> list[AssignmentOut]:
    ids = accessible_class_ids(db, user)
    q = select(Assignment).order_by(Assignment.created_at.desc())
    if class_id is not None:
        ensure_class_access(db, user, class_id)
        q = q.where(Assignment.class_id == class_id)
    elif ids is not None:
        q = q.where(Assignment.class_id.in_(ids))
    return [assignment_out(db, a) for a in db.execute(q).scalars()]


@router.post("/assignments", response_model=AssignmentOut, status_code=201, summary="布置作业（任课教师）")
def create_assignment(body: AssignmentCreate, user: User = Depends(require_roles("teacher", "admin")),
                      db: Session = Depends(get_db)) -> AssignmentOut:
    ensure_class_access(db, user, body.class_id, teacher_only=True)
    try:
        r = get_rubric(body.rubric_key, db)
    except RubricError as e:
        raise HTTPException(400, str(e)) from e
    if r.kind == "short_answer" and not (body.reference.get("questions")):
        raise HTTPException(400, "简答/数学量规需要在 reference.questions 中填写每题的参考答案与分值")
    a = Assignment(class_id=body.class_id, title=body.title, subject=body.subject, rubric_key=body.rubric_key,
                   prompt=body.prompt, reference=body.reference, due_date=body.due_date, created_by=user.id)
    db.add(a)
    db.flush()
    audit(db, user, "create_assignment", f"assignment:{a.id}")
    db.commit()
    return assignment_out(db, a)


@router.get("/assignments/{assignment_id}", response_model=AssignmentOut, summary="作业详情")
def get_assignment(assignment_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> AssignmentOut:
    a = db.get(Assignment, assignment_id)
    if a is None:
        raise HTTPException(404, "作业不存在")
    ensure_class_access(db, user, a.class_id)
    return assignment_out(db, a)


# ---------------- 量规 ----------------

@router.get("/rubrics", response_model=list[RubricBrief], summary="量规列表（内置 + 自定义）")
def list_rubrics(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[RubricBrief]:
    out = [RubricBrief(key=r.key, name=r.name, subject=r.subject, kind=r.kind, max_score=r.max_score, builtin=True,
                       version=r.version) for r in builtin_rubrics().values()]
    for row in db.execute(select(RubricRow).order_by(RubricRow.id)).scalars():
        try:
            r = parse_rubric_yaml(row.yaml_text)
        except RubricError:
            continue
        out.append(RubricBrief(key=r.key, name=r.name, subject=r.subject, kind=r.kind, max_score=r.max_score,
                               builtin=False, version=r.version))
    return out


@router.get("/rubrics/{key}", summary="量规详情（含 YAML 原文与渲染给模型的提示词）")
def get_rubric_detail(key: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    try:
        r = get_rubric(key, db)
    except RubricError as e:
        raise HTTPException(404, str(e)) from e
    text = builtin_yaml(key)
    builtin = text is not None
    if text is None:
        row = db.execute(select(RubricRow).where(RubricRow.key == key)).scalar_one()
        text = row.yaml_text
    return {"rubric": r.model_dump(), "yaml_text": text, "builtin": builtin, "prompt_preview": r.render_for_prompt()}


@router.post("/rubrics/validate", summary="校验量规 YAML（不保存）")
def validate_rubric(body: RubricIn, user: User = Depends(require_roles("teacher", "admin"))) -> dict:
    try:
        r = parse_rubric_yaml(body.yaml_text)
    except RubricError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "rubric": r.model_dump(), "prompt_preview": r.render_for_prompt()}


@router.post("/rubrics", status_code=201, summary="保存自定义量规（教师/管理员）；同 key 的自定义量规会被更新")
def save_rubric(body: RubricIn, user: User = Depends(require_roles("teacher", "admin")), db: Session = Depends(get_db)) -> dict:
    try:
        r = parse_rubric_yaml(body.yaml_text)
    except RubricError as e:
        raise HTTPException(400, str(e)) from e
    if r.key in builtin_rubrics():
        raise HTTPException(409, "不能覆盖内置量规，请修改 key（例如加上 _custom 后缀）")
    row = db.execute(select(RubricRow).where(RubricRow.key == r.key)).scalar_one_or_none()
    if row is None:
        row = RubricRow(key=r.key, name=r.name, yaml_text=body.yaml_text, created_by=user.id)
        db.add(row)
    else:
        if row.created_by != user.id and user.role != "admin":
            raise HTTPException(403, "只能修改自己创建的量规")
        row.name, row.yaml_text = r.name, body.yaml_text
    audit(db, user, "save_rubric", f"rubric:{r.key}")
    db.commit()
    return {"ok": True, "key": r.key}
