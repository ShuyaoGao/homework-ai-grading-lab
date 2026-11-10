"""OCR provider 实现：mock / Claude 视觉 / OpenAI 兼容视觉（通义 Qwen-VL、vLLM 本地）/ PaddleOCR（可选）。"""
from __future__ import annotations

import base64
import io
import json
import logging
import re

from PIL import Image

from ...config import get_settings
from ..errors import ProviderError
from .base import OCR_INSTRUCTION, OCRContext, OCRPage, OCRProvider, OCRResult, PageInput, join_pages

log = logging.getLogger(__name__)

DEMO_TEXT = {
    "english": "Dear Tom,\nI am writing to tell you about our school English party. It will be held next Friday.\n"
               "[mock：未在图片中找到演示文本，这是一段示例文字]\nYours,\nLi Hua",
    "chinese": "我的老师\n[mock：未在图片中找到演示文本，这是一段示例文字]\n我的老师是一位和蔼可亲的人。",
    "math": "1. x=4\n2. 12\n[mock：未在图片中找到演示文本]",
    "paper": "[mock：未在图片中找到演示文本]",
}


class MockOCR(OCRProvider):
    """mock 识别：读取演示图片里嵌入的标准文本（PNG tEXt 块 edu_eval_text，由 scripts/make_test_images.py 写入）。

    真实拍照的图片里没有这段文本，此时返回示例文字并把置信度设为 0.2，触发“建议人工复核”。
    这个 provider 不做任何真实识别，只用于离线演示和测试流水线。
    """

    name = "mock"
    model = "embedded-text"

    def recognize(self, pages: list[PageInput], ctx: OCRContext) -> OCRResult:
        out, warnings = [], []
        for p in pages:
            if p.embedded_text:
                out.append(OCRPage(p.page_no, p.embedded_text, 0.95))
            else:
                out.append(OCRPage(p.page_no, DEMO_TEXT.get(ctx.subject, DEMO_TEXT["paper"]), 0.2))
                warnings.append(f"第{p.page_no}页没有嵌入演示文本，mock 返回示例文字（不是真实识别结果）")
        conf = min((pg.confidence or 0) for pg in out) if out else None
        return OCRResult(text=join_pages(out), confidence=conf, pages=out, provider=self.name, model=self.model,
                         warnings=warnings)


def _image_b64(path, max_side: int = 1568) -> tuple[str, str]:  # noqa: ANN001
    """压缩到长边 max_side 以内并转 JPEG base64（Claude 视觉建议长边 ≤1568px）。"""
    im = Image.open(path).convert("RGB")
    if max(im.size) > max_side:
        im.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=88)
    return base64.standard_b64encode(buf.getvalue()).decode("ascii"), "image/jpeg"


def _parse_ocr_json(text: str) -> dict:
    t = text.strip()
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", t, re.S)
    if m:
        t = m.group(1)
    else:
        i, j = t.find("{"), t.rfind("}")
        if i >= 0 and j > i:
            t = t[i:j + 1]
    try:
        d = json.loads(t)
        if isinstance(d, dict) and "text" in d:
            return d
    except json.JSONDecodeError:
        pass
    # 模型没按 JSON 输出：整段当作转写文本，置信度未知
    return {"text": text.strip(), "confidence": None, "illegible_count": text.count("[?]"), "notes": "非 JSON 输出"}


OCR_SCHEMA = {
    "type": "object",
    "properties": {"text": {"type": "string"}, "confidence": {"type": "number"},
                   "illegible_count": {"type": "integer"}, "notes": {"type": "string"}},
    "required": ["text", "confidence", "illegible_count", "notes"],
    "additionalProperties": False,
}


class ClaudeOCR(OCRProvider):
    name = "claude"
    is_cloud = True

    def __init__(self, model: str | None = None) -> None:
        s = get_settings()
        self.model = model or s.claude_ocr_model or s.claude_model
        self.effort = s.claude_effort

    def available(self) -> tuple[bool, str]:
        if not get_settings().has_anthropic:
            return False, "未设置 ANTHROPIC_API_KEY"
        return True, ""

    def recognize(self, pages: list[PageInput], ctx: OCRContext) -> OCRResult:
        import anthropic

        client = anthropic.Anthropic()
        out: list[OCRPage] = []
        warnings: list[str] = []
        illegible = 0
        model_used = self.model
        for p in pages:
            data, mt = _image_b64(p.image_path)
            hint = {"english": "这是一篇英语作文。", "chinese": "这是一篇中文作文。", "math": "这是数学/简答题作答。",
                    "paper": "这是一篇小论文。"}.get(ctx.subject, "")
            try:
                resp = client.messages.create(
                    model=self.model, max_tokens=8000,
                    output_config={"effort": "low", "format": {"type": "json_schema", "schema": OCR_SCHEMA}},
                    messages=[{"role": "user", "content": [
                        {"type": "image", "source": {"type": "base64", "media_type": mt, "data": data}},
                        {"type": "text", "text": f"{hint}\n{OCR_INSTRUCTION}"},
                    ]}],
                )
            except anthropic.APIStatusError as e:
                raise ProviderError(f"Claude 识别失败 {e.status_code}：{e.message}") from e
            except anthropic.APIConnectionError as e:
                raise ProviderError(f"无法连接 Claude API：{e}") from e
            if resp.stop_reason == "refusal":
                raise ProviderError("模型拒绝识别该图片（refusal）")
            model_used = resp.model
            text = next((b.text for b in resp.content if b.type == "text"), "")
            d = _parse_ocr_json(text)
            illegible += int(d.get("illegible_count") or 0)
            if d.get("notes"):
                warnings.append(f"第{p.page_no}页：{d['notes']}")
            out.append(OCRPage(p.page_no, d.get("text", ""), d.get("confidence")))
        confs = [pg.confidence for pg in out if pg.confidence is not None]
        return OCRResult(text=join_pages(out), confidence=min(confs) if confs else None, pages=out,
                         provider=self.name, model=model_used, warnings=warnings, illegible_count=illegible)


class OpenAICompatOCR(OCRProvider):
    """OpenAI 兼容视觉接口：DashScope qwen-vl-max / qwen-vl-ocr，或 vLLM 部署的 Qwen2.5-VL / Qwen3-VL。"""

    name = "openai_compat"

    def __init__(self, model: str | None = None) -> None:
        s = get_settings()
        self.base_url, self.api_key, self.is_local = s.openai_compat_resolved("ocr")
        self.is_cloud = not self.is_local
        default_model = "qwen-vl-max" if "dashscope" in self.base_url else ""
        self.model = model or s.openai_compat_ocr_model or default_model

    def available(self) -> tuple[bool, str]:
        if not self.base_url:
            return False, "未配置 OPENAI_COMPAT_BASE_URL / DASHSCOPE_API_KEY"
        if not self.model:
            return False, "未配置 OPENAI_COMPAT_OCR_MODEL"
        return True, ""

    def recognize(self, pages: list[PageInput], ctx: OCRContext) -> OCRResult:
        import openai

        client = openai.OpenAI(base_url=self.base_url, api_key=self.api_key, timeout=180)
        out, warnings, illegible = [], [], 0
        for p in pages:
            data, mt = _image_b64(p.image_path, max_side=2048)
            try:
                resp = client.chat.completions.create(
                    model=self.model, temperature=0, max_tokens=4096,
                    messages=[{"role": "user", "content": [
                        {"type": "image_url", "image_url": {"url": f"data:{mt};base64,{data}"}},
                        {"type": "text", "text": OCR_INSTRUCTION},
                    ]}],
                )
            except openai.APIError as e:
                raise ProviderError(f"OpenAI 兼容视觉接口调用失败：{e}") from e
            d = _parse_ocr_json(resp.choices[0].message.content or "")
            illegible += int(d.get("illegible_count") or 0)
            out.append(OCRPage(p.page_no, d.get("text", ""), d.get("confidence")))
        confs = [pg.confidence for pg in out if pg.confidence is not None]
        return OCRResult(text=join_pages(out), confidence=min(confs) if confs else None, pages=out,
                         provider=self.name, model=self.model, warnings=warnings, illegible_count=illegible)


class PaddleOCRProvider(OCRProvider):
    """本地 PaddleOCR（可选依赖，见 requirements-optional.txt）。对手写中文效果有限，主要用于印刷体/工整字迹。"""

    name = "paddle"
    model = "PaddleOCR"
    _engine = None

    def available(self) -> tuple[bool, str]:
        try:
            import paddleocr  # noqa: F401
        except ImportError:
            return False, "未安装 paddleocr（可选依赖）"
        return True, ""

    def _get(self):  # noqa: ANN202
        if PaddleOCRProvider._engine is None:
            from paddleocr import PaddleOCR

            try:  # PaddleOCR 3.x
                PaddleOCRProvider._engine = PaddleOCR(lang="ch", use_doc_orientation_classify=False,
                                                      use_doc_unwarping=False, use_textline_orientation=True)
            except TypeError:  # 2.x
                PaddleOCRProvider._engine = PaddleOCR(lang="ch", use_angle_cls=True)
        return PaddleOCRProvider._engine

    def recognize(self, pages: list[PageInput], ctx: OCRContext) -> OCRResult:
        eng = self._get()
        out = []
        for p in pages:
            texts, scores = [], []
            if hasattr(eng, "predict"):
                for res in eng.predict(str(p.image_path)):
                    d = res if isinstance(res, dict) else getattr(res, "json", {}).get("res", {})
                    texts += list(d.get("rec_texts", []))
                    scores += [float(s) for s in d.get("rec_scores", [])]
            else:
                for line in (eng.ocr(str(p.image_path), cls=True) or [[]])[0] or []:
                    texts.append(line[1][0])
                    scores.append(float(line[1][1]))
            out.append(OCRPage(p.page_no, "\n".join(texts), sum(scores) / len(scores) if scores else None))
        confs = [pg.confidence for pg in out if pg.confidence is not None]
        return OCRResult(text=join_pages(out), confidence=min(confs) if confs else None, pages=out,
                         provider=self.name, model=self.model)
