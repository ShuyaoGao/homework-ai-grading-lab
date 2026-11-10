"""登录、当前用户、改密码；管理员管理账号。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import audit, get_current_user, require_roles
from ..models import Student, User
from ..schemas import PasswordChange, TokenOut, UserCreate, UserOut, UserPatch
from ..security import create_access_token, hash_password, login_throttle, verify_password

router = APIRouter(prefix="/api", tags=["鉴权与账号"])


@router.post("/auth/login", response_model=TokenOut, summary="登录（OAuth2 表单：username / password）")
def login(request: Request, form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)) -> TokenOut:
    key = form.username.lower()
    if login_throttle.blocked(key):
        raise HTTPException(429, "登录失败次数过多，请 10 分钟后再试")
    user = db.execute(select(User).where(User.username == form.username)).scalar_one_or_none()
    if user is None or not user.is_active or not verify_password(form.password, user.password_hash):
        login_throttle.fail(key)
        raise HTTPException(401, "用户名或密码错误")
    login_throttle.reset(key)
    audit(db, user, "login", ip=request.client.host if request.client else "")
    db.commit()
    return TokenOut(access_token=create_access_token(user.id, user.role), user=UserOut.model_validate(user))


@router.get("/auth/me", response_model=UserOut, summary="当前用户")
def me(user: User = Depends(get_current_user)) -> User:
    return user


@router.post("/auth/change-password", summary="修改密码")
def change_password(body: PasswordChange, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    if not verify_password(body.old_password, user.password_hash):
        raise HTTPException(400, "原密码错误")
    user.password_hash = hash_password(body.new_password)
    audit(db, user, "change_password")
    db.commit()
    return {"ok": True}


@router.get("/users", response_model=list[UserOut], summary="账号列表（管理员；教师可查家长账号用于绑定）")
def list_users(role: str | None = None, user: User = Depends(require_roles("admin", "teacher")),
               db: Session = Depends(get_db)) -> list[User]:
    q = select(User).order_by(User.id)
    if user.role == "teacher":
        q = q.where(User.role == "parent")
    elif role:
        q = q.where(User.role == role)
    return list(db.execute(q).scalars())


@router.post("/users", response_model=UserOut, status_code=201, summary="创建账号（管理员；教师只能建家长/学生账号）")
def create_user(body: UserCreate, user: User = Depends(require_roles("admin", "teacher")),
                db: Session = Depends(get_db)) -> User:
    if user.role == "teacher" and body.role not in ("parent", "student"):
        raise HTTPException(403, "教师只能创建家长或学生账号")
    if db.execute(select(User).where(User.username == body.username)).scalar_one_or_none():
        raise HTTPException(409, "用户名已存在")
    if body.role == "student":
        if not body.student_id or db.get(Student, body.student_id) is None:
            raise HTTPException(400, "学生账号必须关联已有的学生档案 student_id")
    u = User(username=body.username, password_hash=hash_password(body.password), display_name=body.display_name,
             role=body.role, student_id=body.student_id if body.role == "student" else None)
    db.add(u)
    db.flush()
    audit(db, user, "create_user", f"user:{u.id}", role=body.role)
    db.commit()
    return u


@router.patch("/users/{user_id}", response_model=UserOut, summary="修改账号（管理员）")
def patch_user(user_id: int, body: UserPatch, user: User = Depends(require_roles("admin")),
               db: Session = Depends(get_db)) -> User:
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(404, "账号不存在")
    if body.display_name is not None:
        u.display_name = body.display_name
    if body.is_active is not None:
        u.is_active = body.is_active
    if body.password:
        u.password_hash = hash_password(body.password)
    audit(db, user, "patch_user", f"user:{u.id}")
    db.commit()
    return u
