# briefing — 多智能体文献简报系统

输入一个研究 topic，自动检索 3–5 篇相关论文，逐篇分析，产出一份**可引用的 PDF 简报**（含逐篇卡片、横向对比表、研究空白与参考文献）。

模型层基于 DeepSeek（默认 `deepseek-v4-pro`），通过 OpenAI 兼容接口调用。

**想先看看它产出什么？** 仓库里有一份人工确认过的真实运行产物：[`examples/2026-09-17-diffusion-weather-forecasting/`](examples/2026-09-17-diffusion-weather-forecasting/)（11 页 PDF + 4 个伴随文件）。

---

## 快速开始

### 1. 环境

需要 **Python 3.11+**、[`uv`](https://docs.astral.sh/uv/)，以及：

```bash
uv sync --dev          # 安装依赖（含开发依赖）
uv run briefing --help
```

> **macOS 上的 PDF 渲染**：WeasyPrint 需要 Pango，且它是通过 dyld 加载 Homebrew 的库的。装一次即可：`brew install pango`。代码会在导入 WeasyPrint 之前自动设置 `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib`（细节见 `docs/decisions/001-runtime-and-renderer.md`）。

> **网络**：如果 `pypi.org` 不可达，仓库已把清华镜像配置为默认索引；换到能直连的机器时用 `uv sync --default-index https://pypi.org/simple`（见 `docs/decisions/002-package-index-mirror.md`）。

### 2. 配置密钥

```bash
cp .env.example .env
# 编辑 .env，填入 DEEPSEEK_API_KEY
```

`.env` 已被 `.gitignore` 忽略，提交前钩子也会拒绝提交它。密钥不要写进任何被跟踪的文件。

### 3. 先离线跑一遍（**不需要密钥、不联网**）

`BRIEFING_LLM_MODE=stub` 表示"完全离线"：模型回复与论文检索都从 `tests/fixtures/llm/` 里回放录制。

```bash
BRIEFING_LLM_MODE=stub BRIEFING_FIXTURES_DIR=tests/fixtures/llm \
  uv run briefing run --topic "diffusion models for weather forecasting" \
  --out /tmp/briefing-demo --no-cache
```

### 4. 真实运行

```bash
uv run briefing run --topic "diffusion models for weather forecasting" \
  --out outputs/2026-09-17-weather
```

以仓库中的真实基线为参考：一次运行约 **10 次模型调用、3.5 万 prompt token、80 秒**（上限 40 次调用）。

常用参数：

| 参数 | 作用 |
| --- | --- |
| `--topic` | 研究主题（3–300 字符） |
| `--out` | 产物输出目录 |
| `--lang zh\|en` | 报告语言；默认跟随 topic 的语言 |
| `--no-cache` | 不使用检索缓存（每次真实抓取） |
| `--resume` | 复用输入指纹未变的阶段检查点，跳过已完成的阶段 |
| `--verbose` | 输出 debug 级结构化日志 |

---

## 产物清单

每次运行都会在 `--out` 目录生成：

| 文件 | 内容 |
| --- | --- |
| `report.pdf` | 最终交付物：10 章固定结构，CJK 字体内嵌，文本可选中，目录带页码 |
| `briefing.md` | 可编辑版本，与 PDF 同源于同一个通过校验的 `Briefing` |
| `briefing.json` | 结构化简报 |
| `papers.json` | 入选论文的元数据 + 逐篇分析（含证据引文） |
| `manifest.json` | 运行审计：模型与参数、prompt 版本、检索式、token 用量、阶段耗时、渲染器、错误 |
| `_stages/*.json` | 阶段检查点与中间产物（调试与 `--resume` 用；`_stages/08_report.html` 是渲染前的 HTML） |

**只要运行过，`manifest.json` 一定存在**——包括失败和中止的情况，这是不变量 I7。

---

## 退出码

| 退出码 | 含义 | 说明 |
| --- | --- | --- |
| `0` | 成功 | 5 个产物齐全，`manifest.status == "ok"` |
| `1` | 运行失败 | `manifest.status` 会说明原因（见下表） |
| `2` | 参数错误 | topic 太短/为空等，流水线未启动，未产生任何调用 |

---

## 常见错误与处理

先用 `manifest.json` 的 `status`、`error`、`llm_usage.per_stage` 定位，再看下表：

| 现象 | 含义 | 怎么办 |
| --- | --- | --- |
| `ConfigError: DEEPSEEK_API_KEY is required ...` | 没配密钥却在 live 模式 | 写 `.env`，或用 `BRIEFING_LLM_MODE=stub` 离线跑 |
| `status: insufficient_papers` | 筛选后可用的论文不足 3 篇 | 错误信息里附了检索式与放宽建议：换措辞、放宽时间窗、加数据源 |
| `status: analysis_failed` | 某篇论文无法分析（例如引文无法在摘要中溯源） | 看 `error.message` 里列出的 paper_id；这类论文不会被静默丢弃 |
| `status: verification_failed` | 修订一次后仍有 fatal 违规 | 看 `verification` 与 `error.message` 的违规位置 |
| `status: budget_exceeded` | 超出 `BRIEFING_MAX_LLM_CALLS` / `BRIEFING_MAX_TOKENS_BUDGET` | 调大上限，或缩小候选池 |
| `status: render_failed` | 所有 PDF 引擎都失败 | 检查 `brew install pango` 是否完成 |
| `finish_reason=length` | 模型回复被输出上限截断（推理模型的思维 token 也占这个预算） | 调大 `BRIEFING_MAX_OUTPUT_TOKENS`，或调小 `BRIEFING_MAX_CANDIDATES` |
| `SourceError: no recorded source response` | stub 模式缺少录制文件 | 确认 `BRIEFING_FIXTURES_DIR` 下有 `*.xml` 录制 |
| 检索偶发失败 | 单个查询 429/超时会降级，其余查询继续 | 正常现象；失败详情记在日志与 `retrieval` 中 |

---

## 它是怎么工作的

```
Planner → Retriever → Normalizer → Screener → Analyzer ×N → Synthesizer → Verifier → Renderer
 (LLM)      (arXiv)      (纯代码)     (LLM)      (LLM,并行)      (LLM)     (L1代码+L2可选)  (纯代码)
```

三个贯穿全局的设计原则：

1. **能用代码算的绝不交给模型**：引用编号、参考文献、候选去重、并发上限、预算核算、阶段检查点。
2. **模型只接触它该决定的东西**：选文时只看到候选 tag，写简报时不能改动画像数据，修复由"校验违规 → 一次修订"完成。
3. **失败要显式**：宁可失败并保留中间产物，也不产出看起来完整、实际缺斤少两的报告。

完整设计见 [`docs/architecture.md`](docs/architecture.md)（契约、状态机、不变量 I1–I9）；项目契约见 [`AGENTS.md`](AGENTS.md)；进度与已知缺口见 [`docs/SPEC.md`](docs/SPEC.md)。

---

## 配置项

全部通过环境变量（或 `.env`）配置，前缀 `BRIEFING_`。完整清单与说明见 [`.env.example`](.env.example)，常用的：

| 变量 | 默认值 | 作用 |
| --- | --- | --- |
| `DEEPSEEK_API_KEY` | — | 凭证（不加 `BRIEFING_` 前缀） |
| `BRIEFING_DEEPSEEK_MODEL` | `deepseek-v4-pro` | 模型 id，必须来自配置 |
| `BRIEFING_DEEPSEEK_THINKING` | `disabled` | 关闭思维链：两个可用模型都是推理模型，思维 token 按输出计费且会挤占回复预算 |
| `BRIEFING_MAX_CANDIDATES` | `40` | 送入筛选的候选池上限（`AGENTS.md` §7 规定 20–40） |
| `BRIEFING_MAX_OUTPUT_TOKENS` | `8192` | 单次回复上限 |
| `BRIEFING_MAX_LLM_CALLS` | `40` | 单次运行的模型调用上限 |
| `BRIEFING_MAX_CONCURRENCY` | `4` | 并行 LLM 调用上限 |
| `BRIEFING_LLM_MODE` | `live` | `stub` = 完全离线（模型与检索都回放录制） |
| `BRIEFING_VERIFY_SEMANTIC` | `0` | 是否启用 L2 语义验证（额外的 LLM 调用） |

---

## 测试

```bash
# 提交前门禁（必须全绿）
uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest -q

# 全离线端到端（无需密钥、不联网）
uv run pytest tests/integration/test_end_to_end_offline.py -q

# live 检查：默认跳过
RUN_LIVE_TESTS=1 uv run pytest -m live -q          # 廉价档：模型 id + 一次结构化调用
RUN_LIVE_TESTS=1 RUN_LIVE_E2E=1 uv run pytest -m live -q   # 再加整条流水线（会花钱）
```

默认测试套件**完全不联网**：HTTP 用 `respx` 或录制样本，模型用 stub。

---

## 密钥安全

仓库有两层防护，改敏感配置时请保持有效：

1. **提交前钩子** `.githooks/pre-commit` —— 每个 clone 执行一次 `git config core.hooksPath .githooks` 启用；它拒绝暂存 `.env` 家族与密钥文件，并扫描暂存 diff。
2. **`tests/unit/test_no_secrets.py`** —— 属于门禁的一部分，扫描"会被上传的那些文件"，任何密钥形状的内容都会让它失败。

```bash
git config core.hooksPath .githooks
```

另外：`.env` 与 `data/`、`outputs/`、`scratch/` 都在 `.gitignore` 中。

---

## 目录结构

```
├── AGENTS.md                 # 项目契约（最高优先级）
├── TASK.md                   # 分步执行计划
├── README.md
├── docs/
│   ├── SPEC.md               # 进度、已知缺口、待办
│   ├── architecture.md       # 多 Agent 架构设计
│   └── decisions/            # 关键决策记录
├── src/briefing/
│   ├── agents/               # Planner / Retriever / Normalizer / Screener / Analyzer / Synthesizer
│   ├── llm/                  # LLM 客户端、预算、stub 回放
│   ├── prompts/              # 带版本号的 prompt（*_vN.md）
│   ├── report/               # 引用编号、Markdown 导出、HTML 模板、PDF 渲染
│   ├── sources/              # arXiv 源、缓存、录制回放
│   ├── verify/               # L1 确定性验证、L2 语义验证、报告组装
│   ├── manifest.py           # 运行审计契约与检查点
│   └── orchestrator.py       # 流水线编排
├── tests/
│   ├── unit/                 # 全离线
│   ├── integration/          # 离线端到端、PDF 渲染、live 冒烟
│   └── fixtures/             # 录制的 HTTP 响应与模型回复
└── examples/                 # 人工确认过的样例产物
```
