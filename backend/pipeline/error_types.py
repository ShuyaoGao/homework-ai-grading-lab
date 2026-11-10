"""错误类型字典：代码 → 中文名、所属维度、对应知识点。AI 与教师批注统一使用这些代码，便于画像统计。"""
from __future__ import annotations

ERROR_TYPES: dict[str, dict[str, str]] = {
    # 通用 / 英语
    "spelling": {"label": "拼写错误", "dimension": "conventions", "knowledge_point": "英语-单词拼写"},
    "grammar.tense": {"label": "时态错误", "dimension": "language", "knowledge_point": "英语-动词时态"},
    "grammar.agreement": {"label": "主谓一致", "dimension": "language", "knowledge_point": "英语-主谓一致"},
    "grammar.article": {"label": "冠词错误", "dimension": "language", "knowledge_point": "英语-冠词"},
    "grammar.preposition": {"label": "介词错误", "dimension": "language", "knowledge_point": "英语-介词"},
    "grammar.comparative": {"label": "比较级错误", "dimension": "language", "knowledge_point": "英语-形容词比较级"},
    "grammar.other": {"label": "其他语法错误", "dimension": "language", "knowledge_point": "英语-句法"},
    "capitalization": {"label": "大小写错误", "dimension": "conventions", "knowledge_point": "英语-书写规范"},
    "word_choice": {"label": "用词不当", "dimension": "language", "knowledge_point": "词汇运用"},
    "repetition": {"label": "重复/赘余", "dimension": "language", "knowledge_point": "语言简洁"},
    # 中文
    "typo": {"label": "错别字", "dimension": "conventions", "knowledge_point": "语文-字词书写"},
    "punctuation": {"label": "标点错误", "dimension": "conventions", "knowledge_point": "标点符号"},
    "sentence": {"label": "病句", "dimension": "language", "knowledge_point": "语文-病句修改"},
    "off_topic": {"label": "偏题/立意不清", "dimension": "content", "knowledge_point": "审题立意"},
    "length": {"label": "字数/词数不足", "dimension": "content", "knowledge_point": "篇幅要求"},
    "title": {"label": "缺标题", "dimension": "conventions", "knowledge_point": "格式规范"},
    # 结构/论证
    "structure": {"label": "结构问题", "dimension": "organization", "knowledge_point": "篇章结构"},
    "coherence": {"label": "衔接不连贯", "dimension": "organization", "knowledge_point": "衔接与连贯"},
    "logic": {"label": "论证/逻辑问题", "dimension": "development", "knowledge_point": "论证推理"},
    "citation": {"label": "引用不规范", "dimension": "conventions", "knowledge_point": "引用规范"},
    # 数学/简答
    "calculation": {"label": "计算错误", "dimension": "accuracy", "knowledge_point": "运算能力"},
    "concept": {"label": "概念错误", "dimension": "accuracy", "knowledge_point": "概念理解"},
    "missing_step": {"label": "步骤缺失", "dimension": "process", "knowledge_point": "解题过程规范"},
    "unit": {"label": "单位/符号错误", "dimension": "conventions", "knowledge_point": "单位与符号"},
    "incomplete": {"label": "答案不完整", "dimension": "accuracy", "knowledge_point": "答题完整性"},
    "ocr_uncertain": {"label": "识别存疑", "dimension": "", "knowledge_point": ""},
    "other": {"label": "其他", "dimension": "", "knowledge_point": ""},
}

DIMENSION_LABELS = {
    "content": "内容/立意",
    "organization": "结构/连贯",
    "language": "语言表达",
    "conventions": "书写规范",
    "development": "思想深度/创新",
    "accuracy": "答案正确",
    "process": "解题过程",
}


def normalize_error_type(t: str) -> str:
    t = (t or "").strip().lower()
    if t in ERROR_TYPES:
        return t
    if t.startswith("grammar"):
        return "grammar.other"
    aliases = {"错别字": "typo", "标点": "punctuation", "拼写": "spelling", "语法": "grammar.other",
               "病句": "sentence", "计算": "calculation", "引用": "citation", "结构": "structure"}
    for k, v in aliases.items():
        if k in t:
            return v
    return "other"


def label_of(t: str) -> str:
    return ERROR_TYPES.get(t, ERROR_TYPES["other"])["label"]
