"""演示种子数据：一个班（初二(3)班）、8 名学生、3 位家长、7 次作业，每份作业都生成“手写照片”并走真实流水线；
较早的作业模拟教师复核（有改分），最近的作业留在待复核队列。

用法：python -m backend.seed [--reset] [--no-images]
所有姓名均为虚构。演示账号密码见 README，部署前务必修改或删除。
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import random
import shutil

from sqlalchemy import select

from .config import get_settings
from .db import SessionLocal, init_db, reset_engine
from .demo import corpus
from .demo.handwriting import make_homework_photo, style_for
from .models import Assignment, ClassRoom, Consent, GuardianLink, Review, Student, Submission, User, utcnow
from .pipeline.grading.schema import GradingResult
from .pipeline.rubrics import get_rubric
from .pipeline.runner import process_submission, save_pages
from .security import hash_password

log = logging.getLogger("edu_eval.seed")

DEMO_USERS = [
    ("admin", "admin12345", "系统管理员", "admin"),
    ("teacher_wang", "teacher12345", "王老师", "teacher"),
    ("parent_zhang", "parent12345", "张小明家长", "parent"),
    ("parent_li", "parent12345", "李华家长", "parent"),
    ("parent_chen", "parent12345", "陈雨家长", "parent"),
]
STUDENTS = [  # 姓名, 学号, 出生年, 能力, 能力随时间变化
    ("张小明", "2025301", 2012, 0.62, +0.04),
    ("李华", "2025302", 2012, 0.86, +0.01),
    ("陈雨", "2025303", 2013, 0.45, +0.05),
    ("王芳", "2025304", 2012, 0.74, 0.0),
    ("刘洋", "2025305", 2012, 0.33, +0.02),
    ("赵子涵", "2025306", 2013, 0.92, 0.0),
    ("孙浩", "2025307", 2012, 0.55, -0.05),
    ("周婷", "2025308", 2013, 0.68, +0.03),
    ("吴磊", "2025309", 2012, 0.26, +0.03),
    ("郑佳怡", "2025310", 2013, 0.80, +0.02),
    ("黄子轩", "2025311", 2012, 0.40, -0.02),
    ("林晓", "2025312", 2013, 0.57, +0.04),
]
ASSIGNMENTS = [  # (key, 类型, 量规, 日期[UTC，即北京时间 16:00], 是否已复核)
    ("weekend", "english", "english_essay_zhongkao", dt.datetime(2026, 9, 8, 8), True),
    ("equation", "math", "short_answer_math", dt.datetime(2026, 9, 12, 8), True),
    ("grownup", "chinese", "chinese_essay_gaokao", dt.datetime(2026, 9, 15, 8), True),
    ("invite", "english", "english_essay_zhongkao", dt.datetime(2026, 9, 22, 8), True),
    ("area", "math", "short_answer_math", dt.datetime(2026, 9, 26, 8), True),
    ("paper", "paper", "paper_essay", dt.datetime(2026, 9, 29, 8), False),
    ("hero", "chinese", "chinese_essay_gaokao", dt.datetime(2026, 10, 5, 8), False),
]


def _assignment_spec(key: str) -> tuple[str, str, dict, str]:
    """返回 (标题, 题目要求, reference, 语言)。"""
    if key in corpus.EN_PROMPTS:
        p = corpus.EN_PROMPTS[key]
        return p["title"], p["prompt"], {"key_points": p["key_points"], "target_words": [80, 100]}, "en"
    if key in corpus.ZH_PROMPTS:
        p = corpus.ZH_PROMPTS[key]
        return p["title"], p["prompt"], {"keywords": p["keywords"], "min_chars": 450}, "zh"
    if key in corpus.MATH_SETS:
        m = corpus.MATH_SETS[key]
        return m["title"], m["prompt"], corpus.math_reference(key), "zh"
    return corpus.PAPER["title"], corpus.PAPER["prompt"], {"min_chars": 500}, "zh"


def _gen_text(key: str, ability: float, seed: int) -> str:
    if key in corpus.EN_PROMPTS:
        return corpus.gen_english(key, ability, seed)
    if key in corpus.ZH_PROMPTS:
        return corpus.gen_chinese(key, ability, seed)
    if key in corpus.MATH_SETS:
        return corpus.gen_math(key, ability, seed)
    return corpus.gen_paper(ability, seed)


def simulate_teacher(result: GradingResult, rubric, ability: float, rng: random.Random) -> list[dict]:  # noqa: ANN001
    """模拟教师复核：教师对规则评分的修正带有系统性（对好文章更宽、对差文章更严）+ 随机波动。"""
    out = []
    objective = rubric.kind == "short_answer"
    for c in result.criteria:
        if objective:  # 客观题：教师大多认可规则判分，偶尔给/扣过程分
            delta = rng.choice([0] * 8 + [0.5, -0.5])
        else:
            delta = rng.choice([0, 0, 0, 0.5, -0.5, 1, -1])
            delta += (ability - 0.6) * c.max_score * 0.15
        step = 0.5 if c.max_score <= 20 else 1
        s = round(max(0, min(c.max_score, c.score + delta)) / step) * step
        out.append({"id": c.id, "score": s, "comment": c.comment})
    return out


def seed(reset: bool = False, images: bool = True, quiet: bool = False) -> dict:
    s = get_settings()
    if reset:
        reset_engine()
        db_path = s.database_url.replace("sqlite:///", "")
        for suffix in ("", "-wal", "-shm"):
            p = __import__("pathlib").Path(db_path + suffix)
            if p.exists():
                p.unlink()
        shutil.rmtree(s.upload_dir, ignore_errors=True)
        s.upload_dir.mkdir(parents=True, exist_ok=True)
    init_db()
    db = SessionLocal()
    if db.execute(select(User).where(User.username == "teacher_wang")).scalar_one_or_none():
        db.close()
        return {"skipped": True, "reason": "演示数据已存在（使用 --reset 重建）"}

    users = {}
    for uname, pw, name, role in DEMO_USERS:
        u = User(username=uname, password_hash=hash_password(pw), display_name=name, role=role)
        db.add(u)
        users[uname] = u
    db.flush()
    c = ClassRoom(name="初二(3)班", grade="初中二年级", school_year="2026-2027", teacher_id=users["teacher_wang"].id)
    db.add(c)
    db.flush()
    studs = []
    for name, no, by, ab, drift in STUDENTS:
        st = Student(class_id=c.id, name=name, student_no=no, birth_year=by)
        db.add(st)
        studs.append((st, ab, drift))
    db.flush()
    # 学生账号（张小明）
    db.add(User(username="student_zxm", password_hash=hash_password("student12345"), display_name="张小明",
                role="student", student_id=studs[0][0].id))
    # 家长绑定 + 同意：刘洋（studs[4]）的家长未签署同意 → 演示合规闸门
    for parent, idx in (("parent_zhang", 0), ("parent_li", 1), ("parent_chen", 2)):
        db.add(GuardianLink(parent_user_id=users[parent].id, student_id=studs[idx][0].id, relation="母亲"))
    for i, (st, _, _) in enumerate(studs):
        if i == 4:
            continue
        guardian = users["parent_zhang"].id if i == 0 else users["parent_li"].id if i == 1 else users["parent_chen"].id
        db.add(Consent(student_id=st.id, guardian_user_id=guardian, scope="basic"))
        if i in (1, 5):  # 两位家长额外同意云端 AI 处理
            db.add(Consent(student_id=st.id, guardian_user_id=guardian, scope="cloud_ai"))
    db.commit()

    teacher = users["teacher_wang"]
    rng = random.Random(2026)
    n_sub = n_rev = 0
    img_root = s.data_dir / "seed_images"
    for ai, (key, subject, rkey, when, reviewed) in enumerate(ASSIGNMENTS):
        title, prompt, ref, lang = _assignment_spec(key)
        a = Assignment(class_id=c.id, title=title, subject=subject, rubric_key=rkey, prompt=prompt, reference=ref,
                       due_date=when + dt.timedelta(days=2), created_by=teacher.id, created_at=when - dt.timedelta(days=1))
        db.add(a)
        db.flush()
        rubric = get_rubric(rkey, db)
        for si, (st, ab, drift) in enumerate(studs):
            if si == 4:  # 未获同意的学生不能上传
                continue
            if (ai + si) % 11 == 10:  # 偶尔缺交
                continue
            ability = max(0.1, min(0.98, ab + drift * ai + rng.uniform(-0.05, 0.05)))
            seed_no = 1000 * ai + si
            text = _gen_text(key, ability, seed_no)
            sub = Submission(assignment_id=a.id, student_id=st.id, uploaded_by=teacher.id, status="queued",
                             stage="uploaded", submitted_at=when + dt.timedelta(minutes=7 * si), is_demo=True)
            db.add(sub)
            db.flush()
            if images:
                # 同一个学生每次作业笔迹一致（按学生选字体/倾斜/墨色），拍照角度与光线每次不同
                paper = "squared" if subject == "math" else ("lined" if lang == "en" or subject == "paper" else "grid")
                style = style_for(si, lang, messiness=1.25 - ability * 0.7, paper=paper)
                p = make_homework_photo(text, lang, img_root / f"a{ai}_s{si}", seed=seed_no, style=style, out_width=1100)
                save_pages(db, sub, [p.read_bytes()])
            else:
                sub.ocr_text = text
            db.commit()
            if images:
                process_submission(sub.id, "mock", "mock", db=db)
            else:
                sub.ocr_confidence = 0.95
                process_submission(sub.id, "mock", "mock", regrade_only=True, db=db)
            db.refresh(sub)
            n_sub += 1
            if reviewed and sub.status == "ai_done":
                res = GradingResult.model_validate(sub.ai_result)
                crit = simulate_teacher(res, rubric, ability, rng)
                _review(db, sub, teacher, rubric, crit, res)
                n_rev += 1
            sub.submitted_at = when + dt.timedelta(minutes=7 * si)
            db.commit()
            if not quiet:
                log.info("seed %s / %s -> %s %s", title, st.name, sub.status, sub.final_total)
    shutil.rmtree(img_root, ignore_errors=True)
    db.close()
    return {"submissions": n_sub, "reviewed": n_rev, "students": len(studs), "assignments": len(ASSIGNMENTS)}


def _review(db, sub: Submission, teacher: User, rubric, crit: list[dict], ai: GradingResult) -> None:  # noqa: ANN001
    from .routers.submissions import _apply_review
    from .schemas import ReviewIn

    body = ReviewIn(criteria=crit, deductions=ai.deductions, note="演示数据：模拟教师复核")
    after = _apply_review(rubric, ai, body, sub.assignment.reference or {})
    db.add(Review(submission_id=sub.id, reviewer_id=teacher.id, rubric_key=sub.assignment.rubric_key,
                  ai_total=ai.total_score, teacher_total=after.total_score, max_score=after.max_score, ai_band=ai.band,
                  teacher_band=after.band, before=ai.model_dump(), after=after.model_dump(),
                  diff={"total": {"ai": ai.total_score, "after": after.total_score}}, note=body.note,
                  created_at=sub.submitted_at + dt.timedelta(days=1)))
    sub.final_result = after.model_dump()
    sub.final_total = after.total_score
    sub.needs_review = False
    sub.status = "reviewed"
    sub.reviewed_by = teacher.id
    sub.reviewed_at = utcnow()


def main() -> None:
    ap = argparse.ArgumentParser(description="写入演示种子数据")
    ap.add_argument("--reset", action="store_true", help="删除现有数据库与上传文件后重建")
    ap.add_argument("--no-images", action="store_true", help="不生成图片（更快，结果页无原图）")
    args = ap.parse_args()
    print(seed(reset=args.reset, images=not args.no_images))


if __name__ == "__main__":
    main()
