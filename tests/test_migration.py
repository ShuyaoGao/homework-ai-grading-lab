"""极简迁移：老数据库缺少新列时启动自动补列（只加不删）。"""
from __future__ import annotations

import sqlite3

from sqlalchemy import create_engine, inspect

from backend.db import _add_missing_columns


def test_add_missing_columns(tmp_path):
    p = tmp_path / "old.db"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE submissions (id INTEGER PRIMARY KEY, status VARCHAR(16))")
    con.execute("INSERT INTO submissions (status) VALUES ('ai_done')")
    con.commit()
    con.close()
    eng = create_engine(f"sqlite:///{p.as_posix()}")
    _add_missing_columns(eng)
    cols = {c["name"] for c in inspect(eng).get_columns("submissions")}
    assert {"is_demo", "client_token", "final_result"} <= cols
    with eng.connect() as c:
        assert c.exec_driver_sql("SELECT is_demo FROM submissions").scalar() == 0  # 老数据默认非演示
    _add_missing_columns(eng)  # 幂等
    eng.dispose()
