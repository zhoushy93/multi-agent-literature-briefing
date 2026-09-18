# TASK.md — 分步执行计划

> 依据：`AGENTS.md`（项目契约，冲突时以其为准）+ `docs/architecture.md`（实现级设计）。
> 本文档是**执行计划**：每一步都有产出、完成判据和可运行的 check 命令。
> 进度追踪以 `docs/SPEC.md` 为准（Step 16 创建），本文档的勾选框仅作对照。

---

## 使用方式

1. 按 Step 顺序执行；每一步的 **Check 必须全绿**才进入下一步。
2. 每个 Step 结尾的 **Gate** 是统一门禁，四段命令缺一不可：

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest -q
```

3. 任何 Gate 失败：修问题，不要改断言语义来绕过（`AGENTS.md` §13.6）。
4. 需要联网或花钱的动作（`uv python install`、`uv sync`、`brew install`、live 测试）**先说明预计开销再执行**（`AGENTS.md` §13.4）。

### 依赖顺序（DAG）

```
Step 0 环境探测
  └─> Step 1 脚手架
        └─> Step 2 schemas 契约
              ├─> Step 3 config + LLMClient ─┐
              └─> Step 4 cache + arXiv 源 ────┤
                                              ├─> Step 5 Normalizer（纯代码）
                                              ├─> Step 6 Planner
                                              ├─> Step 7 Screener
                                              ├─> Step 8 Analyzer（依赖 5）
                                              ├─> Step 9 Synthesizer
                                              ├─> Step 10 Verifier L1（依赖 2、9）
                                              └─> Step 11 Verifier L2（依赖 10、3）
                                                     └─> Step 12 Renderer（只依赖 2）
                                                           └─> Step 13 Orchestrator + CLI
                                                                 └─> Step 14 端到端离线
                                                                       └─> Step 15 live 冒烟
                                                                             └─> Step 16 文档收尾
```

---

## Step 0 — 环境与前置决策

**产出**：`docs/decisions/001-runtime-and-renderer.md`

**已探测到的本机事实**（2026-09-17，macOS arm64）：

| 项 | 现状 | 影响 |
| --- | --- | --- |
| `python3` | 3.9.6 | **不满足** Python 3.11+，必须由 uv 管理解释器 |
| `uv` | 0.12.15 可用 | 可直接使用 |
| `pkg-config` / `brew pango` | 都不存在 | WeasyPrint 大概率无法 import |
| Google Chrome | 已安装 | 可作为降级渲染器（headless `--print-to-pdf`） |
| CJK 字体 | 无 PingFang.ttc；有 `STHeiti Light/Medium` | 字体回退链必须含 `STHeiti` |
| `git config user.name/email` | 均未配置 | 提交前必须配置，否则 commit 失败 |

**要做的事**：

1. 安装 Python 3.11+：`uv python install 3.11`（需网络）。
2. 确定 PDF 渲染器，二选一：
   - **(A) 保持 WeasyPrint 为主**：`brew install pango`（需网络），并在代码中处理 macOS 动态库路径问题；
   - **(B) Chrome 为主渲染器**：无需新依赖，但这**改变了 `AGENTS.md` §8 的架构级约定**，必须在同一次改动中更新 `AGENTS.md` §8 并写进决策记录。
3. 确认 CJK 字体策略：默认用系统 `STHeiti`；若坚持 Noto Sans CJK 则需 `brew install --cask font-noto-sans-cjk`（需网络）。
4. 配置 git 身份：`git config user.name "..."` 与 `git config user.email "..."`。
5. 把以上结论写入 `docs/decisions/001-runtime-and-renderer.md`，并关闭 `AGENTS.md` §14 中的第 3 条。

**Check**：

```bash
# 1) uv 能拿到 3.11+
uv python install 3.11 && uv run --python 3.11 python -c "import sys; assert sys.version_info >= (3,11); print(sys.version)"

# 2) 决策记录已存在且要素齐全（渲染器决策 + 字体决策）
rg -n "渲染器|pango|STHeiti" docs/decisions/001-runtime-and-renderer.md

# 3) git 身份已配置
git config user.name && git config user.email
```

**注意**：若选 (B)，`AGENTS.md` §8 中「WeasyPrint 为主」的措辞要同步修改，否则文档与代码不一致。

---

## Step 1 — 项目脚手架

**产出**：`pyproject.toml`、`uv.lock`、`src/briefing/__init__.py`、`src/briefing/cli.py`（仅 `--help` 骨架）、`.env.example`、`.gitignore`、`tests/unit/test_package.py`

**要点**：

- 依赖严格限定在 `AGENTS.md` §4 白名单内；CLI **使用 stdlib `argparse`**（`typer` 不在白名单，引入需先写决策记录）。
- Ruff、mypy、pytest 配置统一写在 `pyproject.toml`；mypy 对 `src` 开严格模式。
- `.gitignore` 覆盖 `data/cache/`、`outputs/`、`.env`、`scratch/`、`__pycache__/`。
- `.env.example` 只放占位值，至少包含：`DEEPSEEK_API_KEY=`、`BRIEFING_DEEPSEEK_MODEL=`、`BRIEFING_DEEPSEEK_BASE_URL=`、`BRIEFING_MAX_CONCURRENCY=4`、`BRIEFING_MAX_LLM_CALLS=40`、`BRIEFING_MAX_TOKENS_BUDGET=400000`、`BRIEFING_LLM_MODE=live`、`BRIEFING_FIXTURES_DIR=`。

**完成判据**：`uv sync` 成功；`briefing --help` 有输出；至少 1 个测试通过。

**Check**：

```bash
uv sync --dev
uv run briefing --help
uv run python -c "import briefing; print(briefing.__version__)"
uv run pytest -q   # 注意：无测试时 pytest 退出码为 5，故 Step 1 必须含 test_package.py
```

随后跑 **Gate**。

**注意**：`briefing` 命令通过 `[project.scripts]` 暴露；若 `--help` 报 command not found，说明 entry point 或 `uv sync` 未生效，先修这个再继续。

---

## Step 2 — 数据契约 `schemas.py`

**产出**：`src/briefing/schemas.py`、`tests/unit/test_schemas.py`

**要点**：实现 `docs/architecture.md` §3 的全部模型，统一 `extra="forbid"`；`SelectedPapers` 强制 3..5；`Paper` 强制 `authors ≥1` 且 `url` 非空；`Evidence.quote` ≤300 字符；`Claim`、`Violation` 的 `Literal` 取值与文档完全一致。

**完成判据**：正例可构造；反例（2 篇选文、多余字段、缺 authors、未知 `Violation.kind`）全部被 pydantic 拒绝。

**Check**：

```bash
uv run pytest tests/unit/test_schemas.py -q
# 契约模型名与设计文档比对
uv run python -c "import briefing.schemas as s; print([n for n in dir(s) if n[0].isupper()])"
```

随后跑 **Gate**。

---

## Step 3 — 配置、`LLMClient` 协议与 DeepSeek 客户端

**产出**：`src/briefing/errors.py`、`src/briefing/config.py`、`src/briefing/llm/base.py`、`deepseek_client.py`、`budget.py`、`stub_client.py`、`tests/unit/test_config.py`、`test_deepseek_client.py`、`test_budget.py`、`test_stub_client.py`

**要点**：

- `config.py` 用 `pydantic-settings`，env 前缀 `BRIEFING_`；模型名与 base url 只来自配置。
- `deepseek_client.py` 走 OpenAI 兼容的 `POST {base_url}/chat/completions`；JSON 输出 + pydantic 校验；校验失败时携带错误原文重试 ≤2；429/5xx/超时退避 ≤5；非 429 的 4xx 不重试。
- `stub_client.py` 让 `BRIEFING_LLM_MODE=stub` 时从 `BRIEFING_FIXTURES_DIR` 读取预置响应 —— 这是 Step 14 离线端到端的基础。
- `budget.py` 在**每次调用前**检查 `MAX_LLM_CALLS` 与 `MAX_TOKENS_BUDGET`，超限抛 `BudgetExceeded`。

**完成判据**：全部用 `respx` mock，无真实网络；覆盖退避、schema 重试、4xx 不重试、预算超限四条路径。

**Check**：

```bash
uv run pytest tests/unit/test_config.py tests/unit/test_deepseek_client.py tests/unit/test_budget.py tests/unit/test_stub_client.py -q

# 模型名不得硬编码到业务代码或 prompt（有命中即失败）。
# config.py 是唯一例外：AGENTS.md §6 规定默认模型 id 就写在那里。
! rg -n "deepseek-(chat|reasoner)" src/briefing/ -g '!config.py'

# 禁止阻塞式 HTTP 客户端与阻塞式 sleep（有命中即失败）
! rg -n "^(\s*)(import|from) requests\b" src/briefing/
! rg -n "time\.sleep\(" src/briefing/
```

随后跑 **Gate**。

---

## Step 4 — 缓存与 arXiv 数据源

**产出**：`src/briefing/sources/base.py`、`sources/cache.py`、`sources/arxiv.py`、`tests/fixtures/arxiv_*.xml`、`tests/unit/test_cache.py`、`tests/unit/test_arxiv_source.py`

**要点**：

- `Source` Protocol：`async def search(query, params) -> list[Paper]`。
- 缓存键 = `sha256(source + query + params)`，落盘 `data/cache/<source>/<key>.json`，TTL 30 天，支持 `--no-cache` 绕过；命中、未命中、绕过都要计数。
- arXiv Atom 解析：抽取 title、authors、year、venue、url、doi、arxiv_id、abstract；`url` 必须来自响应，不得拼接猜测。
- 请求头 `User-Agent: briefing/0.1 (+repo url)`；单次 `max_results` ≤ 100。

**完成判据**：fixtures 驱动、零网络；缓存键稳定；TTL 过期后重新请求；`--no-cache` 不读缓存。

**Check**：

```bash
uv run pytest tests/unit/test_cache.py tests/unit/test_arxiv_source.py -q
uv run pytest tests/unit/test_arxiv_source.py -q -k "doi or no_abstract or url_from_response"
```

随后跑 **Gate**。

---

## Step 5 — `Normalizer`（纯确定性）

**产出**：`src/briefing/agents/normalizer.py`、`tests/unit/test_normalizer.py`

**要点**：去重优先级 DOI → arXiv ID → 归一化标题（lower、去标点、压缩空白、去副标题）；预过滤统计（`no_abstract`、`short_abstract`）写入 `dropped` 台账，但**论文本身保留**（AGENTS.md §7 的「降权」是软信号，Step 7 的 S4 需读取该台账给这些候选降权）；输出排序必须**跨运行稳定**。

**完成判据**：三条去重路径各有正反用例；同一输入两次运行输出顺序完全一致。

**Check**：

```bash
uv run pytest tests/unit/test_normalizer.py -q
uv run pytest tests/unit/test_normalizer.py -q -k "stable or order"
```

随后跑 **Gate**。

---

## Step 6 — `Planner`

**产出**：`src/briefing/prompts/planner_v1.md`、`src/briefing/agents/planner.py`、`src/briefing/prompts/loader.py`、`tests/unit/test_planner.py`、`tests/unit/test_prompts.py`

**要点**：中文 topic 也要产出**英文**检索式；`queries` 2..6、`keywords` 3..12；temperature 0。prompt 文件头部必须含 `agent`、`version`、`inputs`、`output_json_schema`、`failure_modes`（`docs/architecture.md` §9）。

**Check**：

```bash
uv run pytest tests/unit/test_planner.py tests/unit/test_prompts.py -q
# prompt 元数据完整性
uv run pytest tests/unit/test_prompts.py -q -k "header or version"
```

随后跑 **Gate**。

---

## Step 7 — `Screener`

**产出**：`src/briefing/prompts/screener_v1.md`、`src/briefing/agents/screener.py`、`tests/unit/test_screener.py`

**要点**：候选集外的 `paper_id` 由代码丢弃并记 `warn`；选文 `< min_papers` ⇒ `InsufficientPapersError`（须含查询式、候选数、放宽建议）；选文 `> max_papers` ⇒ 截断并记 `warn`；LLM 调用 ≤3。

**Check**：

```bash
uv run pytest tests/unit/test_screener.py -q
uv run pytest tests/unit/test_screener.py -q -k "insufficient or out_of_candidate or truncate"
```

随后跑 **Gate**。

---

## Step 8 — `Analyzer`（fan-out）

**产出**：`src/briefing/prompts/analyzer_v2.md`、`src/briefing/agents/analyzer.py`、`tests/unit/test_analyzer.py`

**要点**：每篇并发，受全局 `Semaphore(MAX_CONCURRENCY)` 限制；`paper_id` 不匹配即失败；`evidence.quote` 必须能在输入摘要或元数据中原文匹配；重试 ≤2 后仍失败 ⇒ 整次运行失败（不得静默丢弃）；temperature 0.2；单次输入 ≤8k 字符。

**Check**：

```bash
uv run pytest tests/unit/test_analyzer.py -q
# 并发上限：stub client 记录峰值并发
uv run pytest tests/unit/test_analyzer.py -q -k "concurrency or semaphore"
# 串页防护与证据溯源
uv run pytest tests/unit/test_analyzer.py -q -k "paper_id or evidence"
```

随后跑 **Gate**。

---

## Step 9 — `Synthesizer`

**产出**：`src/briefing/prompts/synthesizer_v2.md`、`src/briefing/agents/synthesizer.py`、`tests/unit/test_synthesizer.py`

**要点**：只允许使用给定引用键 `P1..Pn`；`comparison` 行数 == 论文数；`executive_summary` ≤120 字；支持带 `violations` 的**修订模式**（供 S7 回路调用）；temperature 0.3。

**Check**：

```bash
uv run pytest tests/unit/test_synthesizer.py -q
uv run pytest tests/unit/test_synthesizer.py -q -k "revision or comparison_rows or summary_length"
```

随后跑 **Gate**。

---

## Step 10 — `Verifier` L1（确定性层）

**产出**：`src/briefing/verify/deterministic.py`、`tests/unit/test_verify_deterministic.py`

**要点**：检查未知引用键、key 到 `paper_id` 的映射、`comparison` 与 `paper_cards` 覆盖完整且无重复、`executive_summary` 长度、语言一致性、`evidence` 溯源。**不含 LLM**，永远开启。

**Check**：

```bash
uv run pytest tests/unit/test_verify_deterministic.py -q
# 断言该模块不引入任何 LLM 依赖（有命中即失败）
! rg -n "LLMClient|deepseek" src/briefing/verify/deterministic.py
```

随后跑 **Gate**。

---

## Step 11 — `Verifier` L2（语义层）

**产出**：`src/briefing/prompts/verifier_v1.md`、`src/briefing/verify/semantic.py`、`tests/unit/test_verify_semantic.py`

**要点**：只输出 `Violation` 列表，不得改写正文；temperature 0；`BRIEFING_VERIFY_SEMANTIC=0` 可关闭（离线测试默认关闭）；无支撑断言 ⇒ 触发一次修订，第二次仍失败 ⇒ `verification_failed`。

**Check**：

```bash
uv run pytest tests/unit/test_verify_semantic.py -q
uv run pytest tests/unit/test_verify_semantic.py -q -k "revision_once or second_failure or disabled"
```

随后跑 **Gate**。

---

## Step 12 — `Renderer`（编号分配 → HTML → PDF）

**产出**：`src/briefing/report/references.py`、`report/render_pdf.py`、`report/templates/report.html.jinja`、`report/styles.css`、`tests/unit/test_references.py`、`tests/integration/test_pdf_render.py`

**要点**：

- `references.py` 按 `SelectedPapers.items` 顺序**确定性**分配 `[1]..[n]`，引用键映射为 `Pk → [k]`；参考文献只从 `papers.json` 生成。
- HTML 采用 `AGENTS.md` §8 的固定 10 章结构；字体回退链必须包含 `STHeiti`（本机无 PingFang 与 Noto CJK）。
- 主渲染器按 Step 0 的决策；发生降级时置 `renderer.fallback = true`。
- 校验：页数 > 0、`pypdf` 能提取到中文文本、字体字典含 `FontFile*` 条目（证明字体内嵌）。

**Check**：

```bash
uv run pytest tests/unit/test_references.py tests/integration/test_pdf_render.py -q
# 编号确定性：同输入两次渲染，编号映射一致
uv run pytest tests/unit/test_references.py -q -k "deterministic"
# 中文字体嵌入与文本可提取
uv run pytest tests/integration/test_pdf_render.py -q -k "cjk or embedded or page_count"
# 回退链里的字体确实装在本机
ls /System/Library/Fonts/STHeiti*.ttc
```

随后跑 **Gate**。

---

## Step 13 — Orchestrator 与 CLI

**产出**：`src/briefing/orchestrator.py`、`src/briefing/manifest.py`、完整 `src/briefing/cli.py`、`tests/integration/test_orchestrator_offline.py`

**要点**：

- 状态机与 `manifest.status` 取值集合严格按 `docs/architecture.md` §2.2。
- 每阶段写 `_stages/<NN>_<name>.json`；`--resume` 依据 `input_fingerprint` 跳过已完成阶段。
- `finally` 分支保证**任意结束路径都写出 `manifest.json`**（不变量 I7）。
- 退出码：参数错误 2、运行失败 1、成功 0。

**Check**：

```bash
uv run pytest tests/integration/test_orchestrator_offline.py -q

# 退出码：参数错误必须是 2
uv run briefing run --topic "" --out /tmp/briefing-argcheck; echo "exit=$? 期望 2"

# resume 行为与 manifest 必备字段
uv run pytest tests/integration/test_orchestrator_offline.py -q -k "resume"
uv run pytest tests/integration/test_orchestrator_offline.py -q -k "manifest"
```

随后跑 **Gate**。

---

## Step 14 — 端到端（全离线 fixtures）

**产出**：`tests/integration/test_end_to_end_offline.py`、`tests/fixtures/llm/*.json`

**要点**：用 stub LLM + 录制的 HTTP fixtures 跑完整 S1→S8，逐条断言 `docs/architecture.md` §7 的 I1–I9，重点是 I1（3–5 篇）、I3（引用编号可映射）、I4（参考文献来自真实检索）、I5（无静默丢论文）。

**完成判据**：一次运行产出全部 5 个产物；注入式失败用例（候选仅 2 篇、Analyzer 失败、Verifier 二次失败）分别得到正确的 `status`。

**Check**：

```bash
uv run pytest tests/integration/test_end_to_end_offline.py -q
uv run pytest tests/integration/test_end_to_end_offline.py -q -k "invariant"
uv run pytest tests/integration/test_end_to_end_offline.py -q -k "failure_status"

# 手动跑一次离线端到端，确认 5 个产物落盘
BRIEFING_LLM_MODE=stub BRIEFING_FIXTURES_DIR=tests/fixtures/llm \
  uv run briefing run --topic "diffusion models for weather forecasting" \
  --out /tmp/briefing-e2e --no-cache
ls -1 /tmp/briefing-e2e | sort
uv run python -c "import json; d=json.load(open('/tmp/briefing-e2e/manifest.json')); print(d['status'], d['llm_usage']['calls'], d['retrieval']['selected'])"
```

随后跑 **Gate**。

---

## Step 15 — Live 冒烟（真实联网 + 真实 LLM）

**前置**：`DEEPSEEK_API_KEY` 已配置、网络可用、Step 0 已确认可用模型 id。
**预计开销**：单次运行 7–9 次 LLM 调用（上限 40），执行前先向用户确认。

**live 测试分两档**（避免每次跑测试都花钱）：`RUN_LIVE_TESTS=1` 只跑廉价连通性检查（确认模型 id 可用 + 一次结构化调用）；完整流水线那一条还要再加 `RUN_LIVE_E2E=1` 才会执行。

**要点**：确认 `DEEPSEEK_MODEL` 是官方当前有效的模型 id，并把结论写入 `docs/decisions/`（关闭 `AGENTS.md` §14 第 1 条）；观察真实限流下退避是否生效。

**Check**：

```bash
# 1) 先确认账号下可用模型 id（不要凭记忆填）
curl -s https://api.deepseek.com/models -H "Authorization: Bearer $DEEPSEEK_API_KEY" | head -c 600

# 2) 真实端到端跑一次
uv run briefing run --topic "diffusion models for weather forecasting" \
  --out "outputs/smoke-$(date +%Y%m%d)" --no-cache
uv run python -c "import json,glob; p=sorted(glob.glob('outputs/smoke-*/manifest.json'))[-1]; d=json.load(open(p)); print(d['status'], d['model']['id'], d['llm_usage'])"

# 3) live 标记的测试
RUN_LIVE_TESTS=1 uv run pytest -m live -q
```

**注意**：产出物不得提交（`outputs/` 已在 `.gitignore`）；若模型 id 与配置默认值不符，改 `.env.example` 与决策记录，不改业务代码。

---

## Step 16 — 文档收尾与示例

**产出**：`README.md`、`docs/SPEC.md`、`docs/architecture.md`（或指向 `docs/architecture.md` 的指针）、`examples/<date>-<topic-slug>/`、更新后的 `AGENTS.md` §14 勾选状态

**要点**：

- 解决文档路径不一致：`AGENTS.md` §5 与 §13 指向 `docs/architecture.md`，而多 Agent 设计实际在根目录 `docs/architecture.md`。二选一并补齐 —— 把文件移入 `docs/`，或在 `AGENTS.md` 与 `docs/architecture.md` 中加入指针。
- README 必须写明：安装、`DEEPSEEK_API_KEY` 配置、离线 stub 模式怎么跑、产物清单、常见错误码。
- `examples/` 放一份人工确认过的报告，并标注为样例。
- 逐条勾掉 `AGENTS.md` §14 中已完成项，未完成的保留并说明原因。

**Check**：

```bash
# 文档交叉引用可达
rg -n "docs/architecture.md|docs/architecture.md" AGENTS.md README.md docs/architecture.md
# README 覆盖关键小节
rg -n "DEEPSEEK_API_KEY|BRIEFING_LLM_MODE|退出码|产物" README.md
# 示例产物存在且含 PDF
ls -1 examples/*/report.pdf
# 最终验收门禁
uv sync --frozen && uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest -q
```

---

## 验收对照表（映射 `AGENTS.md` §1.1）

| `AGENTS.md` 验收条件 | 覆盖步骤 | 验证方式 |
| --- | --- | --- |
| 5 个产物齐全 | Step 13 / 14 / 16 | `/tmp/briefing-e2e` 与 `examples/` 的 `ls` |
| 论文数 3 ≤ n ≤ 5，不足即失败 | Step 7 / 14 | `test_screener.py -k insufficient`、`-k failure_status` |
| 元数据完整、URL 来自 API | Step 2 / 4 | `test_arxiv_source.py`、`test_schemas.py` |
| 零虚构引用 | Step 2 / 10 / 12 | `test_verify_deterministic.py`、`test_references.py` |
| PDF 字体内嵌、文本可选中 | Step 12 | `test_pdf_render.py -k "cjk or embedded"` |
| 调用与 token 上限可审计 | Step 3 / 13 | `test_budget.py`、`-k manifest` |
| 可回答「哪版模型与 prompt」 | Step 6 / 9 / 13 | `-k manifest` 的字段断言 |

---

## 未决项（执行中遇到即停手确认）

- `docs/architecture.md` §14 的 4 条待确认项：并发配额、L2 语义验证开销、`--resume` 指纹是否排除 `retrieved_at`、报告页数与卡片字数上限。
- 文档路径统一方案（Step 16 第 1 条）。
- 若 Step 0 选 (B) Chrome 作主渲染器，`AGENTS.md` §8 必须同步修改 —— 属架构级决策变更。
