"""录制静态演示数据：在临时目录里用种子数据跑一遍真实后端（进程内 TestClient，强制 mock，不联网、不花钱），
把前端会调用的 GET 接口的返回值和作业图片录下来，供 GitHub Pages 上的静态演示（tools/static_demo/mock.js）回放。

  .venv\\Scripts\\python tools\\static_demo\\record.py        # 输出到 build/static_demo/（已 gitignore）

录三个时刻的快照：
  before  种子数据的初始状态
  mid     自动演示里家长上传了一份作业、AI 已初评、老师还没复核
  after   老师按自动演示的同一规则复核改分之后
只记录与 before 不同的条目；图片重新压缩成较小的 JPEG（只用于网页展示）。
数据全部来自 data/demo_seed（程序合成的演示数据），不读取 data/edu_eval.db 和 data/uploads。
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SEED = ROOT / "data" / "demo_seed"
OUT = ROOT / "build" / "static_demo"

ACCOUNTS = {"teacher": ("teacher_wang", "teacher12345"), "parent": ("parent_zhang", "parent12345"),
            "student": ("student_zxm", "student12345"), "admin": ("admin", "admin12345")}


def setup_env() -> Path:
    if not (SEED / "edu_eval.db").exists():
        raise SystemExit("没有种子快照 data/demo_seed：先运行一次 启动演示.bat（或 run.py --demo）生成")
    tmp = Path(tempfile.mkdtemp(prefix="edu_static_rec_"))
    data = tmp / "data"
    shutil.copytree(SEED, data)
    os.environ["EDU_DATA_DIR"] = str(data)
    os.environ["EDU_DEMO_MODE"] = "1"
    os.environ["EDU_PIPELINE_SYNC"] = "1"
    os.environ["OCR_PROVIDER"] = "mock"
    os.environ["GRADER_PROVIDER"] = "mock"
    os.environ.pop("EDU_DEMO_LAN_URL", None)
    for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY", "DASHSCOPE_API_KEY",
              "OPENAI_COMPAT_BASE_URL", "OPENAI_COMPAT_OCR_BASE_URL", "OPENAI_COMPAT_GRADER_BASE_URL"):
        os.environ.pop(k, None)
    sys.path.insert(0, str(ROOT))
    return tmp


class Recorder:
    def __init__(self, client):
        self.c = client
        self.tokens: dict[str, str] = {}
        self.users: dict[str, dict] = {}
        self.blobs: dict[str, object] = {}     # hash -> JSON 值
        self.images: dict[str, str] = {}       # 原始字节 hash -> 输出文件名
        self.img_bytes = 0

    def login(self):
        for role, (u, p) in ACCOUNTS.items():
            r = self.c.post("/api/auth/login", data={"username": u, "password": p})
            r.raise_for_status()
            d = r.json()
            self.tokens[role] = d["access_token"]
            self.users[role] = d["user"]

    def _h(self, role):
        return {"Authorization": f"Bearer {self.tokens[role]}"}

    def put_json(self, v) -> str:
        s = json.dumps(v, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        h = hashlib.sha1(s.encode("utf-8")).hexdigest()[:12]
        self.blobs[h] = v
        return h

    def put_image(self, data: bytes, keep_png: bool = False) -> str:
        h = hashlib.sha1(data).hexdigest()[:12]
        if h in self.images:
            return self.images[h]
        from PIL import Image
        if keep_png:
            name = f"{h}.png"
            (OUT / "img" / name).write_bytes(data)
        else:
            im = Image.open(io.BytesIO(data))
            im = im.convert("L") if im.mode in ("L", "LA") else im.convert("RGB")
            scale = min(1.0, 1200 / max(im.size))
            if scale < 1:
                im = im.resize((round(im.size[0] * scale), round(im.size[1] * scale)), Image.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=72, optimize=True, progressive=True)
            name = f"{h}.jpg"
            (OUT / "img" / name).write_bytes(buf.getvalue())
        self.img_bytes += (OUT / "img" / name).stat().st_size
        self.images[h] = name
        return name

    def get(self, role, path, snap, image=False, keep_png=False):
        r = self.c.get(path, headers=self._h(role))
        if image and r.status_code == 200:
            snap[path] = [200, "img:" + self.put_image(r.content, keep_png)]
            return None
        try:
            body = r.json()
        except ValueError:
            body = {"detail": r.text}
        snap[path] = [r.status_code, self.put_json(body)]
        return body if r.status_code == 200 else None

    def crawl(self) -> dict:
        """按前端实际会发的请求，逐角色录一遍。返回 {role: {key: [status, hash|img:file]}}。"""
        out = {}
        for role in ACCOUNTS:
            snap: dict = {}
            g = lambda p, **kw: self.get(role, p, snap, **kw)  # noqa: E731
            g("/api/auth/me")
            for p in ("/api/meta/providers", "/api/meta/error-types", "/api/users", "/api/admin/audit?limit=30",
                      "/api/agreement", "/api/assignments"):
                g(p)
            sc = g("/api/demo/scenario")
            classes = g("/api/classes") or []
            for c in classes:
                g(f"/api/classes/{c['id']}/students")
                g(f"/api/classes/{c['id']}/dashboard")
            for s in g("/api/students") or []:
                for suf in ("", "/profile", "/consents", "/export"):
                    g(f"/api/students/{s['id']}{suf}")
            for rb in g("/api/rubrics") or []:
                g(f"/api/rubrics/{rb['key']}")
            subs = self.get(role, "/api/submissions?limit=1000", snap) or []
            for s in subs:
                d = g(f"/api/submissions/{s['id']}")
                g(f"/api/submissions/{s['id']}/reviews")
                if d:
                    for pg in d.get("pages", []):
                        for kind in ("original", "processed"):
                            g(f"/api/submissions/{s['id']}/pages/{pg['page_no']}/image?kind={kind}", image=True)
                    if d.get("has_stitched"):
                        g(f"/api/submissions/{s['id']}/stitched", image=True)
            if sc is None:   # 家长/学生看不到演示场景，但自动演示里家长要取示例照片
                sc = self.scenario
            if sc:
                g(f"/api/demo/sample-photo?assignment_id={sc['assignment_id']}", image=True, keep_png=True)
            out[role] = snap
        return out


def review_body(ai: dict) -> dict:
    """与 frontend/js/views/demo_auto.js 第 7 步完全相同的改分规则。"""
    room = sorted(({"c": c, "r": c["max_score"] - c["score"]} for c in ai["criteria"]), key=lambda x: -x["r"])
    target, delta = room[0]["c"], min(2, math.floor(room[0]["r"] * 2) / 2)
    if delta <= 0:
        target = sorted(ai["criteria"], key=lambda c: -c["score"])[0]
        delta = -min(1, target["score"])

    def fmt(x):
        return f"{x:g}"
    reason = (f"老师认为“{target['name']}”AI 给得偏严，加 {fmt(delta)} 分，并写下修改理由。" if delta > 0
              else f"老师认为“{target['name']}”略高，扣 {fmt(-delta)} 分。")
    return {
        "criteria": [{"id": c["id"], "score": c["score"] + delta if c["id"] == target["id"] else c["score"],
                      "comment": c.get("comment") or ""} for c in ai["criteria"]],
        "deductions": [{"id": x["id"], "name": x.get("name") or "", "points": x["points"], "reason": x.get("reason") or ""}
                       for x in ai.get("deductions", [])],
        "errors": ai.get("errors", []),
        "comment": ((ai.get("comment") + " ") if ai.get("comment") else "") + "（老师补充：整体完成得不错，记得订正标出的问题。）",
        "note": "自动演示：" + reason,
    }


def diff(base: dict, snap: dict) -> dict:
    return {role: {k: v for k, v in s.items() if base.get(role, {}).get(k) != v} for role, s in snap.items()}


def main() -> int:
    tmp = setup_env()
    try:
        from fastapi.testclient import TestClient

        from backend.db import init_db
        from backend.main import app

        if OUT.exists():
            shutil.rmtree(OUT)
        (OUT / "img").mkdir(parents=True)
        init_db()
        with TestClient(app) as client:
            rec = Recorder(client)
            rec.login()
            health = client.get("/api/health").json()
            rec.scenario = client.get("/api/demo/scenario", headers=rec._h("teacher")).json()
            sc = rec.scenario
            before = rec.crawl()

            # 自动演示第 3 步：家长上传示例照片（与前端相同：文件名 homework.png）
            photo = client.get(f"/api/demo/sample-photo?assignment_id={sc['assignment_id']}", headers=rec._h("parent")).content
            r = client.post("/api/submissions", headers=rec._h("parent"),
                            data={"assignment_id": str(sc["assignment_id"]), "student_id": str(sc["student_id"]),
                                  "client_token": "static-demo-recording"},
                            files={"files": ("homework.png", photo, "image/png")})
            r.raise_for_status()
            upload = r.json()
            sub_id = upload["id"]
            mid = rec.crawl()

            # 第 7 步：老师复核改分
            ai = client.get(f"/api/submissions/{sub_id}", headers=rec._h("teacher")).json()["ai_result"]
            r = client.post(f"/api/submissions/{sub_id}/review", headers=rec._h("teacher"), json=review_body(ai))
            r.raise_for_status()
            review = r.json()
            after = rec.crawl()

        users = {role: rec.users[role] for role in ACCOUNTS}
        data = {
            "recorded_from": "data/demo_seed（程序合成的演示数据）+ 后端 mock provider",
            "health": {**health, "static": True},
            "accounts": {u: {"password": p, "role": role} for role, (u, p) in ACCOUNTS.items()},
            "users": users,
            "tour": {"assignment_id": sc["assignment_id"], "student_id": sc["student_id"], "submission_id": sub_id,
                     "upload": rec.put_json(upload), "review": rec.put_json(review)},
            "snapshots": {"before": before, "mid": diff(before, mid), "after": diff(before, after)},
            "blobs": rec.blobs,
        }
        js = ("// 由 tools/static_demo/record.py 生成，不要手改。全部是程序合成的演示数据（非真实学生、非真实模型输出）。\n"
              "window.EDU_MOCK_DATA = " + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + ";\n")
        (OUT / "data.js").write_text(js, encoding="utf-8")
        n = sum(len(s) for s in before.values())
        print(f"录制完成：before {n} 条，mid 差异 {sum(len(s) for s in data['snapshots']['mid'].values())} 条，"
              f"after 差异 {sum(len(s) for s in data['snapshots']['after'].values())} 条；"
              f"JSON {len(rec.blobs)} 份，data.js {len(js.encode('utf-8')) / 1e6:.2f} MB；"
              f"图片 {len(rec.images)} 张 {rec.img_bytes / 1e6:.1f} MB；自动演示提交 #{sub_id}")
        return 0
    finally:
        try:
            from backend.db import reset_engine
            reset_engine()
        except Exception:  # noqa: BLE001
            pass
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
