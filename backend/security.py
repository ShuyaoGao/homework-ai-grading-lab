"""密码哈希（PBKDF2-SHA256，标准库）与 JWT（PyJWT, HS256）。"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import os
import threading
import time

import jwt

from .config import get_settings

_ITER = 200_000
ALGO = "HS256"


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _ITER)
    return f"pbkdf2_sha256${_ITER}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt_b64, dk_b64 = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), base64.b64decode(salt_b64), int(iters))
        return hmac.compare_digest(dk, base64.b64decode(dk_b64))
    except Exception:  # noqa: BLE001
        return False


def create_access_token(user_id: int, role: str, expires_minutes: int | None = None) -> str:
    s = get_settings()
    now = dt.datetime.now(dt.timezone.utc)
    exp = now + dt.timedelta(minutes=expires_minutes or s.token_expire_minutes)
    payload = {"sub": str(user_id), "role": role, "iat": now, "exp": exp}
    return jwt.encode(payload, s.secret_key, algorithm=ALGO)


def decode_token(token: str) -> dict:
    return jwt.decode(token, get_settings().secret_key, algorithms=[ALGO])


class LoginThrottle:
    """简单的内存登录失败限流：同一用户名 10 分钟内失败 ≥8 次则锁 10 分钟。"""

    def __init__(self, max_fail: int = 8, window: int = 600) -> None:
        self.max_fail, self.window = max_fail, window
        self._fails: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def blocked(self, key: str) -> bool:
        with self._lock:
            now = time.time()
            hits = [t for t in self._fails.get(key, []) if now - t < self.window]
            self._fails[key] = hits
            return len(hits) >= self.max_fail

    def fail(self, key: str) -> None:
        with self._lock:
            self._fails.setdefault(key, []).append(time.time())

    def reset(self, key: str) -> None:
        with self._lock:
            self._fails.pop(key, None)


login_throttle = LoginThrottle()
