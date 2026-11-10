"""全局配置：全部来自环境变量（可选 .env 文件），绝不在代码里写 key。

provider 选择规则（auto）：
  OCR:    ANTHROPIC_API_KEY → claude；否则 DASHSCOPE_API_KEY → openai_compat(DashScope)；
          否则 OPENAI_COMPAT_BASE_URL（如服务器上的 vLLM）→ openai_compat；否则 mock
  评分:   同上顺序；都没有 → mock（规则启发式评分，离线可跑）
"""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """极简 .env 读取（不覆盖已有环境变量），避免多一个依赖。"""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        os.environ.setdefault(k, v)


_load_dotenv(ROOT / ".env")


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(_env("EDU_DATA_DIR", str(ROOT / "data"))))
    database_url: str = ""
    secret_key: str = ""
    token_expire_minutes: int = int(_env("EDU_TOKEN_EXPIRE_MINUTES", "720"))

    # provider 选择："auto" | "mock" | "claude" | "openai_compat" | "paddle"(仅 OCR)
    ocr_provider: str = _env("OCR_PROVIDER", "auto")
    grader_provider: str = _env("GRADER_PROVIDER", "auto")

    # Claude（Anthropic SDK）
    claude_model: str = _env("CLAUDE_MODEL", "claude-sonnet-5-5")
    claude_ocr_model: str = _env("CLAUDE_OCR_MODEL", "")  # 空则同 claude_model
    claude_effort: str = _env("CLAUDE_EFFORT", "medium")
    claude_fallbacks: bool = _env_bool("CLAUDE_FALLBACKS", True)

    # OpenAI 兼容接口（DashScope 通义 / 服务器 vLLM / 其他）
    openai_compat_base_url: str = _env("OPENAI_COMPAT_BASE_URL", "")
    openai_compat_api_key: str = _env("OPENAI_COMPAT_API_KEY", "")
    # 识别和评分可以分别指向两个服务（如服务器 0 号卡跑视觉模型、1 号卡跑文本模型）；留空则用上面的公共地址
    openai_compat_ocr_base_url: str = _env("OPENAI_COMPAT_OCR_BASE_URL", "")
    openai_compat_grader_base_url: str = _env("OPENAI_COMPAT_GRADER_BASE_URL", "")
    openai_compat_ocr_model: str = _env("OPENAI_COMPAT_OCR_MODEL", "")
    openai_compat_grader_model: str = _env("OPENAI_COMPAT_GRADER_MODEL", "")
    openai_compat_is_local: bool = _env_bool("OPENAI_COMPAT_IS_LOCAL", False)
    openai_compat_json_mode: str = _env("OPENAI_COMPAT_JSON_MODE", "none")  # none|json_object|json_schema

    # 合规
    allow_cloud_providers: bool = _env_bool("ALLOW_CLOUD_PROVIDERS", True)
    retention_days: int = int(_env("EDU_RETENTION_DAYS", "365"))
    show_ai_before_review: bool = _env_bool("EDU_SHOW_AI_BEFORE_REVIEW", True)
    minor_age: int = 14

    # 流水线
    pipeline_sync: bool = _env_bool("EDU_PIPELINE_SYNC", False)
    max_upload_mb: int = int(_env("EDU_MAX_UPLOAD_MB", "15"))
    max_pages: int = int(_env("EDU_MAX_PAGES", "10"))

    # 本机一键演示（启动演示.bat → run.py --demo）：开启 /api/demo/*（演示场景、示例照片、关闭服务）
    demo_mode: bool = _env_bool("EDU_DEMO_MODE", False)

    def __post_init__(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "uploads").mkdir(exist_ok=True)
        if not self.database_url:
            self.database_url = _env("EDU_DATABASE_URL", f"sqlite:///{(self.data_dir / 'edu_eval.db').as_posix()}")
        if not self.secret_key:
            self.secret_key = _env("EDU_SECRET_KEY") or self._persisted_secret()

    def _persisted_secret(self) -> str:
        p = self.data_dir / "secret.key"
        if p.exists():
            return p.read_text(encoding="utf-8").strip()
        s = secrets.token_urlsafe(48)
        p.write_text(s, encoding="utf-8")
        return s

    @property
    def upload_dir(self) -> Path:
        return self.data_dir / "uploads"

    # ---- key 探测（只看是否存在，从不打印值）----
    @property
    def has_anthropic(self) -> bool:
        return bool(_env("ANTHROPIC_API_KEY") or _env("ANTHROPIC_AUTH_TOKEN"))

    @property
    def has_dashscope(self) -> bool:
        return bool(_env("DASHSCOPE_API_KEY"))

    def openai_compat_resolved(self, role: str = "") -> tuple[str, str, bool]:
        """返回 (base_url, api_key, is_local)。role="ocr"/"grader" 时优先用各自的地址。
        DashScope 只在未显式配置任何 base_url 时作为预设。"""
        specific = {"ocr": self.openai_compat_ocr_base_url, "grader": self.openai_compat_grader_base_url}.get(role, "")
        base = specific or self.openai_compat_base_url
        if base:
            key = self.openai_compat_api_key or _env("OPENAI_API_KEY") or "EMPTY"
            return base, key, self.openai_compat_is_local
        if self.has_dashscope:
            return DASHSCOPE_BASE_URL, _env("DASHSCOPE_API_KEY"), False
        return "", "", False

    def resolve_ocr_provider(self) -> str:
        if self.ocr_provider != "auto":
            return self.ocr_provider
        if self.has_anthropic:
            return "claude"
        if self.openai_compat_resolved("ocr")[0]:
            return "openai_compat"
        return "mock"

    def resolve_grader_provider(self) -> str:
        if self.grader_provider != "auto":
            return self.grader_provider
        if self.has_anthropic:
            return "claude"
        if self.openai_compat_resolved("grader")[0]:
            return "openai_compat"
        return "mock"


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings(**overrides) -> Settings:
    """测试用：重建配置。"""
    global _settings
    _settings = Settings(**overrides)
    return _settings
