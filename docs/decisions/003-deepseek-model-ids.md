# 003 — DeepSeek 模型 id 与默认值

状态：**已决定**
日期：2026-09-17
关联：`AGENTS.md` §6 §14、`TASK.md` Step 15

---

## 背景

AGENTS.md §14 第 1 条要求"以官方文档公布的确切模型 id 为准，不要凭记忆填写"。此前 `BRIEFING_DEEPSEEK_MODEL` 的默认值是从文档惯例推测的 `deepseek-chat`。

Step 15 实测（`GET https://api.deepseek.com/models`，账号本人密钥）：

```
可用模型 id: ['deepseek-flash', 'deepseek-v4-pro']
```

**`deepseek-chat` 不在其中**。如果沿用旧默认值，任何一次真实调用都会失败——这正是 §14 那条警告要防的事。

## 决定

`BRIEFING_DEEPSEEK_MODEL` 默认值改为 **`deepseek-v4-pro`**：

- 它正是本项目目标里说的"DeepSeek V4"；
- 它是账号实际提供的两个模型之一；
- `deepseek-flash` 作为更快更省的备选，已记入 `.env.example` 注释。

## 后果与边界

- **两个可用模型都是推理模型**：最小请求下 `deepseek-v4-pro` 的输出 token 中 34/40 是思维 token，`deepseek-flash` 是 72/86。思维 token 按输出计费，且**占用 `max_tokens` 预算**——这正是 2026-09-17 两次真实端到端失败的原因（4096 与 8192 都被截断在 JSON 中间）。
- **决定：结构化阶段默认关闭思考。** 实测 `"thinking": {"type": "disabled"}` 被接受且思维 token 归零（输出 40 → 5）；`"enable_thinking": false` **无效**（仍有 29 个思维 token），不要用后者。配置项 `BRIEFING_DEEPSEEK_THINKING=disabled|auto`（默认 `disabled`），值写入 manifest 的 `model.params.thinking`。
- **`BRIEFING_DEEPSEEK_MODEL_REASONING` 目前是空转的**：配置项存在、也会写入 manifest，但 `create_llm_client` 只按 `BRIEFING_DEEPSEEK_MODEL` 建一个客户端，没有为 Screener/Synthesizer 单独建第二个。AGENTS.md §6 允许这种拆分，代码尚未实现——需要时再补，并同步该文档。若要恢复推理能力，用 `BRIEFING_DEEPSEEK_THINKING=auto` 并同时调大 `BRIEFING_MAX_OUTPUT_TOKENS`。
- 模型 id 会随服务商调整而变化。**换机器或换账号时先跑一次 `GET /models`**，不要照抄这里的值。
- 上下文长度与 JSON 输出：实测 `deepseek-v4-pro` 支持 `response_format={"type": "json_object"}`（Step 15 的结构化调用与整条流水线均通过）。具体上下文窗口未单独测量，prompt 侧已有 8k 字符的输入上限（§4 S5），因此不作为阻塞项。

## 验证

```bash
set -a; . ./.env; set +a
curl -sS https://api.deepseek.com/models -H "Authorization: Bearer $DEEPSEEK_API_KEY"
RUN_LIVE_TESTS=1 uv run pytest -m live -q
```

2026-09-17 的真实端到端结果（`briefing run --topic "diffusion models for weather forecasting" --no-cache`）：

| 项 | 值 |
| --- | --- |
| status | `ok`（修订 1 次后通过验证） |
| 模型 / 思考 | `deepseek-v4-pro` / `disabled` |
| 模型调用 | 10（S1 1、S4 2、S5 5、S6 2），上限 40 |
| token | 34,962 prompt + 7,037 completion |
| 检索 | 107 篇候选 → 上限截断至 40 → 入选 5 篇 |
| 渲染 | weasyprint 70.0，无降级，字体内嵌 |
| 耗时 / 页数 | 77.9 秒 / 11 页 |

这一轮真实运行暴露并修复了四个只在真实 API 上才会出现的问题：输出被思维 token 挤爆而截断、截断未被识别导致盲目重试、引文超长被 schema 拒收、以及模型把卡片里的分析结构压平成字符串。四处均已修复并有回归测试。
