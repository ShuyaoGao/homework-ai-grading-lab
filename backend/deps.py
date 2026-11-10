"""依赖注入：当前用户、角色校验、数据访问范围（谁能看哪个学生）。"""
from __future__ import annotations

from collections.abc import Callable

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_db
from .models import Assignment, AuditLog, ClassRoom, GuardianLink, Student, Submission, User
from .security import decode_token

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    cred_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效，请重新登录",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = decode_token(token)
        uid = int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise cred_exc from None
    user = db.get(User, uid)
    if user is None or not user.is_active:
        raise cred_exc
    return user


def require_roles(*roles: str) -> Callable[[User], User]:
    def _dep(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status_code=403, detail="无权执行此操作")
        return user

    return _dep


# ---------------- 访问范围 ----------------

def accessible_student_ids(db: Session, user: User) -> set[int] | None:
    """返回用户可访问的学生 id 集合；None 表示全部（管理员）。"""
    if user.role == "admin":
        return None
    if user.role == "teacher":
        rows = db.execute(
            select(Student.id).join(ClassRoom, Student.class_id == ClassRoom.id).where(ClassRoom.teacher_id == user.id)
        ).scalars()
        return set(rows)
    if user.role == "parent":
        rows = db.execute(select(GuardianLink.student_id).where(GuardianLink.parent_user_id == user.id)).scalars()
        return set(rows)
    if user.role == "student":
        return {user.student_id} if user.student_id else set()
    return set()


def accessible_class_ids(db: Session, user: User) -> set[int] | None:
    if user.role == "admin":
        return None
    if user.role == "teacher":
        return set(db.execute(select(ClassRoom.id).where(ClassRoom.teacher_id == user.id)).scalars())
    sids = accessible_student_ids(db, user) or set()
    if not sids:
        return set()
    return set(db.execute(select(Student.class_id).where(Student.id.in_(sids))).scalars())


def ensure_student_access(db: Session, user: User, student_id: int) -> Student:
    st = db.get(Student, student_id)
    if st is None:
        raise HTTPException(404, "学生不存在")
    ids = accessible_student_ids(db, user)
    if ids is not None and student_id not in ids:
        raise HTTPException(403, "无权访问该学生的数据")
    return st


def ensure_class_access(db: Session, user: User, class_id: int, teacher_only: bool = False) -> ClassRoom:
    c = db.get(ClassRoom, class_id)
    if c is None:
        raise HTTPException(404, "班级不存在")
    if user.role == "admin":
        return c
    if teacher_only and not (user.role == "teacher" and c.teacher_id == user.id):
        raise HTTPException(403, "仅任课教师可操作")
    ids = accessible_class_ids(db, user)
    if ids is not None and class_id not in ids:
        raise HTTPException(403, "无权访问该班级")
    return c


def ensure_submission_access(db: Session, user: User, submission_id: int) -> Submission:
    sub = db.get(Submission, submission_id)
    if sub is None:
        raise HTTPException(404, "提交不存在")
    ensure_student_access(db, user, sub.student_id)
    return sub


def ensure_teacher_of_submission(db: Session, user: User, sub: Submission) -> None:
    if user.role == "admin":
        return
    a = db.get(Assignment, sub.assignment_id)
    c = db.get(ClassRoom, a.class_id) if a else None
    if user.role != "teacher" or c is None or c.teacher_id != user.id:
        raise HTTPException(403, "仅任课教师可复核")


def audit(db: Session, user: User | None, action: str, target: str = "", **detail) -> None:
    db.add(AuditLog(user_id=user.id if user else None, action=action, target=target, detail=detail))
