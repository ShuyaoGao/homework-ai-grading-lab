"""评分 schema 校验、量规 YAML 校验、后处理规则。"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.pipeline.grading.postprocess import finalize, locate_quote, parse_llm_json
from backend.pipeline.grading.schema import GradingResult, LLMGradingOutput, llm_output_json_schema
from backend.pipeline.rubrics import RubricError, builtin_rubrics, parse_rubric_yaml

R = builtin_rubrics()


def test_builtin_rubrics_load_and_are_consistent():
    assert set(R) == {"english_essay_zhongkao", "english_essay_gaokao", "chinese_essay_gaokao", "short_answer_math",
                      "paper_essay"}
    for r in R.values():
        if r.kind != "short_answer":
            assert sum(c.max for c in r.criteria) == r.max_score
        # 档次覆盖 0..满分，且互不重叠（按 min 排序后首尾相接或有 ≤1 分的整数缝）
        bands = sorted(r.bands, key=lambda b: b.min)
        assert bands[0].min == 0
        top = r.max_score if r.band_scale == "score" else 1.0
        assert bands[-1].max == top
        for a, b in zip(bands, bands[1:]):
            assert b.min > a.max - 1e-9


def test_band_of():
    zk = R["english_essay_zhongkao"]
    assert zk.band_of(20) == "第五档" and zk.band_of(16.5) == "第四档" and zk.band_of(0) == "第一档"
    assert zk.band_of(12.5) == "第三档" and zk.band_of(17) == "第五档"  # 未达上一档下限归入较低档
    sa = R["short_answer_math"]
    assert sa.band_of(9, 10) == "优秀" and sa.band_of(5, 10) == "待提高"


GOOD = """
key: my_rubric
name: 测试量规
subject: english
kind: essay_en
max_score: 10
bands:
  - {name: 高, min: 6, max: 10}
  - {name: 低, min: 0, max: 5}
criteria:
  - {id: a, name: A, max: 6}
  - {id: b, name: B, max: 4}
"""


def test_rubric_yaml_ok():
    r = parse_rubric_yaml(GOOD)
    assert r.max_score == 10 and r.band_of(7) == "高"


@pytest.mark.parametrize("bad,msg", [
    (GOOD.replace("max_score: 10", "max_score: 12"), "不一致"),
    (GOOD.replace("{id: b, name: B, max: 4}", "{id: a, name: B, max: 4}"), "重复"),
    (GOOD.replace("key: my_rubric", "key: 中文键"), "校验失败"),
    ("key: [unclosed", "YAML"),
    ("- just a list", "映射"),
])
def test_rubric_yaml_errors(bad, msg):
    with pytest.raises(RubricError) as e:
        parse_rubric_yaml(bad)
    assert msg in str(e.value)


def test_rubric_api_validate_and_save(client, world):
    from tests.conftest import token

    ht = token(client, "t_teacher")
    assert client.post("/api/rubrics/validate", json={"yaml_text": GOOD}, headers=ht).json()["ok"]
    assert not client.post("/api/rubrics/validate", json={"yaml_text": "key: x"}, headers=ht).json()["ok"]
    assert client.post("/api/rubrics", json={"yaml_text": GOOD}, headers=ht).status_code == 201
    keys = {r["key"] for r in client.get("/api/rubrics", headers=ht).json()}
    assert "my_rubric" in keys
    builtin = GOOD.replace("key: my_rubric", "key: paper_essay")
    assert client.post("/api/rubrics", json={"yaml_text": builtin}, headers=ht).status_code in (400, 409)
    # 家长不能保存量规
    assert client.post("/api/rubrics", json={"yaml_text": GOOD}, headers=token(client, "t_parent")).status_code == 403


def test_grading_result_schema_rejects_bad_values():
    base = dict(rubric_key="x", total_score=5, max_score=10, criteria=[], confidence=0.5)
    GradingResult(**base)
    with pytest.raises(ValidationError):
        GradingResult(**{**base, "confidence": 1.5})
    with pytest.raises(ValidationError):
        GradingResult(**{**base, "total_score": -1})


def test_llm_json_schema_is_strict():
    s = llm_output_json_schema()
    assert s["additionalProperties"] is False
    assert set(s["required"]) == set(s["properties"])
    item = s["properties"]["errors"]["items"]
    assert item["additionalProperties"] is False and set(item["required"]) == set(item["properties"])


def test_parse_llm_json_variants():
    raw = '{"criteria": [{"id": "a", "score": 1}], "confidence": 0.8}'
    assert parse_llm_json(raw).confidence == 0.8
    assert parse_llm_json(f"结果如下：\n```json\n{raw}\n```\n以上").criteria[0].id == "a"
    with pytest.raises(Exception):
        parse_llm_json("没有 JSON")


def test_finalize_rules():
    r = parse_rubric_yaml(GOOD)
    text = "I has a apple.\nHe go to school."
    raw = LLMGradingOutput.model_validate({
        "criteria": [{"id": "a", "score": 8}, {"id": "zzz", "score": 3}],  # a 超满分；b 缺失；zzz 不存在
        "errors": [{"type": "grammar", "quote": "a apple", "message": "an"},
                   {"type": "错别字", "quote": "Hego", "message": "空白不同也能定位"}],
        "confidence": 0.95,
    })
    res = finalize(raw, r, text, None, provider="claude")
    by = {c.id: c.score for c in res.criteria}
    assert by == {"a": 6, "b": 0}
    assert res.total_score == 6 and res.band == "高"
    assert res.needs_review and any("越界或缺项" in x for x in res.review_reasons)
    assert any("zzz" in w for w in res.warnings)
    assert res.errors[0].type == "grammar.other" and text[res.errors[0].start:res.errors[0].end] == "a apple"
    assert res.errors[1].type == "typo" and res.errors[1].start is not None


def test_finalize_low_ocr_confidence_and_boundary():
    r = R["english_essay_zhongkao"]
    raw = LLMGradingOutput.model_validate({"criteria": [{"id": "content", "score": 7}, {"id": "language", "score": 7},
                                                        {"id": "organization", "score": 3}], "confidence": 0.95})
    res = finalize(raw, r, "text", None, provider="claude", ocr_confidence=0.5)
    assert res.total_score == 17 and res.band == "第五档"
    reasons = " ".join(res.review_reasons)
    assert "识别置信度" in reasons and "贴近档次边界" in reasons
    raw.criteria[2].score = 1
    raw.criteria[0].score = 6
    res = finalize(raw, r, "text", None, provider="claude", ocr_confidence=0.99)
    assert res.total_score == 14 and not res.needs_review


def test_locate_quote_skips_used_spans():
    t = "aa bb aa"
    assert locate_quote(t, "aa") == (0, 2)
    assert locate_quote(t, "aa", {(0, 2)}) == (6, 8)
    assert locate_quote(t, "") is None
    assert locate_quote(t, "zz") is None
