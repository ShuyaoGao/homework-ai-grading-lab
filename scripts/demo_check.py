"""一键演示的浏览器验收：真实启动 run.py --demo，用本机 Edge（无头）走一遍，并截图到 demo/screenshots/。

    .venv\\Scripts\\python scripts\\demo_check.py              # 复制 data\\ 到临时目录再跑，不改动你的演示数据
    .venv\\Scripts\\python scripts\\demo_check.py --no-shots   # 只验证，不覆盖截图

检查项：
 1. 打开 /#/demo 自动以教师身份登录，演示首页有班级统计和“已批改作文”入口
 2. 自动演示（speed=fast）9 步全部完成；中途暂停一次截图“教师复核改分”
 3. 班级看板、已批改作文、学生画像页面能打开
 4. 手机尺寸（390×844，DevTools 模拟）下自动演示完成，没有横向溢出
 5. 全程没有 JS 报错（pageerror / console.error）
 6. 点“关闭演示”后服务进程退出，状态文件被删除
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
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import run  # noqa: E402

SHOTS = ROOT / "demo" / "screenshots"


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-shots", action="store_true")
    ap.add_argument("--work", default="", help="临时目录（默认系统临时目录）")
    args = ap.parse_args()
    from playwright.sync_api import sync_playwright

    work = Path(args.work or tempfile.mkdtemp(prefix="edu_demo_check_"))
    data = work / "data"
    if data.exists():
        shutil.rmtree(data)
    src = ROOT / "data"
    if (src / "edu_eval.db").exists():
        shutil.copytree(src, data, ignore=shutil.ignore_patterns(run.STATE_NAME, "demo_samples"))
    else:
        data.mkdir(parents=True)
    run_dir = ROOT / "logs" / f"demo_check_{dt.datetime.now():%Y%m%d_%H%M%S}"
    run_dir.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "EDU_DATA_DIR": str(data), "PYTHONIOENCODING": "utf-8"}
    port = free_port()
    server_log = (run_dir / "server.log").open("w", encoding="utf-8")
    proc = subprocess.Popen([sys.executable, "run.py", "--demo", "--no-browser", "--port", str(port)],
                            cwd=ROOT, env=env, stdout=server_log, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    checks: dict[str, dict] = {}
    errors: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks[name] = {"ok": bool(ok), "detail": detail}
        print(f"{'通过' if ok else '失败'}  {name}  {detail}")

    try:
        for _ in range(600):  # 首次写入带图演示数据可能要 1–2 分钟
            if proc.poll() is not None:
                break
            try:
                with urllib.request.urlopen(base + "/api/health", timeout=1) as r:
                    if json.loads(r.read()).get("demo"):
                        break
            except OSError:
                time.sleep(0.5)
        check("演示服务启动", proc.poll() is None, base)
        if proc.poll() is not None:
            return 1
        state = json.loads((data / run.STATE_NAME).read_text(encoding="utf-8"))
        if not args.no_shots:
            SHOTS.mkdir(parents=True, exist_ok=True)

        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel="msedge", headless=True)

            def watch(page, tag: str) -> None:  # noqa: ANN001
                page.on("pageerror", lambda e: errors.append(f"[{tag}] pageerror: {e}"))
                page.on("console", lambda m: m.type == "error" and errors.append(f"[{tag}] console: {m.text}"))
                page.on("dialog", lambda d: d.accept())

            # ---- 电脑端
            ctx = browser.new_context(viewport={"width": 1366, "height": 900}, locale="zh-CN")
            page = ctx.new_page()
            watch(page, "desktop")
            page.goto(base + "/#/demo")
            page.wait_for_selector("#go-auto", timeout=30000)
            page.wait_for_timeout(500)
            check("自动以教师身份登录", "王老师" in page.inner_text("#usermenu"))
            body = page.inner_text("#app")
            check("演示首页有班级数据与已批改作文", "名学生" in body and "批改好的作文" in body)
            check("页面标注演示数据", "非真实模型输出" in page.inner_text("body"))
            if not args.no_shots:
                page.screenshot(path=str(SHOTS / "01_demo_home.png"))

            links = {h: page.locator(f'a.demo-card[href^="{h}"]').first.get_attribute("href")
                     for h in ("#/class/", "#/submission/", "#/student/")}
            for href, url in links.items():
                page.goto(base + "/" + url)
                page.wait_for_function("() => !document.querySelector('#app .loading')", timeout=20000)
                page.wait_for_timeout(800)
                txt = page.inner_text("#app")
                check(f"打开 {href}", "出错" not in txt and len(txt) > 50, url)
                if href == "#/submission/" and not args.no_shots:
                    page.screenshot(path=str(SHOTS / "02_reviewed_essay.png"))

            page.goto(base + "/#/demo/auto?start=1&speed=fast")
            page.wait_for_selector('#ad-steps li[data-i="6"].done', timeout=60000)
            page.click("#ad-pause")
            page.wait_for_timeout(300)
            check("教师复核步骤显示改分对比", "老师复核" in page.inner_text("#ad-panel"))
            if not args.no_shots:
                page.screenshot(path=str(SHOTS / "03_auto_demo_review.png"))
            page.click("#ad-pause")
            page.wait_for_function("() => ['done','failed'].includes(document.body.dataset.autodemo)", timeout=60000)
            check("电脑端自动演示 9 步走完", page.evaluate("document.body.dataset.autodemo") == "done",
                  page.inner_text("#ad-panel")[:120].replace("\n", " "))
            ctx.close()

            # ---- 手机端（DevTools 模拟 390×844）
            m = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True,
                                    has_touch=True, locale="zh-CN")
            mp = m.new_page()
            watch(mp, "mobile")
            mp.goto(base + "/#/demo/auto?start=1&speed=fast")
            mp.wait_for_function("() => ['done','failed'].includes(document.body.dataset.autodemo)", timeout=90000)
            check("手机端自动演示走完", mp.evaluate("document.body.dataset.autodemo") == "done")
            overflow = mp.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
            check("手机端无横向溢出", overflow <= 1, f"{overflow}px")
            if not args.no_shots:
                mp.locator("#ad-panel").scroll_into_view_if_needed()
                mp.screenshot(path=str(SHOTS / "04_mobile_auto_demo_done.png"))

            # ---- 关闭演示按钮
            mp.goto(base + "/#/demo")
            mp.wait_for_selector("#stop2", timeout=20000)
            mp.click("#stop2")
            mp.wait_for_selector(".shutdown-page", timeout=10000)
            m.close()
            browser.close()

        for _ in range(60):
            if proc.poll() is not None:
                break
            time.sleep(0.25)
        check("点“关闭演示”后服务进程退出", proc.poll() is not None and not run.pid_alive(int(state["pid"])))
        check("状态文件已删除", not (data / run.STATE_NAME).exists())
        check("没有 JS 报错", not errors, "; ".join(errors)[:500])
    except Exception as e:  # noqa: BLE001
        check("脚本运行", False, repr(e)[:300])
        raise
    finally:
        if proc.poll() is None:
            proc.kill()
        server_log.close()
        ok = all(c["ok"] for c in checks.values())
        (run_dir / "summary.json").write_text(json.dumps({"ok": ok, "checks": checks, "js_errors": errors},
                                                         ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n{'全部通过' if ok else '有失败项'}：{sum(c['ok'] for c in checks.values())}/{len(checks)}，日志 {run_dir}")
        if not args.work:
            shutil.rmtree(work, ignore_errors=True)
    return 0 if all(c["ok"] for c in checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
