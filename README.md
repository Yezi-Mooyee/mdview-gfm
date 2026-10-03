# mdview

用 **GitHub 官方渲染器**（cmark-gfm）看 Markdown 的轻量查看器，两种形态：

- **GUI** —— 原生窗口，不跳浏览器，工具栏点选文件，带最近文件、明暗主题、缩放。
- **CLI** —— 一条命令把 Markdown 渲染成 HTML 丢给默认浏览器。

渲染器是 [github/cmark-gfm](https://github.com/github/cmark-gfm)（GFM 规范的行为定义就是它），
样式表是 GitHub 官方的 [github-markdown-css](https://github.com/sindresorhus/github-markdown-css)。
不是「仿 GitHub 风格」，而是同一套解析器加同一套 CSS。

![浅色主题](screenshot-light.png)

![深色主题](screenshot-dark.png)

## 快速开始

双击 `mdview-gui.cmd` 启动窗口，也可以先把文件拖到它上面。

命令行：

```powershell
.\mdview-gui.cmd                  # 空窗口，用工具栏选文件
.\mdview-gui.cmd README.md        # 直接打开某个文件
```

## 界面

| 控件 | 作用 | 快捷键 |
|:-----|:-----|:-------|
| 打开… | 弹出文件选择框 | `Ctrl+O` |
| 刷新 | 重新读取当前文件（文件被外部改过时用） | `F5` |
| 浏览器 | 用系统默认浏览器打开同一份渲染结果 | `Ctrl+B` |
| − / + | 缩放 70%–200% | `Ctrl+-` / `Ctrl+=` |
| 自动 / 浅色 / 深色 | 主题，自动即跟随系统 | — |

主题、缩放、最近文件都记在 `%APPDATA%\mdview\config.json`，下次启动自动恢复。
窗口会读取并兼容带 BOM 的配置文件（记事本保存的那种）。

## 高分辨率屏

这块是专门处理过的：

- **DPI 感知在建窗口之前就声明**（`SetProcessDpiAwareness(2)`）。否则进程会被系统当成
  DPI 不敏感的，WebView 内容被整块位图拉伸，200% 缩放下文字必然发糊。
- **窗口尺寸按屏幕的逻辑分辨率算**，交给 pywebview 换算物理像素。在 2736×1824 @200%
  的机器上（逻辑 1368×912），窗口会开到约 1180×820 逻辑像素，占满屏幕的大部分而不越界。
- 页面内容走 CSS 像素，由 WebView2 按设备像素比自行清晰渲染。

## 文件说明

| 文件 | 说明 |
|:-----|:-----|
| `mdview_gui.py` | GUI 主体（原生窗口 + 工具栏） |
| `mdview-gui.cmd` | GUI 入口，用 `pythonw` 启动所以没有黑框 |
| `mdview.py` | CLI 入口 |
| `mdview.cmd` | CLI 入口的 cmd 包装 |
| `mdview_core.py` | 渲染核心，GUI 与 CLI 共用，保证两边输出一致 |
| `github-markdown.css` | GitHub 官方样式表，跟随系统自动明暗 |
| `github-markdown-light.css` | 强制浅色，供主题按钮使用 |
| `github-markdown-dark.css` | 强制深色，供主题按钮使用 |
| `test.md` | GFM 特性自测样例 |

## CLI 用法

```powershell
.\mdview.cmd README.md                 # 渲染并打开浏览器
python mdview.py README.md --no-open   # 只生成 HTML，打印路径
type notes.md | python mdview.py -     # 从标准输入读取
```

| 选项 | 作用 |
|:-----|:-----|
| `--out 路径` | 指定生成的 HTML 位置（默认在 `%TEMP%\mdview\`） |
| `--no-open` | 只生成，不打开浏览器 |
| `--safe` | 退回 cmark-gfm 的严格过滤模式，原生 HTML 全部转义 |
| `--css 路径` | 换一套样式表 |
| `--title 文本` | 指定页面标题 |

## 与 GitHub 网页的差异

渲染器一致，但 GitHub 网页还会在渲染之后做几件后处理，这里没有做：

- **代码高亮**：GitHub 在服务端用 Highlight.js 上色，这里只有 `<pre lang="...">` 结构，不着色。
- **Emoji 短代码**：GitHub 会把 `:smile:` 转成 😄，这里原样显示。
- **标题锚点**：GitHub 给每个标题加 `id` 以便锚点跳转，这里没有。

## 环境要求

- Python 3.8+（本机为 `T:\apps\Python312\python.exe`）
- `pycmarkgfm` —— cmark-gfm 的 Python 绑定
- `pywebview` —— 仅 GUI 需要，Windows 上走 Edge WebView2，Win10/11 自带运行时
- 相对路径的图片靠页面里的 `<base>` 标签解析，同目录图片能正常显示
