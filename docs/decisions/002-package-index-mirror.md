# 002 — Python 包索引使用国内镜像

状态：**已决定**
日期：2026-09-17
关联：`AGENTS.md` §4、`TASK.md` Step 1

---

## 背景

`TASK.md` Step 1 的检查要求 `uv sync --dev` 可执行。实测本机网络：

| 目标 | 结果 |
| --- | --- |
| `https://pypi.org/simple/respx/` | 连接超时（20s，`curl` 退出码 28） |
| `https://pypi.tuna.tsinghua.edu.cn/simple/respx/` | HTTP 200，3.2s |
| `https://mirrors.aliyun.com/pypi/simple/respx/` | HTTP 200，2.5s |

首次 `uv sync --dev` 在 3 次重试、44.9s 后失败：

```
error: Request failed after 3 retries in 44.9s
  cause: Failed to fetch: `https://pypi.org/simple/respx/`
  cause: operation timed out
```

## 决定

在 `pyproject.toml` 中把清华 TUNA 设为**项目默认索引**：

```toml
[[tool.uv.index]]
name = "tuna"
url = "https://pypi.tuna.tsinghua.edu.cn/simple"
default = true
```

## 理由

- 不设镜像则本机无法安装任何依赖，Step 1 无法通过。
- 放在 `pyproject.toml` 而不是让使用者每次 `export`，可保证 `TASK.md` 中的裸命令 `uv sync --dev` 直接可用。
- 选 TUNA 而非 aliyun：两者速度接近（3.2s vs 2.5s），TUNA 的 PyPI 同步完整度与 uv 兼容性更常被验证。

## 后果与边界

- `uv.lock` 中的包来源会记录镜像 URL，**可移植性下降**：在能直连 PyPI 的机器/CI 上应改用
  `uv sync --default-index https://pypi.org/simple`（或 `UV_DEFAULT_INDEX` 环境变量）。
- 镜像不保证与 PyPI 实时同步；若某版本刚发布而解析失败，先确认镜像是否已同步，再决定是否临时切回官方源。
- 该决策只影响依赖获取，不影响运行期行为，因此不需要改动 `AGENTS.md` 的架构级约定。

## 验证

```bash
uv sync --dev          # 应成功，且不再出现 pypi.org 超时
uv run pytest -q
```
