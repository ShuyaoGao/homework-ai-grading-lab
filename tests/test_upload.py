"""上传接口：成功路径（mock 流水线）、同意闸门、文件校验、EXIF 去除、多页拼接、删除。"""
from __future__ import annotations

import io
from pathlib import Path

from PIL import Image

from backend.db import SessionLocal
from backend.models import SubmissionPage
from backend.pipeline.runner import submission_dir
from tests.conftest import EN_TEXT, token


def _files(*blobs: bytes, name="p.png", ctype="image/png"):
    return [("files", (f"{i}{name}", b, ctype)) for i, b in enumerate(blobs)]


def test_parent_upload_full_pipeline(client, world, en_png):
    hp = token(client, "t_parent")
    r = client.post("/api/submissions", data={"assignment_id": world["a_en"], "student_id": world["s_ok"]},
                    files=_files(en_png), headers=hp)
    assert r.status_code == 202, r.text
    sid = r.json()["id"]
    d = client.get(f"/api/submissions/{sid}", headers=hp).json()
    assert d["status"] == "ai_done", d["error"]
    assert d["ocr_text"] == EN_TEXT  # mock OCR 读取嵌入的标准文本
    fr = d["final_result"]
    assert fr["max_score"] == 20 and 0 <= fr["total_score"] <= 20
    assert d["ai_result"] is None  # 家长看不到 AI 原始结果，只看最终结果
    assert "仅供参考" in d["disclaimer"]
    # 预处理：检测到纸张
    assert d["preprocess_meta"]["pages"][0]["paper_detected"] is True
    # 图片需要鉴权
    assert client.get(f"/api/submissions/{sid}/pages/1/image").status_code == 401
    img = client.get(f"/api/submissions/{sid}/pages/1/image?kind=original", headers=hp)
    assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg"


def test_consent_gate_blocks_upload(client, world, en_png):
    hp = token(client, "t_parent")
    r = client.post("/api/submissions", data={"assignment_id": world["a_en"], "student_id": world["s_none"]},
                    files=_files(en_png), headers=hp)
    assert r.status_code == 403
    assert "监护人" in r.json()["detail"]


def test_parent_cannot_upload_for_other_child(client, world, en_png):
    hp = token(client, "t_parent")
    r = client.post("/api/submissions", data={"assignment_id": world["a_other"], "student_id": world["s_other"]},
                    files=_files(en_png), headers=hp)
    assert r.status_code == 403


def test_assignment_class_mismatch(client, world, en_png):
    ht = token(client, "t_teacher")
    r = client.post("/api/submissions", data={"assignment_id": world["a_other"], "student_id": world["s_ok"]},
                    files=_files(en_png), headers=ht)
    assert r.status_code in (400, 403)


def test_reject_non_image(client, world):
    ht = token(client, "t_teacher")
    r = client.post("/api/submissions", data={"assignment_id": world["a_en"], "student_id": world["s_ok"]},
                    files=_files(b"not an image at all", name="x.png"), headers=ht)
    assert r.status_code == 400
    r = client.post("/api/submissions", data={"assignment_id": world["a_en"], "student_id": world["s_ok"]},
                    files=_files(b"%PDF-1.4", name="x.pdf", ctype="application/pdf"), headers=ht)
    assert r.status_code == 415


def test_exif_gps_is_stripped(client, world):
    im = Image.new("RGB", (800, 600), "white")
    exif = Image.Exif()
    exif[0x8825] = {1: "N", 2: (30.0, 0.0, 0.0)}  # GPSInfo
    exif[0x010F] = "PhoneMaker"
    buf = io.BytesIO()
    im.save(buf, "JPEG", exif=exif)
    assert Image.open(io.BytesIO(buf.getvalue())).getexif()  # 原图确实有 EXIF
    ht = token(client, "t_teacher")
    r = client.post("/api/submissions", data={"assignment_id": world["a_en"], "student_id": world["s_ok"]},
                    files=_files(buf.getvalue(), name="p.jpg", ctype="image/jpeg"), headers=ht)
    assert r.status_code == 202
    db = SessionLocal()
    pg = db.query(SubmissionPage).filter(SubmissionPage.submission_id == r.json()["id"]).one()
    stored = Image.open(pg.original_path)
    assert len(stored.getexif()) == 0
    db.close()
    # 真实照片没有嵌入文本 → mock 返回示例文字，必须提示复核
    d = client.get(f"/api/submissions/{r.json()['id']}", headers=ht).json()
    assert d["final_result"]["needs_review"] is True
    assert any("mock" in w for w in d["ocr_meta"]["warnings"])


def test_multi_page_stitch(client, world, en_png, math_png):
    ht = token(client, "t_teacher")
    r = client.post("/api/submissions", data={"assignment_id": world["a_en"], "student_id": world["s_ok"]},
                    files=_files(en_png, math_png), headers=ht)
    assert r.status_code == 202
    sid = r.json()["id"]
    d = client.get(f"/api/submissions/{sid}", headers=ht).json()
    assert d["page_count"] == 2 and d["has_stitched"]
    assert client.get(f"/api/submissions/{sid}/stitched", headers=ht).status_code == 200
    assert len(d["ocr_meta"]["pages"]) == 2


def test_delete_submission_removes_files(client, world, en_png):
    hp = token(client, "t_parent")
    r = client.post("/api/submissions", data={"assignment_id": world["a_en"], "student_id": world["s_ok"]},
                    files=_files(en_png), headers=hp)
    sid = r.json()["id"]
    d = submission_dir(sid)
    assert Path(d).exists()
    assert client.delete(f"/api/submissions/{sid}", headers=hp).status_code == 200
    assert not Path(d).exists()
    assert client.get(f"/api/submissions/{sid}", headers=hp).status_code == 404


def test_parent_cannot_pick_provider(client, world, en_png):
    hp = token(client, "t_parent")
    r = client.post("/api/submissions", data={"assignment_id": world["a_en"], "student_id": world["s_ok"],
                                              "grader_provider": "claude"}, files=_files(en_png), headers=hp)
    assert r.status_code == 202
    ht = token(client, "t_teacher")
    d = client.get(f"/api/submissions/{r.json()['id']}", headers=ht).json()
    assert d["grader_provider"].startswith("mock")


def test_edits_rotate_and_crop_applied_on_server(client, world):
    im = Image.new("RGB", (800, 400), "white")  # 横图
    buf = io.BytesIO()
    im.save(buf, "JPEG")
    ht = token(client, "t_teacher")
    edits = '[{"rotate": 90, "crop": [0.1, 0.0, 0.9, 0.5]}]'
    r = client.post("/api/submissions", data={"assignment_id": world["a_en"], "student_id": world["s_ok"],
                                              "edits": edits}, files=_files(buf.getvalue(), name="p.jpg",
                                                                            ctype="image/jpeg"), headers=ht)
    assert r.status_code == 202, r.text
    db = SessionLocal()
    pg = db.query(SubmissionPage).filter(SubmissionPage.submission_id == r.json()["id"]).one()
    # 顺时针转 90° 后为 400×800，再裁 x 10%–90%、y 0–50% → 320×400
    assert (pg.width, pg.height) == (320, 400)
    db.close()
    bad = client.post("/api/submissions", data={"assignment_id": world["a_en"], "student_id": world["s_ok"],
                                                "edits": "not json"}, files=_files(buf.getvalue(), name="p.jpg",
                                                                                   ctype="image/jpeg"), headers=ht)
    assert bad.status_code == 400


def test_apply_edits_ignores_garbage():
    from backend.pipeline.preprocess import apply_edits

    im = Image.new("RGB", (100, 50))
    assert apply_edits(im, None).size == (100, 50)
    assert apply_edits(im, {"rotate": 45, "crop": ["a", 1, 2, 3]}).size == (100, 50)
    assert apply_edits(im, {"crop": [0.5, 0.5, 0.51, 0.51]}).size == (100, 50)  # 过小的裁剪框忽略
    assert apply_edits(im, {"rotate": 270}).size == (50, 100)


def test_client_token_makes_retry_idempotent(client, world, en_png):
    hp = token(client, "t_parent")
    data = {"assignment_id": world["a_en"], "student_id": world["s_ok"], "client_token": "retry-abc-123"}
    r1 = client.post("/api/submissions", data=data, files=_files(en_png), headers=hp)
    r2 = client.post("/api/submissions", data=data, files=_files(en_png), headers=hp)  # 模拟回包丢失后的重试
    assert r1.status_code == r2.status_code == 202
    assert r1.json()["id"] == r2.json()["id"]
    # 另一个用户用同样的令牌不会拿到别人的提交
    r3 = client.post("/api/submissions", data=data, files=_files(en_png), headers=token(client, "t_teacher"))
    assert r3.json()["id"] != r1.json()["id"]


def test_upload_is_not_demo_by_default(client, world, en_png):
    hp = token(client, "t_parent")
    r = client.post("/api/submissions", data={"assignment_id": world["a_en"], "student_id": world["s_ok"]},
                    files=_files(en_png), headers=hp)
    assert r.json()["is_demo"] is False
