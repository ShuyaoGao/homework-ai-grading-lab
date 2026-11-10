# 第三方组件、数据与许可证

本仓库的代码（Python、JavaScript、HTML/CSS、构建脚本）由本项目编写。随仓库分发的第三方作品只有下面列出的 Chart.js。仓库**不包含任何外部数据集、真实学生作业或照片**。

## 随网页分发的 JavaScript 库

| 组件 | 版本 | 位置 | 许可证 |
|---|---|---|---|
| Chart.js | 4.4.7 | `frontend/vendor/chart.umd.min.js`、`play/vendor/chart.umd.min.js` | MIT，原文见 `frontend/vendor/chart.js.LICENSE.md`（Copyright (c) 2014-2024 Chart.js Contributors） |

## 字体

- 应用界面（`frontend/`、`play/`）只用系统自带字体，不分发字体文件。
- 产品展示页 `showcase/index.html` 在线加载 Google Fonts（Noto Serif SC、Noto Sans SC、JetBrains Mono，均为 SIL Open Font License 1.1）；离线时退回系统字体。
- 演示用的“仿手写作业照片”由 `backend/demo/handwriting.py` 用运行机器上已安装的字体渲染成图片（Windows 上例如 Ink Free、Segoe Print、楷体等）。仓库不包含这些字体文件，只包含渲染出来的图片。

## 数据

| 内容 | 来源 | 说明 |
|---|---|---|
| 演示班级、学生、作业、分数、复核记录 | `backend/seed.py` 程序生成 | **全部为合成数据**，人物姓名为虚构 |
| 作业照片（`play/mock/img/`、`showcase/assets/`、`demo/screenshots/`） | `backend/demo/handwriting.py` 程序合成 | 仿手写图片，内嵌标准文本供 mock 识别读取；网页版重新压缩为较小的 JPEG |
| 网页版回放数据（`play/mock/data.js`） | `tools/static_demo/record.py` 用上面的合成数据跑真实后端（mock provider）录制 | 不含真实学生信息，不是真实模型输出 |
| `eval/fixtures/*` | 手工编造 | 只模仿 ASAP / ELLIPSE / CFMS-34 的文件格式，内容与分数都不是原数据集 |
| ASAP、ELLIPSE、CFMS-34 等公开数据集 | **不随仓库分发** | 需要自行下载并遵守各自的许可证，调研与核实情况见 `eval/DATASETS.md` |

`eval/DATASETS.md` 和 `README.md` 里引用的基线数字（例如 mock 规则评分的 QWK）是作者在本机下载这些数据集后跑 `eval/run_eval.py` 得到的，仓库里只保留数字，不保留数据。

## 内置量规

`backend/rubrics/*.yaml` 是参照公开评分原则自行整理的，**不是官方评分标准原文**，各地细则不同，使用时请按本地要求调整。

## Python 依赖（不随仓库分发，由使用者自行安装）

版本为作者测试时的版本；许可证取自各包发布的元数据。

| 包 | 测试版本 | 许可证 |
|---|---|---|
| fastapi | 0.142.2 | MIT |
| uvicorn | 0.54.0 | BSD-3-Clause |
| sqlalchemy | 2.1.3 | MIT |
| pydantic | 2.13.5 | MIT |
| pyjwt | 2.15.1 | MIT |
| python-multipart | 0.0.32 | Apache-2.0 |
| pyyaml | 6.0.3 | MIT |
| pillow | 12.3.0 | MIT-CMU（HPND） |
| numpy | 2.5.3 | BSD-3-Clause（及内含组件的 0BSD / MIT / Zlib 等） |
| opencv-python-headless | 5.0.0.93 | Apache-2.0 |
| anthropic | 1.11.0 | MIT |
| openai | 3.26.0 | Apache-2.0 |
| httpx | 0.28.1 | BSD-3-Clause |
| pytest | 9.1.1 | MIT |
| playwright（可选，开发用） | 1.63.0 | Apache-2.0 |
| scikit-learn（可选，测试用） | 1.9.1 | BSD-3-Clause |

可选的 PaddleOCR / PaddlePaddle（`requirements-optional.txt`）为 Apache-2.0，作者未实测。

## 商标

Claude、通义千问（Qwen / DashScope）、vLLM、PaddleOCR 等名称属于各自的所有者，本项目只是描述可以对接这些服务或软件，与它们没有隶属或背书关系。
