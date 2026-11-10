# 公开作文评分数据集调研

核实日期：2026-10-07。“已下载核实”表示本项目实际下载并用加载器读通；其余条目来自论文、项目主页和二手资料，使用前请再核对一次。
所有数据集都**不放进 git 仓库**。下载到 `eval/datasets/<名称>/`，这个目录已在 gitignore 里。

## 一览

| 数据集 | 语言/对象 | 规模 | 人工分 | 获取方式 | 许可 | 加载器 | 核实情况 |
|---|---|---|---|---|---|---|---|
| **ELLIPSE** | 英语，美国 8–12 年级英语学习者 | 训练集 3,911 篇（GitHub 版） | Overall + 6 个分项，1.0–5.0，步长 0.5（两位评分员的平均分） | GitHub 直接下载 `scrosseye/ELLIPSE-Corpus`：`ELLIPSE_Final_github_train.csv`；测试集 zip 的密码在仓库 README 里 | **CC BY-NC-SA 4.0**（仅限非商业使用） | `load_ellipse` | ✅ 已下载核实。列为 `text_id_kaggle, full_text, …, prompt, Overall, Cohesion, Syntax, Vocabulary, Phraseology, Grammar, Conventions, set`。实测 Overall 范围 1.0–5.0，均值 3.11 |
| **CFMS-34** | 中文，小学课堂作文 | 951 篇（train 751 / dev 100 / test 100） | 两位专家独立打分，每人 0–5；另有 34 个分项（core 版保留 20 个） | GitHub 直接下载 `Atiyahsama/HiFTS` 下的 `CFMS-34/{core,full}/{train,dev,test}.csv` | **CC BY 4.0**，README 写明仅供研究，不得在没有人工监督的情况下用于高利害决策 | `load_cfms34` | ✅ 已下载核实。列为 `编号, 作文内容, 作文题目, 总分_1, 总分_2, {trait}_1/_2`。**test 集只保留两位评分员总分一致的作文**，所以人和人之间的一致性要在 train/dev 上算 |
| ASAP（Hewlett AES，2012） | 英语，美国 7–10 年级 | 有标注的约 12,976 篇，分 8 个题目 | `domain1_score`，各组范围：1: 2–12，2: 1–6，3–4: 0–3，5–6: 0–4，7: 0–30，8: 0–60 | Kaggle `c/asap-aes`，需要登录并接受比赛规则 | 没有找到明确的许可文本。按 Kaggle 比赛规则通常不允许再分发，所以**不要打包分发**，让使用者自己下载 | `load_asap` | ⚠️ 列名和分值范围已由多篇论文交叉印证。Kaggle 页面是 JS 渲染的，规则原文没能读到。文件常见编码为 latin-1，人名等实体已替换成 `@PERSON1` 一类的标记 |
| ASAP++ | ASAP 第 1–6 组的属性分 | 与 ASAP 相同 | P1–2：Content / Organization / Word Choice / Sentence Fluency / Conventions（1–6）。P3–6：Content / Prompt Adherence / Language / Narrativity（0–3 或 0–4） | 项目主页 `lwsam.github.io/ASAP++/lrec2018.html` 提供 `Prompt-N.csv` 下载。不含作文原文，需要按作文 ID 与 ASAP 合并 | 未声明许可，要求引用；同时受 ASAP 条款约束 | `load_asap_plus_plus` | ⚠️ CSV 的 ID 列名没有核实，加载器会自动找名字里含 id 的列 |
| ASAP 2.0 / AES 2.0（2024） | 英语，6–12 年级 | 约 2.4 万篇 | 整体分 1–6 | Kaggle（`lburleigh/asap-2-0` 或比赛 `learning-agency-lab-automated-essay-scoring-2`） | Learning Agency 的数据集页面写的是 CC BY 4.0。比赛数据的条款没有核实 | `load_generic` | ⚠️ 未下载 |
| PERSUADE 2.0 | 英语议论文，6–12 年级 | 约 2.5 万篇，15 个题目 | 整体分 1–6 | GitHub `scrosseye/persuade_corpus_2.0`（训练 CSV 放在 Google Drive） | GitHub 写的是 CC BY-NC-SA 4.0，机构页面写的是 CC BY 4.0，两处冲突，**按更严的 NC-SA 处理** | `load_persuade` | ⚠️ 整体分的列名没有核实（推测为 `holistic_essay_score`），可以用 `--score-col` 指定 |
| HSK 动态作文语料库（北语） | 中文，外国留学生 | 约 1.1 万篇 | HSK 作文分（40–100） | 需要在 `hsk.blcu.edu.cn` 注册。只能在线检索，自动下载上限 500 篇，没有批量文件 | 非商业，须注明出处 | `load_generic` | ⚠️ 网站当天没能打开。写作者是外国留学生，**和中小学生作文差别很大** |
| CEDCC（EMNLP 2023） | 中文，中学 | 501 篇 | 连贯性 0/1/2。论文提到有 0–100 的教师分，但 JSON 里有没有这一项没有核实 | GitHub `cubenlp/CEDCC_corpus` | **仓库没有许可文件**，再分发前需要征得作者同意 | 未实现 | ⚠️ |
| CEFA、ACEA、Song et al. 2020 等 | 中文 | 数百到上千篇 | 流畅度或组织结构等级 | 没有公开数据，或者仓库里只有 README | — | — | ❌ 需要向作者申请 |

## 结论与建议

1. **英文作文**：ELLIPSE 可以直接下载，对象又是英语学习者，最接近中国学生的英语作文，首选它。ASAP 是 AES 领域引用最多的基准，但需要自己去 Kaggle 下载。
2. **中文作文**：目前能直接用的只有 **CFMS-34**。它有双评分员，能给出人和人之间的一致性上限。不过它是小学作文、5 分制，和高考作文的 60 分量规尺度差别很大。
3. **最有价值的数据是本校自己的阅卷数据**。用 `--dataset generic` 导入历次考试的作文和阅卷分；如果有双评，填上 `--score2-col`，就能同时算出人和人之间的基线。系统里教师复核留下的记录可以直接用 `--dataset app-db` 评估。
4. 不同数据集的分值尺度不同。本工具同时报告两种结果：一种是把量规得分率线性映射到数据集分值，另一种是用 30% 样本拟合线性校准后，在其余 70% 上评估。两者都看，避免只报好看的那个。

## 已跑的基线（mock 规则评分，离线，2026-10-07）

这些数字只说明评估管线能跑通，以及**规则基线有多弱**，不代表大模型的水平。

| 数据集 | 样本 | 评分方式 | QWK | Pearson | MAE（原分值） | ±1 档 | 人-人 QWK |
|---|---|---|---|---|---|---|---|
| ELLIPSE Overall（1–5，9 档） | 3,911 | 线性映射 | 0.052 | 0.284 | 1.53 | 11.5% | —（只有平均分） |
| ELLIPSE Overall | 2,738（留出） | 线性校准 | 0.218 | 0.322 | 0.46 | 76.7% | — |
| CFMS-34 train，单评分员（0–5） | 751 | 线性映射 | 0.271 | 0.277 | 0.62 | 93.7% | **0.569** |
| CFMS-34 test，两评之和（0–10） | 100 | 线性映射 | 0.240 | 0.251 | 1.18 | 66% | —（test 集两评一致） |
| CFMS-34 test | 70（留出） | 线性校准 | null（校准后预测全部落在同一档，方差为 0，已拦截） | — | 1.31 | 84% | — |

参照：只用篇幅（词数或字数）这一个特征，与人工分的 Pearson 在 ELLIPSE 上是 0.23，在 CFMS-34 上是 0.41。
规则基线在 CFMS-34 上**还不如只看篇幅**。所以要拿到有意义的自动评分，必须接入大模型（Claude、通义，或服务器上用 vLLM 部署的本地模型），然后用同一条命令重新评估。
复现命令见 `eval/run_eval.py` 的文件头。每次运行的结果都落在 `eval/results/`（gitignore），包括 `_env_stamp.json`、`raw/predictions.jsonl` 和 `result.json`。
