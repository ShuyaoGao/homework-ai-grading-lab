"""provider 注册表：按名字取实例、列出可用性（给前端/管理员看）。"""
from __future__ import annotations

from ..config import get_settings
from .grading.base import GradingProvider
from .grading.heuristic import MockGrader
from .grading.llm import ClaudeGrader, OpenAICompatGrader
from .ocr.base import OCRProvider
from .ocr.providers import ClaudeOCR, MockOCR, OpenAICompatOCR, PaddleOCRProvider

OCR_PROVIDERS = {"mock": MockOCR, "claude": ClaudeOCR, "openai_compat": OpenAICompatOCR, "paddle": PaddleOCRProvider}
GRADERS = {"mock": MockGrader, "claude": ClaudeGrader, "openai_compat": OpenAICompatGrader}

OCR_LABELS = {"mock": "mock（读取演示图片内嵌文本，离线）", "claude": "Claude 视觉大模型（云端）",
              "openai_compat": "OpenAI 兼容视觉接口（通义 Qwen-VL / 服务器 vLLM）", "paddle": "PaddleOCR（本地，可选）"}
GRADER_LABELS = {"mock": "mock（规则启发式评分，离线）", "claude": "Claude（云端）",
                 "openai_compat": "OpenAI 兼容接口（通义 / 服务器 vLLM）"}


def get_ocr(name: str | None = None, model: str | None = None) -> OCRProvider:
    name = name or get_settings().resolve_ocr_provider()
    cls = OCR_PROVIDERS.get(name)
    if cls is None:
        raise ValueError(f"未知 OCR provider：{name}")
    return cls(model=model) if name in ("claude", "openai_compat") and model else cls()


def get_grader(name: str | None = None, model: str | None = None) -> GradingProvider:
    name = name or get_settings().resolve_grader_provider()
    cls = GRADERS.get(name)
    if cls is None:
        raise ValueError(f"未知评分 provider：{name}")
    return cls(model=model) if name in ("claude", "openai_compat") and model else cls()


def describe_providers() -> dict:
    s = get_settings()

    def info(kind: str, name: str, inst) -> dict:  # noqa: ANN001
        ok, why = inst.available()
        return {"name": name, "label": (OCR_LABELS if kind == "ocr" else GRADER_LABELS)[name], "available": ok,
                "reason": why, "is_cloud": bool(getattr(inst, "is_cloud", False)), "model": getattr(inst, "model", "")}

    return {
        "ocr_default": s.resolve_ocr_provider(),
        "grader_default": s.resolve_grader_provider(),
        "allow_cloud": s.allow_cloud_providers,
        "ocr": [info("ocr", n, c()) for n, c in OCR_PROVIDERS.items()],
        "grader": [info("grader", n, c()) for n, c in GRADERS.items()],
        "keys_present": {"ANTHROPIC_API_KEY": s.has_anthropic, "DASHSCOPE_API_KEY": s.has_dashscope,
                         "OPENAI_COMPAT_BASE_URL": bool(s.openai_compat_base_url)},
    }
