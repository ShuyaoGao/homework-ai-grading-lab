"""为展示页/幻灯片截取真实界面截图并录制流程 GIF（用本机 Edge 无头模式，Playwright channel=msedge）。

    .venv\\Scripts\\python run.py --port 8604          # 另开一个终端先启动服务（演示数据）
    .venv\\Scripts\\python showcase\\capture.py --base http://127.0.0.1:8604

输出到 showcase/assets/：m_*.jpg（手机 390×844 @2x）、d_*.jpg（桌面 1440×900）、pp_*.jpg（预处理前后）、flow.gif。
截图里的数据全部是演示数据（合成图片 + mock 评分），页面上有“演示数据”标注。
"""
from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

from PIL import Image
from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
OUT = ROOT / "showcase" / "assets"
ACC = {"teacher": ("teacher_wang", "teacher12345"), "parent": ("parent_zhang", "parent12345"),
       "student": ("student_zxm", "student12345"), "admin": ("admin", "admin12345")}
frames: list[Image.Image] = []


def save(page: Page, name: str, full: bool = False, clip: dict | None = None, frame: bool = False) -> None:
    png = page.screenshot(full_page=full, clip=clip)
    im = Image.open(io.BytesIO(png)).convert("RGB")
    if name.startswith("m_") and im.width > 780:
        im = im.resize((780, int(im.height * 780 / im.width)), Image.LANCZOS)
    im.save(OUT / f"{name}.jpg", "JPEG", quality=82, optimize=True, progressive=True)
    if frame:
        frames.append(im.copy())
    print("  ", name, im.size)


def login(page: Page, base: str, role: str) -> None:
    page.goto(f"{base}/#/login")
    page.wait_for_selector("#u")
    u, p = ACC[role]
    page.fill("#u", u)
    page.fill("#p", p)
    page.click("#f button[type=submit]")
    page.wait_for_function("location.hash === '#/'")
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(500)


def go(page: Page, base: str, h: str, wait: str | None = None, ms: int = 700) -> None:
    page.goto(f"{base}/{h}")
    if wait:
        page.wait_for_selector(wait, timeout=30000)
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(ms)


def scroll_to(page: Page, sel: str, offset: int = 70) -> None:
    page.evaluate(f"(() => {{ const e = document.querySelector({sel!r}); if (e) window.scrollTo(0, e.getBoundingClientRect().top + scrollY - {offset}); }})()")
    page.wait_for_timeout(350)


def preprocessing_samples() -> None:
    """预处理前后对比图（与系统中真实运行的预处理代码一致）。"""
    from backend.demo import corpus
    from backend.demo.handwriting import make_homework_photo, style_for
    from backend.pipeline.preprocess import load_upload, preprocess

    tmp = OUT / "_tmp"
    tmp.mkdir(exist_ok=True)
    for name, text, lang, paper, seed, ang in [
        ("zh", corpus.gen_chinese("grownup", 0.7, 31), "zh", "grid", 41, -5),
        ("en", corpus.gen_english("weekend", 0.5, 32), "en", "lined", 3, 6),
    ]:
        p = make_homework_photo(text, lang, tmp / name, seed=seed, angle=ang, style=style_for(seed, lang, 1.0, paper=paper),
                                out_width=1200)
        li = load_upload(p.read_bytes())
        out, meta = preprocess(li.image)
        a = li.image.copy()
        a.thumbnail((900, 1200))
        b = out.convert("RGB")
        b.thumbnail((900, 1200))
        a.save(OUT / f"pp_{name}_before.jpg", quality=80)
        b.save(OUT / f"pp_{name}_after.jpg", quality=80)
        print("   pp", name, meta.as_dict())
    for f in tmp.iterdir():
        f.unlink()
    tmp.rmdir()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8604")
    args = ap.parse_args()
    base = args.base
    OUT.mkdir(parents=True, exist_ok=True)
    photo = ROOT / "samples" / "generated" / "zh_essay_photo.png"
    if not photo.exists():
        from scripts.make_test_images import main as mk

        mk()
    preprocessing_samples()

    with sync_playwright() as pw:
        br = pw.chromium.launch(channel="msedge", headless=True)
        api = pw.request.new_context(base_url=base)
        tok = api.post("/api/auth/login", form={"username": "teacher_wang", "password": "teacher12345"}).json()["access_token"]
        H = {"Authorization": f"Bearer {tok}"}
        subs = api.get("/api/submissions?limit=500", headers=H).json()
        sid = next(s["student_id"] for s in subs if s["student_name"] == "张小明")
        cid = api.get("/api/classes", headers=H).json()[0]["id"]
        en_rev = next(s["id"] for s in subs if s["student_id"] == sid and s["subject"] == "english" and s["status"] == "reviewed")
        zh_ai = next(s["id"] for s in subs if s["subject"] == "chinese" and s["status"] == "ai_done" and s["needs_review"])
        math_rev = next(s["id"] for s in subs if s["subject"] == "math" and s["status"] == "reviewed" and s["student_id"] == sid)

        # ---------------- 手机：家长 ----------------
        m = br.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True,
                           locale="zh-CN")
        pg = m.new_page()
        go(pg, base, "#/login", "#u")
        save(pg, "m_login", frame=True)
        login(pg, base, "parent")
        save(pg, "m_parent_home", frame=True)
        go(pg, base, f"#/upload?student={sid}", "#drop")
        opts = pg.eval_on_selector_all("#asg option", "os => os.map(o => [o.value, o.textContent])")
        pg.select_option("#asg", next((v for v, t in opts if "英雄" in t), opts[0][0]))
        pg.set_input_files("#gal", str(photo))
        pg.wait_for_selector(".thumb img")
        scroll_to(pg, "#drop", 120)
        save(pg, "m_upload", frame=True)
        # 裁剪/旋转编辑器：拖动左上角和右下角
        pg.click("[data-ed='0']")
        pg.wait_for_selector("dialog.editor canvas")
        pg.wait_for_timeout(500)
        box = pg.locator("dialog.editor canvas").bounding_box()
        for (sx, sy), (dx, dy) in [((0, 0), (0.09, 0.07)), ((1, 1), (0.9, 0.93))]:
            pg.mouse.move(box["x"] + sx * box["width"] + (2 if sx == 0 else -2), box["y"] + sy * box["height"] + (2 if sy == 0 else -2))
            pg.mouse.down()
            pg.mouse.move(box["x"] + dx * box["width"], box["y"] + dy * box["height"], steps=6)
            pg.mouse.up()
        pg.wait_for_timeout(300)
        save(pg, "m_editor", frame=True)
        pg.click("dialog.editor [data-a='ok']")
        pg.wait_for_timeout(300)
        pg.click("#go")
        pg.wait_for_function("location.hash.startsWith('#/submission/')", timeout=30000)
        new_id = pg.evaluate("location.hash.split('/').pop()")
        pg.wait_for_selector(".scorebox", timeout=60000)
        pg.wait_for_timeout(900)
        save(pg, "m_result", frame=True)
        scroll_to(pg, "#ocrText", 90)
        save(pg, "m_result_text")
        scroll_to(pg, ".errlist", 140)
        save(pg, "m_result_errors")
        go(pg, base, f"#/student/{sid}", "#trend", 1200)
        save(pg, "m_profile")
        scroll_to(pg, "#radar", 120)
        save(pg, "m_profile_charts")
        go(pg, base, "#/privacy", ".check")
        scroll_to(pg, "[data-scope]", 260)
        save(pg, "m_privacy")
        m.close()

        # ---------------- 手机：教师复核（接着上面的提交）----------------
        mt = br.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True)
        tp = mt.new_page()
        login(tp, base, "teacher")
        save(tp, "m_teacher_home", frame=True)
        go(tp, base, "#/submissions?status=ai_done", ".list")
        save(tp, "m_queue", frame=True)
        go(tp, base, f"#/submission/{new_id}", "#save", 1000)
        scroll_to(tp, "#rv", 70)
        inputs = tp.locator("#crits input")
        inputs.nth(2).fill(str(float(inputs.nth(2).input_value()) + 2))
        tp.fill("#cm", "立意明确，事例真实感人。注意“鲁迅先生说过”一段引文要准确；结尾可以再升华一点。")
        tp.fill("#note", "发展等级上调 2 分：引用名言、事例典型")
        save(tp, "m_review_form", frame=True)
        tp.click("#save")
        tp.wait_for_selector(".chip.ok", timeout=15000)
        tp.wait_for_timeout(600)
        tp.evaluate("window.scrollTo(0,0)")
        tp.wait_for_timeout(300)
        save(tp, "m_reviewed", frame=True)
        mt.close()

        # 家长再看
        m2 = br.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True)
        p2 = m2.new_page()
        login(p2, base, "parent")
        go(p2, base, f"#/submission/{new_id}", ".scorebox", 900)
        save(p2, "m_parent_sees_review", frame=True)
        go(p2, base, f"#/student/{sid}", "#trend", 1200)
        save(p2, "m_profile_after", frame=True)
        m2.close()

        # 学生
        ms = br.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True)
        sp = ms.new_page()
        login(sp, base, "student")
        sp.wait_for_selector("#trend")
        sp.wait_for_timeout(1000)
        save(sp, "m_student_home")
        ms.close()

        # ---------------- 桌面 ----------------
        d = br.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=1, locale="zh-CN")
        dp = d.new_page()
        login(dp, base, "teacher")
        save(dp, "d_teacher_home")
        go(dp, base, f"#/class/{cid}", "#hist", 1200)
        save(dp, "d_classboard")
        scroll_to(dp, "#cerr", 160)
        save(dp, "d_classboard_issues")
        dp.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        dp.wait_for_timeout(400)
        save(dp, "d_classboard_agreement")
        go(dp, base, f"#/submission/{zh_ai}", "#save", 1200)
        save(dp, "d_result")
        scroll_to(dp, ".grid.result", 80)
        save(dp, "d_result_compare")
        scroll_to(dp, "#reviewArea", 70)
        save(dp, "d_review")
        go(dp, base, f"#/submission/{math_rev}", ".scorebox", 1000)
        scroll_to(dp, ".grid.result", 80)
        save(dp, "d_math_result")
        go(dp, base, f"#/student/{sid}", "#trend", 1300)
        save(dp, "d_profile")
        scroll_to(dp, ".grid.two:nth-of-type(2)", 80)
        go(dp, base, "#/rubrics/chinese_essay_gaokao", "#y", 800)
        save(dp, "d_rubric")
        go(dp, base, "#/assignments", ".list", 600)
        dp.click("details.card summary") if dp.locator("details.card:not([open]) summary").count() else None
        dp.wait_for_timeout(300)
        save(dp, "d_assign")
        go(dp, base, f"#/submission/{en_rev}", ".scorebox", 1000)
        scroll_to(dp, "#ocrText", 120)
        save(dp, "d_en_highlight")
        d.close()

        a = br.new_context(viewport={"width": 1440, "height": 900})
        ap_ = a.new_page()
        login(ap_, base, "admin")
        save(ap_, "d_admin")
        ap_.goto(f"{base}/api/docs")
        ap_.wait_for_selector(".opblock", timeout=30000)
        ap_.wait_for_timeout(800)
        save(ap_, "d_api_docs")
        a.close()
        br.close()

    # ---------------- 流程 GIF（手机）----------------
    w = 360
    gif = [f.resize((w, int(f.height * w / f.width)), Image.LANCZOS) for f in frames]
    hmax = max(g.height for g in gif)
    canvas = []
    for g in gif:
        c = Image.new("RGB", (w, hmax), (244, 246, 243))
        c.paste(g, (0, 0))
        canvas.append(c.quantize(colors=128, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE))
    canvas[0].save(OUT / "flow.gif", save_all=True, append_images=canvas[1:], duration=1600, loop=0, optimize=True)
    print("   flow.gif", len(canvas), "帧", (OUT / "flow.gif").stat().st_size // 1024, "KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
