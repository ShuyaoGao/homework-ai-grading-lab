# 在 GPU 服务器上部署本地模型并接入本系统

目标：作业图片和文字**不出校**，识别和评分都在学校或实验室自己的 GPU 服务器（例如 2×RTX 3090 24GB，装好 vLLM）上完成。
本系统通过 **OpenAI 兼容接口**对接 vLLM，代码已经写好（`backend/pipeline/ocr/providers.py::OpenAICompatOCR`、`backend/pipeline/grading/llm.py::OpenAICompatGrader`），**只需要改环境变量**。

> 状态说明：以下命令**尚未在服务器上实测**。
> 本地已用假的 OpenAI 客户端测过接口调用与解析逻辑（`tests/test_pipeline.py::test_openai_compat_grader_with_fake`）。
> 部署后请按第 5 节“验收”逐项确认。

---

## 1. 选模型

| 用途 | 推荐 | 显存（bf16） | 说明 |
|---|---|---|---|
| 手写识别（视觉） | `Qwen/Qwen2.5-VL-7B-Instruct` | 约 16 GB，单卡 3090 可跑 | 中英文手写识别能力较好，vLLM 支持成熟 |
| 手写识别（更强） | `Qwen/Qwen2.5-VL-32B-Instruct-AWQ` | 约 20 GB 权重，需要双卡 `--tensor-parallel-size 2` | 字迹潦草时更稳 |
| 评分（文本） | `Qwen/Qwen2.5-7B-Instruct` 或 `Qwen/Qwen3-8B` | 约 16 GB | **打分任务至少 7B 起步**，0.6B/1.7B 会退化成常数输出 |
| 评分（更强） | `Qwen/Qwen2.5-32B-Instruct-AWQ` | 双卡 | 与教师的一致性通常明显更好，需用第 5 节验证 |

如果服务器上已经有更新的 Qwen-VL 系列权重（例如 Qwen3-VL），优先用它，下面命令里换掉模型名即可。
一个视觉模型可以同时承担识别和评分（只配一个服务）。但 7B 视觉模型的评分质量一般不如同尺寸的纯文本模型，建议按第 5 节实测后再决定。

## 2. 下载权重（服务器上）

```bash
ssh <你的 GPU 服务器>
# 国内网络建议用 ModelScope 镜像；权重统一放 ~/models
pip install modelscope        # 在装了 vLLM 的虚拟环境里
python -c "from modelscope import snapshot_download as d; d('Qwen/Qwen2.5-VL-7B-Instruct', local_dir='$HOME/models/Qwen2.5-VL-7B-Instruct')"
python -c "from modelscope import snapshot_download as d; d('Qwen/Qwen2.5-7B-Instruct', local_dir='$HOME/models/Qwen2.5-7B-Instruct')"
```

（HuggingFace 可用时也可以 `huggingface-cli download Qwen/Qwen2.5-VL-7B-Instruct --local-dir ~/models/...`。）

## 3. 启动 vLLM 服务（常驻）

推理服务是长期运行的进程，不要放进批处理任务队列（会一直占住槽位）。建议用 `tmux` 或 systemd 用户服务。
vLLM 建议装在单独的 Python 虚拟环境里，不要和训练用的环境混装（vLLM 会锁定 PyTorch 版本）。下面的 `python` 指这个环境里的解释器。

```bash
# 0 号卡：视觉模型，负责手写识别
tmux new -d -s vl 'CUDA_VISIBLE_DEVICES=0 python -m vllm.entrypoints.openai.api_server \
  --model ~/models/Qwen2.5-VL-7B-Instruct --served-model-name qwen-vl \
  --host 0.0.0.0 --port 8101 --api-key "$EDU_VLLM_KEY" \
  --max-model-len 16384 --gpu-memory-utilization 0.90 --limit-mm-per-prompt image=4 \
  2>&1 | tee ~/vllm_vl.log'

# 1 号卡：文本模型，负责评分
tmux new -d -s grader 'CUDA_VISIBLE_DEVICES=1 python -m vllm.entrypoints.openai.api_server \
  --model ~/models/Qwen2.5-7B-Instruct --served-model-name qwen-grader \
  --host 0.0.0.0 --port 8102 --api-key "$EDU_VLLM_KEY" \
  --max-model-len 16384 --gpu-memory-utilization 0.90 \
  2>&1 | tee ~/vllm_grader.log'

# 自检
curl -s -H "Authorization: Bearer $EDU_VLLM_KEY" http://127.0.0.1:8101/v1/models
curl -s -H "Authorization: Bearer $EDU_VLLM_KEY" http://127.0.0.1:8102/v1/models
```

安全要点：
- `--api-key` 必须设置。`EDU_VLLM_KEY` 自己生成一个随机串，只写在服务器环境和本系统的 `.env` 里，**不要提交进 git**。
- 只在内网开放端口：用 `ufw allow from 192.168.1.0/24（换成你的内网网段） to any port 8101:8102 proto tcp`，**不要映射到公网**。
- 改防火墙前先确认有物理控制台或其他备用登录通道，以免把自己锁在外面。
- 双卡方案（32B AWQ）：去掉 `CUDA_VISIBLE_DEVICES`，加上 `--tensor-parallel-size 2`，只起一个服务。

## 4. 本系统改配置（运行后端的机器上）

在项目根目录建 `.env`（已 gitignore；模板见 `.env.example`）：

```dotenv
OCR_PROVIDER=openai_compat
GRADER_PROVIDER=openai_compat
OPENAI_COMPAT_IS_LOCAL=true                 # 标记为本地部署：无需监护人“云端同意”也可使用
OPENAI_COMPAT_API_KEY=<与 EDU_VLLM_KEY 相同>
OPENAI_COMPAT_OCR_BASE_URL=http://<服务器IP>:8101/v1
OPENAI_COMPAT_OCR_MODEL=qwen-vl
OPENAI_COMPAT_GRADER_BASE_URL=http://<服务器IP>:8102/v1
OPENAI_COMPAT_GRADER_MODEL=qwen-grader
OPENAI_COMPAT_JSON_MODE=json_schema         # vLLM 支持按 JSON Schema 约束输出，评分结果更稳
```

只起一个服务时，用 `OPENAI_COMPAT_BASE_URL` 代替上面两个分开的地址，两个模型名都填同一个 `served-model-name`。
`<服务器IP>` 换成运行 vLLM 的服务器在内网中的地址。
重启后端（`python run.py`），用管理员登录，首页“模型 provider”表格里 `openai_compat` 应显示“可用 · 本地”。

## 5. 验收（逐项确认）

1. **连通**：`python -c "import openai; c=openai.OpenAI(base_url='http://<服务器IP>:8102/v1', api_key='...'); print(c.models.list())"`
2. **识别**：教师端对一份已有作业点“重新识别/评分”，选 `openai_compat`，再与 `samples/generated/manifest.json` 里的标准文本对比。
   注意 mock 图片是电脑字体渲染的“仿手写”，比真实手写容易认，**一定要再拿几张真实学生作业试**。
3. **评分一致性**（关键）：不要只看几份样例就下结论，用评估工具和人工分对比。
   ```powershell
   # 英语：ELLIPSE（本地模型不收费，不需要 --yes）
   .venv\Scripts\python -m eval.run_eval --dataset ellipse --path eval\datasets\ellipse\ELLIPSE_Final_github_train.csv --provider openai_compat --limit 300 --workers 8
   # 中文：CFMS-34（rater 模式同时给出人-人基线 QWK≈0.57 作参照）
   .venv\Scripts\python -m eval.run_eval --dataset cfms34 --path eval\datasets\cfms34\train.csv --cfms-mode rater --provider openai_compat --workers 8
   # 本校教师复核数据
   .venv\Scripts\python -m eval.run_eval --dataset app-db --path data\edu_eval.db
   ```
   对照基线：mock 规则评分的 QWK 在 ELLIPSE 上为 0.05（线性映射）/ 0.22（校准后），在 CFMS-34 上为 0.27。数字见 `eval/DATASETS.md`。
4. **退化检查**：如果 `result.json` 里 `qwk` 为 `null` 且带有 `blocked_reason`，说明模型对所有作文给了同一个分。这不是脚本出错，而是模型不胜任这个任务，需要换更大的模型或调整提示词。
5. 结果目录 `eval/results/<run>/` 中的 `_env_stamp.json` 会记录模型名、git 提交号和数据文件哈希。小文件可以拉回来存档，大文件不要提交进 git。

## 6. 常见问题

- **vLLM 起不来**：先确认 `nvidia-smi` 能看到显卡、vLLM 与 CUDA / PyTorch 版本匹配；部分版本需要设置 `VLLM_USE_FLASHINFER_SAMPLER=0`。
- **识别结果是一大段说明文字而不是 JSON**：本系统会兜底，把整段文字当作识别结果、置信度记为未知（结果页会提示复核）。可以换更大的模型，或在 `OCR_INSTRUCTION` 里加更严格的要求。
- **评分 JSON 解析失败**：把 `OPENAI_COMPAT_JSON_MODE` 设为 `json_schema`（vLLM guided decoding）。DashScope 用 `json_object`。
- **显存不足**：调小 `--max-model-len`，或把 `--gpu-memory-utilization` 降到 0.85；VL 模型可以把 `--limit-mm-per-prompt image` 设为 2。
- **想用通义云端（DashScope）而不是本地**：只设置 `DASHSCOPE_API_KEY` 即可（地址自动指向 `dashscope.aliyuncs.com/compatible-mode/v1`，默认识别用 `qwen-vl-max`、评分用 `qwen-plus`）。此时属于云端处理，只对监护人勾选了“云端 AI”的学生生效，见 `docs/PRIVACY.md`。
