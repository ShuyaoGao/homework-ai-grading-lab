"""画像计算、一致性指标、教师复核流程（复核后画像更新）。"""
from __future__ import annotations

import datetime as dt

import pytest

from backend.analytics.metrics import agreement_report, quadratic_weighted_kappa
from backend.analytics.profile import class_dashboard, student_profile
from backend.db import SessionLocal
from backend.models import Assignment, ClassRoom, Student, Submission, User
from backend.pipeline.grading.schema import GradingResult
from tests.conftest import token


# ---------------- 指标 ----------------

def test_qwk_known_values():
    assert quadratic_weighted_kappa([1, 2, 3], [1, 2, 3]) == pytest.approx(1.0)
    # a=[0,1,2,3] b=[0,1,2,2]：sklearn cohen_kappa_score(weights="quadratic") = 0.875
    assert quadratic_weighted_kappa([0, 1, 2, 3], [0, 1, 2, 2]) == pytest.approx(0.875, abs=1e-9)
    # 完全反向
    assert quadratic_weighted_kappa([0, 1, 2], [2, 1, 0]) == pytest.approx(-1.0)


def test_qwk_matches_sklearn_if_available():
    sk = pytest.importorskip("sklearn.metrics")
    import random

    rng = random.Random(0)
    a = [rng.randint(0, 6) for _ in range(200)]
    b = [min(6, max(0, x + rng.choice([-2, -1, 0, 0, 1]))) for x in a]
    assert quadratic_weighted_kappa(a, b) == pytest.approx(sk.cohen_kappa_score(a, b, weights="quadratic"), abs=1e-9)


def test_agreement_report_degenerate_blocks_qwk():
    rep = agreement_report([5, 5, 5, 5], [3, 4, 5, 6])
    assert rep["qwk"] is None and rep["pearson"] is None and "方差为 0" in rep["blocked_reason"]
    assert rep["mae"] == 1.0


def test_agreement_report_normal():
    rep = agreement_report([10, 12, 15, 18], [11, 12, 14, 18], [1, 2, 3, 4], [1, 2, 2, 4])
    assert rep["n"] == 4 and rep["qwk"] > 0.8 and rep["within1_band"] == 1.0
    assert rep["band_exact"] == 0.75 and rep["bias"] == 0.0


# ---------------- 画像 ----------------

def _result(rubric_key, crit, total, mx, kps=(), errors=()):
    return GradingResult(rubric_key=rubric_key, total_score=total, max_score=mx, criteria=crit, confidence=0.9,
                         knowledge_points=list(kps), errors=list(errors)).model_dump()


@pytest.fixture(scope="module")
def profile_student():
    db = SessionLocal()
    t = User(username="p_teacher", password_hash="x", display_name="t", role="teacher")
    db.add(t)
    db.flush()
    c = ClassRoom(name="画像班", teacher_id=t.id)
    db.add(c)
    db.flush()
    st = Student(class_id=c.id, name="画像同学", birth_year=2012)
    db.add(st)
    db.flush()
    a1 = Assignment(class_id=c.id, title="E1", subject="english", rubric_key="english_essay_zhongkao", created_by=t.id)
    a2 = Assignment(class_id=c.id, title="E2", subject="english", rubric_key="english_essay_zhongkao", created_by=t.id)
    a3 = Assignment(class_id=c.id, title="M1", subject="math", rubric_key="short_answer_math", created_by=t.id)
    db.add_all([a1, a2, a3])
    db.flush()
    crit = lambda c_, l_, o_: [{"id": "content", "name": "内容", "score": c_, "max_score": 8},  # noqa: E731
                               {"id": "language", "name": "语言", "score": l_, "max_score": 8},
                               {"id": "organization", "name": "结构", "score": o_, "max_score": 4}]
    base = dt.datetime(2026, 9, 1)
    rows = [
        (a1, _result("english_essay_zhongkao", crit(6, 4, 2), 12, 20, [{"name": "英语-主谓一致", "score_ratio": 0.4}],
                     [{"type": "grammar.agreement", "quote": "he go"}, {"type": "spelling", "quote": "freind"}]), "reviewed"),
        (a2, _result("english_essay_zhongkao", crit(8, 6, 4), 18, 20, [{"name": "英语-主谓一致", "score_ratio": 0.8}],
                     [{"type": "grammar.agreement", "quote": "she like"}]), "ai_done"),
        (a3, _result("short_answer_math", [{"id": "1", "name": "第1题", "score": 3, "max_score": 4}], 3, 4,
                     [{"name": "方程", "score_ratio": 0.75}]), "reviewed"),
    ]
    for i, (a, res, status) in enumerate(rows):
        db.add(Submission(assignment_id=a.id, student_id=st.id, uploaded_by=t.id, status=status, final_result=res,
                          final_total=res["total_score"], max_score=res["max_score"],
                          submitted_at=base + dt.timedelta(days=i)))
    db.commit()
    sid, cid = st.id, c.id
    db.close()
    return sid, cid


def test_student_profile(profile_student):
    sid, _ = profile_student
    db = SessionLocal()
    p = student_profile(db, sid)
    db.close()
    assert p["submission_count"] == 3 and p["unreviewed_count"] == 1
    en = p["subjects"]["english"]
    assert en["count"] == 2 and en["avg_ratio"] == pytest.approx(0.75) and en["slope"] == pytest.approx(0.3)
    assert en["trend"] == "上升"
    kp = {k["name"]: k for k in p["knowledge"]}
    assert kp["英语-主谓一致"]["mastery"] == pytest.approx(0.6)  # EWMA: 0.5*0.8 + 0.5*0.4
    assert kp["英语-主谓一致"]["level"] == "基本掌握"
    assert kp["方程"]["level"] == "基本掌握"
    dims = {d["id"]: d["value"] for d in p["radar"]["english"]["dimensions"]}
    assert dims["content"] == pytest.approx((6 / 8 + 8 / 8) / 2) and dims["organization"] == pytest.approx(0.75)
    errs = {e["type"]: e["count"] for e in p["errors"]}
    assert errs == {"grammar.agreement": 2, "spelling": 1}
    assert p["summary"]


def test_class_dashboard(profile_student):
    _, cid = profile_student
    db = SessionLocal()
    d = class_dashboard(db, cid)
    db.close()
    assert d["student_count"] == 1 and d["submission_count"] == 3 and d["pending_review"] == 1
    e1 = next(a for a in d["assignments"] if a["title"] == "E1")
    assert e1["mean"] == 12 and sum(h["count"] for h in e1["histogram"]) == 1
    assert d["common_errors"][0]["type"] == "grammar.agreement"


# ---------------- 复核流程（API）----------------

def test_review_flow_updates_profile(client, world, en_png):
    ht = token(client, "t_teacher")
    r = client.post("/api/submissions", data={"assignment_id": world["a_en"], "student_id": world["s_cloud"]},
                    files=[("files", ("p.png", en_png, "image/png"))], headers=ht)
    sid = r.json()["id"]
    d = client.get(f"/api/submissions/{sid}", headers=ht).json()
    assert d["can_review"] and d["status"] == "ai_done"
    ai = d["ai_result"]
    before = client.get(f"/api/students/{world['s_cloud']}/profile", headers=ht).json()

    # 超过满分 → 400
    bad = {"criteria": [{"id": "content", "score": 99}]}
    assert client.post(f"/api/submissions/{sid}/review", json=bad, headers=ht).status_code == 400
    # 未知维度 → 400
    assert client.post(f"/api/submissions/{sid}/review", json={"criteria": [{"id": "nope", "score": 1}]},
                       headers=ht).status_code == 400
    # 其他班老师不能复核
    assert client.post(f"/api/submissions/{sid}/review", json={"criteria": []},
                       headers=token(client, "t_teacher2")).status_code == 403

    body = {"criteria": [{"id": "content", "score": 8}, {"id": "language", "score": 2}, {"id": "organization", "score": 4}],
            "deductions": [], "comment": "老师修改后的评语",
            "errors": [{"type": "spelling", "quote": "becouse", "message": "拼写", "source": "teacher"}],
            "note": "语言错误较多"}
    r = client.post(f"/api/submissions/{sid}/review", json=body, headers=ht)
    assert r.status_code == 200, r.text
    d2 = r.json()
    assert d2["status"] == "reviewed" and d2["final_result"]["total_score"] == 14
    assert d2["final_result"]["comment"] == "老师修改后的评语" and not d2["final_result"]["needs_review"]
    assert d2["ai_result"]["total_score"] == ai["total_score"]  # AI 原始结果保留

    # 第二次修改 → 两条记录
    body["criteria"][1]["score"] = 3
    client.post(f"/api/submissions/{sid}/review", json=body, headers=ht)
    hist = client.get(f"/api/submissions/{sid}/reviews", headers=ht).json()
    assert [h["teacher_total"] for h in hist] == [14, 15] and hist[0]["note"] == "语言错误较多"

    after = client.get(f"/api/students/{world['s_cloud']}/profile", headers=ht).json()
    assert after["unreviewed_count"] == before["unreviewed_count"] - 1
    assert after["trend"][-1]["score"] == 15 and after["trend"][-1]["reviewed"]

    # 家长看到的是教师复核后的分数
    agree = client.get(f"/api/agreement?class_id={world['class1']}", headers=ht).json()
    assert agree["english_essay_zhongkao"]["n"] >= 1


def test_fix_ocr_text_regrades(client, world, en_png):
    ht = token(client, "t_teacher")
    r = client.post("/api/submissions", data={"assignment_id": world["a_en"], "student_id": world["s_ok"]},
                    files=[("files", ("p.png", en_png, "image/png"))], headers=ht)
    sid = r.json()["id"]
    good = ("Last weekend I went to the library with my friends. She likes reading very much. We read an interesting "
            "book because it was about science. Then we played basketball together. I felt very happy.")
    d = client.put(f"/api/submissions/{sid}/ocr-text", json={"text": good, "regrade": True}, headers=ht).json()
    assert d["ocr_text"] == good and d["ocr_text_edited"]
    assert not any(e["type"] == "spelling" for e in d["final_result"]["errors"])
    # 家长不能改识别文字
    assert client.put(f"/api/submissions/{sid}/ocr-text", json={"text": "x"}, headers=token(client, "t_parent")).status_code == 403


def test_retry_and_consent_endpoints(client, world):
    hp = token(client, "t_parent")
    r = client.post(f"/api/students/{world['s_none']}/consents", json={"scope": "basic", "granted": True}, headers=hp)
    assert r.json()["consents"]["basic"] is True
    r = client.post(f"/api/students/{world['s_none']}/consents", json={"scope": "basic", "granted": False}, headers=hp)
    assert r.json()["consents"] == {"basic": False, "cloud_ai": False}
    rows = client.get(f"/api/students/{world['s_none']}/consents", headers=hp).json()
    assert rows and rows[0]["revoked_at"] is not None  # 撤回留痕，不删除
    # 教师不能代家长签署
    assert client.post(f"/api/students/{world['s_none']}/consents", json={"scope": "basic", "granted": True},
                       headers=token(client, "t_teacher")).status_code == 403
    exp = client.get(f"/api/students/{world['s_ok']}/export", headers=hp).json()
    assert exp["student"]["id"] == world["s_ok"] and "profile" in exp


def test_cer():
    from backend.analytics.metrics import cer, levenshtein

    assert levenshtein("kitten", "sitting") == 3
    assert cer("我的老师", "我的老师") == 0
    assert cer("我的老师很好", "我的老帅很好") == pytest.approx(1 / 6)
    assert cer("我的 老师\n很好", "我的老师很好") == 0  # 中文忽略空白与换行
    assert cer("I like it.", "I  like   it.") == 0  # 英文合并空白
    assert cer("I like it.", "I lik it.") == pytest.approx(1 / 10)
    assert cer("我的老师，", "我的老师,") == 0  # 全角半角统一
    assert cer("abc", "") == 1 and cer("", "") == 0
