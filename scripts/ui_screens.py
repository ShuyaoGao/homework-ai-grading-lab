"""前端冒烟测试 + 截图（开发/验收用）：用本机 Edge/Chrome（Playwright channel），不下载浏览器。

    .venv\\Scripts\\pip install -r requirements-dev.txt
    .venv\\Scripts\\python scripts\\ui_screens.py --base http://127.0.0.1:8000

依次以教师/家长/学生/管理员登录，在手机（390×844）与桌面（1366×900）尺寸下打开主要页面，
收集浏览器控制台错误与页面异常，截图存到 logs/screens/。有任何 JS 错误时退出码为 1。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ROOT = Path(__file__).resolve().parent.parent
ACCOUNTS = {"teacher": ("teacher_wang", "teacher12345"), "parent": ("parent_zhang", "parent12345"),
            "student": ("student_zxm", "student12345"), "admin": ("admin", "admin12345")}


def pages_for(role: str, ctx: dict) -> list[tuple[str, str]]:
    sid, sub_ai, sub_rev, cid = ctx["student_id"], ctx["sub_ai"], ctx["sub_reviewed"], ctx["class_id"]
    if role == "teacher":
        return [("home", "#/"), ("queue", "#/submissions?status=ai_done"), ("result_review", f"#/submission/{sub_ai}"),
                ("class_board", f"#/class/{cid}"), ("profile", f"#/student/{sid}"), ("upload", "#/upload"),
                ("assignments", "#/assignments"), ("rubrics", "#/rubrics"), ("rubric_detail", "#/rubrics/chinese_essay_gaokao")]
    if role == "parent":
        return [("home", "#/"), ("upload", f"#/upload?student={sid}"), ("result", f"#/submission/{sub_rev}"),
                ("profile", f"#/student/{sid}"), ("privacy", "#/privacy")]
    if role == "student":
        return [("home", "#/"), ("list", "#/submissions"), ("result", f"#/submission/{sub_rev}")]
    return [("home", "#/"), ("privacy", "#/privacy")]


def ui_flow(browser, base: str, ctx: dict, out: Path) -> dict:  # noqa: ANN001
    """在浏览器里走一遍：家长（手机尺寸）选图上传 → 等待批改结果 → 教师（桌面）改分保存 → 家长看到复核结果。"""
    errors: list[str] = []
    steps: list[str] = []
    photo = ROOT / "samples" / "generated" / "en_essay_photo.png"
    if not photo.exists():
        from scripts.make_test_images import main as make_images

        make_images()
    m = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True)
    page = m.new_page()
    page.on("pageerror", lambda exc: errors.append(f"pageerror: {exc}"))
    page.goto(f"{base}/#/login")
    page.fill("#u", "parent_zhang")
    page.fill("#p", "parent12345")
    page.click("#f button[type=submit]")
    page.wait_for_function("location.hash === '#/'")
    page.goto(f"{base}/#/upload?student={ctx['student_id']}")
    page.wait_for_selector("#gal", state="attached")
    # 选英语作业
    opts = page.eval_on_selector_all("#asg option", "os => os.map(o => [o.value, o.textContent])")
    eng = next((v for v, t in opts if "英语" in t), opts[0][0])
    page.select_option("#asg", eng)
    page.set_input_files("#gal", str(photo))
    page.wait_for_selector(".thumb img")
    page.screenshot(path=str(out / "flow_1_parent_upload.png"))
    page.click("#go")
    page.wait_for_function("location.hash.startsWith('#/submission/')", timeout=30000)
    sub_id = page.evaluate("location.hash.split('/').pop()")
    page.wait_for_selector(".scorebox", timeout=60000)
    score_ai = page.inner_text(".scorebox .big")
    steps.append(f"家长上传成功，提交 #{sub_id}，AI 初评 {score_ai}")
    page.screenshot(path=str(out / "flow_2_parent_result.png"), full_page=True)

    t = browser.new_context(viewport={"width": 1366, "height": 900})
    tp = t.new_page()
    tp.on("pageerror", lambda exc: errors.append(f"pageerror(teacher): {exc}"))
    tp.goto(f"{base}/#/login")
    tp.fill("#u", "teacher_wang")
    tp.fill("#p", "teacher12345")
    tp.click("#f button[type=submit]")
    tp.wait_for_function("location.hash === '#/'")
    tp.goto(f"{base}/#/submission/{sub_id}")
    tp.wait_for_selector("#save", timeout=30000)
    inputs = tp.locator("#crits input")
    target = next((inputs.nth(i) for i in range(inputs.count())
                   if float(inputs.nth(i).input_value()) < float(inputs.nth(i).get_attribute("max"))), inputs.first)
    target.fill(target.get_attribute("max"))  # 把一个未满分的维度改成满分，确保分数确实变化
    tp.fill("#cm", "（浏览器流程测试）要点齐全，注意语法。")
    tp.click("#save")
    tp.wait_for_selector(".chip.ok", timeout=15000)
    teacher_score = tp.inner_text(".scorebox .big")
    steps.append(f"教师复核保存，复核后 {teacher_score}")
    tp.screenshot(path=str(out / "flow_3_teacher_reviewed.png"), full_page=True)

    page.reload()
    page.wait_for_selector(".scorebox")
    parent_sees = page.inner_text(".scorebox .big")
    status = page.inner_text(".spread .row")
    if parent_sees != teacher_score or "教师已复核" not in status:
        errors.append(f"家长看到的分数 {parent_sees} / 状态 {status!r} 与教师复核 {teacher_score} 不一致")
    steps.append(f"家长刷新后看到 {parent_sees}（{status.strip()}）")
    page.goto(f"{base}/#/student/{ctx['student_id']}")
    page.wait_for_selector("#trend")
    page.wait_for_timeout(600)
    page.screenshot(path=str(out / "flow_4_parent_profile.png"), full_page=True)
    m.close()
    t.close()
    return {"steps": steps, "errors": errors}


def ui_offline_retry(browser, base: str, ctx: dict) -> dict:  # noqa: ANN001
    """弱网：断网时点上传 → 页面提示等待联网 → 恢复网络后自动继续上传并跳到结果页；裁剪编辑参数在服务器生效。"""
    errors: list[str] = []
    photo = ROOT / "samples" / "generated" / "en_essay_photo.png"
    c = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    page = c.new_page()
    page.on("pageerror", lambda exc: errors.append(f"pageerror: {exc}"))
    page.goto(f"{base}/#/login")
    page.fill("#u", "parent_zhang")
    page.fill("#p", "parent12345")
    page.click("#f button[type=submit]")
    page.wait_for_function("location.hash === '#/'")
    page.goto(f"{base}/#/upload?student={ctx['student_id']}")
    page.wait_for_selector("#drop")
    page.set_input_files("#gal", str(photo))
    page.wait_for_selector(".thumb img")
    # 用编辑器右转一次
    page.click("[data-ed='0']")
    page.wait_for_selector("dialog.editor canvas")
    page.click("dialog.editor [data-a='right']")
    page.click("dialog.editor [data-a='ok']")
    page.wait_for_selector(".edit-tag")
    c.set_offline(True)
    page.click("#go")
    try:
        page.wait_for_function("document.querySelector('#upStatus').textContent.includes('离线')", timeout=8000)
        offline_msg = page.inner_text("#upStatus")
    except Exception:  # noqa: BLE001
        offline_msg = page.inner_text("#upStatus")
        errors.append(f"断网时没有出现离线提示：{offline_msg!r}")
    page.wait_for_timeout(1500)
    c.set_offline(False)
    try:
        page.wait_for_function("location.hash.startsWith('#/submission/')", timeout=30000)
    except Exception:  # noqa: BLE001
        errors.append(f"恢复网络后没有自动完成上传：{page.inner_text('#upStatus')!r}")
    sub_id = page.evaluate("location.hash.split('/').pop()")
    c.close()
    return {"offline_message": offline_msg, "submission": sub_id, "errors": errors}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--channel", default="msedge", help="msedge 或 chrome")
    ap.add_argument("--out", default=str(ROOT / "logs" / "screens"))
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    problems: list[str] = []
    report: dict = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(channel=args.channel, headless=True)
        # 先用 API 找到演示数据里的 id
        api = p.request.new_context(base_url=args.base)
        tok = api.post("/api/auth/login", form={"username": "teacher_wang", "password": "teacher12345"}).json()["access_token"]
        h = {"Authorization": f"Bearer {tok}"}
        subs = api.get("/api/submissions?limit=500", headers=h).json()
        sid = next(s["student_id"] for s in subs if s["student_name"] == "张小明")
        ctx = {"student_id": sid, "class_id": api.get("/api/classes", headers=h).json()[0]["id"],
               "sub_ai": next(s["id"] for s in subs if s["status"] == "ai_done"),
               "sub_reviewed": next(s["id"] for s in subs if s["status"] == "reviewed" and s["student_id"] == sid)}
        for role, (u, pw) in ACCOUNTS.items():
            for vp_name, vp in (("mobile", {"width": 390, "height": 844}), ("desktop", {"width": 1366, "height": 900})):
                bctx = browser.new_context(viewport=vp, device_scale_factor=2 if vp_name == "mobile" else 1,
                                           is_mobile=vp_name == "mobile", has_touch=vp_name == "mobile", locale="zh-CN")
                page = bctx.new_page()
                errors: list[str] = []
                page.on("console", lambda m, e=errors: e.append(f"console.{m.type}: {m.text}") if m.type == "error" else None)
                page.on("pageerror", lambda exc, e=errors: e.append(f"pageerror: {exc}"))
                page.goto(f"{args.base}/#/login")
                page.fill("#u", u)
                page.fill("#p", pw)
                page.click("#f button[type=submit]")
                page.wait_for_function("location.hash === '#/'", timeout=10000)
                for name, hash_ in pages_for(role, ctx):
                    page.goto(f"{args.base}/{hash_}")
                    page.wait_for_load_state("networkidle")
                    page.wait_for_timeout(700)
                    txt = page.inner_text("#app")
                    if "加载中" in txt[:20] or "请求失败" in txt or "页面不存在" in txt:
                        errors.append(f"{name}: 页面内容异常：{txt[:80]!r}")
                    overflow = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
                    if overflow > 2:
                        errors.append(f"{name}: 页面横向溢出 {overflow}px")
                    page.screenshot(path=str(out / f"{role}_{vp_name}_{name}.png"), full_page=vp_name == "desktop")
                report[f"{role}/{vp_name}"] = errors
                problems += [f"[{role}/{vp_name}] {e}" for e in errors]
                bctx.close()
        report["flow"] = ui_flow(browser, args.base, ctx, out)
        problems += [f"[flow] {e}" for e in report["flow"]["errors"]]
        report["offline_retry"] = ui_offline_retry(browser, args.base, ctx)
        problems += [f"[offline] {e}" for e in report["offline_retry"]["errors"]]
        browser.close()
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=1))
    print(f"截图：{out}；问题 {len(problems)} 个")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
