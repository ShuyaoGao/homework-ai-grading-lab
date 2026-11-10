"""公开作文评分数据集加载器。所有加载器返回统一的 EssayItem 列表。

数据集都不随仓库分发（许可证限制 / 体积），请按 eval/DATASETS.md 自行下载到 eval/datasets/（已 gitignore）。
列名与分值范围依据数据集官方说明整理（核实日期 2026-10-07），未能核实之处在注释中标明，
加载时会校验必需列，缺列时报错并列出实际列名，便于调整。
"""
from __future__ import annotations

import csv
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class EssayItem:
    id: str
    text: str
    human: float  # 主人工分（用于和 AI 比较）
    score_min: float
    score_max: float
    score_step: float = 1.0
    prompt: str = ""
    group: str = ""  # 分组（如 ASAP essay_set、题目），一致性按组计算
    human2: float | None = None  # 第二评分员（用于人-人一致性基线）
    extra: dict = field(default_factory=dict)


class DatasetError(ValueError):
    pass


def _require(cols: list[str], need: list[str], path: Path) -> None:
    miss = [c for c in need if c not in cols]
    if miss:
        raise DatasetError(f"{path.name} 缺少列 {miss}；实际列：{cols[:30]}")


def _read_csv(path: Path, delimiter: str = ",", encodings=("utf-8-sig", "utf-8", "gb18030", "latin-1")) -> list[dict]:
    last = None
    for enc in encodings:
        try:
            with path.open(encoding=enc, newline="") as f:
                return list(csv.DictReader(f, delimiter=delimiter))
        except UnicodeDecodeError as e:
            last = e
    raise DatasetError(f"无法解码 {path}：{last}")


def _f(v) -> float | None:  # noqa: ANN001
    try:
        if v is None or str(v).strip() == "":
            return None
        return float(v)
    except ValueError:
        return None


# ------------------------------------------------------------------ ASAP（Kaggle asap-aes）

# domain1_score 的范围（多篇论文一致）。注意 1/7/8 组的 domain1 是两位评分员的合成分，
# 与单个评分员列（rater1_domain1）尺度不同，人-人一致性只能用 rater1 vs rater2 在评分员尺度上算。
ASAP_RANGES = {1: (2, 12), 2: (1, 6), 3: (0, 3), 4: (0, 3), 5: (0, 4), 6: (0, 4), 7: (0, 30), 8: (0, 60)}
ASAP_PROMPTS = {
    1: "Write a letter to your local newspaper stating your opinion on the effects computers have on people.",
    2: "Write a persuasive essay to a newspaper reflecting your views on censorship in libraries.",
    7: "Write a story about a time when you were patient.",
    8: "Tell a true story in which laughter was one element.",
}  # 3–6 组为基于阅读材料的回答（source-dependent），题目原文较长，这里不内置


def load_asap(path: str | Path, essay_set: int | None = None) -> list[EssayItem]:
    """training_set_rel3.tsv（制表符分隔，常见为 latin-1 编码；文中人名等被替换为 @PERSON1 之类的标记）。"""
    path = Path(path)
    rows = _read_csv(path, delimiter="\t", encodings=("latin-1",)) if path.suffix == ".tsv" else _read_csv(path)
    if not rows:
        return []
    _require(list(rows[0]), ["essay_id", "essay_set", "essay", "domain1_score"], path)
    out = []
    for r in rows:
        s = int(r["essay_set"])
        if essay_set is not None and s != essay_set:
            continue
        h = _f(r["domain1_score"])
        if h is None:
            continue
        lo, hi = ASAP_RANGES[s]
        out.append(EssayItem(id=str(r["essay_id"]), text=r["essay"], human=h, score_min=lo, score_max=hi,
                             prompt=ASAP_PROMPTS.get(s, ""), group=f"set{s}",
                             extra={"rater1": _f(r.get("rater1_domain1")), "rater2": _f(r.get("rater2_domain1"))}))
    return out


def load_asap_plus_plus(asap_items: list[EssayItem], csv_path: str | Path) -> list[EssayItem]:
    """ASAP++（Mathias & Bhattacharyya, LREC 2018）：Prompt-N.csv 只有属性分，需要按作文 ID 与 ASAP 合并。
    属性列（Content/Organization/Word Choice/Sentence Fluency/Conventions 或 Content/Prompt Adherence/
    Language/Narrativity）原样放进 extra["traits"]。ID 列名未核实，取第一个名字含 id 的列。"""
    path = Path(csv_path)
    rows = _read_csv(path)
    if not rows:
        return asap_items
    cols = list(rows[0])
    id_col = next((c for c in cols if "id" in c.lower()), None)
    if id_col is None:
        raise DatasetError(f"{path.name} 找不到 ID 列；实际列：{cols}")
    traits = {str(int(float(r[id_col]))): {k: _f(v) for k, v in r.items() if k != id_col and _f(v) is not None}
              for r in rows if _f(r[id_col]) is not None}
    for it in asap_items:
        if it.id in traits:
            it.extra["traits"] = traits[it.id]
    return asap_items


# ------------------------------------------------------------------ ELLIPSE（英语学习者作文，CC BY-NC-SA 4.0）

ELLIPSE_TRAITS = ["Overall", "Cohesion", "Syntax", "Vocabulary", "Phraseology", "Grammar", "Conventions"]


def load_ellipse(path: str | Path, target: str = "Overall") -> list[EssayItem]:
    """GitHub 版 ELLIPSE_Final_github_train.csv（含 Overall 与 6 个分项，1.0–5.0，步长 0.5，为两位评分员平均）。
    也兼容 Kaggle Feedback Prize-ELL 的 train.csv（小写列名、无 Overall，此时 target 需选分项，如 cohesion）。"""
    path = Path(path)
    rows = _read_csv(path)
    if not rows:
        return []
    cols = list(rows[0])
    text_col = "full_text"
    id_col = "text_id_kaggle" if "text_id_kaggle" in cols else "text_id"
    tgt = target if target in cols else target.lower()
    _require(cols, [text_col, id_col, tgt], path)
    out = []
    for r in rows:
        h = _f(r[tgt])
        if h is None:
            continue
        out.append(EssayItem(id=str(r[id_col]), text=r[text_col], human=h, score_min=1.0, score_max=5.0, score_step=0.5,
                             prompt=r.get("prompt", ""), group=r.get("prompt", "") or "all",
                             extra={t: _f(r.get(t)) for t in ELLIPSE_TRAITS if t in r}))
    return out


# ------------------------------------------------------------------ PERSUADE 2.0（CC BY-NC-SA 4.0）

def load_persuade(path: str | Path, score_col: str = "holistic_essay_score") -> list[EssayItem]:
    """PERSUADE 2.0 语料：整体分 1–6。整体分列名未核实（常见为 holistic_essay_score），可用 score_col 指定。"""
    path = Path(path)
    rows = _read_csv(path)
    if not rows:
        return []
    cols = list(rows[0])
    text_col = "full_text" if "full_text" in cols else "text"
    id_col = "essay_id_comp" if "essay_id_comp" in cols else ("essay_id" if "essay_id" in cols else cols[0])
    _require(cols, [text_col, score_col], path)
    seen, out = set(), []
    for r in rows:  # 该语料按“论证片段”逐行展开，同一作文会出现多行，按 id 去重
        if r[id_col] in seen:
            continue
        seen.add(r[id_col])
        h = _f(r[score_col])
        if h is None:
            continue
        out.append(EssayItem(id=str(r[id_col]), text=r[text_col], human=h, score_min=1, score_max=6,
                             prompt=r.get("assignment", "") or r.get("prompt_name", ""), group=r.get("prompt_name", "all")))
    return out


# ------------------------------------------------------------------ CFMS-34（中文小学作文，CC BY 4.0）

def load_cfms34(path: str | Path, mode: str = "sum") -> list[EssayItem]:
    """HiFTS 仓库 CFMS-34/{core,full}/{train,dev,test}.csv：列 编号、作文内容、作文题目、总分_1、总分_2（各 0–5）。

    mode="sum"：按 HiFTS 论文做法两位评分员相加（0–10），human2 为空；
    mode="rater"：human=评分员1、human2=评分员2（0–5），可计算人-人一致性基线。
    注意 test 集只保留两位评分员总分一致的作文，人-人一致性应在 train/dev 上看。"""
    path = Path(path)
    rows = _read_csv(path)
    if not rows:
        return []
    _require(list(rows[0]), ["编号", "作文内容", "总分_1", "总分_2"], path)
    out = []
    for r in rows:
        a, b = _f(r["总分_1"]), _f(r["总分_2"])
        if a is None or b is None:
            continue
        title = r.get("作文题目", "")
        if mode == "sum":
            out.append(EssayItem(id=str(r["编号"]), text=r["作文内容"], human=a + b, score_min=0, score_max=10,
                                 prompt=title, group="all"))
        else:
            out.append(EssayItem(id=str(r["编号"]), text=r["作文内容"], human=a, human2=b, score_min=0, score_max=5,
                                 prompt=title, group="all"))
    return out


# ------------------------------------------------------------------ 通用 CSV / JSONL

def load_generic(path: str | Path, text_col: str, score_col: str, score_min: float, score_max: float,
                 id_col: str | None = None, prompt_col: str | None = None, score2_col: str | None = None,
                 step: float = 1.0) -> list[EssayItem]:
    """自有数据：CSV 或 JSONL，指定文本列、分数列与分值范围（例如学校历次考试的作文与阅卷分）。"""
    path = Path(path)
    if path.suffix == ".jsonl":
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    else:
        rows = _read_csv(path)
    if not rows:
        return []
    _require(list(rows[0]), [text_col, score_col], path)
    out = []
    for i, r in enumerate(rows):
        h = _f(r[score_col])
        if h is None:
            continue
        out.append(EssayItem(id=str(r[id_col]) if id_col else str(i), text=str(r[text_col]), human=h, score_min=score_min,
                             score_max=score_max, score_step=step, prompt=str(r.get(prompt_col, "")) if prompt_col else "",
                             human2=_f(r.get(score2_col)) if score2_col else None, group="all"))
    return out


# ------------------------------------------------------------------ 本系统数据库：AI 初评 vs 教师复核

def load_app_db(db_path: str | Path) -> list[dict]:
    """读取本系统 SQLite 中每份作业最新一次教师复核（只读），返回 AI 分与教师分配对。"""
    con = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)
    try:
        rows = con.execute("""SELECT r.submission_id, r.rubric_key, r.ai_total, r.teacher_total, r.max_score,
                                     r.ai_band, r.teacher_band
                              FROM reviews r JOIN (SELECT submission_id, MAX(id) mid FROM reviews GROUP BY submission_id) m
                              ON r.id = m.mid WHERE r.ai_total IS NOT NULL""").fetchall()
    finally:
        con.close()
    return [dict(zip(["submission_id", "rubric_key", "ai", "teacher", "max_score", "ai_band", "teacher_band"], r))
            for r in rows]


LOADERS = {"asap": load_asap, "ellipse": load_ellipse, "persuade": load_persuade, "cfms34": load_cfms34,
           "generic": load_generic}
