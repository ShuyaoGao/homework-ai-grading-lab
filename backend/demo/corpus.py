"""演示用作文/作答生成器：按“能力值”(0–1) 生成不同质量的学生作答文本（确定性，seed 可复现）。

高能力：要点齐全、句式丰富、错误少；低能力：篇幅短、缺要点、注入常见错误（单三、拼写、冠词、错别字等）。
全部文本为虚构内容，仅用于演示和测试。
"""
from __future__ import annotations

import random

# ---------------------------------------------------------------- 英语

EN_ERRORS = [  # (正确, 错误)  —— 低能力学生更可能出现
    ("He likes", "He like"), ("She likes", "she like"), ("because", "becouse"), ("interesting", "intresting"),
    ("an interesting", "a interesting"), ("better", "more better"), ("look forward to seeing", "look forward to see"),
    ("didn't go", "didn't went"), ("friends", "freinds"), ("tomorrow", "tommorow"), ("beautiful", "beautifull"),
    ("enjoyed playing", "enjoyed to play"), (" I ", " i "), ("really like", "very like"), ("library", "libary"),
    ("together", "togather"), ("There are many", "There is many"), ("people", "pepole"),
]

EN_PROMPTS = {
    "weekend": {
        "title": "英语书面表达：My Weekend",
        "prompt": "以“My Weekend”为题写一篇短文，介绍你上周末做了什么、和谁一起、你的感受。词数 80–100。",
        "key_points": [["weekend", "saturday", "sunday"], ["with my", "together", "friends", "family"],
                       ["felt", "happy", "tired", "enjoyed", "feel"]],
        "paras": [
            [("Last weekend was busy but meaningful for me.", "core"),
             ("I had a lot of things to do, so I made a plan on Friday evening.", "plus")],
            [("On Saturday morning, I went to the library with my friends.", "core"),
             ("We read some interesting books about science together.", "core"),
             ("In the afternoon, I helped my mother clean the house because she was very tired.", "plus"),
             ("He likes playing basketball, so my brother and I played it in the park.", "plus"),
             ("On Sunday, I didn't go out.", "core"), ("I stayed at home and finished my homework.", "core"),
             ("Then I practised the piano for an hour, which made me feel relaxed.", "advanced"),
             ("I also enjoyed playing chess with my father, although I lost twice.", "advanced")],
            [("I really like my weekend.", "core"),
             ("Not only did I learn a lot, but I also had a good rest.", "advanced"),
             ("I hope next weekend will be even better, and I look forward to seeing my friends again.", "plus")],
        ],
    },
    "invite": {
        "title": "英语书面表达：邀请外教参加英语节",
        "prompt": "假定你是李华，学校下周五举办英语节。请给外教 Mr. Smith 写一封邀请信，内容包括：时间地点、活动内容、希望他做什么。词数 80–100。",
        "key_points": [["friday", "next week"], ["hall", "playground", "place", "held in"], ["sing", "play", "speech", "activities"],
                       ["judge", "give us", "speech", "join"]],
        "paras": [
            [("Dear Mr. Smith,", "core")],
            [("I am Li Hua, and I am writing to invite you to our English Festival.", "core"),
             ("It will be held in the school hall next Friday afternoon.", "core"),
             ("There are many activities, such as singing English songs and acting short plays.", "core"),
             ("We will also have a speech contest, which is the most exciting part.", "plus"),
             ("Since you are an excellent teacher, we would like you to be one of the judges.", "advanced"),
             ("If you are free, could you give us a short speech about Western culture?", "plus"),
             ("I am sure the students will find it interesting and useful.", "advanced")],
            [("We would be very glad if you could come.", "core"),
             ("I look forward to seeing you there.", "plus")],
            [("Yours,", "core"), ("Li Hua", "core")],
        ],
    },
}


def gen_english(kind: str, ability: float, seed: int) -> str:
    rng = random.Random(seed)
    spec = EN_PROMPTS[kind]
    out_paras = []
    for para in spec["paras"]:
        sents = []
        for s, lvl in para:
            need = {"core": 0.0, "plus": 0.45, "advanced": 0.72}[lvl]
            if ability + rng.uniform(-0.08, 0.08) >= need and not (lvl != "core" and rng.random() < 0.12):
                sents.append(s)
        if sents:
            out_paras.append(" ".join(sents))
    text = "\n".join(out_paras)
    p_err = max(0.05, (0.85 - ability) * 0.9)
    for right, wrong in EN_ERRORS:
        if right in text and rng.random() < p_err:
            text = text.replace(right, wrong, 1)
    return text


# ---------------------------------------------------------------- 语文

ZH_TYPOS = [("再接再厉", "在接再厉"), ("迫不及待", "迫不急待"), ("即使", "既使"), ("刻苦", "克苦"), ("已经", "以经"),
            ("应该", "因该"), ("毕竟", "必竟"), ("震撼", "震憾"), ("部分", "部份"), ("安详", "安祥"),
            ("一如既往", "一如继往"), ("专心致志", "专心至志"), ("兴高采烈", "兴高彩烈"), ("历历在目", "历历再目")]

ZH_PROMPTS = {
    "grownup": {
        "title": "语文作文：那一刻，我长大了",
        "prompt": "以“那一刻，我长大了”为题，写一篇记叙文。要求：写出真情实感，不少于450字。",
        "keywords": ["那一刻", "长大"],
        "heading": "那一刻，我长大了",
        "paras": [
            [("成长是什么？", "plus"), ("有人说成长是个子变高了，有人说成长是懂得了更多的道理。", "core"),
             ("而对我来说，成长就藏在那个平凡的傍晚里。", "core")],
            [("那是去年冬天的一个傍晚，妈妈因为加班还没有回家。", "core"),
             ("我放学回到家，看见爷爷一个人坐在沙发上，脸色很不好。", "core"),
             ("我问他怎么了，他说头有点晕，想躺一会儿。", "core"),
             ("我心里一下子慌了，手心全是汗，脑子里一片空白。", "plus"),
             ("可是我知道，现在家里只有我能帮爷爷，我不能慌。", "core")],
            [("我深吸一口气，先扶爷爷躺下，又给他倒了一杯温水。", "core"),
             ("然后我拨通了妈妈的电话，把爷爷的情况一五一十地告诉了她。", "plus"),
             ("妈妈让我先给爷爷量血压，我翻出血压计，照着说明书一步一步地操作。", "plus"),
             ("血压果然有些高，我按照医生以前的嘱咐，给爷爷拿了药。", "core"),
             ("那半个小时，时间仿佛被拉得很长很长，我一直守在爷爷身边，不敢离开半步。", "advanced"),
             ("窗外的天渐渐黑了，路灯一盏一盏亮起来，像一双双关切的眼睛。", "advanced")],
            [("终于，门锁响了，妈妈迫不及待地冲进屋里。", "core"),
             ("看到爷爷已经安详地睡着了，她紧紧地抱住了我，说：“孩子，你长大了。”", "core"),
             ("那一刻，我的眼泪一下子涌了出来。", "core"),
             ("原来，长大不是等来的，而是在需要你的时候，你能勇敢地站出来。", "plus")],
            [("如今回想起那个傍晚，一切依然历历在目。", "plus"),
             ("我明白了，成长意味着责任，意味着即使害怕，也要把肩膀挺起来。", "advanced"),
             ("正如一位作家所说：“成长，就是学会为爱的人遮风挡雨。”", "advanced"),
             ("我会一如既往地努力，做一个有担当的人。", "plus")],
        ],
    },
    "hero": {
        "title": "语文作文：我心中的英雄",
        "prompt": "以“我心中的英雄”为题写一篇文章，文体不限（诗歌除外），不少于450字。",
        "keywords": ["英雄"],
        "heading": "我心中的英雄",
        "paras": [
            [("提起英雄，很多人会想到战场上冲锋陷阵的战士，想到电影里拯救世界的超人。", "core"),
             ("可是在我心中，英雄就在我们身边。", "core")],
            [("我心中的英雄，是我们小区的保安刘叔叔。", "core"),
             ("他五十多岁，个子不高，皮肤晒得黝黑。", "core"),
             ("每天早上六点，他已经站在小区门口，笑着和每一个出门的人打招呼。", "plus"),
             ("去年夏天的一个深夜，小区里突然着火了，浓烟从三楼的窗户里冒出来。", "core"),
             ("刘叔叔第一个冲了上去，一边大声喊人报警，一边拿着灭火器往楼上跑。", "core"),
             ("他挨家挨户地敲门，把还在睡梦中的老人和孩子叫醒，扶着他们下楼。", "plus"),
             ("等消防车赶到时，火势已经被控制住了，而刘叔叔的手臂却被烫伤了一大片。", "plus")],
            [("第二天，大家拿着水果去看望他，他却摆摆手说：“这是我应该做的，没什么。”", "core"),
             ("这句朴实的话，深深地震撼了我。", "plus"),
             ("毕竟，在危险面前，他也会害怕，可他还是选择了站在最前面。", "advanced")],
            [("英雄是什么？", "plus"),
             ("英雄不一定有超能力，也不一定要做惊天动地的大事。", "core"),
             ("英雄是危难时挺身而出的勇气，是平凡日子里默默坚守的责任。", "advanced"),
             ("像刘叔叔这样的普通人，像医生、像消防员、像每一个认真工作的人，都是我心中的英雄。", "advanced"),
             ("鲁迅先生说过：“我们自古以来，就有埋头苦干的人，有拼命硬干的人……这就是中国的脊梁。”", "advanced")],
            [("我也要向他们学习，刻苦学习，努力成长，将来做一个对社会有用的人。", "core")],
        ],
    },
}


def gen_chinese(kind: str, ability: float, seed: int) -> str:
    rng = random.Random(seed)
    spec = ZH_PROMPTS[kind]
    paras = []
    for para in spec["paras"]:
        sents = []
        for s, lvl in para:
            need = {"core": 0.0, "plus": 0.45, "advanced": 0.7}[lvl]
            if ability + rng.uniform(-0.08, 0.08) >= need and not (lvl != "core" and rng.random() < 0.12):
                sents.append(s)
        if sents:
            paras.append("".join(sents))
    body = "\n".join(paras)
    p_err = max(0.04, 0.8 - ability)
    for right, wrong in ZH_TYPOS:
        if right in body and rng.random() < p_err:
            body = body.replace(right, wrong, 1)
    if ability < 0.45 and rng.random() < 0.6:
        body = body.replace("，", ",", 2)  # 半角标点
    has_title = ability >= 0.3 or rng.random() < 0.5
    return (spec["heading"] + "\n" + body) if has_title else body


# ---------------------------------------------------------------- 数学

MATH_SETS = {
    "equation": {
        "title": "数学：一元一次方程练习",
        "prompt": "解下列方程（写出过程）",
        "questions": [
            {"id": 1, "question": "解方程 2x+3=11", "answer": "x=4", "points": 3, "knowledge_point": "一元一次方程-移项",
             "steps": ["2x=8"], "work": "2x+3=11\n2x=11-3\n2x=8\nx={a}"},
            {"id": 2, "question": "解方程 3(x-1)=12", "answer": "x=5", "points": 3, "knowledge_point": "一元一次方程-去括号",
             "steps": ["3x-3=12", "3x=15"], "work": "3x-3=12\n3x=15\nx={a}"},
            {"id": 3, "question": "解方程 x/2+1=4", "answer": "x=6", "points": 2, "knowledge_point": "一元一次方程-去分母",
             "steps": ["x+2=8"], "work": "x+2=8\nx={a}"},
            {"id": 4, "question": "某数的3倍减去5等于16，求这个数", "answer": "7", "points": 2,
             "knowledge_point": "列方程解应用题", "steps": ["3x-5=16"], "work": "设这个数为x\n3x-5=16\n3x=21\nx={a}\n答：这个数是{a}"},
        ],
    },
    "area": {
        "title": "数学：面积计算",
        "prompt": "计算下列图形的面积（写出过程和单位）",
        "questions": [
            {"id": 1, "question": "长方形长8cm，宽5cm，求面积", "answer": "40", "unit": "cm²", "points": 2,
             "knowledge_point": "长方形面积", "steps": ["8×5"], "work": "S=8×5={a}cm²"},
            {"id": 2, "question": "三角形底6cm，高4cm，求面积", "answer": "12", "unit": "cm²", "points": 3,
             "knowledge_point": "三角形面积", "steps": ["6×4÷2"], "work": "S=6×4÷2={a}cm²"},
            {"id": 3, "question": "梯形上底3cm，下底7cm，高4cm，求面积", "answer": "20", "unit": "cm²", "points": 3,
             "knowledge_point": "梯形面积", "steps": ["(3+7)×4÷2"], "work": "S=(3+7)×4÷2={a}cm²"},
            {"id": 4, "question": "圆的半径为2cm，求面积（π取3.14）", "answer": "12.56", "unit": "cm²", "points": 2,
             "knowledge_point": "圆的面积", "steps": ["3.14×2×2", "3.14×2²"], "work": "S=3.14×2×2={a}cm²"},
        ],
    },
}


def math_reference(kind: str) -> dict:
    qs = [{k: v for k, v in q.items() if k != "work"} for q in MATH_SETS[kind]["questions"]]
    return {"questions": qs}


def gen_math(kind: str, ability: float, seed: int) -> str:
    rng = random.Random(seed)
    lines = []
    for q in MATH_SETS[kind]["questions"]:
        ans = q["answer"].replace("x=", "")
        correct = rng.random() < 0.25 + 0.75 * ability
        a = ans
        if not correct:
            try:
                v = float(ans)
                a = f"{v + rng.choice([-2, -1, 1, 2, 3]):g}"
            except ValueError:
                a = ans + "1"
        work = q["work"].format(a=a)
        if rng.random() > 0.3 + 0.7 * ability:  # 低能力：只写最后一行
            work = work.splitlines()[-1]
        if "unit" in q and rng.random() > 0.4 + 0.6 * ability:
            work = work.replace(q["unit"], "")
        lines.append(f"{q['id']}. " + work.replace("\n", "\n   "))
    return "\n".join(lines)


# ---------------------------------------------------------------- 小论文

PAPER = {
    "title": "研究性学习小论文：校园垃圾分类现状调查",
    "prompt": "围绕“校园垃圾分类”开展小调查，写一篇不少于500字的研究性学习小论文，要求结构完整、有数据支撑、规范引用参考文献。",
    "sections": [
        ("校园垃圾分类现状调查与改进建议", "core"),
        ("摘要：本文通过问卷调查了解我校学生垃圾分类的认知与行为，发现多数同学知道分类的意义，但实际投放正确率不高。"
         "文章分析了原因并提出了改进建议。", "plus"),
        ("一、问题提出", "core"),
        ("垃圾分类是建设美丽中国的重要举措。2019年起，上海等城市开始实行生活垃圾强制分类[1]。"
         "学校是培养良好习惯的重要场所，因此我们想了解同学们在校园里是否能做到正确分类。", "core"),
        ("二、研究方法", "plus"),
        ("我们设计了一份包含10道题的问卷，在初二年级随机发放了120份，收回有效问卷112份。"
         "同时，我们在两周内每天中午观察教学楼垃圾桶的投放情况。", "plus"),
        ("三、调查结果与分析", "core"),
        ("调查显示，92%的同学认为垃圾分类很重要，但只有41%的同学能正确区分厨余垃圾和其他垃圾。"
         "观察发现，可回收物桶中混入其他垃圾的比例约为35%。", "core"),
        ("原因主要有三点：一是分类知识掌握不牢；二是垃圾桶标识不够醒目；三是部分同学存在“别人不分我也不分”的从众心理。"
         "有研究表明，清晰的提示和及时的反馈能明显提高分类正确率[2]。", "advanced"),
        ("四、结论与建议", "core"),
        ("我们认为，校园垃圾分类的关键不在于“知道”，而在于“做到”。建议学校：（1）在垃圾桶旁张贴图文并茂的分类指南；"
         "（2）每班设立垃圾分类监督员；（3）开展分类知识竞赛。", "core"),
        ("本研究样本只来自一个年级，结论还有局限，今后可以扩大调查范围。", "advanced"),
        ("参考文献", "plus"),
        ("[1] 上海市人民代表大会常务委员会. 上海市生活垃圾管理条例[Z]. 2019.", "plus"),
        ("[2] 王明. 提示与反馈对居民垃圾分类行为的影响[J]. 环境教育, 2020(3): 45-48.", "advanced"),
    ],
}


def gen_paper(ability: float, seed: int) -> str:
    rng = random.Random(seed)
    out = []
    for s, lvl in PAPER["sections"]:
        need = {"core": 0.0, "plus": 0.45, "advanced": 0.7}[lvl]
        if ability + rng.uniform(-0.06, 0.06) >= need and not (lvl == "advanced" and rng.random() < 0.15):
            out.append(s)
    text = "\n".join(out)
    if ability < 0.7:
        text = text.replace("[2]", "")
    p_err = max(0.0, 0.75 - ability)
    for right, wrong in ZH_TYPOS:
        if right in text and rng.random() < p_err:
            text = text.replace(right, wrong, 1)
    return text
