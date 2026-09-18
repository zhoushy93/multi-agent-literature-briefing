# 样例：人工确认过的真实运行产物

**这不是测试 fixtures，而是一次真实运行的输出**，由人（项目作者）通读并确认内容可用后才放进仓库（`AGENTS.md` §12）。

运行命令：

```bash
briefing run --topic "diffusion models for weather forecasting" --out <out> --no-cache
```

关键事实（取自同目录 `manifest.json`）：

| 项 | 值 |
| --- | --- |
| status | `ok` |
| 模型 / 思考 | `deepseek-v4-pro` / `disabled` |
| 模型调用 | 10（S1 1、S4 2、S5 5、S6 2），上限 40 |
| token | 34,962 prompt + 7,037 completion |
| 检索 | 107 篇候选 → 上限截断至 40 → 入选 5 篇 |
| 验证 | 通过；修订 1 次（首稿有 1 处 fatal 违规，修订后清除） |
| 渲染 | WeasyPrint 70.0，无降级，CJK 字体内嵌 |
| 耗时 / 页数 | 77.9 秒 / 11 页 |

人工确认的内容：5 篇论文都是真实存在的 arXiv 记录（WIND、PuYun-LDM、LaDCast 等），标题、作者、年份、链接与 arXiv 一致；每条 `evidence.quote` 都能在对应论文的摘要或元数据里原文找到；正文引用编号 `[1]`–`[5]` 与参考文献一一对应；结论语气没有超出摘要所支持的范围。

目录内容：

- `report.pdf` —— 最终交付物（11 页）
- `briefing.md` —— 可编辑版本，与 PDF 同源于同一个通过校验的 `Briefing`
- `briefing.json` —— 结构化简报
- `papers.json` —— 入选论文元数据与逐篇分析
- `manifest.json` —— 本次运行的审计记录

**注意**：本次运行没有 `_stages/` 目录（调试用的阶段检查点未复制进来）。

**版式说明**：这份样例由 `analyzer_v1` 生成，**早于** 2026-09-17 的报告改版——它没有「主题速览」章节，卡片里也没有 `relevance`（与主题的关系）一行。想要看当前版式，按 `README.md` 的离线演示命令跑一次即可（不需要密钥）。
