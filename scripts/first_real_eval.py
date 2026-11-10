"""首轮真实评估：一条命令同时跑“识别准确率（CER）”和“评分一致性（QWK）”，先估算费用再执行。

    # 只估算，不调用任何接口（任何时候都可以先跑这个）
    python scripts/first_real_eval.py --provider claude --dry-run
    # 真跑（云端付费接口必须加 --yes）
    python scripts/first_real_eval.py --provider claude --yes
    python scripts/first_real_eval.py --provider dashscope --yes
    python scripts/first_real_eval.py --provider vllm            # 服务器本地模型，不收费，不需要 --yes
    python scripts/first_real_eval.py --provider mock            # 离线验证本脚本本身

做三件事：
 1. 识别（CER）：用 N 张合成的仿手写照片（英文横线本/中文作文格/数学方格本，多种笔迹），以及你放进
    --handwriting-dir 的真实作业照片（同名 .txt 为人工转写），逐张预处理 + 识别，计算字符错误率；
 2. 评分（QWK）：在 CFMS-34（中文，带人-人基线）和 ELLIPSE（英文）上各抽 --n-grade 篇调用评分模型；
 3. 汇总：eval/results/first_real_<时间>/REPORT.md + summary.json，附费用估算与实际调用次数。
详细说明见 scripts/first_real_eval.md。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PROVIDERS = {
    # 名称: (OCR provider, 评分 provider, 默认识别模型, 默认评分模型, 是否云端付费)
    "claude": ("claude", "claude", "claude-sonnet-5-5", "claude-sonnet-5-5", True),
    "dashscope": ("openai_compat", "openai_compat", "qwen-vl-max", "qwen-plus", True),
    "vllm": ("openai_compat", "openai_compat", None, None, False),
    "mock": ("mock", "mock", "embedded-text", "heuristic-v1", False),
}
DATASETS = {
    "cfms34": ("eval/datasets/cfms34/train.csv", ["--cfms-mode", "rater"]),
    "ellipse": ("eval/datasets/ellipse/ELLIPSE_Final_github_train.csv", []),
}


# ------------------------------------------------------------------ 准备环境

def setup_env(args: argparse.Namespace, require_keys: bool = True) -> dict:
    """返回本次运行用的环境变量（子进程也用它）。只检查 key 是否存在，绝不打印 key。dry-run 时不要求 key。"""
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    if not require_keys:
        def _exit(msg: str) -> None:
            print(f"（dry-run，忽略）{msg}")
    else:
        def _exit(msg: str) -> None:
            sys.exit(msg)
    ocr_p, grd_p, ocr_m, grd_m, _ = PROVIDERS[args.provider]
    env["OCR_PROVIDER"], env["GRADER_PROVIDER"] = ocr_p, grd_p
    if args.provider == "claude":
        if not (env.get("ANTHROPIC_API_KEY") or env.get("ANTHROPIC_AUTH_TOKEN")):
            _exit("缺少 ANTHROPIC_API_KEY（写进 .env 或环境变量，不要写进代码）")
        env["CLAUDE_MODEL"] = args.model or env.get("CLAUDE_MODEL") or grd_m
        env["CLAUDE_OCR_MODEL"] = args.ocr_model or env.get("CLAUDE_OCR_MODEL") or env["CLAUDE_MODEL"]
    elif args.provider == "dashscope":
        if not env.get("DASHSCOPE_API_KEY"):
            _exit("缺少 DASHSCOPE_API_KEY")
        env.pop("OPENAI_COMPAT_BASE_URL", None)  # 走 DashScope 预设地址
        env["OPENAI_COMPAT_OCR_MODEL"] = args.ocr_model or ocr_m
        env["OPENAI_COMPAT_GRADER_MODEL"] = args.model or grd_m
        env.setdefault("OPENAI_COMPAT_JSON_MODE", "json_object")
    elif args.provider == "vllm":
        if not (env.get("OPENAI_COMPAT_BASE_URL") or env.get("OPENAI_COMPAT_GRADER_BASE_URL")):
            _exit("缺少 OPENAI_COMPAT_BASE_URL（见 docs/DEPLOY_LOCAL_MODEL.md 第 4 节）")
        env["OPENAI_COMPAT_IS_LOCAL"] = "true"
        if args.ocr_model:
            env["OPENAI_COMPAT_OCR_MODEL"] = args.ocr_model
        if args.model:
            env["OPENAI_COMPAT_GRADER_MODEL"] = args.model
        env.setdefault("OPENAI_COMPAT_JSON_MODE", "json_schema")
    os.environ.update({k: v for k, v in env.items() if k.startswith(("OCR_", "GRADER_", "CLAUDE_", "OPENAI_COMPAT_"))})
    if args.provider == "dashscope":
        os.environ.pop("OPENAI_COMPAT_BASE_URL", None)
    return env


# ------------------------------------------------------------------ 识别样本

def build_ocr_set(n: int, out: Path) -> list[dict]:
    """合成 n 张仿手写照片：英文横线（6 种笔迹）、中文作文格（4 种）、数学方格，难度与潦草程度各不相同。"""
    from backend.demo import corpus
    from backend.demo.handwriting import make_homework_photo, style_for

    out.mkdir(parents=True, exist_ok=True)
    items = []
    for i in range(n):
        kind = ["en", "zh", "en", "zh", "math"][i % 5]
        ability = 0.3 + 0.6 * ((i * 37) % 10) / 9
        mess = 0.5 + 0.9 * ((i * 53) % 7) / 6
        if kind == "en":
            text, lang, paper = corpus.gen_english(["weekend", "invite"][i % 2], ability, 900 + i), "en", "lined"
        elif kind == "zh":
            text, lang, paper = corpus.gen_chinese(["grownup", "hero"][i % 2], ability, 900 + i), "zh", "grid"
        else:
            text, lang, paper = corpus.gen_math(["equation", "area"][i % 2], ability, 900 + i), "zh", "squared"
        style = style_for(i, lang, messiness=mess, paper=paper)
        p = make_homework_photo(text, lang, out / f"ocr_{i:03d}_{kind}", seed=700 + i, style=style, out_width=1400)
        items.append({"id": p.stem, "path": str(p), "kind": kind, "text": text, "font": style.font.name if style.font else "",
                      "messiness": round(mess, 2), "synthetic": True})
    return items


def load_real_set(d: Path) -> list[dict]:
    """真实作业：图片 + 同名 .txt（人工逐字转写，不纠正学生的错误）。"""
    items = []
    for img in sorted(d.iterdir()):
        if img.suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp", ".heic"):
            continue
        txt = img.with_suffix(".txt")
        if not txt.exists():
            print(f"跳过 {img.name}：缺少同名 .txt 人工转写")
            continue
        text = txt.read_text(encoding="utf-8")
        kind = "zh" if sum("一" <= c <= "鿿" for c in text) > len(text) * 0.3 else "en"
        items.append({"id": img.stem, "path": str(img), "kind": kind, "text": text, "synthetic": False})
    return items


# ------------------------------------------------------------------ 费用估算

def text_tokens(s: str, claude: bool) -> int:
    cjk = sum(1 for c in s if "一" <= c <= "鿿")
    other = len(s) - cjk
    t = cjk * 1.0 + other / 3.6
    return int(t * (1.3 if claude else 1.0))  # Claude 4.7+ 新分词器约多 30%（官方定价页说明）


def image_tokens(w: int, h: int, max_side: int) -> int:
    s = min(1.0, max_side / max(w, h))
    w, h = int(w * s), int(h * s)
    return math.ceil(w / 28) * math.ceil(h / 28)  # 28×28 像素一个视觉 token（Claude 视觉文档；Qwen2.5-VL 同为 28px）


def estimate(args: argparse.Namespace, ocr_items: list[dict], n_grade_total: int, grade_samples: list[str]) -> dict:
    from PIL import Image

    from backend.pipeline.grading.base import SYSTEM_PROMPT, GradingContext, build_user_prompt
    from backend.pipeline.rubrics import builtin_rubrics

    prices = json.loads((ROOT / "scripts" / "pricing.json").read_text(encoding="utf-8"))
    _, _, ocr_m, grd_m, paid = PROVIDERS[args.provider]
    claude = args.provider == "claude"
    ocr_model = args.ocr_model or (os.environ.get("CLAUDE_OCR_MODEL") if claude else None) or ocr_m
    grd_model = args.model or (os.environ.get("CLAUDE_MODEL") if claude else None) or grd_m
    max_side = 1568 if claude else 2048
    ocr_in = ocr_out = 0
    for it in ocr_items:
        w, h = Image.open(it["path"]).size
        w, h = int(w * 0.85), int(h * 0.85)  # 预处理裁掉桌面后大约剩 85% 边长
        ocr_in += image_tokens(w, h, max_side) + 350  # + 识别指令
        ocr_out += text_tokens(it["text"], claude) + 80 + (300 if claude else 0)  # + JSON 外壳 + 少量思考
    r = builtin_rubrics()
    avg_prompt = statistics.mean(
        text_tokens(SYSTEM_PROMPT + build_user_prompt(GradingContext(rubric=r[k], text=t, prompt="", reference={})), claude)
        for k, t in grade_samples) if grade_samples else 2500
    grd_in = int(avg_prompt * n_grade_total + 900 * n_grade_total)  # + JSON Schema
    grd_out = int((900 + (1500 if claude else 0)) * n_grade_total)  # 评分 JSON + 思考（effort=medium 粗估）

    def cost(model: str | None, tin: int, tout: int) -> dict:
        p = prices.get(model or "", prices.get("vllm-local") if args.provider == "vllm" else None)
        if not p or args.provider in ("mock",):
            return {"model": model, "input_tokens": tin, "output_tokens": tout, "cost": 0, "currency": "-", "status": "不收费"}
        c = (tin * p["input"] + tout * p["output"]) / 1e6
        return {"model": model, "input_tokens": tin, "output_tokens": tout, "cost": round(c, 3), "currency": p["currency"],
                "status": p["status"], "source": p["source"], "checked": p["checked"]}

    o, g = cost(ocr_model, ocr_in, ocr_out), cost(grd_model, grd_in, grd_out)
    total = round(o["cost"] + g["cost"], 2) if o["currency"] == g["currency"] else None
    return {"paid": paid, "ocr": o, "grading": g, "total": total, "currency": o["currency"],
            "range": [round(total * 0.6, 2), round(total * 1.6, 2)] if total is not None else None,
            "usd_cny": prices["usd_cny"]["rate"], "calls": {"ocr": len(ocr_items), "grading": n_grade_total}}


# ------------------------------------------------------------------ 执行

def run_ocr(items: list[dict], raw: Path) -> list[dict]:
    from backend.analytics.metrics import cer
    from backend.pipeline.errors import ProviderError
    from backend.pipeline.ocr.base import OCRContext, PageInput
    from backend.pipeline.preprocess import load_upload, preprocess, save_image
    from backend.pipeline.registry import get_ocr

    ocr = get_ocr()
    tmp = raw.parent / "ocr_processed"
    tmp.mkdir(exist_ok=True)
    out = []
    with raw.open("w", encoding="utf-8") as f:
        for it in items:
            t0 = time.time()
            row = {k: v for k, v in it.items() if k != "text"}
            try:
                li = load_upload(Path(it["path"]).read_bytes())
                im, meta = preprocess(li.image)
                pp = tmp / f"{it['id']}.jpg"
                save_image(im, pp)
                subj = {"en": "english", "zh": "chinese", "math": "math"}[it["kind"]]
                res = ocr.recognize([PageInput(1, pp, li.embedded_text if ocr.name == "mock" else "")], OCRContext(subj))
                row.update(ok=True, cer=round(cer(it["text"], res.text), 4), confidence=res.confidence,
                           model=res.model, hyp=res.text, ref=it["text"], paper_detected=meta.paper_detected)
            except (ProviderError, ValueError) as e:
                row.update(ok=False, error=str(e)[:300])
            row["sec"] = round(time.time() - t0, 2)
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            out.append(row)
            print(f"  OCR {it['id']}: " + (f"CER={row['cer']:.3f}" if row.get("ok") else f"失败 {row.get('error')}"))
    return out


def run_grading(args: argparse.Namespace, env: dict, out: Path) -> dict:
    res = {}
    _, grd_p, *_ = PROVIDERS[args.provider]
    for name in args.datasets:
        path, extra = DATASETS[name]
        if not (ROOT / path).exists():
            res[name] = {"skipped": f"数据集不存在：{path}（下载方法见 eval/DATASETS.md）"}
            continue
        d = out / f"grade_{name}"
        cmd = [sys.executable, "-m", "eval.run_eval", "--dataset", name, "--path", path, "--provider", grd_p,
               "--limit", str(args.n_grade), "--seed", "2026", "--workers", str(args.workers), "--out", str(d), *extra]
        if PROVIDERS[args.provider][4]:
            cmd.append("--yes")
        print(f"  评分 {name}：{' '.join(cmd[2:])}")
        p = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8")
        if p.returncode != 0:
            res[name] = {"error": (p.stderr or p.stdout)[-800:]}
            continue
        r = json.loads((d / "result.json").read_text(encoding="utf-8"))
        res[name] = {"n": r["n_scored"], "failed": r["n_failed"], "linear": r["overall"]["linear"],
                     "calibrated": r["overall"]["calibrated"], "human_human": r["human_human"], "out": str(d)}
    return res


def grade_samples() -> list[tuple[str, str]]:
    from eval.datasets import load_cfms34, load_ellipse

    s = []
    p = ROOT / DATASETS["cfms34"][0]
    if p.exists():
        s += [("chinese_essay_gaokao", it.text) for it in load_cfms34(p)[:20]]
    p = ROOT / DATASETS["ellipse"][0]
    if p.exists():
        s += [("english_essay_gaokao", it.text) for it in load_ellipse(p)[:20]]
    return s


def write_report(out: Path, summary: dict) -> None:
    e = summary["estimate"]
    L = [f"# 首轮真实评估报告（{summary['provider']}）", "", f"时间：{summary['started']} · git {summary['git']}", ""]
    if summary["provider"] == "mock":
        L += ["> ⚠️ 本次为 mock：识别读的是图片内嵌的标准文本（CER 必然为 0），评分为规则模拟。只用于验证脚本流程。", ""]
    L += ["## 费用估算（运行前）", "",
          "| 项目 | 模型 | 输入 token | 输出 token | 估算 | 价格状态 |", "|---|---|---|---|---|---|"]
    for k in ("ocr", "grading"):
        c = e[k]
        L.append(f"| {'识别' if k == 'ocr' else '评分'} | {c['model']} | {c['input_tokens']:,} | {c['output_tokens']:,} | "
                 f"{c['cost']} {c['currency']} | {c['status']} |")
    if e["total"] is not None:
        L += ["", f"合计约 **{e['total']} {e['currency']}**（考虑思考长度与图片尺寸的不确定性，区间 {e['range'][0]}–{e['range'][1]}）。"]
    L += ["", "## 识别准确率（CER，越低越好）", ""]
    o = summary.get("ocr")
    if o:
        L += [f"- 样本 {o['n']}（成功 {o['ok']}），平均 CER **{o['mean_cer']}**，中位数 {o['median_cer']}",
              f"- 按类型：{json.dumps(o['by_kind'], ensure_ascii=False)}",
              f"- 合成样本 vs 真实样本：{json.dumps(o['by_source'], ensure_ascii=False)}",
              "- 合成图是电脑字体渲染，比真实学生字迹容易，**以真实样本的 CER 为准**。"]
    L += ["", "## 评分一致性（QWK，越高越好；人-人 QWK 是参照上限）", "",
          "| 数据集 | n | QWK 线性映射 | QWK 校准后 | Pearson | MAE | 人-人 QWK |", "|---|---|---|---|---|---|---|"]
    for name, r in (summary.get("grading") or {}).items():
        if "linear" not in r:
            L.append(f"| {name} | — | {r.get('skipped') or r.get('error', '')[:60]} | | | | |")
            continue
        lin, cal, hh = r["linear"], r["calibrated"] or {}, r["human_human"] or {}
        L.append(f"| {name} | {r['n']} | {lin.get('qwk')} | {cal.get('qwk')} | {lin.get('pearson')} | "
                 f"{lin.get('mae_points')} | {hh.get('qwk', '—')} |")
    L += ["", "对照（2026-10-07 mock 规则基线）：CFMS-34 QWK 0.271、ELLIPSE 0.052（校准后 0.218）、CFMS-34 人-人 0.569。", ""]
    (out / "REPORT.md").write_text("\n".join(L), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="首轮真实评估：CER + QWK + 费用估算")
    ap.add_argument("--provider", required=True, choices=list(PROVIDERS))
    ap.add_argument("--model", help="评分模型（覆盖默认）")
    ap.add_argument("--ocr-model", help="识别模型（覆盖默认）")
    ap.add_argument("--n-ocr", type=int, default=20, help="合成识别样本张数")
    ap.add_argument("--handwriting-dir", help="真实作业照片目录（每张图配同名 .txt 人工转写）")
    ap.add_argument("--n-grade", type=int, default=100, help="每个数据集抽多少篇做评分")
    ap.add_argument("--datasets", default="cfms34,ellipse")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--skip-ocr", action="store_true")
    ap.add_argument("--skip-grading", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="只估算费用，不调用任何模型")
    ap.add_argument("--yes", action="store_true", help="确认使用付费云端接口")
    args = ap.parse_args()
    args.datasets = [d for d in args.datasets.split(",") if d]

    env = setup_env(args, require_keys=not args.dry_run)
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out = ROOT / "eval" / "results" / f"first_real_{args.provider}_{stamp}"
    out.mkdir(parents=True, exist_ok=True)
    git = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    summary = {"provider": args.provider, "started": dt.datetime.now().isoformat(timespec="seconds"), "git": git,
               "args": {k: v for k, v in vars(args).items() if k != "yes"}}

    print("① 准备识别样本 …")
    ocr_items = [] if args.skip_ocr else build_ocr_set(args.n_ocr, ROOT / "samples" / "eval_ocr")
    if args.handwriting_dir and not args.skip_ocr:
        ocr_items += load_real_set(Path(args.handwriting_dir))
    n_grade_total = 0 if args.skip_grading else args.n_grade * sum(1 for d in args.datasets if (ROOT / DATASETS[d][0]).exists())
    print("② 估算费用 …")
    est = estimate(args, ocr_items, n_grade_total, grade_samples())
    summary["estimate"] = est
    print(json.dumps({k: est[k] for k in ("ocr", "grading", "total", "currency", "range", "calls")}, ensure_ascii=False, indent=1))
    if args.dry_run:
        write_report(out, summary)
        (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"（dry-run）报告：{out / 'REPORT.md'}")
        return 0
    if est["paid"] and not args.yes:
        print(f"\n这是付费接口，预计约 {est['total']} {est['currency']}。确认后加 --yes 重新运行。")
        return 2

    if ocr_items:
        print("③ 识别 …")
        rows = run_ocr(ocr_items, out / "ocr_raw.jsonl")
        ok = [r for r in rows if r.get("ok")]

        def agg(rs: list[dict]) -> dict:
            v = [r["cer"] for r in rs]
            return {"n": len(v), "mean": round(statistics.mean(v), 4), "median": round(statistics.median(v), 4)} if v else {}

        summary["ocr"] = {"n": len(rows), "ok": len(ok), "mean_cer": agg(ok).get("mean"), "median_cer": agg(ok).get("median"),
                          "by_kind": {k: agg([r for r in ok if r["kind"] == k]) for k in ("en", "zh", "math")},
                          "by_source": {"synthetic": agg([r for r in ok if r.get("synthetic")]),
                                        "real": agg([r for r in ok if not r.get("synthetic")])}}
    if not args.skip_grading:
        print("④ 评分 …")
        summary["grading"] = run_grading(args, env, out)
    summary["finished"] = dt.datetime.now().isoformat(timespec="seconds")
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    write_report(out, summary)
    print(f"\n完成。报告：{out / 'REPORT.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
