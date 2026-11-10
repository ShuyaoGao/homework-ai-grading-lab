"""学生画像与班级看板。

数据口径：每份提交取“最终结果”（教师复核过的用教师版本，否则用 AI 初评，并在画像里标明含未复核数据）。

- 成绩趋势：按提交时间的得分率（0–100%），每个学科做一条线并给出线性斜率（每次作业变化几个百分点）；
- 知识点掌握度：同一知识点按时间做指数加权平均（新近表现权重大，α=0.5），≥80% 掌握 / 60–80% 基本掌握 / <60% 薄弱；
- 写作能力雷达：作文/论文类量规各维度带 dimension 标签，按维度取得分率平均；
- 高频错误：按错误类型计数（来源于 AI 或教师批注）。
"""
from __future__ import annotations

import statistics
from collections import Counter, defaultdict

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Assignment, Review, Student, Submission
from ..pipeline.error_types import DIMENSION_LABELS, label_of
from ..pipeline.rubrics import RubricError, get_rubric
from .metrics import agreement_report

DONE = ("ai_done", "reviewed")
SUBJECT_LABELS = {"english": "英语", "chinese": "语文", "math": "数学", "paper": "论文", "other": "其他"}
WRITING_KINDS = {"essay_en", "essay_zh", "paper"}


def _mastery_level(v: float) -> str:
    return "掌握" if v >= 0.8 else ("基本掌握" if v >= 0.6 else "薄弱")


def _slope(ys: list[float]) -> float | None:
    if len(ys) < 2:
        return None
    x = np.arange(len(ys), dtype=float)
    return float(np.polyfit(x, np.asarray(ys, float), 1)[0])


def _done_submissions(db: Session, student_ids: list[int] | None = None, class_id: int | None = None,
                      assignment_id: int | None = None) -> list[Submission]:
    q = select(Submission).where(Submission.status.in_(DONE), Submission.final_result.is_not(None))
    if student_ids is not None:
        q = q.where(Submission.student_id.in_(student_ids))
    if assignment_id is not None:
        q = q.where(Submission.assignment_id == assignment_id)
    if class_id is not None:
        q = q.join(Assignment, Submission.assignment_id == Assignment.id).where(Assignment.class_id == class_id)
    return list(db.execute(q.order_by(Submission.submitted_at)).scalars())


def student_profile(db: Session, student_id: int) -> dict:
    st = db.get(Student, student_id)
    subs = _done_submissions(db, [student_id])
    trend, by_subject = [], defaultdict(list)
    kp_series: dict[str, list[tuple[str, float]]] = defaultdict(list)
    radar_acc: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    err_counter: Counter = Counter()
    err_by_subject: dict[str, Counter] = defaultdict(Counter)
    unreviewed = 0
    demo = sum(1 for s in subs if s.is_demo)
    for s in subs:
        r = s.final_result or {}
        a = s.assignment
        mx = r.get("max_score") or s.max_score or 0
        ratio = (r.get("total_score", 0) / mx) if mx else 0
        if s.status != "reviewed":
            unreviewed += 1
        date = s.submitted_at.strftime("%Y-%m-%d")
        trend.append({"submission_id": s.id, "date": date, "assignment": a.title, "subject": a.subject,
                      "score": r.get("total_score"), "max_score": mx, "ratio": round(ratio, 4), "band": r.get("band", ""),
                      "reviewed": s.status == "reviewed"})
        by_subject[a.subject].append(ratio)
        for k in r.get("knowledge_points", []):
            kp_series[k["name"]].append((date, float(k["score_ratio"])))
        try:
            rub = get_rubric(a.rubric_key, db)
        except RubricError:
            rub = None
        if rub and rub.kind in WRITING_KINDS:
            dims = {c.id: c.dimension for c in rub.criteria}
            for c in r.get("criteria", []):
                dim = dims.get(c["id"])
                if dim and c.get("max_score"):
                    radar_acc[a.subject][dim].append(c["score"] / c["max_score"])
        for e in r.get("errors", []):
            t = e.get("type", "other")
            if t in ("ocr_uncertain",):
                continue
            err_counter[t] += 1
            err_by_subject[a.subject][t] += 1

    knowledge = []
    for name, series in kp_series.items():
        v = None
        for _, x in series:
            v = x if v is None else 0.5 * x + 0.5 * v
        knowledge.append({"name": name, "mastery": round(v, 3), "level": _mastery_level(v), "n": len(series),
                          "last_date": series[-1][0], "history": [x for _, x in series]})
    knowledge.sort(key=lambda k: k["mastery"])

    subjects = {}
    for subj, ys in by_subject.items():
        sl = _slope(ys)
        subjects[subj] = {"label": SUBJECT_LABELS.get(subj, subj), "count": len(ys), "avg_ratio": round(sum(ys) / len(ys), 4),
                          "latest_ratio": round(ys[-1], 4), "slope": None if sl is None else round(sl, 4),
                          "trend": ("上升" if sl and sl > 0.01 else "下降" if sl and sl < -0.01 else "平稳") if sl is not None else "数据不足"}

    radar = {}
    for subj, dims in radar_acc.items():
        radar[subj] = {"label": SUBJECT_LABELS.get(subj, subj),
                       "dimensions": [{"id": d, "label": DIMENSION_LABELS.get(d, d), "value": round(sum(v) / len(v), 3),
                                       "n": len(v)} for d, v in dims.items()]}

    errors = [{"type": t, "label": label_of(t), "count": c} for t, c in err_counter.most_common(10)]
    summary = _summary(subjects, knowledge, errors, radar)
    return {
        "student": {"id": st.id, "name": st.name, "class_id": st.class_id},
        "submission_count": len(subs), "unreviewed_count": unreviewed, "demo_count": demo,
        "data_note": "画像中含未经教师复核的 AI 初评数据" if unreviewed else "全部数据已经教师复核",
        "trend": trend, "subjects": subjects, "knowledge": knowledge, "radar": radar, "errors": errors,
        "errors_by_subject": {k: [{"type": t, "label": label_of(t), "count": c} for t, c in v.most_common(6)]
                              for k, v in err_by_subject.items()},
        "summary": summary,
    }


def _summary(subjects: dict, knowledge: list, errors: list, radar: dict) -> list[str]:
    out = []
    for s in subjects.values():
        if s["count"] >= 2:
            out.append(f"{s['label']}：共 {s['count']} 次作业，平均得分率 {s['avg_ratio']:.0%}，近期趋势{s['trend']}。")
    weak = [k["name"] for k in knowledge if k["level"] == "薄弱"][:3]
    if weak:
        out.append("需要加强的知识点：" + "、".join(weak) + "。")
    strong = [k["name"] for k in sorted(knowledge, key=lambda k: -k["mastery"]) if k["level"] == "掌握"][:3]
    if strong:
        out.append("掌握较好的知识点：" + "、".join(strong) + "。")
    if errors:
        out.append("高频错误：" + "、".join(f"{e['label']}（{e['count']}次）" for e in errors[:3]) + "。")
    for r in radar.values():
        dims = sorted(r["dimensions"], key=lambda d: d["value"])
        if len(dims) >= 2:
            out.append(f"{r['label']}写作：{dims[-1]['label']}相对较强，{dims[0]['label']}相对薄弱。")
    return out or ["数据还不够，完成更多作业后会生成画像。"]


def class_dashboard(db: Session, class_id: int) -> dict:
    assignments = list(db.execute(select(Assignment).where(Assignment.class_id == class_id)
                                  .order_by(Assignment.created_at)).scalars())
    students = list(db.execute(select(Student).where(Student.class_id == class_id)).scalars())
    all_subs = list(db.execute(select(Submission).join(Assignment, Submission.assignment_id == Assignment.id)
                               .where(Assignment.class_id == class_id)).scalars())
    status_count = Counter(s.status for s in all_subs)
    pending = sum(1 for s in all_subs if s.status == "ai_done")
    per_assignment = []
    class_err: Counter = Counter()
    err_students: dict[str, set[int]] = defaultdict(set)
    quote_counter: Counter = Counter()
    for a in assignments:
        subs = [s for s in all_subs if s.assignment_id == a.id and s.status in DONE and s.final_result]
        scores = [s.final_result["total_score"] for s in subs]
        mx = subs[0].final_result.get("max_score") if subs else None
        bands = Counter(s.final_result.get("band", "") for s in subs)
        hist = []
        if scores and mx:
            edges = np.linspace(0, mx, 11)
            counts, _ = np.histogram(scores, bins=edges)
            hist = [{"from": round(float(edges[i]), 1), "to": round(float(edges[i + 1]), 1), "count": int(c)}
                    for i, c in enumerate(counts)]
        crit_avg = defaultdict(list)
        for s in subs:
            for c in s.final_result.get("criteria", []):
                if c.get("max_score"):
                    crit_avg[c.get("name") or c["id"]].append(c["score"] / c["max_score"])
            for e in s.final_result.get("errors", []):
                t = e.get("type", "other")
                if t == "ocr_uncertain":
                    continue
                class_err[t] += 1
                err_students[t].add(s.student_id)
                if e.get("quote") and t in ("spelling", "typo", "grammar.agreement", "grammar.tense", "punctuation"):
                    quote_counter[(t, e["quote"].strip().lower()[:30])] += 1
        per_assignment.append({
            "id": a.id, "title": a.title, "subject": a.subject, "rubric_key": a.rubric_key,
            "submitted": sum(1 for s in all_subs if s.assignment_id == a.id), "graded": len(subs),
            "reviewed": sum(1 for s in subs if s.status == "reviewed"), "max_score": mx,
            "mean": round(statistics.mean(scores), 2) if scores else None,
            "median": round(statistics.median(scores), 2) if scores else None,
            "std": round(statistics.pstdev(scores), 2) if len(scores) > 1 else None,
            "min": min(scores) if scores else None, "max": max(scores) if scores else None,
            "histogram": hist, "bands": dict(bands),
            "criteria_avg": [{"name": k, "ratio": round(sum(v) / len(v), 3)} for k, v in crit_avg.items()],
        })
    common = [{"type": t, "label": label_of(t), "count": c, "students": len(err_students[t]),
               "student_ratio": round(len(err_students[t]) / max(1, len(students)), 3)}
              for t, c in class_err.most_common(8)]
    common_quotes = [{"type": t, "label": label_of(t), "quote": q, "count": c}
                     for (t, q), c in quote_counter.most_common(10) if c >= 2]
    # 需要关注：最近两次得分率下降超过 15 个百分点，或平均得分率 < 60%
    attention = []
    for st in students:
        ss = sorted([s for s in all_subs if s.student_id == st.id and s.status in DONE and s.final_result],
                    key=lambda s: s.submitted_at)
        ratios = [s.final_result["total_score"] / s.final_result["max_score"] for s in ss if s.final_result.get("max_score")]
        if not ratios:
            continue
        reason = None
        if len(ratios) >= 2 and ratios[-1] - ratios[-2] < -0.15:
            reason = f"最近一次得分率下降 {abs(ratios[-1] - ratios[-2]):.0%}"
        elif sum(ratios) / len(ratios) < 0.6:
            reason = f"平均得分率 {sum(ratios) / len(ratios):.0%}"
        if reason:
            attention.append({"student_id": st.id, "name": st.name, "reason": reason})
    return {
        "class_id": class_id, "student_count": len(students), "submission_count": len(all_subs),
        "status": dict(status_count), "pending_review": pending, "assignments": per_assignment,
        "demo_count": sum(1 for s in all_subs if s.is_demo),
        "common_errors": common, "common_quotes": common_quotes, "attention": attention,
        "agreement": ai_teacher_agreement(db, class_id=class_id),
    }


def ai_teacher_agreement(db: Session, class_id: int | None = None, rubric_key: str | None = None) -> dict:
    """AI 初评 vs 教师复核：每份提交取最新一次复核。按量规分组（不同量规分值尺度不同，不能混算 QWK）。"""
    q = select(Review).order_by(Review.created_at)
    if rubric_key:
        q = q.where(Review.rubric_key == rubric_key)
    rows = list(db.execute(q).scalars())
    if class_id is not None:
        sub_ids = set(db.execute(select(Submission.id).join(Assignment, Submission.assignment_id == Assignment.id)
                                 .where(Assignment.class_id == class_id)).scalars())
        rows = [r for r in rows if r.submission_id in sub_ids]
    latest: dict[int, Review] = {}
    for r in rows:
        latest[r.submission_id] = r
    # 按（量规, 满分）分组：同一量规（如简答题）不同作业满分可能不同，分值尺度不同不能混算 QWK
    groups: dict[tuple[str, float], list[Review]] = defaultdict(list)
    for r in latest.values():
        if r.ai_total is not None:
            groups[(r.rubric_key, r.max_score)].append(r)
    out = {}
    for (rkey, gmax), rs in groups.items():
        try:
            rub = get_rubric(rkey, db)
        except RubricError:
            continue
        key = rkey if sum(1 for k in groups if k[0] == rkey) == 1 else f"{rkey}（满分{gmax:g}）"
        ai = [r.ai_total for r in rs]
        hu = [r.teacher_total for r in rs]
        mx = rs[0].max_score
        rep = agreement_report(ai, hu, [rub.band_index(x, mx) for x in ai], [rub.band_index(x, mx) for x in hu],
                               min_rating=0, max_rating=int(round(mx)))
        rep["rubric_name"] = rub.name
        rep["rubric_key"] = rkey
        rep["max_score"] = mx
        rep["changed_ratio"] = round(sum(1 for r in rs if abs((r.ai_total or 0) - r.teacher_total) > 1e-6) / len(rs), 3)
        out[key] = rep
    return out
