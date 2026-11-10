"""大模型评分 provider：Claude（Anthropic SDK）与 OpenAI 兼容接口（DashScope 通义 / 服务器 vLLM）。

两者共享同一套提示词（base.SYSTEM_PROMPT + build_user_prompt）与后处理（postprocess.finalize）。
"""
from __future__ import annotations

import json
import logging

from pydantic import ValidationError

from ...config import get_settings
from ..errors import ProviderError
from .base import SYSTEM_PROMPT, GradingContext, GradingProvider, build_user_prompt
from .postprocess import finalize, parse_llm_json
from .schema import GradingResult, LLMGradingOutput, llm_output_json_schema

log = logging.getLogger(__name__)


class ClaudeGrader(GradingProvider):
    name = "claude"
    is_cloud = True

    def __init__(self, model: str | None = None) -> None:
        s = get_settings()
        self.model = model or s.claude_model
        self.effort = s.claude_effort
        self.use_fallbacks = s.claude_fallbacks

    def available(self) -> tuple[bool, str]:
        if not get_settings().has_anthropic:
            return False, "未设置 ANTHROPIC_API_KEY"
        return True, ""

    def _client(self):  # noqa: ANN202
        import anthropic

        return anthropic.Anthropic()  # 从环境变量读取凭据，不在代码里放 key

    def grade(self, ctx: GradingContext) -> GradingResult:
        import anthropic

        client = self._client()
        kwargs = dict(
            model=self.model,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": build_user_prompt(ctx)}],
            output_config={"effort": self.effort,
                           "format": {"type": "json_schema", "schema": llm_output_json_schema()}},
        )
        try:
            if self.use_fallbacks:
                # 服务端拒答回退：按拒答类别自动换模型重试（Claude API 支持；其他平台需关闭 CLAUDE_FALLBACKS）
                resp = client.beta.messages.create(betas=["server-side-fallback-2026-07-01"], fallbacks="default",
                                                   **kwargs)
            else:
                resp = client.messages.create(**kwargs)
        except anthropic.RateLimitError as e:
            raise ProviderError(f"Claude 限流（429），稍后重试：{e}") from e
        except anthropic.AuthenticationError as e:
            raise ProviderError("Claude 鉴权失败：请检查 ANTHROPIC_API_KEY") from e
        except anthropic.BadRequestError as e:
            raise ProviderError(f"Claude 请求参数错误：{e.message}") from e
        except anthropic.APIStatusError as e:
            raise ProviderError(f"Claude 服务错误 {e.status_code}：{e.message}") from e
        except anthropic.APIConnectionError as e:
            raise ProviderError(f"无法连接 Claude API：{e}") from e

        if resp.stop_reason == "refusal":
            raise ProviderError("模型拒绝处理该内容（refusal），请人工批改")
        if resp.stop_reason == "max_tokens":
            raise ProviderError("模型输出被截断（max_tokens），请人工批改或重试")
        text = next((b.text for b in resp.content if b.type == "text"), "")
        try:
            raw = LLMGradingOutput.model_validate(json.loads(text))
        except (json.JSONDecodeError, ValidationError):
            raw = parse_llm_json(text)
        return finalize(raw, ctx.rubric, ctx.text, ctx.reference, provider=self.name, model=resp.model,
                        ocr_confidence=ctx.ocr_confidence)


class OpenAICompatGrader(GradingProvider):
    """OpenAI 兼容 Chat Completions：DashScope（qwen-plus / qwen-max …）或 vLLM 部署的本地模型。"""

    name = "openai_compat"

    def __init__(self, model: str | None = None) -> None:
        s = get_settings()
        self.base_url, self.api_key, self.is_local = s.openai_compat_resolved("grader")
        self.is_cloud = not self.is_local
        default_model = "qwen-plus" if "dashscope" in self.base_url else ""
        self.model = model or s.openai_compat_grader_model or default_model
        self.json_mode = s.openai_compat_json_mode

    def available(self) -> tuple[bool, str]:
        if not self.base_url:
            return False, "未配置 OPENAI_COMPAT_BASE_URL / DASHSCOPE_API_KEY"
        if not self.model:
            return False, "未配置 OPENAI_COMPAT_GRADER_MODEL"
        return True, ""

    def grade(self, ctx: GradingContext) -> GradingResult:
        import openai

        client = openai.OpenAI(base_url=self.base_url, api_key=self.api_key, timeout=180)
        schema_hint = json.dumps(llm_output_json_schema(), ensure_ascii=False)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT + "\n输出 JSON Schema：\n" + schema_hint},
            {"role": "user", "content": build_user_prompt(ctx)},
        ]
        extra: dict = {}
        if self.json_mode == "json_object":
            extra["response_format"] = {"type": "json_object"}
        elif self.json_mode == "json_schema":  # vLLM guided decoding
            extra["response_format"] = {"type": "json_schema",
                                        "json_schema": {"name": "grading", "schema": llm_output_json_schema()}}
        try:
            resp = client.chat.completions.create(model=self.model, messages=messages, temperature=0, max_tokens=4096,
                                                  **extra)
        except openai.APIError as e:
            raise ProviderError(f"OpenAI 兼容接口调用失败：{e}") from e
        text = resp.choices[0].message.content or ""
        try:
            raw = parse_llm_json(text)
        except (json.JSONDecodeError, ValidationError) as e:
            raise ProviderError(f"模型输出不是合法的评分 JSON：{e}") from e
        return finalize(raw, ctx.rubric, ctx.text, ctx.reference, provider=self.name, model=self.model,
                        ocr_confidence=ctx.ocr_confidence)
