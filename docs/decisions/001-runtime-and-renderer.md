# 001 — 运行时与 PDF 渲染器决策

状态：**已决定**
日期：2026-09-17
关联：`AGENTS.md` §4 §8 §14、`TASK.md` Step 0、`docs/architecture.md` §4 S8

---

## 背景

`TASK.md` Step 0 要求在开始编码前确认三件事：Python 运行时、PDF 渲染器、CJK 字体。
探测到的本机（macOS arm64，2026-09-17）事实：

| 项 | 现状 |
| --- | --- |
| `python3` | 3.9.6 — 不满足 `AGENTS.md` §4 的 3.11+ |
| `uv` | 0.12.15 可用 |
| `pkg-config` | 不存在 |
| `brew pango` | 未安装 |
| Google Chrome | 已安装（`/Applications/Google Chrome.app`） |
| CJK 字体 | 无 PingFang.ttc、无 Noto CJK；有 `STHeiti Light/Medium` |
| git 身份 | 全局与仓库均未配置 |

---

## 决定 1：Python 运行时由 uv 管理

**决定**：使用 `uv python install 3.11` 安装的 CPython 3.11.16，不依赖系统 `python3`。

**理由**：系统解释器 3.9.6 低于项目要求，且不应污染系统环境。`AGENTS.md` §4 已规定用 uv 管理依赖与虚拟环境。

**后果**：

- 所有命令必须通过 `uv run ...` 执行，禁止直接调用 `python3`。
- `~/.local/bin` 不在 PATH 上（uv 已提示），因此不依赖 `python3.11` 直接可执行。

---

## 决定 2：WeasyPrint 为主渲染器（保持 `AGENTS.md` §8 不变）

**决定**：保持 **WeasyPrint 为主、headless Chrome 为降级**，不改变 `AGENTS.md` §8 的架构级约定。

**依赖安装**：`brew install pango` → pango 1.58.2（连带 cairo / harfbuzz / icu4c / fribidi / graphite2 / libthai 等）。

**关键发现**：仅安装 pango **不足以**让 WeasyPrint 工作。直接 import 会失败：

```
OSError: cannot load library 'libgobject-2.0-0': dlopen(libgobject-2.0-0, 0x0002): tried:
'libgobject-2.0-0' (no such file), ... '/usr/lib/libgobject-2.0-0' (no such file, not in dyld cache)
```

原因是 Homebrew 的库在 `/opt/homebrew/lib`，不在 dyld 搜索路径上。已验证两种进程内解法：

| 方案 | 结果 |
| --- | --- |
| ① 在 import weasyprint **之前** `os.environ.setdefault("DYLD_FALLBACK_LIBRARY_PATH", "/opt/homebrew/lib")` | ✅ 可用 |
| ② 用 `ctypes.CDLL(..., RTLD_GLOBAL)` 预加载 `libgobject-2.0.dylib` | ❌ 仍然失败 |

**实现约束（Step 12 必须遵守）**：`src/briefing/report/render_pdf.py` 在 `import weasyprint` **之前**完成 macOS 路径修复，且满足：

1. 只在 `sys.platform == "darwin"` 且 `/opt/homebrew/lib` 存在时设置；
2. 用 `setdefault`，不覆盖用户已显式设置的值；
3. 该逻辑放在模块顶部、任何 weasyprint 导入之前 —— 顺序错误会直接 ImportError。

**被否方案**：

| 方案 | 否掉原因 |
| --- | --- |
| Chrome headless 作主渲染器 | Chrome 不支持 CSS `@page` 的 `counter(page)`，而 `AGENTS.md` §8 要求页码；且会改变已定的架构级约定，收益不足 |
| 要求用户自行 `export DYLD_FALLBACK_LIBRARY_PATH` | 把环境配置负担转嫁给使用者，CLI 体验差；方案 ① 已能在进程内解决 |
| 不装 pango、直接只用 Chrome | 放弃页码与更完整的 paged-media 支持 |

**验证证据**（A4 + `@bottom-center { content: counter(page) }` + STHeiti）：

```
PAGES 1
TEXT '中文测试 CJK 123\ndiffusion models for weather forecasting\n1'
FONT /RWPFGO+STHeiti-Bold embedded= True
FONT /OLBGDB+STHeiti embedded= True
```

即：中文可正确提取、页码计数器生效、字体已内嵌（`FontFile2` 存在）。

---

## 决定 3：CJK 字体使用系统 STHeiti

**决定**：字体回退链以系统 `STHeiti` 为准，**不**安装 Noto Sans CJK。

**理由**：`STHeiti Light/Medium` 已随 macOS 提供，实测能正确渲染中文并内嵌（见上）。额外安装字体对当前目标机器没有收益。

**后果 / 边界**：

- `styles.css` 的 `font-family` 需写成含 `STHeiti` 的回退链，例如 `"Noto Sans CJK SC", "Source Han Sans SC", "STHeiti", "PingFang SC", sans-serif` —— 既兼容已装 Noto 的环境，也保证本机可用。
- `AGENTS.md` §8「Noto Sans CJK / Source Han 之一」的措辞需要放宽为「Noto Sans CJK / Source Han / 系统等价 CJK 字体」；若将来要求严格一致，再补装 Noto。
- **跨平台风险**：换到 Linux/容器时 STHeiti 不存在，需重新确认字体可用性。

---

## 决定 4：git 身份（repo-local）

**决定**：仅在仓库内配置 `user.name = Pr0v1dence`（来自 `id -F`）与 `user.email = zhoushy93@gmail.com`（用户提供），不改动全局 git 配置。

**背景**：本机全局与仓库原本都没有任何 git 身份，会导致 commit 失败；采用 repo-local 配置可避免为单个项目写入全局设置。

---

## 对 `AGENTS.md` §14 的影响

- 第 3 条（WeasyPrint 的 GTK 依赖是否可用）→ **已关闭**，结论见决定 2。
- 第 1、2、4 条仍待办（模型 id、Semantic Scholar key、页数与字数上限）。

---

## 复现命令

```bash
uv python install 3.11
brew install pango

# 渲染探针（install pango 之后、且仅在进程内设置 dyld 变量时通过）
DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib uv run --python 3.11 \
  --with weasyprint --with pypdf --no-project python -c "
from weasyprint import HTML; import pypdf
open('/tmp/cjk_probe.pdf','wb').write(HTML(string='<p>中文测试 CJK</p>').write_pdf())
r = pypdf.PdfReader('/tmp/cjk_probe.pdf'); print(len(r.pages), repr(r.pages[0].extract_text()))
"
```
