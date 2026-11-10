"""评估工具：数据集加载器（用合成夹具）与命令行流程（mock provider）。"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from eval.datasets import DatasetError, load_asap, load_cfms34, load_ellipse, load_generic
from eval.run_eval import to_grid

FIX = Path(__file__).resolve().parent.parent / "eval" / "fixtures"
ROOT = FIX.parent.parent


def test_load_asap():
    items = load_asap(FIX / "asap_sample.tsv")
    assert len(items) == 4
    s1 = load_asap(FIX / "asap_sample.tsv", essay_set=1)
    assert {i.id for i in s1} == {"1", "2", "4"}
    assert s1[0].score_min == 2 and s1[0].score_max == 12 and s1[0].extra["rater1"] == 4


def test_load_ellipse_and_cfms():
    e = load_ellipse(FIX / "ellipse_sample.csv")
    assert len(e) == 3 and e[0].score_step == 0.5 and e[1].human == 1.5
    assert load_ellipse(FIX / "ellipse_sample.csv", target="Grammar")[1].human == 1.0
    c = load_cfms34(FIX / "cfms34_sample.csv")
    assert c[2].human == 9 and c[2].score_max == 10
    r = load_cfms34(FIX / "cfms34_sample.csv", mode="rater")
    assert r[1].human == 2 and r[1].human2 == 3


def test_missing_columns_error(tmp_path):
    p = tmp_path / "x.csv"
    p.write_text("a,b\n1,2\n", encoding="utf-8")
    with pytest.raises(DatasetError) as e:
        load_generic(p, "text", "score", 0, 10)
    assert "缺少列" in str(e.value)


def test_to_grid():
    assert to_grid(1.0, 1, 5, 0.5) == 0 and to_grid(5.0, 1, 5, 0.5) == 8 and to_grid(3.26, 1, 5, 0.5) == 5
    assert to_grid(99, 0, 10, 1) == 10


def utf8_env(**extra: str) -> dict:
    """子进程统一用 UTF-8 输出（否则在 Windows 控制台下按 GBK 编码，读取时解码失败）。"""
    import os

    return {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", **extra}


def test_run_eval_cli_mock(tmp_path):
    out = tmp_path / "res"
    r = subprocess.run([sys.executable, "-m", "eval.run_eval", "--dataset", "ellipse", "--path",
                        str(FIX / "ellipse_sample.csv"), "--provider", "mock", "--out", str(out)],
                       cwd=ROOT, capture_output=True, text=True, encoding="utf-8", env=utf8_env())
    assert r.returncode == 0, r.stderr
    res = json.loads((out / "result.json").read_text(encoding="utf-8"))
    assert res["n_scored"] == 3 and res["n_failed"] == 0
    assert "qwk" in res["overall"]["linear"]
    stamp = json.loads((out / "_env_stamp.json").read_text(encoding="utf-8"))
    assert stamp["provider"] == "mock" and stamp["data_sha256"]
    lines = (out / "raw" / "predictions.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3


def test_run_eval_refuses_cloud_without_yes(tmp_path, monkeypatch):
    env = utf8_env(DASHSCOPE_API_KEY="dummy-not-used")
    r = subprocess.run([sys.executable, "-m", "eval.run_eval", "--dataset", "ellipse", "--path",
                        str(FIX / "ellipse_sample.csv"), "--provider", "openai_compat", "--out", str(tmp_path / "o")],
                       cwd=ROOT, capture_output=True, text=True, encoding="utf-8", env=env)
    assert r.returncode != 0 and "--yes" in (r.stderr + r.stdout)


def test_first_real_eval_dry_run_needs_no_key():
    env = utf8_env()
    for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "DASHSCOPE_API_KEY"):
        env.pop(k, None)
    r = subprocess.run([sys.executable, "scripts/first_real_eval.py", "--provider", "claude", "--dry-run", "--n-ocr", "2",
                        "--datasets", ""], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", env=env)
    assert r.returncode == 0, r.stderr[-500:]
    assert "dry-run" in r.stdout and '"currency": "USD"' in r.stdout
    # 真跑付费接口但没 key → 拒绝
    r = subprocess.run([sys.executable, "scripts/first_real_eval.py", "--provider", "claude", "--n-ocr", "1", "--yes"],
                       cwd=ROOT, capture_output=True, text=True, encoding="utf-8", env=env)
    assert r.returncode != 0 and "ANTHROPIC_API_KEY" in (r.stdout + r.stderr)
