"""AI 评分 vs 人工评分一致性评估（QWK / Pearson / MAE / 精确一致 / ±1 档）。

示例：
    # 规则基线（mock，离线、免费）在 ELLIPSE 英语学习者作文上
    python -m eval.run_eval --dataset ellipse --path eval/datasets/ellipse/ELLIPSE_Final_github_train.csv \
        --rubric english_essay_gaokao --provider mock --limit 1000
    # 中文小学作文 CFMS-34（两位评分员，可算人-人基线）
    python -m eval.run_eval --dataset cfms34 --path eval/datasets/cfms34/train.csv --cfms-mode rater \
        --rubric chinese_essay_gaokao --provider mock
    # 有 key 时换成 Claude（会产生费用，必须加 --yes）
    python -m eval.run_eval --dataset ellipse --path ... --provider claude --limit 100 --yes
    # 本系统数据库里“AI 初评 vs 教师复核”
    python -m eval.run_eval --dataset app-db --path data/edu_eval.db

产出目录 eval/results/<数据集>_<provider>_<时间>/：
    _env_stamp.json      运行环境（版本、git 提交、provider/模型、数据文件哈希、参数）
    raw/predictions.jsonl  每条样本的人工分、AI 原始分、映射后分数、模型输出摘要（不落盘不算跑过）
    result.json          指标；键路径可直接引用，如 overall.linear.qwk
评分尺度不同：AI 按量规打分后用得分率线性映射到数据集分值范围（linear）；另用 --calib-frac 比例的样本
拟合线性校准（calibrated，在其余样本上报告），两者都给出，避免只报好看的那个。
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import hashlib
import json
import platform
import random
import subprocess
import sys
import time
from collections import defaultdict
from importlib import metadata
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.analytics.metrics import agreement_report  # noqa: E402
from backend.pipeline.grading.base import GradingContext  # noqa: E402
from backend.pipeline.registry import get_grader  # noqa: E402
from backend.pipeline.rubrics import builtin_rubrics, get_rubric  # noqa: E402
from eval.datasets import EssayItem, load_app_db, load_asap, load_cfms34, load_ellipse, load_generic, load_persuade  # noqa: E402


def to_grid(score: float, lo: float, hi: float, step: float) -> int:
    v = min(hi, max(lo, score))
    return int(round((v - lo) / step))


def git_rev() -> str:
    try:
        rev = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
        return rev + ("-dirty" if dirty else "")
    except OSError:
        return "unknown"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def env_stamp(args: argparse.Namespace, grader, rubric) -> dict:  # noqa: ANN001
    pk = {}
    for p in ("fastapi", "pydantic", "numpy", "anthropic", "openai", "opencv-python-headless", "pillow"):
        try:
            pk[p] = metadata.version(p)
        except metadata.PackageNotFoundError:
            pk[p] = None
    path = Path(args.path)
    return {
        "timestamp": dt.datetime.now().isoformat(timespec="seconds"), "git": git_rev(),
        "python": sys.version.split()[0], "platform": platform.platform(), "packages": pk,
        "provider": grader.name if grader else None, "model": getattr(grader, "model", None) if grader else None,
        "rubric": {"key": rubric.key, "version": rubric.version} if rubric else None,
        "dataset": args.dataset, "data_file": str(path), "data_sha256": sha256(path) if path.is_file() else None,
        "args": {k: v for k, v in vars(args).items() if k not in ("yes",)},
    }


def load_items(args: argparse.Namespace) -> list[EssayItem]:
    if args.dataset == "asap":
        return load_asap(args.path, args.essay_set)
    if args.dataset == "ellipse":
        return load_ellipse(args.path, args.target)
    if args.dataset == "persuade":
        return load_persuade(args.path, args.score_col or "holistic_essay_score")
    if args.dataset == "cfms34":
        return load_cfms34(args.path, args.cfms_mode)
    if args.dataset == "generic":
        return load_generic(args.path, args.text_col, args.score_col, args.score_min, args.score_max,
                            prompt_col=args.prompt_col, score2_col=args.score2_col, step=args.step)
    raise SystemExit(f"未知数据集 {args.dataset}")


def report_for(pairs: list[dict], key_ai: str) -> dict:
    """pairs 内含 human_grid / ai_grid 等；按网格（整数档）算 QWK 与一致率，MAE 换回原分值。"""
    if not pairs:
        return {"n": 0}
    lo, hi, step = pairs[0]["lo"], pairs[0]["hi"], pairs[0]["step"]
    n_levels = int(round((hi - lo) / step))
    rep = agreement_report([p[key_ai] for p in pairs], [p["human_grid"] for p in pairs], min_rating=0, max_rating=n_levels)
    if rep.get("mae") is not None:
        rep["mae_points"] = round(rep["mae"] * step, 4)
    rep["scale"] = {"min": lo, "max": hi, "step": step, "levels": n_levels + 1}
    return rep


def run_app_db(args: argparse.Namespace, out: Path) -> dict:
    rows = load_app_db(args.path)
    groups = defaultdict(list)
    for r in rows:
        groups[(r["rubric_key"], r["max_score"])].append(r)
    res = {}
    (out / "raw").mkdir(parents=True, exist_ok=True)
    with (out / "raw" / "predictions.jsonl").open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    for (rk, mx), rs in groups.items():
        try:
            rub = get_rubric(rk)
            ab = [rub.band_index(r["ai"], mx) for r in rs]
            hb = [rub.band_index(r["teacher"], mx) for r in rs]
        except Exception:  # noqa: BLE001  自定义量规不在内置表中
            ab = hb = None
        rep = agreement_report([r["ai"] for r in rs], [r["teacher"] for r in rs], ab, hb, min_rating=0,
                               max_rating=int(round(mx)))
        res[f"{rk}@{mx:g}"] = rep
    return {"dataset": "app-db", "groups": res, "n": len(rows)}


def main() -> int:
    ap = argparse.ArgumentParser(description="AI 评分与人工评分一致性评估")
    ap.add_argument("--dataset", required=True, choices=["asap", "ellipse", "persuade", "cfms34", "generic", "app-db"])
    ap.add_argument("--path", required=True)
    ap.add_argument("--provider", default="mock", help="mock / claude / openai_compat")
    ap.add_argument("--model", default=None)
    ap.add_argument("--rubric", default=None, help="评分用的量规 key（默认按数据集语言选择）")
    ap.add_argument("--limit", type=int, default=None, help="随机抽样条数（固定种子）")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--calib-frac", type=float, default=0.3, help="用于线性校准的样本比例（0 关闭）")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--essay-set", type=int, default=None)
    ap.add_argument("--target", default="Overall", help="ELLIPSE 目标分项")
    ap.add_argument("--cfms-mode", default="sum", choices=["sum", "rater"])
    ap.add_argument("--text-col"), ap.add_argument("--score-col"), ap.add_argument("--score2-col")
    ap.add_argument("--prompt-col")
    ap.add_argument("--score-min", type=float), ap.add_argument("--score-max", type=float)
    ap.add_argument("--step", type=float, default=1.0)
    ap.add_argument("--out", default=None)
    ap.add_argument("--yes", action="store_true", help="确认使用云端 provider（会产生费用）")
    args = ap.parse_args()

    stamp_time = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out = Path(args.out) if args.out else ROOT / "eval" / "results" / f"{args.dataset}_{args.provider}_{stamp_time}"
    out.mkdir(parents=True, exist_ok=True)

    if args.dataset == "app-db":
        result = run_app_db(args, out)
        (out / "_env_stamp.json").write_text(json.dumps(env_stamp(args, None, None), ensure_ascii=False, indent=2), encoding="utf-8")
        (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=1))
        return 0

    items = load_items(args)
    if not items:
        raise SystemExit("没有读到样本")
    rng = random.Random(args.seed)
    if args.limit and len(items) > args.limit:
        items = rng.sample(items, args.limit)
    lang = "zh" if args.dataset == "cfms34" else "en"
    rubric_key = args.rubric or ("chinese_essay_gaokao" if lang == "zh" else "english_essay_gaokao")
    rubric = builtin_rubrics().get(rubric_key) or get_rubric(rubric_key)
    grader = get_grader(args.provider, args.model)
    ok, why = grader.available()
    if not ok:
        raise SystemExit(f"provider {args.provider} 不可用：{why}")
    if grader.is_cloud and not args.yes:
        raise SystemExit(f"provider {args.provider} 是云端付费接口，将调用 {len(items)} 次。确认后加 --yes 重新运行。")
    stamp = env_stamp(args, grader, rubric)
    (out / "_env_stamp.json").write_text(json.dumps(stamp, ensure_ascii=False, indent=2), encoding="utf-8")

    ref = {"min_chars": 300} if lang == "zh" else {}

    def grade(it: EssayItem) -> dict:
        t0 = time.time()
        try:
            r = grader.grade(GradingContext(rubric=rubric, text=it.text, prompt=it.prompt, reference=ref))
            return {"id": it.id, "ok": True, "ai_total": r.total_score, "ai_max": r.max_score, "band": r.band,
                    "criteria": {c.id: c.score for c in r.criteria}, "confidence": r.confidence,
                    "needs_review": r.needs_review, "model": r.model, "sec": round(time.time() - t0, 3)}
        except Exception as e:  # noqa: BLE001  单条失败不中断，记录下来
            return {"id": it.id, "ok": False, "error": str(e)[:300], "sec": round(time.time() - t0, 3)}

    t_start = time.time()
    with cf.ThreadPoolExecutor(max_workers=1 if args.provider == "mock" else args.workers) as ex:
        preds = list(ex.map(grade, items))
    by_id = {p["id"]: p for p in preds}

    pairs = []
    (out / "raw").mkdir(exist_ok=True)
    with (out / "raw" / "predictions.jsonl").open("w", encoding="utf-8") as f:
        for it in items:
            p = by_id[it.id]
            row = {"id": it.id, "group": it.group, "human": it.human, "human2": it.human2, **p}
            if p["ok"]:
                ratio = p["ai_total"] / p["ai_max"] if p["ai_max"] else 0
                lin = it.score_min + ratio * (it.score_max - it.score_min)
                row.update(ratio=round(ratio, 4), ai_linear=round(lin, 3))
                pairs.append({"id": it.id, "group": it.group, "ratio": ratio, "human": it.human, "human2": it.human2,
                              "lo": it.score_min, "hi": it.score_max, "step": it.score_step,
                              "human_grid": to_grid(it.human, it.score_min, it.score_max, it.score_step),
                              "ai_linear_grid": to_grid(lin, it.score_min, it.score_max, it.score_step), "extra": it.extra})
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    failed = sum(1 for p in preds if not p["ok"])

    # 线性校准：在校准集上拟合 human ≈ a·ratio + b，在其余样本上报告
    calibrated = None
    if args.calib_frac and len(pairs) >= 20:
        idx = list(range(len(pairs)))
        random.Random(args.seed + 1).shuffle(idx)
        k = int(len(idx) * args.calib_frac)
        cal, test = [pairs[i] for i in idx[:k]], [pairs[i] for i in idx[k:]]
        x = np.array([p["ratio"] for p in cal])
        y = np.array([p["human"] for p in cal])
        if x.std() > 0:
            a, b = np.polyfit(x, y, 1)
            for p in test:
                p["ai_cal_grid"] = to_grid(a * p["ratio"] + b, p["lo"], p["hi"], p["step"])
            calibrated = {"fit": {"a": round(float(a), 4), "b": round(float(b), 4), "n_calib": k},
                          **report_for(test, "ai_cal_grid")}
            calibrated["linear_on_same_test"] = report_for(test, "ai_linear_grid")
        else:
            calibrated = {"blocked_reason": "AI 得分率在校准集上方差为 0，无法校准"}

    # 人-人基线
    hh = [p for p in pairs if p["human2"] is not None]
    human_human = None
    if hh:
        lo, hi, st = hh[0]["lo"], hh[0]["hi"], hh[0]["step"]
        human_human = agreement_report([to_grid(p["human2"], lo, hi, st) for p in hh], [p["human_grid"] for p in hh],
                                       min_rating=0, max_rating=int(round((hi - lo) / st)))
    elif args.dataset == "asap":
        r12 = [(p["extra"].get("rater1"), p["extra"].get("rater2")) for p in pairs]
        r12 = [(a, b) for a, b in r12 if a is not None and b is not None]
        if r12:
            human_human = agreement_report([b for _, b in r12], [a for a, _ in r12])
            human_human["note"] = "rater1 vs rater2，评分员自身尺度（1/7/8 组与 domain1 尺度不同）"

    per_group = {}
    groups = defaultdict(list)
    for p in pairs:
        groups[p["group"]].append(p)
    if len(groups) > 1:
        per_group = {g: report_for(ps, "ai_linear_grid") for g, ps in groups.items() if len(ps) >= 10}

    result = {
        "dataset": args.dataset, "provider": grader.name, "model": grader.model, "rubric": rubric.key,
        "n_items": len(items), "n_scored": len(pairs), "n_failed": failed,
        "seconds": round(time.time() - t_start, 2),
        "overall": {"linear": report_for(pairs, "ai_linear_grid"), "calibrated": calibrated},
        "human_human": human_human, "per_group": per_group,
        "ai_distribution": {"mean_ratio": round(float(np.mean([p["ratio"] for p in pairs])), 4) if pairs else None,
                            "std_ratio": round(float(np.std([p["ratio"] for p in pairs])), 4) if pairs else None},
        "notes": [
            "QWK 在数据集分值网格上计算（如 ELLIPSE 1–5 步长 0.5 → 9 档）；mae_points 为原分值单位。",
            "linear：量规得分率线性映射到数据集分值范围；calibrated：用部分样本拟合线性校准后在其余样本上评估。",
            "mock 为规则启发式基线，不代表大模型水平；方差为 0 时 QWK 置为 null 并给出原因。",
        ],
    }
    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    lin = result["overall"]["linear"]
    print(json.dumps({"out": str(out), "n": len(pairs), "failed": failed, "linear": {k: lin.get(k) for k in ("qwk", "pearson", "mae_points", "exact", "within1_score")},
                      "calibrated": None if not calibrated or "qwk" not in calibrated else {k: calibrated.get(k) for k in ("qwk", "pearson", "mae_points", "exact", "within1_score")},
                      "human_human": None if not human_human else {k: human_human.get(k) for k in ("qwk", "pearson", "exact", "within1_score")}},
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
