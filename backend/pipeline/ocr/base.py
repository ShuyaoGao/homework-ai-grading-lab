"""手写识别 provider 接口。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class PageInput:
    page_no: int
    image_path: Path  # 预处理后的图片
    embedded_text: str = ""  # 演示图片里嵌的标准文本（仅 mock 用）


@dataclass
class OCRPage:
    page_no: int
    text: str
    confidence: float | None = None


@dataclass
class OCRResult:
    text: str
    confidence: float | None
    pages: list[OCRPage]
    provider: str
    model: str = ""
    warnings: list[str] = field(default_factory=list)
    illegible_count: int = 0


@dataclass
class OCRContext:
    subject: str = ""  # english | chinese | math | paper
    language_hint: str = ""  # en | zh | mixed


class OCRProvider(ABC):
    name = "base"
    is_cloud = False
    model = ""

    @abstractmethod
    def recognize(self, pages: list[PageInput], ctx: OCRContext) -> OCRResult: ...

    def available(self) -> tuple[bool, str]:
        return True, ""


OCR_INSTRUCTION = """请逐字转写图片中学生的手写作业内容。
规则：
1. 忠实转写，**不要纠正**任何拼写、语法、错别字或计算错误（这些要留给老师评分）。
2. 保留原有的分段和题号；每个自然段一行，段与段之间换行。忽略印刷的题目、表格线、页眉页脚和涂改掉的内容。
3. 看不清的字用 [?] 表示，不要猜。数学式用普通文本表示，如 2x+3=11、3/4、x^2。
4. confidence 为你对整体转写准确度的估计（0–1）；illegible_count 为 [?] 的个数。
只输出 JSON：{"text": "...", "confidence": 0.9, "illegible_count": 0, "notes": "..."}"""


def join_pages(pages: list[OCRPage]) -> str:
    return "\n".join(p.text.strip() for p in pages if p.text.strip())
