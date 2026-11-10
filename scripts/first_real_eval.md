# 首轮真实评估：拿到 key 或服务器后，一条命令跑完 CER 和 QWK

这份评估回答两个问题：

1. **手写识别准不准**：用 CER（字符错误率）衡量，越低越好。
2. **AI 打分和老师像不像**：用 QWK（二次加权 Kappa）衡量，越高越好。参照上限是两位人类评分员之间的 QWK。

脚本是 `scripts/first_real_eval.py`。它会**先估算费用**；付费接口必须加 `--yes` 才会真正调用。所有 key 都只从环境变量或 `.env` 读取，脚本不打印 key，也不写进任何文件。

---

## 0. 先做一次 dry-run（不调用任何模型，不花钱）

```powershell
cd homework-ai-grading-lab
.venv\Scripts\python scripts\first_real_eval.py --provider claude --dry-run
.venv\Scripts\python scripts\first_real_eval.py --provider dashscope --dry-run
```

dry-run 会生成 20 张合成识别样本，按数据集里的真实作文长度估算 token 数和费用，报告写到 `eval/results/first_real_<provider>_<时间>/REPORT.md`。

## 1. 按拿到的资源选一条命令

| 拿到了什么 | 先做的设置 | 一条命令 |
|---|---|---|
| **Claude key** | `.env` 里写 `ANTHROPIC_API_KEY=...` | `.venv\Scripts\python scripts\first_real_eval.py --provider claude --yes` |
| 想用 Opus 5.5 | 同上 | `... --provider claude --model claude-opus-5-5 --yes` |
| **通义（DashScope）key** | `.env` 里写 `DASHSCOPE_API_KEY=...` | `.venv\Scripts\python scripts\first_real_eval.py --provider dashscope --yes` |
| **服务器 vLLM** | 按 `docs/DEPLOY_LOCAL_MODEL.md` 第 3、4 节起好服务，并写好 `OPENAI_COMPAT_*` | `.venv\Scripts\python scripts\first_real_eval.py --provider vllm` |
| 什么都没有 | — | `.venv\Scripts\python scripts\first_real_eval.py --provider mock`（只验证流程，CER 必然为 0） |

常用参数：

- `--n-ocr 20`：合成识别样本的张数。
- `--handwriting-dir D:\真实作业`：真实作业照片目录，见第 3 节。
- `--n-grade 100`：每个数据集抽多少篇。
- `--datasets cfms34,ellipse`：评哪些数据集。
- `--skip-ocr` / `--skip-grading`：只跑其中一半。
- `--workers 4`：并发数。遇到限流（429）就调小。

前提：评分数据集要先下载到 `eval/datasets/`，方法见 `eval/DATASETS.md`。2026-10-07 已在本机下载好 CFMS-34 和 ELLIPSE。

## 2. 大概要花多少钱

按默认规模（20 张识别样本，每个数据集 100 篇，共 200 次评分调用），用脚本里的 token 估算乘以单价：

| 路线 | 识别模型 | 评分模型 | 估算费用 | 价格来源与状态 |
|---|---|---|---|---|
| Claude | claude-sonnet-5-5 | claude-sonnet-5-5 | **约 6.5 美元**（区间 3.9–10.4） | 官方定价页 platform.claude.com/docs/en/about-claude/pricing，**2026-10-08 已核实**：Sonnet 5.5 输入 $2、输出 $10 / 百万 token |
| Claude | claude-opus-5-5 | claude-opus-5-5 | **约 13 美元** | 同上：Opus 5.5 输入 $4、输出 $20 / 百万 token |
| 通义 | qwen-vl-max | qwen-plus | **约 2.6 元人民币** | **待核实**。百炼官方模型页没有列出价格，这里用的是二手资料转述的价格（qwen-vl-max 0.02 元/千 token，qwen-plus 输入 0.8 / 输出 4.8 元/百万 token）。以百炼控制台计费页为准，改 `scripts/pricing.json` 后重新 dry-run |
| 服务器 vLLM | Qwen2.5-VL-7B 等 | Qwen2.5-7B 等 | **0 元 API 费用** | 只有电费，两张 3090 满载约 0.4 元/小时（粗估）。200 篇大约十几分钟 |

估算口径：

- **图片 token**：每 28×28 像素算 1 个视觉 token（Claude 视觉文档）。识别时图片会缩到长边 1568 px，一页作业大约 2,300 token。
- **文字 token**：中文约 1 字 1 token，英文约 3.6 字符 1 token。Claude 4.7 及以后的模型按官方说明再乘 1.3。
- **评分**：每篇作文的输入是量规、题目和作文正文，约 3,700 token（ELLIPSE 的作文较长）。输出是评分 JSON 约 900 token，再加上 Claude 的思考约 1,500 token（effort=medium，粗估）。
- 思考长度不确定性最大，所以给出了 0.6–1.6 倍的区间。实际花费以控制台账单为准。

省钱的办法：

- 先用 `--n-grade 30` 小规模试一次，确认输出正常后再放大。
- 只评中文就加 `--datasets cfms34`。
- Claude 的 Batch API 半价，但本脚本是同步调用，没有接入 Batch。

## 3. 真实手写样本（最重要，强烈建议准备）

合成图是电脑字体渲染的，比学生真实字迹容易认，**合成样本上的 CER 只能当下限参考**。准备方法：

1. 收集 20–50 张真实作业照片，覆盖不同年级、字迹清楚与潦草、中文和英文。必须取得监护人同意，并遮挡姓名。
2. 每张图配一个**同名 `.txt`**，内容是人工逐字转写。**不要纠正学生的错别字和语法错误**，看不清的字写 `[?]`。
3. 运行 `... --handwriting-dir D:\真实作业`，报告里会把“合成”和“真实”两组的 CER 分开列出。

## 4. 怎么读结果

**CER（识别）**

- < 5%：可以直接进入评分。
- 5–15%：可用，但老师复核时要留意识别有误的地方。系统会在识别置信度低时自动建议复核，老师也可以在结果页修正识别文字后重新评分。
- \> 15%：先别用来评分。可以换更大的视觉模型（如 Qwen2.5-VL-32B、claude-opus-5-5），或者改进拍照条件。

**QWK（评分）**

| 对照项 | 数值 |
|---|---|
| CFMS-34 上两位人类评分员之间（上限参照） | 0.57 |
| mock 规则基线（下限参照） | CFMS-34 0.27，ELLIPSE 0.05（校准后 0.22） |

- 结果接近或超过 0.57，说明和人类评分员之间的一致程度相当。
- 只比规则基线高一点，说明模型没有发挥作用，需要调整提示词或换模型。
- 如果 `qwk` 是 `null`，同时有 `blocked_reason`，说明模型给所有作文打了同一个分（退化）。这不是脚本坏了，是模型不胜任这个任务。

“线性映射”和“校准后”两列都要看：

- **线性映射**是直接把量规得分率换算到数据集的分值范围。
- **校准后**是用 30% 的样本拟合一条直线，再在剩下 70% 上评估。它反映的是“排序对、只是尺度不同”的情况。

## 5. 产物

`eval/results/first_real_<provider>_<时间>/` 目录（已 gitignore）：

| 文件 | 内容 |
|---|---|
| `REPORT.md` | 人读的汇总 |
| `summary.json` | 机读的汇总 |
| `ocr_raw.jsonl` | 每张图的参考文本、识别结果、CER、置信度 |
| `grade_<数据集>/` | `_env_stamp.json`、`raw/predictions.jsonl`、`result.json` |

结果可以拷回仓库存档；大文件不要提交进 git。
