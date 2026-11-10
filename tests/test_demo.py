"""本机一键演示：/api/demo/* 的开关与权限、自动演示用到的完整接口流程、run.py --demo / --stop 不留后台进程。"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.config import get_settings
from backend.demo import lifecycle
from backend.demo.sample import sample_text
from backend.main import app
from backend.pipeline.preprocess import load_upload
from backend.routers import demo as demo_router
from tests.conftest import token

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def demo_on(monkeypatch, world):  # noqa: ANN001
    monkeypatch.setattr(get_settings(), "demo_mode", True)
    monkeypatch.setattr(demo_router, "DEMO_PARENT", "t_parent")
    return world


def test_demo_endpoints_hidden_when_off(client, world):
    ht = token(client, "t_teacher")
    assert client.get("/api/health").json()["demo"] is False
    assert client.get("/api/demo/scenario", headers=ht).status_code == 404
    assert client.get(f"/api/demo/sample-photo?assignment_id={world['a_en']}", headers=ht).status_code == 404
    assert client.post("/api/demo/shutdown", headers={"X-Demo-Action": "shutdown"}).status_code == 404


def test_scenario_and_permissions(client, demo_on):
    w = demo_on
    assert client.get("/api/health").json()["demo"] is True
    sc = client.get("/api/demo/scenario", headers=token(client, "t_teacher")).json()
    assert sc["class_id"] == w["class1"] and sc["student_id"] == w["s_ok"]
    assert sc["assignment_id"] == w["a_en"]  # 一班没有语文作文，退回到最新的英语作文
    assert sc["counts"]["students"] == 3
    assert "password" not in json.dumps(sc)
    # 家长不能看场景；另一个班的老师看不到演示家长的孩子
    assert client.get("/api/demo/scenario", headers=token(client, "t_parent")).status_code == 403
    assert client.get("/api/demo/scenario", headers=token(client, "t_teacher2")).status_code == 404
    # 示例照片：本班家长可取，别的班家长不行
    assert client.get(f"/api/demo/sample-photo?assignment_id={w['a_en']}", headers=token(client, "t_parent2")).status_code == 403


def test_auto_demo_flow(client, demo_on):
    """和前端 demo_auto.js 相同的调用顺序：家长取照片并上传 → 识别评分 → 老师改分 → 家长看到 → 画像更新。"""
    w = demo_on
    ht, hp = token(client, "t_teacher"), token(client, "t_parent")
    sc = client.get("/api/demo/scenario", headers=ht).json()
    prof0 = client.get(f"/api/students/{sc['student_id']}/profile", headers=hp).json()

    r = client.get(f"/api/demo/sample-photo?assignment_id={sc['assignment_id']}", headers=hp)
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    expected, lang = sample_text("英语作文", "english")
    assert lang == "en" and load_upload(r.content).embedded_text == expected

    up = client.post("/api/submissions", headers=hp,
                     data={"assignment_id": sc["assignment_id"], "student_id": sc["student_id"], "client_token": "demo-flow-1"},
                     files=[("files", ("homework.png", r.content, "image/png"))])
    assert up.status_code == 202, up.text
    sid = up.json()["id"]
    d = client.get(f"/api/submissions/{sid}", headers=ht).json()
    assert d["status"] == "ai_done" and d["ocr_text"] == expected
    assert d["preprocess_meta"]["pages"][0]["paper_detected"]
    ai = d["ai_result"]

    room = sorted(ai["criteria"], key=lambda c: c["score"] - c["max_score"])
    target = room[0]
    delta = min(2, int((target["max_score"] - target["score"]) * 2) / 2)
    assert delta > 0
    body = {"criteria": [{"id": c["id"], "score": c["score"] + (delta if c["id"] == target["id"] else 0)} for c in ai["criteria"]],
            "deductions": ai["deductions"], "errors": ai["errors"], "comment": "老师补充", "note": "自动演示"}
    rv = client.post(f"/api/submissions/{sid}/review", headers=ht, json=body)
    assert rv.status_code == 200, rv.text
    assert rv.json()["final_result"]["total_score"] == pytest.approx(min(ai["max_score"], ai["total_score"] + delta))

    pd = client.get(f"/api/submissions/{sid}", headers=hp).json()
    assert pd["status"] == "reviewed" and pd["ai_result"] is None
    prof1 = client.get(f"/api/students/{sc['student_id']}/profile", headers=hp).json()
    assert prof1["submission_count"] == prof0["submission_count"] + 1
    assert any(t["submission_id"] == sid and t["reviewed"] for t in prof1["trend"])


def test_shutdown_only_local_with_header(demo_on, monkeypatch):
    calls = []
    monkeypatch.setattr(lifecycle, "request_shutdown", lambda *a, **k: calls.append(1))
    remote = TestClient(app)  # 客户端地址是 "testclient"，不是本机回环
    assert remote.post("/api/demo/shutdown", headers={"X-Demo-Action": "shutdown"}).status_code == 403
    local = TestClient(app, client=("127.0.0.1", 50123))
    assert local.post("/api/demo/shutdown").status_code == 400  # 没有自定义头（防止别的网页跨站触发）
    assert not calls
    r = local.post("/api/demo/shutdown", headers={"X-Demo-Action": "shutdown"})
    assert r.status_code == 200 and calls == [1]


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def test_run_demo_then_stop_leaves_no_process(tmp_path):
    """真实启动 run.py --demo（不开浏览器、无图快速种子），再用 run.py --stop 关闭：进程退出、状态文件删除。"""
    import run  # 项目根目录的 run.py

    env = {**os.environ, "EDU_DATA_DIR": str(tmp_path), "PYTHONIOENCODING": "utf-8"}
    env.pop("EDU_PIPELINE_SYNC", None)
    real_db = tmp_path / "edu_eval.db"  # 模拟正式数据：演示的重置绝不能碰它
    real_db.write_bytes(b"REAL-DATA")
    port = _free_port()
    log = (tmp_path / "server.log").open("w", encoding="utf-8")
    proc = subprocess.Popen([sys.executable, "run.py", "--demo", "--no-browser", "--no-images", "--port", str(port)],
                            cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    state = tmp_path / run.STATE_NAME
    try:
        h = None
        for _ in range(240):
            if proc.poll() is not None:
                break
            if state.exists():
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1) as r:
                        h = json.loads(r.read())
                        break
                except OSError:
                    pass
            time.sleep(0.25)
        assert h is not None, (tmp_path / "server.log").read_text(encoding="utf-8")[-2000:]
        assert h["demo"] is True and h["ocr"] == "mock" and h["grader"] == "mock"
        st = json.loads(state.read_text(encoding="utf-8"))
        # .venv 的 python.exe 是启动器，真正的解释器是它的子进程，所以状态文件里记的是子进程 pid
        assert st["port"] == port and run.pid_alive(st["pid"])

        out = subprocess.run([sys.executable, "run.py", "--stop"], cwd=ROOT, env=env, capture_output=True,
                             text=True, encoding="utf-8", timeout=60)
        assert out.returncode == 0, out.stdout + out.stderr
        proc.wait(timeout=20)
        assert not run.pid_alive(proc.pid) and not run.pid_alive(st["pid"])
        assert not state.exists()
        again = subprocess.run([sys.executable, "run.py", "--stop"], cwd=ROOT, env=env, capture_output=True,
                               text=True, encoding="utf-8", timeout=30)
        assert again.returncode == 0 and "没有正在运行" in again.stdout

        # 演示期间产生的数据（这里用一个多出来的文件模拟），下次启动时应被恢复为种子状态
        junk = tmp_path / run.DEMO_DIR_NAME / "uploads" / "junk.txt"
        junk.parent.mkdir(parents=True, exist_ok=True)
        junk.write_text("x", encoding="utf-8")
        port2 = _free_port()
        proc2 = subprocess.Popen([sys.executable, "run.py", "--demo", "--no-browser", "--no-images", "--port", str(port2)],
                                 cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
        try:
            for _ in range(240):
                if state.exists():
                    break
                time.sleep(0.25)
            assert state.exists()
            assert not junk.exists(), "第二次启动应把演示数据恢复为种子状态"
            assert (tmp_path / run.DEMO_DIR_NAME / "edu_eval.db").exists()
        finally:
            subprocess.run([sys.executable, "run.py", "--stop"], cwd=ROOT, env=env, capture_output=True, timeout=60)
            proc2.wait(timeout=20)
        assert real_db.read_bytes() == b"REAL-DATA"  # 正式数据没有被动过
    finally:
        if proc.poll() is None:
            proc.kill()
        log.close()


def test_reset_guard_refuses_non_demo_dirs(tmp_path, monkeypatch):
    """重置只删除基础目录下带标记文件的 demo / demo_seed，其他目录一律拒绝。"""
    import run

    monkeypatch.setattr(run, "BASE_DIR", tmp_path)
    unmarked = tmp_path / run.DEMO_DIR_NAME
    (unmarked / "keep.txt").parent.mkdir(parents=True)
    (unmarked / "keep.txt").write_text("x", encoding="utf-8")
    with pytest.raises(SystemExit):
        run._safe_rmtree(unmarked)  # 没有标记文件
    assert (unmarked / "keep.txt").exists()
    other = tmp_path / "uploads"
    other.mkdir()
    (other / run.DEMO_MARKER).write_text("images", encoding="utf-8")
    with pytest.raises(SystemExit):
        run._safe_rmtree(other)  # 有标记但不是演示目录
    assert other.exists()
    (unmarked / run.DEMO_MARKER).write_text("images", encoding="utf-8")
    run._safe_rmtree(unmarked)
    assert not unmarked.exists()
