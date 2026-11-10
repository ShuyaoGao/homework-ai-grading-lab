"""测试环境：临时数据目录、同步流水线、清除所有模型 key（保证只用 mock，不联网、不花钱）。"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="edu_eval_test_")
os.environ["EDU_DATA_DIR"] = _TMP
os.environ["EDU_PIPELINE_SYNC"] = "1"
os.environ["OCR_PROVIDER"] = "auto"
os.environ["GRADER_PROVIDER"] = "auto"
for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY", "DASHSCOPE_API_KEY", "OPENAI_COMPAT_BASE_URL"):
    os.environ.pop(k, None)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend.db import SessionLocal, init_db  # noqa: E402
from backend.demo.handwriting import make_homework_photo  # noqa: E402
from backend.main import app  # noqa: E402
from backend.models import Assignment, ClassRoom, Consent, GuardianLink, Student, User  # noqa: E402
from backend.security import hash_password  # noqa: E402

PW = "password123"


@pytest.fixture(scope="session")
def client() -> TestClient:
    init_db()
    return TestClient(app)


@pytest.fixture(scope="session")
def world(client) -> dict:  # noqa: ANN001
    """一套最小的测试数据：管理员、两位教师、两位家长、两个班、四名学生、三份作业。"""
    db = SessionLocal()
    u = {}
    for name, role in [("t_admin", "admin"), ("t_teacher", "teacher"), ("t_teacher2", "teacher"),
                       ("t_parent", "parent"), ("t_parent2", "parent")]:
        u[name] = User(username=name, password_hash=hash_password(PW), display_name=name, role=role)
        db.add(u[name])
    db.flush()
    c1 = ClassRoom(name="测试一班", grade="初二", teacher_id=u["t_teacher"].id)
    c2 = ClassRoom(name="测试二班", grade="初二", teacher_id=u["t_teacher2"].id)
    db.add_all([c1, c2])
    db.flush()
    s_ok = Student(class_id=c1.id, name="甲", student_no="1", birth_year=2013)  # 已同意 basic
    s_cloud = Student(class_id=c1.id, name="乙", student_no="2", birth_year=2013)  # basic + cloud_ai
    s_none = Student(class_id=c1.id, name="丙", student_no="3", birth_year=2013)  # 未同意
    s_other = Student(class_id=c2.id, name="丁", student_no="4", birth_year=2013)  # 另一个班
    db.add_all([s_ok, s_cloud, s_none, s_other])
    db.flush()
    db.add(GuardianLink(parent_user_id=u["t_parent"].id, student_id=s_ok.id))
    db.add(GuardianLink(parent_user_id=u["t_parent"].id, student_id=s_none.id))
    db.add(GuardianLink(parent_user_id=u["t_parent2"].id, student_id=s_other.id))
    db.add(Consent(student_id=s_ok.id, guardian_user_id=u["t_parent"].id, scope="basic"))
    db.add(Consent(student_id=s_cloud.id, guardian_user_id=u["t_parent"].id, scope="basic"))
    db.add(Consent(student_id=s_cloud.id, guardian_user_id=u["t_parent"].id, scope="cloud_ai"))
    db.add(Consent(student_id=s_other.id, guardian_user_id=u["t_parent2"].id, scope="basic"))
    stu_user = User(username="t_student", password_hash=hash_password(PW), display_name="甲", role="student",
                    student_id=None)
    db.add(stu_user)
    db.flush()
    stu_user.student_id = s_ok.id
    en = Assignment(class_id=c1.id, title="英语作文", subject="english", rubric_key="english_essay_zhongkao",
                    prompt="Write about your weekend", reference={"key_points": [["weekend"], ["friends", "family"]],
                                                                 "target_words": [60, 100]}, created_by=u["t_teacher"].id)
    math = Assignment(class_id=c1.id, title="数学", subject="math", rubric_key="short_answer_math", prompt="解方程",
                      reference={"questions": [{"id": 1, "answer": "x=4", "points": 4, "knowledge_point": "方程",
                                                "steps": ["2x=8"]},
                                               {"id": 2, "answer": "12", "points": 6, "knowledge_point": "面积"}]},
                      created_by=u["t_teacher"].id)
    other = Assignment(class_id=c2.id, title="二班作文", subject="english", rubric_key="english_essay_zhongkao",
                       prompt="x", reference={}, created_by=u["t_teacher2"].id)
    db.add_all([en, math, other])
    db.commit()
    w = {"users": {k: v.id for k, v in u.items()}, "class1": c1.id, "class2": c2.id, "s_ok": s_ok.id,
         "s_cloud": s_cloud.id, "s_none": s_none.id, "s_other": s_other.id, "a_en": en.id, "a_math": math.id,
         "a_other": other.id}
    db.close()
    return w


def token(client: TestClient, username: str, password: str = PW) -> dict:
    r = client.post("/api/auth/login", data={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


EN_TEXT = ("Last weekend I went to the library with my friends. He like reading very much. "
           "We read a interesting book becouse it was about science. Then we played basketball together. "
           "I felt very happy. In the evening I finished my homework and helped my mother.")
MATH_TEXT = "1. 2x+3=11\n   2x=8\n   x=4\n2. 3×4=10"


@pytest.fixture(scope="session")
def en_png(tmp_path_factory) -> bytes:  # noqa: ANN001
    p = make_homework_photo(EN_TEXT, "en", tmp_path_factory.mktemp("img") / "en", seed=1, angle=4, out_width=900)
    return p.read_bytes()


@pytest.fixture(scope="session")
def math_png(tmp_path_factory) -> bytes:  # noqa: ANN001
    p = make_homework_photo(MATH_TEXT, "zh", tmp_path_factory.mktemp("img") / "math", seed=2, out_width=900)
    return p.read_bytes()
