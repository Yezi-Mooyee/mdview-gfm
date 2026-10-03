# mdview

[English](README.md) · **简体中文**

一个用 **GitHub 官方渲染器**的轻量 Markdown 查看器。

- **GUI** —— 原生窗口，不是浏览器标签页。带工具栏、最近文件、明暗主题、缩放、拖放打开。
- **CLI** —— 一条命令把 Markdown 渲染成 HTML 丢给浏览器。

渲染用的是 [github/cmark-gfm](https://github.com/github/cmark-gfm)——GFM 规范的行为定义就是它；样式表是 GitHub 官方的 [github-markdown-css](https://github.com/sindresorhus/github-markdown-css)。这不是「仿 GitHub 风格」，而是同一套解析器加同一套 CSS。

![浅色主题](screenshot-light.png)

![深色主题](screenshot-dark.png)

## 为什么要做它

市面上其他标榜「GitHub 风格」的 Markdown 查看器（MarkText、Obsidian、Typora、各种浏览器扩展……）实际都用 markdown-it、marked 或 goldmark 渲染，只是抄了样子。这个用的是真家伙，所以表格、任务列表、脚注、删除线、自动链接、原生 HTML 的表现和 GitHub 上完全一致。

## 亮点

- **真的很小。** Python 源码四十多 KB，两个运行依赖，无需构建，没有框架。
- **日常使用很流畅。** 滚动长文档、切换文件、改主题、缩放都是瞬时的——在一台低功耗高分辨率平板级设备上实测如此。唯一慢的是窗口出现那一下（见[启动耗时](#启动耗时)），那是 WebView2 运行时的开销，不是本项目的。
- **绿色软件。** 没有安装程序，不写注册表，不动 `PATH`。文件夹放哪都能跑，删掉文件夹就是卸载。数据位置见[运行数据放在哪](#运行数据放在哪)。
- **高分辨率屏专门处理过。** DPI 感知在建窗口之前就声明，200% 缩放下文字是清晰的，而不是被位图拉伸。
- **双语文档，英文为主文件。** 本篇是中文版，[英文版在这里](README.md)。

## 快速开始

### 1. 环境要求

| | |
|:--|:--|
| 系统 | Windows 10 / 11（GUI 基于 Edge WebView2） |
| Python | 3.8 或更高 |

### 2. 安装两个依赖

```powershell
python -m pip install pycmarkgfm pywebview
```

`pycmarkgfm`（142 KB 的 wheel）GUI 和 CLI 都要用；`pywebview` 只有 GUI 需要——如果你只要 CLI，装 `pycmarkgfm` 就够了。

只要把那几个 `.py` / `.pyw` / `.cmd` 文件和三个 `github-markdown-*.css` 放在一起就行，不需要构建。

### 3. 启动 GUI

双击 **`mdview-gui.pyw`**。`.pyw` 后缀意味着 Windows 用 `pythonw` 运行它，不会闪控制台黑框。

也可以从终端启动：

```powershell
.\mdview-gui.cmd                  # 空窗口，从工具栏选文件
.\mdview-gui.cmd README.md        # 直接打开某个文件
```

把 `.md` 文件拖到 `mdview-gui.pyw` 上，或者拖到已经打开的窗口里，都能打开。

### 4. 启动 CLI

```powershell
.\mdview.cmd README.md                 # 渲染并用默认浏览器打开
python mdview.py README.md --no-open   # 只生成 HTML 并打印路径
type notes.md | python mdview.py -     # 从标准输入读取
```

| 选项 | 作用 |
|:-----|:-----|
| `--out 路径` | 指定生成的 HTML 位置（默认 `%TEMP%\mdview\`） |
| `--no-open` | 只生成，不打开浏览器 |
| `--safe` | 退回 cmark-gfm 的严格过滤模式（原生 HTML 全部转义） |
| `--css 路径` | 换一套样式表 |
| `--title 文本` | 指定页面标题 |

## 运行数据放在哪

没有安装步骤，也不会往项目文件夹里藏东西。程序写出的所有内容都在项目文件夹之外的三个位置：

| 路径 | 内容 |
|:-----|:-----|
| `%APPDATA%\mdview\config.json` | 主题、缩放、最近文件 |
| `%APPDATA%\mdview\webview\` | Edge WebView2 的配置缓存（仅 GUI） |
| `%TEMP%\mdview\` | CLI 和「用浏览器打开」生成的 HTML |

删掉这三处就回到全新状态；删掉项目文件夹就是卸载。配置文件按 `utf-8-sig` 读取，所以用记事本编辑后带 BOM 也不会有问题。

## 界面

| 控件 | 作用 | 快捷键 |
|:-----|:-----|:-------|
| ← / → | 在访问过的文档之间后退 / 前进 | `Alt+←` / `Alt+→` |
| 主页 | 回欢迎页。按**新的一页**入栈，所以「后退」还能回到刚才在读的文档 | `Alt+Home` |
| 打开… | 弹出文件选择框 | `Ctrl+O` |
| 刷新 | 重新读取当前文件（保持滚动位置） | `F5` |
| 浏览器 | 用系统默认浏览器打开同一份渲染结果 | `Ctrl+B` |
| − / + | 缩放 70%–200% | `Ctrl+-` / `Ctrl+=` |
| 自动 / 浅色 / 深色 | 主题，自动即跟随系统 | — |

几处值得说明的行为：

- **把文件拖到窗口上**即可打开。外部拖放其实被 WebView2 自己吃掉了、不会传给父窗口（关掉 `AllowExternalDrop` 只会让它变成「拒绝接收」，内容区光标变禁止样式），所以走的是 WebView2 官方的文件通道：页面把 `File` 对象交给宿主，宿主读出真实路径。加载动作放在后台线程——拖放回调跑在 UI 线程上，在那里等 JavaScript 会让窗口**和源资源管理器窗口**一起死锁。
- **只有鼠标左键会触发链接。** 中键和右键都不打开任何东西（右键照常弹出 WebView2 自己的上下文菜单）。链接一律不在窗口内导航——那会把整个 GUI 换成目标页面，而且退不回来。指向本地 Markdown 的链接就地切换文档，网页交给浏览器，其余文件交给系统默认程序。
- **悬停链接**时底部状态栏显示目标地址；本地文件显示成 Windows 路径而不是 `file://` URL。
- **最近文件**每次进主页都从磁盘重读，已经不存在的条目会被剔除。所以同时开两个实例时，彼此打开过的文件都会出现在对方列表里；落盘时也会先和磁盘上已有的条目合并，而不是覆盖掉。
- **历史栈**上限 100 条，同一页不连续重复入栈，从中间岔开时会丢弃后面的分支。
- **主题切换**用 `media` 属性而不是 `disabled` 在三份官方样式表之间切换。`media="not all"` 的样式表浏览器照样会下载，所以切换时新样式已经在内存里——不存在「旧的已失效、新的还在加载」的空窗，滚动和排版也就不会跳。

## 高分辨率屏

这块是专门处理过的，做错了就会整个糊掉：

- **DPI 感知在建任何窗口之前就声明**（`SetProcessDpiAwareness(2)`）。否则进程会被系统当成 DPI 不敏感的，整个 WebView 被位图拉伸，200% 缩放下肉眼可见地发虚。
- **窗口尺寸按屏幕的「逻辑分辨率」算**，交给 pywebview 换算成物理像素。在 2736×1824 @200% 的屏幕上（逻辑 1368×912），窗口开到约 1180×820 逻辑像素——占满大部分屏幕又不越界。
- 页面内容走 CSS 像素，由 WebView2 按实际设备像素比清晰渲染。

## 启动耗时

窗口先出现，内容随后跟上。这个间隔是 Edge WebView2 运行时初始化，和文档大小无关——实测只含一行 `<h1>` 的页面反而比完整文档更慢。本项目自身只占约 1.3 秒（声明 DPI、加载 pythonnet/CLR、建窗口），其余 6–20 秒都是 WebView2 的，而且随系统负载大幅波动。

试过给 WebView2 传 `--disable-features=msSmartScreenProtection` 之类的启动参数，没有可测量的改善，因此没有采用。

窗口出来之后，一切操作都是瞬时的。

## 关于 Edge WebView2

GUI 通过 **Microsoft Edge WebView2 Runtime** 渲染。它是系统组件，不是你必须使用的浏览器：

- Windows 11 自带，几乎所有保持更新的 Windows 10 机器上也有。
- 你**不必**使用 Edge 浏览器，也不必留着它。起作用的是 WebView2 **运行时**。
- 万一它缺失，从微软装 Evergreen Runtime 即可：<https://developer.microsoft.com/microsoft-edge/webview2/>

CLI 完全不需要 WebView2——它只是写一个 HTML 文件然后请浏览器打开。

## 与 github.com 的差异

渲染器一致，但 GitHub 还会在渲染之后做几件后处理，这里没有做：

- **语法高亮** —— GitHub 在服务端用 Highlight.js 上色；这里只有 `<pre lang="...">` 结构，没有颜色。
- **Emoji 短代码** —— GitHub 会把 `:smile:` 变成 😄；这里原样显示。
- **标题锚点** —— GitHub 给每个标题加 `id` 以便锚点跳转；这里没有。

## 文件说明

| 文件 | 作用 |
|:-----|:-----|
| `mdview_gui.py` | GUI 主体：原生窗口、工具栏、拖放、导航 |
| `mdview-gui.pyw` | GUI 双击入口（`.pyw` → 不创建控制台窗口） |
| `mdview-gui.cmd` | 同一入口的命令行版本（会先起一个 cmd 宿主窗口） |
| `mdview.py` | CLI 入口 |
| `mdview.cmd` | CLI 的 cmd 包装 |
| `mdview_core.py` | GUI 与 CLI 共用的渲染核心，保证两边产出一致 |
| `github-markdown.css` | GitHub 官方样式表——变量驱动，跟随系统主题 |
| `github-markdown-light.css` | 强制浅色——配色是**硬编码**的，不是同一套规则换变量 |
| `github-markdown-dark.css` | 强制深色——同样是硬编码配色 |
| `test.md` | 用于测试的 GFM 特性样例 |
| `THIRD-PARTY-NOTICES.md` | 再分发文件与运行依赖的许可证 |

## 由 AI 生成

**本项目 100% 由 AI 生成。** 每一行代码、每一条提交信息和本文档都由 **DeepSeek Harness** 驱动 **DeepSeek V4.1 Flash** 模型产出；人类维护者负责定方向和审阅结果。

因此提交都带有这些 trailer：

```
Co-Authored-By: dsh <dsh@users.noreply.github.com>
Generated-By: dsh (DeepSeek Harness)
```

## 许可证

MIT —— 见 [LICENSE](LICENSE)。

本仓库再分发了 [github-markdown-css](https://github.com/sindresorhus/github-markdown-css) 的三份样式表（MIT © Sindre Sorhus），其许可证原文收录在 [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md) 中，同时列出了**不随仓库分发**的运行依赖的许可证。
