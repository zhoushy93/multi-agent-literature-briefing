# SPEC — 当前范围、进度与待办

> 本文件是**进度快照**，不是契约。契约见 `AGENTS.md`，实现级设计见 `docs/architecture.md`。
> 最近更新：2026-09-17

---

## 1. 当前范围（v0）

输入一个 topic，检索 3–5 篇相关论文，逐篇分析，输出可引用的 PDF 简报。

已实现的范围**与 `AGENTS.md` §1.1 的验收条件一致**，非目标（§1.2）仍非目标：不做全文抓取、不做引文网络、不做 Web UI、不做系统性综述。

---

## 2. 进度

`TASK.md` 的 Step 0–16 全部完成。

| 阶段 | 状态 | 证据 |
| --- | --- | --- |
| Step 0 环境与渲染器决策 | ✅ | `docs/decisions/001-runtime-and-renderer.md` |
| Step 1 脚手架 | ✅ | `pyproject.toml`、`uv.lock`、离线 `/ Gate` 全绿 |
| Step 2 数据契约 | ✅ | `src/briefing/schemas.py`、39 个契约测试 |
| Step 3 LLM 客户端与预算 | ✅ | `llm/`、50 个测试（respx 全离线） |
| Step 4 缓存与 arXiv 源 | ✅ | `sources/`、录制响应驱动的解析测试 |
| Step 5 Normalizer | ✅ | 去重（并查集）+ 预过滤台账 + 稳定排序 |
| Step 6 Planner | ✅ | prompt 合约测试（版本/占位符/schema） |
| Step 7 Screener | ✅ | tag 间接层、不足 3 篇/超出 5 篇/未知 tag 全覆盖 |
| Step 8 Analyzer | ✅ | 引文溯源、串页防护、并发上限实测 |
| Step 9 Synthesizer | ✅ | 封闭引用键集合、修订模式 |
| Step 10 Verifier L1 | ✅ | 六类确定性检查，无 LLM 依赖（AST 测试保证） |
| Step 11 Verifier L2 | ✅ | 只输出违规、默认关闭、修订策略纯函数化 |
| Step 12 Renderer | ✅ | 确定性编号、10 章模板、真 PDF 校验 |
| Step 13 Orchestrator + CLI | ✅ | 状态机、检查点/`--resume`、manifest、退出码 |
| Step 14 全离线端到端 | ✅ | 录制回放（无网络）、不变量 I1–I9 |
| Step 15 live 冒烟 | ✅ | 真实运行 `ok`：5 篇 / 10 次调用 / 11 页 |
| Step 16 文档收尾 | ✅ | 本文件、`README.md`、`examples/` |

### 2.1 报告改版（2026-09-17，Step 16 之后）

为提升可读性做的一次报告增强，涉及契约与提示词，故记录在此：

| 变更 | 内容 | 影响的文件 |
| --- | --- | --- |
| 新增契约字段 | `PaperAnalysis.relevance`（必填）：该论文**对本次 topic** 的意义，由 Analyzer 填写 | `schemas.py`、`prompts/analyzer_v2.md`、`docs/architecture.md` §3.2/§4/§9 |
| 新增章节 | 第 3 章「主题速览」：topic overview 图 + 速览表 | `report/figures.py`（新）、模板、样式、`AGENTS.md` §8 |
| 卡片改版 | 两列表格：问题 / 方法 / 数据与实验 / 主要结论 / 局限 / **与主题的关系** / 可复用点 / 置信度 | 同上 |

分析阶段现在需要 topic（写 `relevance` 用），因此 `Analyzer` 构造参数新增 `topic` / `lang`，由编排器注入。

**注意**：`examples/2026-09-17-diffusion-weather-forecasting/` 是本次改版**之前**的真实运行产物（`analyzer_v1`，无 relevance、无速览章节），保留作为历史样例；新的报告版式见 README 中的离线演示命令。

**测试规模**：447 个离线测试 + 3 个 live 测试（默认跳过）。提交前门禁为 `uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest -q`。

---

## 3. 待办与已知缺口

### 3.1 明确的已知缺口（代码与文档不一致或功能未实现）

| # | 缺口 | 影响 | 建议 |
| --- | --- | --- | --- |
| G1 | `BRIEFING_DEEPSEEK_MODEL_REASONING` 是空转配置：会写入 manifest，但 `create_llm_client` 只按 `BRIEFING_DEEPSEEK_MODEL` 建一个客户端（`AGENTS.md` §6 允许按阶段拆分模型） | 无法给 Screener/Synthesizer 单独指定更强模型 | 需要时实现：按阶段建第二个客户端并由编排器注入 |
| G2 | 数据源层没有重试：arXiv 返回 5xx/超时只会让该查询失败（整体降级，不致命） | 单查询偶发失败会损失召回 | 复用 LLM 侧同规格的退避重试 |
| G3 | `Paper.doi` 与 `arxiv_id` 都可为 `None`，`AGENTS.md` §1.1「至少其一，确实不存在时显式标注 null 并说明」中的「说明」没有承载字段 | 该验收条件只被部分机制保证 | 若需要，加 `identifier_note` 字段并同步契约文档 |

### 3.2 未决事项

| # | 事项 | 现状 |
| --- | --- | --- |
| O1 | 是否接入 Semantic Scholar（需要 API key） | 未接入；`Source` 协议已就绪，加一个实现即可 |
| O2 | 并发上限 `BRIEFING_MAX_CONCURRENCY=4` 与账号实际配额 | 实测 3 篇并行分析无问题；更高并发未压测 |
| O3 | L2 语义验证的成本占比 | 默认关闭，未在真实运行中开启过 |
| O4 | `--resume` 的指纹是否应排除检索时间 | 不排除：`retrieved_at` 来自缓存信封，缓存命中时稳定，因此指纹稳定 |

---

## 4. 真实运行基线（2026-09-17）

一次成功的真实运行（详见 `examples/2026-09-17-diffusion-weather-forecasting/`）：

- 10 次模型调用、34,962 prompt + 7,037 completion token、77.9 秒、11 页 PDF
- 检索 107 篇 → 上限截断至 40 → 入选 5 篇
- 首稿有 1 处 fatal 违规 → 触发 1 次修订 → 通过

成本与耗时以此为基线；超出 2 倍以上时应先看 `manifest.json` 的 `llm_usage.per_stage`。
