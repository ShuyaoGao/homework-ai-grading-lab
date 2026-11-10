"""本机一键演示专用接口（仅在 EDU_DEMO_MODE=1 时开放，否则一律 404）。

- GET  /api/demo/scenario      演示场景：班级、要演示的学生和作业、一份已批改好的作文、统计数
- GET  /api/demo/sample-photo  一张合成的“家长手机拍的作业照片”（PNG，内嵌标准文本，供 mock 识别）
- POST /api/demo/shutdown      关闭演示服务（只接受本机请求，且必须带 X-Demo-Action: shutdown 头）

这些接口不返回任何密码或 key。自动演示流程本身在前端（frontend/js/views/demo_auto.js）用普通业务接口完成。
"""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..deps import accessible_class_ids, accessible_student_ids, get_current_user, require_roles
from ..models import Assignment, ClassRoom, GuardianLink, Student, Submission, User
from ..demo.sample import photo_for

router = APIRouter(prefix="/api/demo", tags=["本机演示"])

DEMO_PARENT = "parent_zhang"  # 自动演示里“上传作业的家长”
ESSAY_SUBJECTS = ("chinese", "english")
LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def _require_demo() -> None:
    if not get_settings().demo_mode:
        raise HTTPException(404, "演示模式未开启")


def _latest_essay_assignment(db: Session, class_id: int) -> Assignment | None:
    for subject in ESSAY_SUBJECTS:
        a = db.execute(select(Assignment).where(Assignment.class_id == class_id, Assignment.subject == subject)
                       .order_by(Assignment.created_at.desc(), Assignment.id.desc())).scalars().first()
        if a is not None:
            return a
    return db.execute(select(Assignment).where(Assignment.class_id == class_id)
                      .order_by(Assignment.created_at.desc(), Assignment.id.desc())).scalars().first()


@router.get("/scenario", summary="演示场景（教师/管理员）", dependencies=[Depends(_require_demo)])
def scenario(user: User = Depends(require_roles("teacher", "admin")), db: Session = Depends(get_db)) -> dict:
    parent = db.execute(select(User).where(User.username == DEMO_PARENT)).scalar_one_or_none()
    if parent is None:
        raise HTTPException(404, "没有找到演示家长账号，请先写入演示数据（run.py --demo 会自动写入）")
    allowed = accessible_class_ids(db, user)
    child = None
    for link in db.execute(select(GuardianLink).where(GuardianLink.parent_user_id == parent.id)
                           .order_by(GuardianLink.id)).scalars():
        st = db.get(Student, link.student_id)
        if st is not None and (allowed is None or st.class_id in allowed):
            child = st
            break
    if child is None:
        raise HTTPException(404, "演示家长的孩子不在你任教的班级里")
    cls = db.get(ClassRoom, child.class_id)
    a = _latest_essay_assignment(db, cls.id)
    if a is None:
        raise HTTPException(404, "演示班级还没有作业")

    in_class = select(Submission).join(Assignment, Submission.assignment_id == Assignment.id) \
        .where(Assignment.class_id == cls.id)
    reviewed = None
    for subject in ESSAY_SUBJECTS:  # 优先找演示学生自己已批改好的作文
        reviewed = db.execute(in_class.where(Submission.status == "reviewed", Assignment.subject == subject,
                                             Submission.student_id == child.id)
                              .order_by(Submission.submitted_at.desc())).scalars().first()
        if reviewed is not None:
            break
    if reviewed is None:
        reviewed = db.execute(in_class.where(Submission.status == "reviewed")
                              .order_by(Submission.submitted_at.desc())).scalars().first()

    def count(*conds) -> int:  # noqa: ANN002
        q = select(func.count(Submission.id)).join(Assignment, Submission.assignment_id == Assignment.id) \
            .where(Assignment.class_id == cls.id, *conds)
        return int(db.execute(q).scalar_one())

    n_students = int(db.execute(select(func.count(Student.id)).where(Student.class_id == cls.id)).scalar_one())
    out = {
        "class_id": cls.id, "class_name": cls.name,
        "student_id": child.id, "student_name": child.name, "parent_username": DEMO_PARENT,
        "assignment_id": a.id, "assignment_title": a.title, "subject": a.subject, "rubric_key": a.rubric_key,
        "reviewed_submission": None,
        "counts": {"students": n_students, "submissions": count(), "reviewed": count(Submission.status == "reviewed"),
                   "pending": count(Submission.status == "ai_done")},
        "lan_url": os.environ.get("EDU_DEMO_LAN_URL", ""),
    }
    if reviewed is not None:
        r = reviewed.final_result or {}
        out["reviewed_submission"] = {"id": reviewed.id, "title": reviewed.assignment.title,
                                      "student_name": reviewed.student.name, "total": r.get("total_score"),
                                      "max_score": r.get("max_score"), "band": r.get("band", "")}
    return out


@router.get("/sample-photo", summary="演示用作业照片（合成的仿手写图，内嵌标准文本）", dependencies=[Depends(_require_demo)])
def sample_photo(assignment_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> FileResponse:
    a = db.get(Assignment, assignment_id)
    if a is None:
        raise HTTPException(404, "作业不存在")
    ids = accessible_student_ids(db, user)
    if ids is not None:
        classes = set(db.execute(select(Student.class_id).where(Student.id.in_(ids))).scalars())
        if a.class_id not in classes:
            raise HTTPException(403, "无权访问该作业")
    path = photo_for(a, get_settings().data_dir / "demo_samples")
    return FileResponse(path, media_type="image/png", filename="demo_homework.png",
                        headers={"Cache-Control": "private, no-store"})


@router.post("/shutdown", summary="关闭本机演示服务（仅本机，需 X-Demo-Action: shutdown）",
             dependencies=[Depends(_require_demo)])
def shutdown(request: Request, x_demo_action: str = Header("", alias="X-Demo-Action")) -> dict:
    host = request.client.host if request.client else ""
    if host not in LOOPBACK:
        raise HTTPException(403, "只能在运行演示的这台电脑上关闭服务")
    if x_demo_action != "shutdown":  # 自定义头会触发浏览器跨域预检，别的网页无法替你发这个请求
        raise HTTPException(400, "缺少 X-Demo-Action: shutdown 请求头")
    from ..demo import lifecycle

    lifecycle.request_shutdown()
    return {"ok": True, "message": "演示服务正在关闭"}
