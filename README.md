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

双击 `mdview-gui.pyw` 启动窗口——`.pyw` 由 `pythonw` 执行，不会闪黑框；想从命令行启动就用 `mdview-gui.cmd`。

命令行：

```powershell
.\mdview-gui.cmd                  # 空窗口，用工具栏选文件
.\mdview-gui.cmd README.md        # 直接打开某个文件
```

## 界面

| 控件 | 作用 | 快捷键 |
|:-----|:-----|:-------|
| ← / → | 在访问过的文档之间后退 / 前进 | `Alt+←` / `Alt+→` |
| 主页 | 回欢迎页；按新的一页入栈，所以后退还能回到刚才那份文档 | `Alt+Home` |
| 打开… | 弹出文件选择框 | `Ctrl+O` |
| 刷新 | 重新读取当前文件（文件被外部改过时用） | `F5` |
| 浏览器 | 用系统默认浏览器打开同一份渲染结果 | `Ctrl+B` |
| − / + | 缩放 70%–200% | `Ctrl+-` / `Ctrl+=` |
| 自动 / 浅色 / 深色 | 主题，自动即跟随系统 | — |

主题、缩放、最近文件都记在 `%APPDATA%\mdview\config.json`，下次启动自动恢复。
窗口会读取并兼容带 BOM 的配置文件（记事本保存的那种）。

除此之外还有几处行为值得说明：

- **拖文件到窗口**：直接把 `.md` 拖进来即可打开。外部拖放其实被 WebView2 自己吃掉了，
  父窗体既收不到事件、也没法让它透传（关掉 `AllowExternalDrop` 只会变成「拒绝接收」，
  内容区光标变禁止样式），所以走的是 WebView2 官方的文件通道：页面把 File 对象交给宿主，
  宿主从中读出真实路径。加载动作放在后台线程——拖放事件本身跑在 UI 线程上，
  在那里同步等 JS 会把窗口和资源管理器一起卡死。
- **点文档里的链接**：只有鼠标左键会触发；中键和右键都不打开（右键照常弹出 WebView2 自己的
  上下文菜单）。链接一律不在窗口内导航，否则目标页面会把整个 GUI 顶掉且退不回来：指向本地
  Markdown 的链接直接在窗口里切换，网页交给系统浏览器，其余文件交给系统默认程序。
- **悬停链接**：底部状态栏显示目标地址，本地文件会显示成本地路径而不是 `file://` URL。
- **最近文件**：每次进主页都会重读配置、剔除已经不存在的路径，所以同时开着两个实例时，
  各自打开过的文件都会出现在对方的主页列表里；落盘时也会先合并磁盘上的现有条目。
- **历史栈**：上限 100 条，同一页不重复入栈，从中间岔开时会丢弃后面的分支。
- **主题切换**：官方那三份样式表是「硬编码浅色 / 硬编码深色 / 变量驱动自动」三套独立文件，
  没法合并成一份（试过：深色下表格会留在白底）。所以三份都挂在页面上，靠 `media` 属性
  切换——`media="not all"` 的样式表浏览器照样下载，切换时样式已在内存里，
  不会出现「旧的已失效、新的还在加载」的空窗，滚动和排版也就不会跳。

## 高分辨率屏

这块是专门处理过的：

- **DPI 感知在建窗口之前就声明**（`SetProcessDpiAwareness(2)`）。否则进程会被系统当成
  DPI 不敏感的，WebView 内容被整块位图拉伸，200% 缩放下文字必然发糊。
- **窗口尺寸按屏幕的逻辑分辨率算**，交给 pywebview 换算物理像素。在 2736×1824 @200%
  的机器上（逻辑 1368×912），窗口会开到约 1180×820 逻辑像素，占满屏幕的大部分而不越界。
- 页面内容走 CSS 像素，由 WebView2 按设备像素比自行清晰渲染。

## 启动耗时

窗口弹出后内容还要等一会儿，这是 Edge WebView2 运行时初始化的固有开销，与文档大小无关：
实测极简页面（只有一行 `<h1>`）反而更慢。本工具自身只占约 1.3 秒（声明 DPI + 加载
pythonnet/CLR + 建窗口），其余 6–20 秒都花在 WebView2 上，并且随系统负载大幅波动。
试过给它传 `--disable-features=msSmartScreenProtection` 之类的启动参数，没有效果，故未采用。

## 文件说明

| 文件 | 说明 |
|:-----|:-----|
| `mdview_gui.py` | GUI 主体（原生窗口 + 工具栏） |
| `mdview-gui.pyw` | GUI 双击入口，`.pyw` 不创建控制台所以不闪窗 |
| `mdview-gui.cmd` | 同一入口的命令行版本（会先起一个 cmd 宿主窗口） |
| `mdview.py` | CLI 入口 |
| `mdview.cmd` | CLI 入口的 cmd 包装 |
| `mdview_core.py` | 渲染核心，GUI 与 CLI 共用，保证两边输出一致 |
| `github-markdown.css` | GitHub 官方样式表，变量驱动，随系统自动明暗 |
| `github-markdown-light.css` | 强制浅色，配色是**硬编码**的（不是同一套规则换变量） |
| `github-markdown-dark.css` | 强制深色，同样是硬编码配色 |
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
