"""鉴权与权限范围。"""
from __future__ import annotations

from backend.security import create_access_token, hash_password, verify_password
from tests.conftest import PW, token


def test_password_hash_roundtrip():
    h = hash_password("秘密password")
    assert h.startswith("pbkdf2_sha256$")
    assert verify_password("秘密password", h)
    assert not verify_password("wrong", h)
    assert not verify_password("x", "garbage")


def test_login_and_me(client, world):
    h = token(client, "t_teacher")
    r = client.get("/api/auth/me", headers=h)
    assert r.status_code == 200
    assert r.json()["role"] == "teacher"


def test_login_wrong_password(client, world):
    r = client.post("/api/auth/login", data={"username": "t_teacher", "password": "nope"})
    assert r.status_code == 401


def test_no_token_and_bad_token(client, world):
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/auth/me", headers={"Authorization": "Bearer abc.def.ghi"}).status_code == 401
    forged = create_access_token(999999, "admin")  # 用户不存在
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {forged}"}).status_code == 401


def test_role_guards(client, world):
    hp = token(client, "t_parent")
    body = {"class_id": world["class1"], "title": "x", "subject": "english", "rubric_key": "english_essay_zhongkao"}
    assert client.post("/api/assignments", json=body, headers=hp).status_code == 403
    # 管理员接口
    assert client.get("/api/admin/audit", headers=hp).status_code == 403
    assert client.get("/api/admin/audit", headers=token(client, "t_admin")).status_code == 200


def test_teacher_cannot_touch_other_class(client, world):
    ht2 = token(client, "t_teacher2")
    body = {"class_id": world["class1"], "title": "x", "subject": "english", "rubric_key": "english_essay_zhongkao"}
    assert client.post("/api/assignments", json=body, headers=ht2).status_code == 403
    assert client.get(f"/api/students/{world['s_ok']}/profile", headers=ht2).status_code == 403
    assert client.get(f"/api/classes/{world['class1']}/dashboard", headers=ht2).status_code == 403


def test_parent_scope(client, world):
    hp = token(client, "t_parent")
    ids = {s["id"] for s in client.get("/api/students", headers=hp).json()}
    assert ids == {world["s_ok"], world["s_none"]}
    assert client.get(f"/api/students/{world['s_other']}/profile", headers=hp).status_code == 403
    assert client.get(f"/api/students/{world['s_ok']}/profile", headers=hp).status_code == 200


def test_student_scope(client, world):
    hs = token(client, "t_student")
    ids = {s["id"] for s in client.get("/api/students", headers=hs).json()}
    assert ids == {world["s_ok"]}
    assert client.get(f"/api/students/{world['s_cloud']}", headers=hs).status_code == 403


def test_teacher_can_create_parent_but_not_admin(client, world):
    ht = token(client, "t_teacher")
    r = client.post("/api/users", json={"username": "new_parent", "password": PW, "display_name": "新家长", "role": "parent"},
                    headers=ht)
    assert r.status_code == 201
    r = client.post("/api/users", json={"username": "evil", "password": PW, "display_name": "x", "role": "admin"},
                    headers=ht)
    assert r.status_code == 403


def test_login_throttle(client, world):
    for _ in range(8):
        client.post("/api/auth/login", data={"username": "throttle_me", "password": "x"})
    r = client.post("/api/auth/login", data={"username": "throttle_me", "password": "x"})
    assert r.status_code == 429
