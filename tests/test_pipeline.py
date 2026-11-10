"""流水线各环节：预处理（纠偏/纸张检测）、mock OCR、mock 评分、合规降级、LLM provider（用假客户端，不联网）。"""
from __future__ import annotations

import json
import types

import numpy as np
import pytest
from PIL import Image

from backend.config import get_settings
from backend.db import SessionLocal
from backend.demo.handwriting import make_homework_photo, render_page, simulate_photo
from backend.models import Student
from backend.pipeline import preprocess as pp
from backend.pipeline.grading.base import GradingContext, build_user_prompt
from backend.pipeline.grading.heuristic import MockGrader
from backend.pipeline.grading.llm import ClaudeGrader, OpenAICompatGrader
from backend.pipeline.grading.schema import GradingResult
from backend.pipeline.ocr.base import OCRContext, PageInput
from backend.pipeline.ocr.providers import MockOCR, _parse_ocr_json
from backend.pipeline.rubrics import builtin_rubrics
from backend.pipeline.runner import choose_providers
from tests.conftest import EN_TEXT

R = builtin_rubrics()
ZH = ("我的老师\n我的老师是一位和蔼可亲的人，她总是在接再厉，克苦工作。因为她，我们班的成绩越来越好。\n"
      "首先，她上课非常认真，像一盏明灯照亮我们前进的路。其次，她关心每一个同学,我们都很喜欢她。\n总之，我爱我的老师。")


@pytest.mark.parametrize("angle", [-6.0, -3.0, 4.0, 7.0])
def test_deskew_estimate(angle):
    page = render_page("The quick brown fox jumps over the lazy dog. " * 12, "en", seed=3, messiness=0.3)
    rotated = page.rotate(angle, expand=True, fillcolor=(250, 249, 244), resample=Image.BICUBIC)
    gray = np.asarray(rotated.convert("L"))
    est = pp.estimate_skew(gray)
    assert abs(est - angle) < 1.5, (est, angle)
    out, meta = pp.preprocess(rotated, do_warp=False)
    assert abs(meta.deskew_angle - angle) < 1.5


def test_paper_detection_and_warp(tmp_path):
    page = render_page(EN_TEXT, "en", seed=5)
    photo = simulate_photo(page, seed=5, angle=5)
    out, meta = pp.preprocess(photo)
    assert meta.paper_detected
    assert 0.4 < meta.paper_area_ratio < 0.95
    # 矫正后纸张基本是正的：剩余倾斜很小
    assert abs(pp.estimate_skew(np.asarray(out))) < 1.5
    assert out.mode == "L" and meta.enhanced


def test_blank_and_tiny_images_do_not_crash():
    for im in (Image.new("RGB", (50, 50), "white"), Image.new("RGB", (1200, 900), (30, 30, 30))):
        out, meta = pp.preprocess(im)
        assert out.size[0] > 0


def test_stitch_vertical():
    a, b = Image.new("L", (400, 300), 255), Image.new("L", (800, 200), 0)
    s = pp.stitch_vertical([a, b], gap=10, width=400)
    assert s.size == (400, 300 + 100 + 10)


def test_load_upload_reads_embedded_text(tmp_path):
    p = make_homework_photo("hello world", "en", tmp_path / "x", seed=1, photo=False)
    li = pp.load_upload(p.read_bytes())
    assert li.embedded_text == "hello world"
    with pytest.raises(ValueError):
        pp.load_upload(b"\x89PNG broken")


def test_mock_ocr(tmp_path):
    p = tmp_path / "a.jpg"
    Image.new("RGB", (10, 10)).save(p)
    res = MockOCR().recognize([PageInput(1, p, "第一页"), PageInput(2, p, "第二页")], OCRContext("chinese"))
    assert res.text == "第一页\n第二页" and res.confidence == 0.95
    res = MockOCR().recognize([PageInput(1, p, "")], OCRContext("english"))
    assert res.confidence == 0.2 and res.warnings


def test_parse_ocr_json_variants():
    assert _parse_ocr_json('```json\n{"text": "abc", "confidence": 0.9}\n```')["text"] == "abc"
    d = _parse_ocr_json("纯文本输出")
    assert d["text"] == "纯文本输出" and d["confidence"] is None


@pytest.mark.parametrize("key,text,ref", [
    ("english_essay_zhongkao", EN_TEXT, {"key_points": [["weekend"], ["friends"]]}),
    ("english_essay_gaokao", EN_TEXT, {}),
    ("chinese_essay_gaokao", ZH, {"min_chars": 600}),
    ("paper_essay", "题目\n摘要：……\n一、引言\n研究表明[1]。\n参考文献\n[1] 张三. 论文[J]. 2020.", {}),
    ("short_answer_math", "1. 2x=8\nx=4\n2. 10", {"questions": [{"id": 1, "answer": "x=4", "points": 4, "steps": ["2x=8"]},
                                                              {"id": 2, "answer": "12", "points": 6}]}),
])
def test_mock_grader_outputs_valid_result(key, text, ref):
    res = MockGrader().grade(GradingContext(rubric=R[key], text=text, prompt="weekend", reference=ref))
    GradingResult.model_validate(res.model_dump())
    assert 0 <= res.total_score <= res.max_score
    assert res.band
    crit_sum = sum(c.score for c in res.criteria) - sum(d.points for d in res.deductions)
    assert abs(max(0, crit_sum) - res.total_score) < 1e-6
    for e in res.errors:  # 有位置的批注必须与原文一致
        if e.start is not None:
            assert text[e.start:e.end] == e.quote
    assert res.needs_review  # mock 一律建议复核


def test_mock_grader_detects_known_errors():
    res = MockGrader().grade(GradingContext(rubric=R["english_essay_zhongkao"], text=EN_TEXT, reference={}))
    types_ = {e.type for e in res.errors}
    assert {"grammar.agreement", "grammar.article", "spelling"} <= types_
    res = MockGrader().grade(GradingContext(rubric=R["chinese_essay_gaokao"], text=ZH, reference={"min_chars": 600}))
    typos = [e for e in res.errors if e.type == "typo"]
    assert {e.quote for e in typos} == {"在接再厉", "克苦"}
    assert any(d.id == "typo" and d.points == 2 for d in res.deductions)
    assert any(d.id == "length" for d in res.deductions)


def test_short_answer_scoring():
    ref = {"questions": [{"id": 1, "answer": "x=4", "points": 4, "steps": ["2x=8"], "knowledge_point": "方程"},
                         {"id": 2, "answer": "12", "points": 6, "knowledge_point": "面积"}]}
    res = MockGrader().grade(GradingContext(rubric=R["short_answer_math"], text="1. 2x=8\n   x=4\n2. 3×4=10", reference=ref))
    by = {c.id: c.score for c in res.criteria}
    assert by["1"] == 4 and by["2"] < 6
    assert res.max_score == 10
    calc = [e for e in res.errors if e.type == "calculation"]
    assert calc and calc[0].quote == "3×4=10"
    kp = {k.name: k.score_ratio for k in res.knowledge_points}
    assert kp["方程"] == 1.0 and kp["面积"] < 1


def test_consent_downgrade(world, monkeypatch):
    db = SessionLocal()
    s = get_settings()
    monkeypatch.setattr(type(s), "has_anthropic", property(lambda self: True))
    no_cloud = db.get(Student, world["s_ok"])
    o, g, notes = choose_providers(db, no_cloud, "claude", "claude")
    assert (o, g) == ("mock", "mock") and len(notes) == 2 and "cloud_ai" in notes[0]
    cloud = db.get(Student, world["s_cloud"])
    o, g, notes = choose_providers(db, cloud, "claude", "claude")
    assert (o, g) == ("claude", "claude") and not notes
    monkeypatch.setattr(s, "allow_cloud_providers", False)
    o, g, notes = choose_providers(db, cloud, "claude", "claude")
    assert (o, g) == ("mock", "mock") and "管理员" in notes[0]
    db.close()


def test_user_prompt_contains_rubric_and_no_student_name():
    ctx = GradingContext(rubric=R["english_essay_zhongkao"], text="I like it.", prompt="Write", reference={})
    p = build_user_prompt(ctx)
    assert "id=content" in p and "第五档" in p and "I like it." in p


# ---------------- LLM provider（假客户端）----------------

LLM_JSON = {
    "criteria": [{"id": "content", "score": 7, "level": "", "comment": "要点齐全"},
                 {"id": "language", "score": 9.5, "level": "", "comment": "超过满分，应被截断"},
                 {"id": "organization", "score": 3, "level": "", "comment": ""}],
    "deductions": [],
    "errors": [{"type": "grammar.agreement", "quote": "He like", "message": "单三", "suggestion": "He likes",
                "severity": "major", "criterion_id": "language"},
               {"type": "spelling", "quote": "不存在的片段", "message": "x", "suggestion": "", "severity": "minor",
                "criterion_id": ""}],
    "comment": "不错", "strengths": ["切题"], "suggestions": ["注意单三"],
    "knowledge_points": [{"name": "主谓一致", "score_ratio": 0.5}], "confidence": 0.9, "review_reasons": [],
}


def test_claude_grader_with_fake_client(monkeypatch):
    calls = {}

    def fake_create(**kw):
        calls.update(kw)
        block = types.SimpleNamespace(type="text", text=json.dumps(LLM_JSON, ensure_ascii=False))
        return types.SimpleNamespace(stop_reason="end_turn", content=[block], model="claude-sonnet-5-5")

    fake = types.SimpleNamespace(beta=types.SimpleNamespace(messages=types.SimpleNamespace(create=fake_create)),
                                 messages=types.SimpleNamespace(create=fake_create))
    g = ClaudeGrader()
    monkeypatch.setattr(g, "_client", lambda: fake)
    res = g.grade(GradingContext(rubric=R["english_essay_zhongkao"], text=EN_TEXT, reference={}))
    assert calls["model"] == "claude-sonnet-5-5"
    assert calls["output_config"]["format"]["type"] == "json_schema"
    assert calls["fallbacks"] == "default" and calls["betas"] == ["server-side-fallback-2026-07-01"]
    by = {c.id: c.score for c in res.criteria}
    assert by["language"] == 8  # 截断到满分
    assert res.total_score == 18
    assert any("截断" in w for w in res.warnings) and res.needs_review
    he = next(e for e in res.errors if e.quote == "He like")
    assert EN_TEXT[he.start:he.end] == "He like"
    missing = next(e for e in res.errors if e.quote == "不存在的片段")
    assert missing.start is None


def test_claude_refusal_raises(monkeypatch):
    from backend.pipeline.errors import ProviderError

    resp = types.SimpleNamespace(stop_reason="refusal", content=[], model="m")
    fake = types.SimpleNamespace(beta=types.SimpleNamespace(messages=types.SimpleNamespace(create=lambda **k: resp)))
    g = ClaudeGrader()
    monkeypatch.setattr(g, "_client", lambda: fake)
    with pytest.raises(ProviderError):
        g.grade(GradingContext(rubric=R["english_essay_zhongkao"], text="x", reference={}))


def test_openai_compat_grader_with_fake(monkeypatch):
    import openai

    class FakeOpenAI:
        def __init__(self, **kw):
            self.kw = kw
            msg = types.SimpleNamespace(content="好的，评分如下：\n```json\n" + json.dumps(LLM_JSON) + "\n```")
            self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(
                create=lambda **k: types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)])))

    monkeypatch.setattr(openai, "OpenAI", FakeOpenAI)
    s = get_settings()
    monkeypatch.setattr(s, "openai_compat_base_url", "http://gpu-host:8000/v1")
    monkeypatch.setattr(s, "openai_compat_grader_model", "Qwen2.5-7B-Instruct")
    monkeypatch.setattr(s, "openai_compat_is_local", True)
    g = OpenAICompatGrader()
    assert g.available()[0] and not g.is_cloud
    res = g.grade(GradingContext(rubric=R["english_essay_zhongkao"], text=EN_TEXT, reference={}))
    assert res.provider == "openai_compat" and res.total_score == 18
