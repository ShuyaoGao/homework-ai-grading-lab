"""班级、学生、监护人绑定、监护人同意、学生画像、班级看板。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..analytics.profile import class_dashboard, student_profile
from ..config import get_settings
from ..db import get_db
from ..deps import (
    accessible_class_ids,
    accessible_student_ids,
    audit,
    ensure_class_access,
    ensure_student_access,
    get_current_user,
    require_roles,
)
from ..models import ClassRoom, Consent, GuardianLink, Student, Submission, User, utcnow
from ..schemas import ClassCreate, ClassOut, ConsentIn, ConsentOut, GuardianLinkIn, StudentCreate, StudentOut

router = APIRouter(prefix="/api", tags=["班级与学生"])


def _consent_map(db: Session, student_id: int) -> dict[str, bool]:
    rows = db.execute(select(Consent).where(Consent.student_id == student_id, Consent.revoked_at.is_(None))).scalars()
    m = {"basic": False, "cloud_ai": False}
    for r in rows:
        m[r.scope] = True
    return m


def student_out(db: Session, st: Student) -> StudentOut:
    o = StudentOut.model_validate(st)
    o.is_minor_under_14 = st.is_minor_under(get_settings().minor_age)
    o.consents = _consent_map(db, st.id)
    return o


@router.get("/classes", response_model=list[ClassOut], summary="可访问的班级")
def list_classes(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[ClassOut]:
    ids = accessible_class_ids(db, user)
    q = select(ClassRoom).order_by(ClassRoom.id)
    if ids is not None:
        q = q.where(ClassRoom.id.in_(ids))
    out = []
    for c in db.execute(q).scalars():
        o = ClassOut.model_validate(c)
        o.student_count = db.scalar(select(func.count()).select_from(Student).where(Student.class_id == c.id)) or 0
        out.append(o)
    return out


@router.post("/classes", response_model=ClassOut, status_code=201, summary="创建班级（教师/管理员）")
def create_class(body: ClassCreate, user: User = Depends(require_roles("teacher", "admin")),
                 db: Session = Depends(get_db)) -> ClassRoom:
    tid = user.id if user.role == "teacher" else (body.teacher_id or user.id)
    c = ClassRoom(name=body.name, grade=body.grade, school_year=body.school_year, teacher_id=tid)
    db.add(c)
    db.flush()
    audit(db, user, "create_class", f"class:{c.id}")
    db.commit()
    return c


@router.get("/classes/{class_id}/students", response_model=list[StudentOut], summary="班级学生（家长/学生只能看到自己可访问的）")
def class_students(class_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[StudentOut]:
    ensure_class_access(db, user, class_id)
    sids = accessible_student_ids(db, user)
    q = select(Student).where(Student.class_id == class_id).order_by(Student.student_no, Student.id)
    rows = [s for s in db.execute(q).scalars() if sids is None or s.id in sids]
    return [student_out(db, s) for s in rows]


@router.post("/classes/{class_id}/students", response_model=StudentOut, status_code=201, summary="添加学生（任课教师）")
def add_student(class_id: int, body: StudentCreate, user: User = Depends(require_roles("teacher", "admin")),
                db: Session = Depends(get_db)) -> StudentOut:
    ensure_class_access(db, user, class_id, teacher_only=True)
    st = Student(class_id=class_id, name=body.name, student_no=body.student_no, birth_year=body.birth_year)
    db.add(st)
    db.flush()
    audit(db, user, "add_student", f"student:{st.id}")
    db.commit()
    return student_out(db, st)


@router.get("/classes/{class_id}/dashboard", summary="班级看板：分数分布、共性问题、AI 与教师一致性")
def dashboard(class_id: int, user: User = Depends(require_roles("teacher", "admin")), db: Session = Depends(get_db)) -> dict:
    ensure_class_access(db, user, class_id, teacher_only=True)
    return class_dashboard(db, class_id)


@router.get("/students", response_model=list[StudentOut], summary="可访问的学生（家长=自己的孩子）")
def list_students(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[StudentOut]:
    ids = accessible_student_ids(db, user)
    q = select(Student).order_by(Student.class_id, Student.student_no)
    if ids is not None:
        q = q.where(Student.id.in_(ids))
    return [student_out(db, s) for s in db.execute(q).scalars()]


@router.get("/students/{student_id}", response_model=StudentOut, summary="学生信息")
def get_student(student_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> StudentOut:
    return student_out(db, ensure_student_access(db, user, student_id))


@router.get("/students/{student_id}/profile", summary="学生画像")
def get_profile(student_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    ensure_student_access(db, user, student_id)
    audit(db, user, "view_profile", f"student:{student_id}")
    db.commit()
    return student_profile(db, student_id)


@router.post("/students/{student_id}/guardians", summary="绑定家长账号（任课教师/管理员）")
def link_guardian(student_id: int, body: GuardianLinkIn, user: User = Depends(require_roles("teacher", "admin")),
                  db: Session = Depends(get_db)) -> dict:
    st = ensure_student_access(db, user, student_id)
    ensure_class_access(db, user, st.class_id, teacher_only=True)
    p = db.get(User, body.parent_user_id)
    if p is None or p.role != "parent":
        raise HTTPException(400, "parent_user_id 必须是家长账号")
    if not db.execute(select(GuardianLink).where(GuardianLink.parent_user_id == p.id,
                                                 GuardianLink.student_id == student_id)).scalar_one_or_none():
        db.add(GuardianLink(parent_user_id=p.id, student_id=student_id, relation=body.relation))
    audit(db, user, "link_guardian", f"student:{student_id}", parent=p.id)
    db.commit()
    return {"ok": True}


@router.get("/students/{student_id}/consents", response_model=list[ConsentOut], summary="监护人同意记录")
def list_consents(student_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[Consent]:
    ensure_student_access(db, user, student_id)
    return list(db.execute(select(Consent).where(Consent.student_id == student_id).order_by(Consent.id)).scalars())


@router.post("/students/{student_id}/consents", summary="监护人授予/撤回同意（仅绑定的家长）")
def set_consent(student_id: int, body: ConsentIn, user: User = Depends(require_roles("parent")),
                db: Session = Depends(get_db)) -> dict:
    ensure_student_access(db, user, student_id)
    active = list(db.execute(select(Consent).where(Consent.student_id == student_id, Consent.scope == body.scope,
                                                   Consent.revoked_at.is_(None))).scalars())
    if body.granted and not active:
        db.add(Consent(student_id=student_id, guardian_user_id=user.id, scope=body.scope,
                       policy_version=body.policy_version))
    if not body.granted:
        for c in active:
            c.revoked_at = utcnow()
        if body.scope == "basic":  # 撤回基本同意时，云端同意一并撤回
            for c in db.execute(select(Consent).where(Consent.student_id == student_id, Consent.scope == "cloud_ai",
                                                      Consent.revoked_at.is_(None))).scalars():
                c.revoked_at = utcnow()
    audit(db, user, "consent", f"student:{student_id}", scope=body.scope, granted=body.granted)
    db.commit()
    return {"ok": True, "consents": _consent_map(db, student_id)}


@router.get("/students/{student_id}/export", summary="导出该学生的全部数据（个人信息可携带/查阅权）")
def export_student(student_id: int, user: User = Depends(require_roles("parent", "admin", "teacher")),
                   db: Session = Depends(get_db)) -> dict:
    st = ensure_student_access(db, user, student_id)
    subs = db.execute(select(Submission).where(Submission.student_id == student_id)).scalars()
    audit(db, user, "export_student", f"student:{student_id}")
    db.commit()
    return {
        "student": student_out(db, st).model_dump(),
        "consents": [ConsentOut.model_validate(c).model_dump(mode="json") for c in
                     db.execute(select(Consent).where(Consent.student_id == student_id)).scalars()],
        "submissions": [{"id": s.id, "assignment": s.assignment.title, "submitted_at": s.submitted_at.isoformat(),
                         "status": s.status, "ocr_text": s.ocr_text, "final_result": s.final_result} for s in subs],
        "profile": student_profile(db, student_id),
    }
