"""真实启动服务的端到端演示：家长上传作业照片 → 识别 → 打分 → 老师复核 → 画像更新。日志落盘。

    .venv\\Scripts\\python scripts\\e2e_demo.py            # 默认 mock provider，不联网
    .venv\\Scripts\\python scripts\\e2e_demo.py --keep     # 结束后保留服务数据目录

步骤：
 0. 生成测试图片；用独立数据目录写入演示数据（无图快速模式）；子进程启动 uvicorn（真实 HTTP）
 1. 教师布置新作业
 2. 家长登录，查看孩子的同意状态与画像（基线）
 3. 家长上传手机拍的作业照片（multipart，与浏览器相同的接口）
 4. 轮询直到识别+评分完成，核对识别文本、分数结构、“仅供参考”提示
 5. 越权检查：家长给非自己孩子上传 → 403
 6. 教师在待复核队列中找到该作业，修改分项分与评语并保存
 7. 家长再次查看：看到教师复核后的分数；画像更新（新数据点、未复核数减少）
 8. 班级看板与 AI-教师一致性
所有请求/响应摘要写入 logs/e2e_<时间>/e2e.log，汇总写入 summary.json，服务日志写入 server.log。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Log:
    def __init__(self, path: Path) -> None:
        self.f = path.open("w", encoding="utf-8")
        self.t0 = time.time()

    def __call__(self, step: str, msg: str, data: object | None = None) -> None:
        line = f"[{time.time() - self.t0:7.2f}s] {step:<14} {msg}"
        print(line)
        self.f.write(line + "\n")
        if data is not None:
            self.f.write(json.dumps(data, ensure_ascii=False, indent=1, default=str)[:4000] + "\n")
        self.f.flush()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()
    run_dir = ROOT / "logs" / f"e2e_{dt.datetime.now():%Y%m%d_%H%M%S}"
    data_dir = run_dir / "data"
    run_dir.mkdir(parents=True)
    log = Log(run_dir / "e2e.log")
    summary: dict = {"started_at": dt.datetime.now().isoformat(), "checks": {}}

    def check(name: str, ok: bool, detail: object = "") -> None:
        summary["checks"][name] = {"ok": bool(ok), "detail": detail}
        log("CHECK", f"{'通过' if ok else '失败'}  {name}  {detail if isinstance(detail, str) else ''}")

    # 0. 准备
    from scripts.make_test_images import main as make_images

    manifest = make_images()
    photo = ROOT / "samples" / "generated" / "en_essay_photo.png"
    expected_text = manifest[photo.name]["text"]
    env = {**os.environ, "EDU_DATA_DIR": str(data_dir), "PYTHONIOENCODING": "utf-8"}
    for k in ("ANTHROPIC_API_KEY", "DASHSCOPE_API_KEY", "OPENAI_COMPAT_BASE_URL"):
        if os.environ.get(k):
            log("ENV", f"检测到 {k}，本次将使用真实 provider（会产生费用）")
    t = time.time()
    subprocess.run([sys.executable, "-m", "backend.seed", "--reset", "--no-images"], cwd=ROOT, env=env, check=True,
                   capture_output=True)
    log("SEED", f"演示数据写入完成（{time.time() - t:.1f}s）")
    port = free_port()
    server_log = (run_dir / "server.log").open("w", encoding="utf-8")
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1", "--port", str(port)],
                            cwd=ROOT, env=env, stdout=server_log, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    try:
        c = httpx.Client(base_url=base, timeout=60)
        for _ in range(60):
            try:
                if c.get("/api/health").status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.5)
        health = c.get("/api/health").json()
        log("SERVER", f"服务已启动 {base}", health)
        summary["providers"] = {"ocr": health["ocr"], "grader": health["grader"]}

        def login(u: str, p: str) -> dict:
            r = c.post("/api/auth/login", data={"username": u, "password": p})
            r.raise_for_status()
            return {"Authorization": f"Bearer {r.json()['access_token']}"}

        # 1. 教师布置作业
        ht = login("teacher_wang", "teacher12345")
        cls = c.get("/api/classes", headers=ht).json()[0]
        from backend.demo import corpus

        spec = corpus.EN_PROMPTS["invite"]
        r = c.post("/api/assignments", headers=ht, json={
            "class_id": cls["id"], "title": "英语书面表达：邀请外教参加英语节（E2E）", "subject": "english",
            "rubric_key": "english_essay_zhongkao", "prompt": spec["prompt"],
            "reference": {"key_points": spec["key_points"], "target_words": [80, 100]}})
        check("教师布置作业", r.status_code == 201, f"HTTP {r.status_code}")
        asg = r.json()
        log("TEACHER", f"布置作业 #{asg['id']} 满分 {asg['max_score']}")

        # 2. 家长基线
        hp = login("parent_zhang", "parent12345")
        kids = c.get("/api/students", headers=hp).json()
        kid = kids[0]
        log("PARENT", f"孩子：{kid['name']}，同意状态 {kid['consents']}")
        check("家长已签署基本同意", kid["consents"]["basic"])
        prof0 = c.get(f"/api/students/{kid['id']}/profile", headers=hp).json()
        log("PROFILE0", f"基线：{prof0['submission_count']} 份作业，未复核 {prof0['unreviewed_count']}",
            {"subjects": prof0["subjects"]})

        # 3. 上传
        t_up = time.time()
        with photo.open("rb") as f:
            r = c.post("/api/submissions", headers=hp, data={"assignment_id": asg["id"], "student_id": kid["id"]},
                       files=[("files", (photo.name, f, "image/png"))])
        check("家长上传照片", r.status_code == 202, f"HTTP {r.status_code}，{photo.stat().st_size // 1024} KB")
        sub = r.json()
        log("UPLOAD", f"提交 #{sub['id']} 状态 {sub['status']}")

        # 4. 轮询
        d = {}
        for _ in range(240):
            d = c.get(f"/api/submissions/{sub['id']}", headers=hp).json()
            if d["status"] not in ("queued", "processing"):
                break
            time.sleep(0.5)
        latency = time.time() - t_up
        log("PIPELINE", f"处理完成：{d['status']}，耗时 {latency:.1f}s", {
            "preprocess": d["preprocess_meta"], "ocr_confidence": d["ocr_confidence"],
            "final_result": {k: d["final_result"][k] for k in ("total_score", "max_score", "band", "criteria",
                                                               "needs_review", "comment")} if d.get("final_result") else None})
        summary["pipeline_seconds"] = round(latency, 2)
        check("识别+评分完成", d["status"] == "ai_done", d.get("error", ""))
        check("识别文本与标准文本一致（mock 读内嵌文本）", d["ocr_text"] == expected_text,
              f"{len(d['ocr_text'])} 字符")
        pm = d["preprocess_meta"]["pages"][0]
        check("预处理检测到纸张并矫正", pm["paper_detected"], f"纸张占比 {pm['paper_area_ratio']}，纠偏 {pm['deskew_angle']}°")
        fr = d["final_result"]
        crit_sum = sum(x["score"] for x in fr["criteria"]) - sum(x["points"] for x in fr["deductions"])
        check("分数结构合法（分项和−扣分=总分，0≤总分≤满分）",
              abs(max(0, crit_sum) - fr["total_score"]) < 1e-6 and 0 <= fr["total_score"] <= fr["max_score"],
              f"{fr['total_score']}/{fr['max_score']} {fr['band']}")
        check("界面提示 AI 仅供参考", "仅供参考" in d["disclaimer"])
        check("家长看不到 AI 原始结果与内部备注", d["ai_result"] is None)
        n_err_pos = sum(1 for e in fr["errors"] if e["start"] is not None)
        check("错误批注可在原文中定位", all(d["ocr_text"][e["start"]:e["end"]] == e["quote"] for e in fr["errors"]
                                     if e["start"] is not None), f"{n_err_pos}/{len(fr['errors'])} 条有位置")
        img = c.get(f"/api/submissions/{sub['id']}/pages/1/image", headers=hp)
        check("处理后图片可取（需鉴权）", img.status_code == 200 and c.get(f"/api/submissions/{sub['id']}/pages/1/image").status_code == 401)

        # 5. 越权
        others = [s for s in c.get("/api/students", headers=ht).json() if s["id"] not in {k["id"] for k in kids}]
        with photo.open("rb") as f:
            r = c.post("/api/submissions", headers=hp, data={"assignment_id": asg["id"], "student_id": others[0]["id"]},
                       files=[("files", (photo.name, f, "image/png"))])
        check("家长不能给别人的孩子上传", r.status_code == 403, f"HTTP {r.status_code}")

        # 6. 教师复核
        queue = c.get("/api/submissions?status=ai_done", headers=ht).json()
        check("作业进入教师待复核队列", any(s["id"] == sub["id"] for s in queue), f"队列 {len(queue)} 份")
        dt_ = c.get(f"/api/submissions/{sub['id']}", headers=ht).json()
        ai = dt_["ai_result"]
        new_crit = []
        for cr in ai["criteria"]:
            s = cr["score"] + (1 if cr["id"] == "content" else -0.5 if cr["id"] == "language" else 0)
            new_crit.append({"id": cr["id"], "score": max(0, min(cr["max_score"], s))})
        body = {"criteria": new_crit, "deductions": ai["deductions"], "errors": ai["errors"],
                "comment": "要点齐全，格式规范。注意第三人称单数和冠词，订正后把邀请信再读一遍。", "note": "E2E：内容给满，语言略扣"}
        r = c.post(f"/api/submissions/{sub['id']}/review", headers=ht, json=body)
        check("教师复核保存", r.status_code == 200, f"HTTP {r.status_code}")
        rv = r.json()
        log("REVIEW", f"AI {ai['total_score']} → 教师 {rv['final_result']['total_score']}（{rv['final_result']['band']}）")
        hist = c.get(f"/api/submissions/{sub['id']}/reviews", headers=ht).json()
        check("复核记录留痕", len(hist) == 1 and hist[0]["ai_total"] == ai["total_score"])

        # 7. 家长再看 + 画像更新
        d2 = c.get(f"/api/submissions/{sub['id']}", headers=hp).json()
        check("家长看到教师复核后的分数", d2["status"] == "reviewed" and
              d2["final_result"]["total_score"] == rv["final_result"]["total_score"],
              f"{d2['final_result']['total_score']}")
        prof1 = c.get(f"/api/students/{kid['id']}/profile", headers=hp).json()
        last = prof1["trend"][-1]
        log("PROFILE1", f"更新后：{prof1['submission_count']} 份作业，未复核 {prof1['unreviewed_count']}",
            {"subjects": prof1["subjects"], "last_point": last, "summary": prof1["summary"]})
        check("画像新增数据点（已复核）", prof1["submission_count"] == prof0["submission_count"] + 1
              and last["submission_id"] == sub["id"] and last["reviewed"])
        check("画像英语均分随复核更新",
              prof1["subjects"]["english"]["count"] == prof0["subjects"]["english"]["count"] + 1,
              f"{prof0['subjects']['english']['avg_ratio']} → {prof1['subjects']['english']['avg_ratio']}")

        # 8. 看板与一致性
        dash = c.get(f"/api/classes/{cls['id']}/dashboard", headers=ht).json()
        ag = dash["agreement"]
        log("DASHBOARD", f"待复核 {dash['pending_review']}，共性问题 {[e['label'] for e in dash['common_errors'][:3]]}",
            {"agreement": ag})
        check("班级看板含 AI-教师一致性", "english_essay_zhongkao" in ag, json.dumps(
            {k: {m: v.get(m) for m in ("n", "qwk", "mae", "within1_band")} for k, v in ag.items()}, ensure_ascii=False))
        summary["agreement"] = ag
        r = c.get("/")
        check("前端页面由同一服务托管", r.status_code == 200 and "作业智评" in r.text)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        server_log.close()
        if not args.keep:
            shutil.rmtree(data_dir, ignore_errors=True)
    ok = all(v["ok"] for v in summary["checks"].values())
    summary["passed"] = sum(v["ok"] for v in summary["checks"].values())
    summary["total"] = len(summary["checks"])
    summary["ok"] = ok
    (run_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    log("DONE", f"{summary['passed']}/{summary['total']} 项检查通过；日志目录 {run_dir}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
