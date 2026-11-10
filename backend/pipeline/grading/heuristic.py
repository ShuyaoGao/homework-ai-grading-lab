"""mock 评分器 = 离线规则启发式评分（不调用任何模型）。

用途：①没有 API key 时让整个系统能完整跑通；②作为评估工具中的“规则基线”。
它真的会读文本（词数/字数、常见拼写与语法错误、错别字、衔接词、要点覆盖、参考答案比对、
论文结构与引用对应），但只是粗糙近似——置信度固定偏低，结果一律建议人工复核。
"""
from __future__ import annotations

import math
import re
from fractions import Fraction

from ..rubrics import Rubric
from .base import GradingContext, GradingProvider
from .postprocess import finalize
from .schema import GradingResult, LLMCriterion, LLMDeduction, LLMError, LLMGradingOutput, LLMKnowledgePoint

# ------------------------------------------------------------------ 公共


class RuleError(LLMError):
    start: int | None = None
    end: int | None = None


def _half(x: float) -> float:
    return round(x * 2) / 2


def _crit(rubric_crit_max: float, ratio: float) -> float:
    return _half(max(0.0, min(1.0, ratio)) * rubric_crit_max)


# ------------------------------------------------------------------ 英语

EN_MISSPELL = {
    "becouse": "because", "beacuse": "because", "freind": "friend", "freinds": "friends", "beautifull": "beautiful",
    "untill": "until", "recieve": "receive", "tommorow": "tomorrow", "tomorow": "tomorrow", "wich": "which",
    "thier": "their", "alot": "a lot", "realy": "really", "definately": "definitely", "goverment": "government",
    "enviroment": "environment", "intresting": "interesting", "interisting": "interesting", "studing": "studying",
    "writting": "writing", "begining": "beginning", "happend": "happened", "differnt": "different",
    "importent": "important", "knowlege": "knowledge", "wether": "whether", "whith": "with", "famliy": "family",
    "familly": "family", "hobbys": "hobbies", "pepole": "people", "peaple": "people", "togather": "together",
    "sucess": "success", "succesful": "successful", "excercise": "exercise", "exercize": "exercise",
    "childs": "children", "foots": "feet", "mouses": "mice", "buyed": "bought", "goed": "went", "teached": "taught",
    "thinked": "thought", "runned": "ran", "swimed": "swam", "stoped": "stopped", "planed": "planned",
    "libary": "library", "hospitel": "hospital", "vegetabels": "vegetables", "healthly": "healthy",
}
EN_IRREGULAR_PAST = {"goed", "buyed", "teached", "thinked", "runned", "swimed", "stoped", "planed"}

EN_CONNECTIVES = [
    "first", "firstly", "second", "secondly", "then", "next", "finally", "lastly", "however", "moreover",
    "besides", "in addition", "what's more", "in a word", "in conclusion", "as a result", "therefore",
    "for example", "for instance", "on the one hand", "on the other hand", "in my opinion", "above all",
    "to begin with", "in short", "although", "because", "so that", "not only",
]
EN_COMPLEX = ["which", "who", "whose", "that", "because", "although", "though", "when", "while", "if", "unless",
              "so that", "not only", "as soon as", "whether", "what"]
EN_STOP = set("""a an the and or but so to of in on at for with by from as is are was were be been being it its this
that these those i you he she we they me him her us them my your his our their not no yes do does did have has had
will would can could should may might must about into over after before than then there here what which who whom
whose when where why how all any some many much more most very also just only too up down out if because while""".split())

_EN_RULES: list[tuple[str, str, str, str, str]] = [
    # (regex, type, message, suggestion, severity)
    (r"\b(?:he|she|it|my (?:mother|father|friend|teacher|sister|brother)|everyone|everybody)\s+"
     r"(?:go|like|have|do|want|play|watch|think|make|take|get|study|live|need)\b",
     "grammar.agreement", "主语是第三人称单数，谓语动词要用单三形式", "go→goes / have→has 等", "major"),
    (r"\b(?:he|she|it)\s+don't\b", "grammar.agreement", "第三人称单数的否定应为 doesn't", "doesn't", "major"),
    (r"\bthere is (?:many|lots of|a lot of|some \w+s)\b", "grammar.agreement", "后接复数名词时用 there are", "there are", "minor"),
    (r"\ba\s+(?!uni|use|usu|one|eur|u\b)(?:[aeiou]\w*)", "grammar.article", "元音音素开头的单词前用 an", "an", "minor"),
    (r"\bmore\s+(?:better|worse|bigger|happier|easier|harder|faster|taller|smaller)\b", "grammar.comparative",
     "比较级前不能再加 more", "去掉 more", "minor"),
    (r"\b(?:didn't|did not)\s+(?:went|saw|came|ate|took|\w+ed)\b", "grammar.tense", "did/didn't 后接动词原形", "动词原形", "major"),
    (r"\benjoy(?:s|ed)? to \w+", "grammar.other", "enjoy 后接动名词", "enjoy doing", "minor"),
    (r"\blook(?:ing)? forward to (?:see|hear|meet|visit|go)\b", "grammar.other", "look forward to 中 to 是介词，后接动名词",
     "look forward to seeing/hearing", "minor"),
    (r"\bvery like\b", "word_choice", "very 不能直接修饰动词 like", "really like / like ... very much", "minor"),
    (r"\bspend(?:s)? \w+ (?:time|hours|minutes) to \w+", "grammar.other", "spend time (in) doing", "spend ... doing", "minor"),
    (r"\bwas \w+ing yesterday and go\b", "grammar.tense", "前后时态不一致", "统一为过去时", "minor"),
]


def _en_errors(text: str) -> list[RuleError]:
    errs: list[RuleError] = []
    for m in re.finditer(r"[A-Za-z']+", text):
        w = m.group(0)
        lw = w.lower()
        if lw in EN_MISSPELL:
            et = "grammar.tense" if lw in EN_IRREGULAR_PAST else "spelling"
            errs.append(RuleError(type=et, quote=w, message=f"“{w}”拼写/词形错误", suggestion=EN_MISSPELL[lw],
                                  severity="minor", criterion_id="language", start=m.start(), end=m.end()))
    for m in re.finditer(r"(?<![A-Za-z'])i(?=\s+[a-z])", text):
        nxt = re.match(r"i\s+\w+", text[m.start():])
        q = nxt.group(0) if nxt else "i"
        errs.append(RuleError(type="capitalization", quote=q, message="代词 I 必须大写", suggestion="I", severity="minor",
                              criterion_id="language", start=m.start(), end=m.start() + len(q)))
    for m in re.finditer(r"(?:^|[.!?]\s+)([a-z][a-z']*)", text):
        w0 = m.group(1)
        if w0 == "i":
            continue
        errs.append(RuleError(type="capitalization", quote=w0, message="句首字母应大写", suggestion=w0.capitalize(),
                              severity="minor", criterion_id="language", start=m.start(1), end=m.end(1)))
    for m in re.finditer(r"\b(\w+)\s+\1\b", text, flags=re.I):
        if m.group(1).lower() in {"that", "had"}:
            continue
        errs.append(RuleError(type="repetition", quote=m.group(0), message="单词重复", suggestion=m.group(1),
                              severity="minor", criterion_id="language", start=m.start(), end=m.end()))
    for pat, et, msg, sug, sev in _EN_RULES:
        for m in re.finditer(pat, text, flags=re.I):
            errs.append(RuleError(type=et, quote=m.group(0), message=msg, suggestion=sug, severity=sev,
                                  criterion_id="language", start=m.start(), end=m.end()))
    # 去重（同一位置只留一条）
    seen: set[tuple[int, int]] = set()
    out = []
    for e in sorted(errs, key=lambda e: (e.start or 0)):
        k = (e.start or 0, e.end or 0)
        if k in seen:
            continue
        seen.add(k)
        out.append(e)
    return out


def _mattr(tokens: list[str], window: int = 40) -> float:
    if not tokens:
        return 0.0
    if len(tokens) <= window:
        return len(set(tokens)) / len(tokens)
    vals = [len(set(tokens[i:i + window])) / window for i in range(0, len(tokens) - window + 1, 5)]
    return sum(vals) / len(vals)


def _keypoint_coverage(text: str, key_points: list) -> tuple[float, list[str]]:
    """key_points: [["birthday", "party"], "invite", ...] 每个要点为关键词或同义关键词列表（命中任一即覆盖）。"""
    low = text.lower()
    missed = []
    hit = 0
    for kp in key_points:
        alts = kp if isinstance(kp, list) else [kp]
        if any(str(a).lower() in low for a in alts):
            hit += 1
        else:
            missed.append(str(alts[0]))
    return (hit / len(key_points) if key_points else 0.0), missed


def grade_english(ctx: GradingContext) -> LLMGradingOutput:
    r, text = ctx.rubric, ctx.text
    words = re.findall(r"[A-Za-z']+", text)
    n = len(words)
    lw = [w.lower() for w in words]
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if re.search(r"[A-Za-z]", s)]
    ns = max(1, len(sentences))
    paragraphs = [p for p in re.split(r"\n\s*\n|\n(?=\s{2,}|\t)", text) if p.strip()]
    lo, hi = (ctx.reference.get("target_words") or r.heuristic.get("target_words") or [80, 120])
    errors = _en_errors(text)
    weight = {"minor": 1.0, "major": 1.6, "critical": 2.5}
    density = sum(weight[e.severity] for e in errors) / max(n, 1) * 100

    kps = ctx.reference.get("key_points") or []
    if kps:
        coverage, missed = _keypoint_coverage(text, kps)
    else:
        pw = {w for w in re.findall(r"[a-z]{4,}", ctx.prompt.lower()) if w not in EN_STOP}
        overlap = len(pw & set(lw)) / len(pw) if pw else 0.6
        coverage, missed = min(1.0, 0.35 + overlap * 1.3), []
    length_factor = min(1.0, n / lo) if lo else 1.0
    if n > hi * 1.6:
        length_factor *= 0.9
    content_ratio = 0.65 * coverage + 0.35 * length_factor
    if n < 15:
        content_ratio *= 0.3

    mattr = _mattr(lw)
    long_words = sum(1 for w in lw if len(w) >= 8) / max(n, 1)
    complex_hits = sum(len(re.findall(rf"\b{re.escape(c)}\b", text, flags=re.I)) for c in EN_COMPLEX)
    vocab = min(1.0, 0.45 * min(1.0, (mattr - 0.45) / 0.35) + 0.25 * min(1.0, long_words / 0.12)
                + 0.30 * min(1.0, complex_hits / (ns * 0.6)))
    accuracy = max(0.05, 1.0 - density * 0.075)
    language_ratio = 0.62 * accuracy + 0.38 * max(0.0, vocab)
    if n < 15:
        language_ratio *= 0.4

    conn = sum(len(re.findall(rf"(?<![A-Za-z]){re.escape(c)}(?![A-Za-z])", text, flags=re.I)) for c in EN_CONNECTIVES)
    org_ratio = 0.35 + 0.12 * min(conn, 4) + (0.1 if len(paragraphs) >= 2 else 0) + (0.07 if ns >= 5 else 0)
    if ns < 3:
        org_ratio *= 0.5

    crit_ratio = {"content": content_ratio, "language": language_ratio, "organization": min(1.0, org_ratio)}
    criteria = []
    for c in r.criteria:
        ratio = crit_ratio.get(c.id, (content_ratio + language_ratio) / 2)
        criteria.append(LLMCriterion(id=c.id, score=_crit(c.max, ratio), comment=""))

    deductions = []
    if any(d.id == "word_count" for d in r.deductions) and lo and n < 0.75 * lo:
        deductions.append(LLMDeduction(id="word_count", points=2, reason=f"词数约 {n}，低于要求 {lo} 词的 75%"))
        errors.append(RuleError(type="length", quote="", message=f"词数约 {n}，明显不足（要求约 {lo}–{hi} 词）",
                                severity="major", criterion_id="content"))

    strengths, suggestions = [], []
    if coverage >= 0.99 and kps:
        strengths.append("要点齐全，完成了写作任务")
    if conn >= 3:
        strengths.append("使用了较多衔接词，行文较连贯")
    if vocab >= 0.6:
        strengths.append("词汇和句式比较丰富")
    if accuracy >= 0.85 and n >= 30:
        strengths.append("语言基本准确，错误较少")
    if missed:
        suggestions.append("遗漏要点：" + "、".join(missed[:4]))
    top = _top_types(errors)
    if top:
        suggestions.append("重点订正：" + "、".join(top))
    if conn < 2:
        suggestions.append("多用 first / however / in addition 等衔接词，让段落之间更连贯")
    if vocab < 0.45:
        suggestions.append("尝试使用定语从句、状语从句等复杂句，并替换重复的简单词汇")
    if length_factor < 0.9:
        suggestions.append(f"篇幅偏短（约 {n} 词），注意满足词数要求")
    comment = _compose_comment(strengths, suggestions)
    return LLMGradingOutput(
        criteria=criteria, deductions=deductions, errors=errors, comment=comment, strengths=strengths,
        suggestions=suggestions, confidence=0.55 if n >= 40 else 0.4,
        knowledge_points=_kp_from_errors(errors, n_units=n / 100),
        review_reasons=["规则启发式评分（mock），仅作流程演示与基线参考"],
    )


# ------------------------------------------------------------------ 中文

ZH_TYPOS = {
    "在接再厉": "再接再厉", "再接再励": "再接再厉", "迫不急待": "迫不及待", "既使": "即使", "默守成规": "墨守成规",
    "一股作气": "一鼓作气", "甘败下风": "甘拜下风", "谈笑风声": "谈笑风生", "走头无路": "走投无路",
    "声名狼籍": "声名狼藉", "按步就班": "按部就班", "委屈求全": "委曲求全", "穿流不息": "川流不息",
    "出奇不意": "出其不意", "变本加利": "变本加厉", "原形必露": "原形毕露", "一愁莫展": "一筹莫展",
    "再所不惜": "在所不惜", "金榜提名": "金榜题名", "美仑美奂": "美轮美奂", "情不自尽": "情不自禁",
    "脍灸人口": "脍炙人口", "鬼鬼崇崇": "鬼鬼祟祟", "坐无虚席": "座无虚席", "因该": "应该", "以经": "已经",
    "必竟": "毕竟", "部份": "部分", "震憾": "震撼", "克苦": "刻苦", "即然": "既然", "安祥": "安详",
    "松驰": "松弛", "一如继往": "一如既往", "不径而走": "不胫而走", "名符其实": "名副其实", "题纲": "提纲",
    "寒喧": "寒暄", "迫切需求": "迫切需求", "精兵减政": "精兵简政", "人才辈出": "人才辈出", "合盘托出": "和盘托出",
    "破斧沉舟": "破釜沉舟", "世外桃园": "世外桃源", "滥芋充数": "滥竽充数", "悬梁刺骨": "悬梁刺股",
    "披星带月": "披星戴月", "心心相映": "心心相印", "随声附合": "随声附和", "直接了当": "直截了当",
    "兴高彩烈": "兴高采烈", "忘乎所已": "忘乎所以", "沤心沥血": "呕心沥血", "历历再目": "历历在目",
    "专心至志": "专心致志", "举一返三": "举一反三", "持之以衡": "持之以恒", "锲而不舍": "锲而不舍",
    "滴水穿石": "滴水石穿", "汗流夹背": "汗流浃背", "高谈阔论": "高谈阔论", "无可非义": "无可非议",
}
ZH_TYPOS = {k: v for k, v in ZH_TYPOS.items() if k != v}

ZH_CONNECTIVES = ["因此", "所以", "然而", "但是", "可是", "不仅", "而且", "首先", "其次", "再次", "最后", "总之",
                  "综上", "由此可见", "一方面", "另一方面", "例如", "比如", "正因为", "于是", "因为", "虽然", "即使",
                  "既然", "况且", "何况", "换言之"]
ZH_RHETORIC = ["像", "仿佛", "如同", "好似", "宛如", "犹如", "难道", "岂不"]
ZH_CITE = ["曾说", "说过", "有言", "古人云", "俗话说", "有句话", "名言", "诗云", "曰", "写道", "正如"]

_CJK = re.compile(r"[一-鿿]")


def _zh_len(text: str) -> int:
    return len(_CJK.findall(text))


def _zh_errors(text: str) -> tuple[list[RuleError], int, int]:
    errs: list[RuleError] = []
    typo_set = set()
    for wrong, right in ZH_TYPOS.items():
        for m in re.finditer(re.escape(wrong), text):
            errs.append(RuleError(type="typo", quote=wrong, message=f"错别字：“{wrong}”应为“{right}”",
                                  suggestion=right, severity="minor", start=m.start(), end=m.end()))
            typo_set.add(wrong)
    punct = 0
    for m in re.finditer(r"[一-鿿]([,.;:!?])[一-鿿]", text):
        errs.append(RuleError(type="punctuation", quote=m.group(0), message="中文行文中误用了半角标点",
                              suggestion="改为全角标点（，。；：！？）", severity="minor", start=m.start(), end=m.end()))
        punct += 1
    for m in re.finditer(r"([，。！？；、])\1+", text):
        errs.append(RuleError(type="punctuation", quote=m.group(0), message="标点重复", suggestion=m.group(1),
                              severity="minor", start=m.start(), end=m.end()))
        punct += 1
    # 一逗到底：一句里 ≥7 个逗号
    for m in re.finditer(r"[^。！？\n]{40,}", text):
        seg = m.group(0)
        if seg.count("，") >= 7:
            q = seg[:16]
            errs.append(RuleError(type="punctuation", quote=q, message="一逗到底，句子过长，层次不清",
                                  suggestion="在意思完整处用句号断开", severity="minor", start=m.start(),
                                  end=m.start() + len(q)))
            punct += 1
    for m in re.finditer(r"([一-鿿])\1\1+", text):
        if m.group(1) in "哈呵啊嘻":
            continue
        errs.append(RuleError(type="repetition", quote=m.group(0), message="字词重复", suggestion=m.group(1),
                              severity="minor", start=m.start(), end=m.end()))
    for m in re.finditer(r"(的的|了了(?!解)|是是)", text):
        errs.append(RuleError(type="repetition", quote=m.group(0), message="字词重复", suggestion=m.group(0)[0],
                              severity="minor", start=m.start(), end=m.end()))
    # 常见病句：成分残缺（“通过……使……”）
    for m in re.finditer(r"通过[^。！？，]{1,30}，使[^。！？]{1,30}", text):
        q = m.group(0)[:20]
        errs.append(RuleError(type="sentence", quote=q, message="病句：“通过……使……”造成主语残缺",
                              suggestion="删去“通过”或“使”", severity="major", start=m.start(), end=m.start() + len(q)))
    return errs, len(typo_set), punct


def _bigrams(s: str) -> set[str]:
    cjk = "".join(_CJK.findall(s))
    return {cjk[i:i + 2] for i in range(len(cjk) - 1)}


_ZH_STOP_BIGRAMS = {"我们", "一个", "自己", "他们", "什么", "没有", "这个", "那个", "可以", "因为", "所以", "但是",
                    "就是", "不是", "这样", "已经", "还是", "作文", "题目", "要求", "字数", "不少", "少于", "文体",
                    "自拟", "不限", "写一", "一篇", "篇文", "文章", "以上", "根据", "材料", "下面", "阅读", "请以",
                    "为题", "立意", "角度", "不得", "抄袭", "套作", "诗歌", "除外", "自选"}


def grade_chinese(ctx: GradingContext) -> LLMGradingOutput:
    r, text = ctx.rubric, ctx.text
    n = _zh_len(text)
    lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
    has_title = bool(lines) and len(lines) > 1 and _zh_len(lines[0]) <= 20 and not re.search(r"[。！？，]$", lines[0])
    body = "\n".join(lines[1:] if has_title else lines)
    paragraphs = [ln for ln in (lines[1:] if has_title else lines) if _zh_len(ln) >= 5]
    min_chars = int(ctx.reference.get("min_chars") or r.heuristic.get("min_chars") or 800)
    errors, n_typo, n_punct = _zh_errors(text)

    kw = ctx.reference.get("keywords") or []
    if kw:
        rel = sum(1 for k in kw if k in text) / len(kw)
    else:
        pb = _bigrams(ctx.prompt) - _ZH_STOP_BIGRAMS
        rel = min(1.0, 0.3 + 3.0 * len(pb & _bigrams(body)) / len(pb)) if pb else 0.7
    length_factor = min(1.0, n / min_chars) if min_chars else 1.0
    sentences = [s for s in re.split(r"[。！？!?]", body) if _zh_len(s) >= 2]
    avg_sent = sum(_zh_len(s) for s in sentences) / max(1, len(sentences))
    content_ratio = 0.5 * rel + 0.35 * length_factor + 0.15 * min(1.0, len(paragraphs) / 4)
    if n < 100:
        content_ratio *= 0.4

    conn = sum(body.count(c) for c in ZH_CONNECTIVES)
    sent_ok = 1.0 if 12 <= avg_sent <= 45 else 0.7
    err_density = (len(errors)) / max(n, 1) * 1000
    expression_ratio = (0.35 + 0.08 * min(conn, 5) + 0.1 * min(1.0, len(paragraphs) / 4)) * sent_ok \
        * max(0.4, 1 - err_density * 0.02) + 0.1 * length_factor
    if n < 100:
        expression_ratio *= 0.5

    rhet = sum(body.count(c) for c in ZH_RHETORIC)
    cites = sum(body.count(c) for c in ZH_CITE) + body.count("“") // 2
    uniq = len(set(_CJK.findall(body))) / max(1, n)
    paiibi = _parallelism(body)
    development_ratio = 0.2 + 0.08 * min(rhet, 4) + 0.08 * min(cites, 3) + 0.12 * paiibi + 0.25 * min(1.0, uniq / 0.45)
    development_ratio *= (0.6 + 0.4 * length_factor)

    ratios = {"content": content_ratio, "expression": expression_ratio, "development": development_ratio}
    criteria = [LLMCriterion(id=c.id, score=_crit(c.max, ratios.get(c.id, 0.5))) for c in r.criteria]
    ded_ids = {d.id for d in r.deductions}
    deductions = []
    if "typo" in ded_ids and n_typo:
        deductions.append(LLMDeduction(id="typo", points=min(5, n_typo), reason=f"发现 {n_typo} 个错别字（重复不计）"))
    if "title" in ded_ids and not has_title and n >= 100:
        deductions.append(LLMDeduction(id="title", points=2, reason="未发现题目"))
        errors.append(RuleError(type="title", quote="", message="缺少标题", severity="minor"))
    if "length" in ded_ids and n < min_chars:
        pts = math.ceil((min_chars - n) / 50)
        deductions.append(LLMDeduction(id="length", points=min(10, pts), reason=f"约 {n} 字，不足 {min_chars} 字"))
        errors.append(RuleError(type="length", quote="", message=f"字数约 {n}，不足 {min_chars} 字", severity="major"))
    if "punctuation" in ded_ids and n_punct >= 3:
        deductions.append(LLMDeduction(id="punctuation", points=1 if n_punct < 6 else 2, reason=f"标点问题 {n_punct} 处"))

    strengths, suggestions = [], []
    if rel >= 0.75:
        strengths.append("紧扣题意，中心较明确")
    if conn >= 4:
        strengths.append("关联词使用得当，层次较清楚")
    if rhet + paiibi >= 2:
        strengths.append("运用了比喻、排比等修辞，语言有一定表现力")
    if cites >= 1:
        strengths.append("能引用名言或材料支撑观点")
    if rel < 0.6:
        suggestions.append("审题再仔细些，文章要紧扣题目材料中的关键词立意")
    if len(paragraphs) < 3:
        suggestions.append("段落层次偏少，建议按“提出观点—分层论述—总结升华”组织结构")
    if n_typo:
        suggestions.append(f"订正 {n_typo} 个错别字")
    if n < min_chars:
        suggestions.append(f"篇幅不足（约 {n} 字），注意写满 {min_chars} 字")
    if rhet + paiibi + cites == 0:
        suggestions.append("适当运用修辞和名言事例，增强文采与说服力")
    comment = _compose_comment(strengths, suggestions)
    return LLMGradingOutput(criteria=criteria, deductions=deductions, errors=errors, comment=comment,
                            strengths=strengths, suggestions=suggestions, confidence=0.5 if n >= 200 else 0.35,
                            knowledge_points=_kp_from_errors(errors, n_units=n / 300),
                            review_reasons=["规则启发式评分（mock），仅作流程演示与基线参考"])


def _parallelism(body: str) -> int:
    clauses = [c.strip() for c in re.split(r"[，。；！？]", body) if len(c.strip()) >= 4]
    count = 0
    for i in range(len(clauses) - 2):
        a, b, c = clauses[i][:2], clauses[i + 1][:2], clauses[i + 2][:2]
        if a == b == c:
            count += 1
    return min(count, 3)


# ------------------------------------------------------------------ 简答 / 数学

_QMARK = re.compile(r"^\s*(?:第\s*(\d+)\s*题|(\d+)\s*[.、．:：)]|[（(]\s*(\d+)\s*[)）])", re.M)
_NUM = re.compile(r"-?\d+(?:\.\d+)?(?:/\d+)?")


def _split_answers(text: str) -> dict[str, tuple[str, int]]:
    """按题号切分作答，返回 {题号: (作答文本, 在原文中的起始位置)}。"""
    marks = list(_QMARK.finditer(text))
    if not marks:
        return {"__all__": (text, 0)}
    out: dict[str, tuple[str, int]] = {}
    for i, m in enumerate(marks):
        qid = next(g for g in m.groups() if g)
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        seg = text[m.end():end]
        lead = len(seg) - len(seg.lstrip())
        out.setdefault(qid, (seg.strip(), m.end() + lead))
    return out


def _to_num(s: str) -> Fraction | None:
    try:
        return Fraction(s)
    except (ValueError, ZeroDivisionError):
        return None


def _final_answer(ans: str) -> str:
    m = re.search(r"答[:：]?(.*)$", ans, re.S)
    if m and m.group(1).strip():
        return m.group(1).strip()
    if "=" in ans:
        return ans.rsplit("=", 1)[1].strip()
    nums = _NUM.findall(ans)
    return nums[-1] if nums else ans.strip()


def grade_short_answer(ctx: GradingContext) -> LLMGradingOutput:
    qs = ctx.reference.get("questions") or []
    tol = float(ctx.rubric.heuristic.get("numeric_tolerance", 1e-3))
    answers = _split_answers(ctx.text)
    criteria, errors, kps = [], [], []
    for i, q in enumerate(qs):
        qid = str(q.get("id", i + 1))
        pts = float(q.get("points", 1))
        ans, pos = answers.get(qid) or (answers.get("__all__") if len(qs) == 1 else None) or ("", 0)
        last_line = ans.strip().splitlines()[-1].strip() if ans.strip() else ""
        ref = str(q.get("answer", "")).strip()
        steps = q.get("steps") or []
        unit = q.get("unit", "")
        final = _final_answer(ans) if ans else ""
        correct = False
        ref_nums = _NUM.findall(ref)
        if ref_nums and final:
            rv = _to_num(ref_nums[-1])
            fn = _NUM.findall(final)
            sv = _to_num(fn[-1]) if fn else None
            correct = rv is not None and sv is not None and abs(float(rv) - float(sv)) <= tol * max(1, abs(float(rv)))
        elif ref and final:
            kw = q.get("keywords") or [ref]
            hit = sum(1 for k in kw if str(k) in ans)
            correct = hit / len(kw) >= 0.6
        step_hit = sum(1 for s in steps if s and str(s).replace(" ", "") in ans.replace(" ", "")) if steps else 0
        step_ratio = step_hit / len(steps) if steps else (1.0 if correct else 0.0)
        if not ans.strip():
            score, comment = 0.0, "未作答"
            errors.append(RuleError(type="incomplete", quote="", message=f"第{qid}题未作答或未识别到答案",
                                    severity="major", criterion_id=qid))
        elif correct:
            score = pts if (not steps or step_ratio >= 0.5) else pts * 0.8
            comment = "答案正确" + ("" if not steps or step_ratio >= 0.5 else "，但过程不完整")
            if steps and step_ratio < 0.5:
                errors.append(RuleError(type="missing_step", quote=last_line[:40], message=f"第{qid}题过程步骤缺失",
                                        severity="minor", criterion_id=qid))
        else:
            score = round(pts * 0.6 * step_ratio * 2) / 2
            comment = f"答案与参考答案（{ref}）不一致" + ("，部分步骤正确" if step_hit else "")
            et = "calculation" if ref_nums and _NUM.findall(final) else "concept"
            errors.append(RuleError(type=et, quote=last_line[:40],
                                    message=f"第{qid}题答案错误，参考答案：{ref}", suggestion=ref, severity="major",
                                    criterion_id=qid))
        if unit and ans and correct and unit not in ans:
            score = max(0.0, score - pts * 0.1)
            errors.append(RuleError(type="unit", quote=last_line[:40],
                                    message=f"第{qid}题缺少单位“{unit}”", suggestion=unit, severity="minor",
                                    criterion_id=qid))
        for e in errors:
            if e.criterion_id == qid and e.quote and e.start is None:
                i = ctx.text.find(e.quote, pos)
                if i >= 0:
                    e.start, e.end = i, i + len(e.quote)
        criteria.append(LLMCriterion(id=qid, score=round(score, 2), comment=comment))
        if q.get("knowledge_point"):
            kps.append(LLMKnowledgePoint(name=q["knowledge_point"], score_ratio=round(score / pts, 3) if pts else 0))
    right = sum(1 for c in criteria if c.comment.startswith("答案正确"))
    comment = f"共 {len(qs)} 题，{right} 题答案正确。" + ("请重点订正标红的题目。" if right < len(qs) else "全部正确，继续保持！")
    return LLMGradingOutput(criteria=criteria, errors=errors, comment=comment, knowledge_points=kps,
                            confidence=0.6, review_reasons=["规则启发式评分（mock），仅作流程演示与基线参考"])


# ------------------------------------------------------------------ 论文

PAPER_SECTIONS = {
    "摘要": ["摘要", "abstract"],
    "引言": ["引言", "前言", "问题提出", "研究背景", "introduction"],
    "方法": ["研究方法", "方法", "调查方法", "研究设计", "method"],
    "结果与分析": ["结果", "分析", "讨论", "调查结果", "results", "discussion"],
    "结论": ["结论", "结语", "总结", "conclusion"],
    "参考文献": ["参考文献", "references"],
}


def grade_paper(ctx: GradingContext) -> LLMGradingOutput:
    r, text = ctx.rubric, ctx.text
    low = text.lower()
    found = [k for k, alts in PAPER_SECTIONS.items() if any(a in low for a in alts)]
    missing = [k for k in PAPER_SECTIONS if k not in found]
    n = _zh_len(text) + len(re.findall(r"[A-Za-z]+", text))
    min_chars = int(ctx.reference.get("min_chars") or r.heuristic.get("min_chars") or 1200)

    ref_pos = max(low.rfind("参考文献"), low.rfind("references"))
    body = text[:ref_pos] if ref_pos > 0 else text
    refs_part = text[ref_pos:] if ref_pos > 0 else ""
    cited_nums = {int(x) for x in re.findall(r"\[(\d{1,3})\]", body)}
    author_year = re.findall(r"[（(][^()（）]{1,30}?[,，]\s*\d{4}[a-z]?[)）]", body)
    ref_entries = re.findall(r"^\s*\[?(\d{1,3})[\].、]\s*\S.*$", refs_part, re.M)
    ref_nums = {int(x) for x in ref_entries}
    errors: list[RuleError] = []
    if not refs_part:
        errors.append(RuleError(type="citation", quote="", message="缺少参考文献列表", severity="major",
                                criterion_id="citation"))
    for num in sorted(cited_nums - ref_nums) if refs_part else []:
        q = f"[{num}]"
        errors.append(RuleError(type="citation", quote=q, message=f"文中引用 {q} 在参考文献列表中找不到",
                                severity="major", criterion_id="citation"))
    uncited = sorted(ref_nums - cited_nums) if cited_nums else []
    if uncited:
        errors.append(RuleError(type="citation", quote="", message=f"参考文献 {uncited} 未在正文中引用",
                                severity="minor", criterion_id="citation"))
    for m in re.finditer(r"(?:有研究表明|研究发现|数据显示|专家指出|据统计)[^。\[（(]{0,40}。", body):
        q = m.group(0)[:24]
        errors.append(RuleError(type="citation", quote=q, message="引用观点或数据但未标注出处", suggestion="补充文献标注",
                                severity="minor", criterion_id="citation", start=m.start(), end=m.start() + len(q)))
    zh_err, n_typo, _ = _zh_errors(text)
    for e in zh_err:
        e.criterion_id = "language"
    errors += zh_err
    for e in errors:
        if e.quote and e.start is None:
            i = text.find(e.quote)
            if i >= 0:
                e.start, e.end = i, i + len(e.quote)

    has_cites = bool(cited_nums or author_year)
    cite_quality = 0.0
    if has_cites and refs_part:
        matched = len(cited_nums & ref_nums) / max(1, len(cited_nums)) if cited_nums else 0.8
        cite_quality = 0.4 + 0.6 * matched - 0.05 * len(uncited)
    elif has_cites or refs_part:
        cite_quality = 0.35
    evidence = len(re.findall(r"\d+(?:\.\d+)?\s*%|\d{2,}\s*(?:人|名|份|个|元|次)", body))
    conn = sum(body.count(c) for c in ZH_CONNECTIVES)
    length_factor = min(1.0, n / min_chars)
    reflective = sum(body.count(k) for k in ["我认为", "笔者认为", "本文发现", "建议", "局限", "不足", "反思", "启示"])
    ratios = {
        "structure": 0.25 + 0.75 * len(found) / len(PAPER_SECTIONS),
        "argument": (0.3 + 0.06 * min(conn, 6) + 0.05 * min(evidence, 5)) * (0.6 + 0.4 * length_factor),
        "citation": max(0.0, min(1.0, cite_quality)),
        "language": max(0.3, 0.92 - 0.06 * len(zh_err)),
        "originality": 0.35 + 0.15 * min(reflective, 4),
    }
    criteria = [LLMCriterion(id=c.id, score=_crit(c.max, ratios.get(c.id, 0.5))) for c in r.criteria]
    strengths, suggestions = [], []
    if not missing:
        strengths.append("论文结构完整")
    if evidence >= 3:
        strengths.append("能用数据支撑论点")
    if has_cites and refs_part and not uncited:
        strengths.append("文中引用与参考文献基本对应")
    if missing:
        suggestions.append("补充缺失部分：" + "、".join(missing))
    if not has_cites:
        suggestions.append("正文中的观点和数据要标注文献出处（如 [1] 或（作者，年份））")
    if evidence < 2:
        suggestions.append("增加调查数据、实验结果或文献证据，避免空泛议论")
    if reflective == 0:
        suggestions.append("结论部分写出自己的发现、建议与研究局限")
    return LLMGradingOutput(criteria=criteria, errors=errors, comment=_compose_comment(strengths, suggestions),
                            strengths=strengths, suggestions=suggestions, confidence=0.45,
                            knowledge_points=_kp_from_errors(errors, n_units=n / 500),
                            review_reasons=["规则启发式评分（mock），仅作流程演示与基线参考"])


# ------------------------------------------------------------------ 辅助

def _top_types(errors: list[LLMError], k: int = 3) -> list[str]:
    from ..error_types import label_of, normalize_error_type

    cnt: dict[str, int] = {}
    for e in errors:
        t = normalize_error_type(e.type)
        if t in {"length", "title", "other"}:
            continue
        cnt[t] = cnt.get(t, 0) + 1
    return [f"{label_of(t)}（{c}处）" for t, c in sorted(cnt.items(), key=lambda x: -x[1])[:k]]


def _kp_from_errors(errors: list[LLMError], n_units: float) -> list[LLMKnowledgePoint]:
    """按错误类型推断相关知识点掌握度：每单位篇幅错误越多，掌握度越低。"""
    from ..error_types import ERROR_TYPES, normalize_error_type

    cnt: dict[str, int] = {}
    for e in errors:
        kp = ERROR_TYPES.get(normalize_error_type(e.type), {}).get("knowledge_point")
        if kp:
            cnt[kp] = cnt.get(kp, 0) + 1
    units = max(n_units, 0.5)
    return [LLMKnowledgePoint(name=k, score_ratio=round(max(0.0, 1 - 0.25 * c / units), 3)) for k, c in cnt.items()]


def _compose_comment(strengths: list[str], suggestions: list[str]) -> str:
    parts = []
    if strengths:
        parts.append("优点：" + "；".join(strengths) + "。")
    if suggestions:
        parts.append("改进建议：" + "；".join(suggestions[:3]) + "。")
    if not parts:
        parts.append("完成了作业，整体表现中等，请对照批注继续完善。")
    return "".join(parts)


class MockGrader(GradingProvider):
    """离线规则评分（mock provider）。"""

    name = "mock"
    is_cloud = False
    model = "heuristic-v1"

    def grade(self, ctx: GradingContext) -> GradingResult:
        kind = ctx.rubric.kind
        if kind == "essay_en":
            raw = grade_english(ctx)
        elif kind == "essay_zh":
            raw = grade_chinese(ctx)
        elif kind == "short_answer":
            raw = grade_short_answer(ctx)
        elif kind == "paper":
            raw = grade_paper(ctx)
        else:
            raw = _generic(ctx.rubric, ctx.text)
        result = finalize(raw, ctx.rubric, ctx.text, ctx.reference, provider=self.name, model=self.model,
                          ocr_confidence=ctx.ocr_confidence)
        # 规则错误已有精确位置，覆盖 quote 定位结果
        _apply_rule_spans(result, raw, ctx.text)
        return result


def _apply_rule_spans(result: GradingResult, raw: LLMGradingOutput, text: str) -> None:
    exact = {}
    for e in raw.errors:
        if isinstance(e, RuleError) and e.start is not None and e.end is not None and text[e.start:e.end] == e.quote:
            exact.setdefault((e.quote, e.message), []).append((e.start, e.end))
    for a in result.errors:
        lst = exact.get((a.quote, a.message))
        if lst:
            a.start, a.end = lst.pop(0)
    result.errors.sort(key=lambda e: (e.start is None, e.start or 0))


def _generic(rubric: Rubric, text: str) -> LLMGradingOutput:
    n = len(text.strip())
    ratio = min(1.0, n / 400) * 0.7 + 0.1
    return LLMGradingOutput(criteria=[LLMCriterion(id=c.id, score=_crit(c.max, ratio)) for c in rubric.criteria],
                            comment="通用规则评分：仅按篇幅粗略估计，请教师复核。", confidence=0.3,
                            review_reasons=["通用量规无专用规则，mock 仅按篇幅估分"])
