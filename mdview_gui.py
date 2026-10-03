#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mdview GUI —— 原生窗口的 Markdown 查看器，渲染器是 GitHub 官方的 cmark-gfm。

Windows 上由 Edge WebView2 承载页面，所以既不用打开浏览器，也不用额外装运行库
（Win10/11 自带）。高分屏下靠两件事保证文字锐利：

1. 在创建任何窗口之前声明 per-monitor DPI 感知，否则整个 WebView 会被当成位图
   拉伸，200% 缩放下必然发糊；
2. 窗口尺寸按屏幕的「逻辑分辨率」计算，pywebview 会自己换算成物理像素。

用法：
    python mdview_gui.py            # 空窗口，可从工具栏选文件
    python mdview_gui.py README.md  # 直接打开某个文件
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import json
import os
import sys
import tempfile
import webbrowser
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname


def _set_dpi_awareness() -> None:
    """必须在任何窗口创建之前调用，否则高分屏下窗口内容会被位图放大而变糊。"""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
        return
    except Exception:  # noqa: BLE001  老系统没有 shcore
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:  # noqa: BLE001
        pass


_set_dpi_awareness()

import webview  # noqa: E402  必须晚于 DPI 设置

import mdview_core as core  # noqa: E402

APP_DIR = Path(__file__).resolve().parent
# 链接指向这些后缀的本地文件时，直接在窗口里切换，而不是丢给外部程序
MARKDOWN_SUFFIXES = {".md", ".markdown", ".mdown", ".mkd", ".mdx", ".txt"}
DATA_DIR = Path(os.environ.get("APPDATA") or Path.home()) / "mdview"
CONFIG_PATH = DATA_DIR / "config.json"
SHELL_PATH = Path(tempfile.gettempdir()) / "mdview" / "gui.html"

SPI_GETWORKAREA = 0x0030
DEFAULT_CONFIG: dict = {"theme": "auto", "zoom": 100, "recent": [], "last_dir": ""}

# 窗口引用放在模块级：pywebview 会遍历 js_api 对象的公开属性，把它挂在
# Api 实例上会让它顺着 window.native 一路递归下去，报一堆无关错误。
_ACTIVE_WINDOW: webview.Window | None = None

# pywebview 6 起 OPEN_DIALOG 改名为 FileDialog.OPEN，这里兼容新旧版本
_FILE_DIALOG_OPEN = getattr(getattr(webview, "FileDialog", None), "OPEN", None)
if _FILE_DIALOG_OPEN is None:
    _FILE_DIALOG_OPEN = webview.OPEN_DIALOG


# --------------------------------------------------------------------------- #
# 屏幕与配置
# --------------------------------------------------------------------------- #
def work_area() -> tuple[int, int]:
    """返回工作区（排除任务栏）的逻辑尺寸。"""
    rect = ctypes.wintypes.RECT()
    try:
        ctypes.windll.user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(rect), 0)
        width, height = rect.right - rect.left, rect.bottom - rect.top
    except Exception:  # noqa: BLE001
        width, height = 0, 0
    if width <= 0 or height <= 0:
        width = ctypes.windll.user32.GetSystemMetrics(0)
        height = ctypes.windll.user32.GetSystemMetrics(1)
    try:
        scale = (ctypes.windll.user32.GetDpiForSystem() or 96) / 96.0
    except Exception:  # noqa: BLE001
        scale = 1.0
    return int(width / scale), int(height / scale)


def default_window_size() -> tuple[int, int]:
    """按屏幕逻辑分辨率算出合适的窗口大小（pywebview 收的是逻辑像素）。"""
    logical_w, logical_h = work_area()
    width = int(min(1180, max(760, logical_w * 0.94)))
    height = int(min(920, max(520, logical_h * 0.94)))
    return width, height


def system_is_dark() -> bool:
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            return winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 0
    except Exception:  # noqa: BLE001
        return False


def load_config() -> dict:
    config = dict(DEFAULT_CONFIG)
    try:
        # utf-8-sig：记事本之类的编辑器会加 BOM，用普通 utf-8 解出来是坏 JSON，
        # 结果配置被当成损坏而重置，所以这里必须容忍 BOM。
        stored = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
        if isinstance(stored, dict):
            config.update({k: v for k, v in stored.items() if k in DEFAULT_CONFIG})
    except (OSError, ValueError):
        pass
    if config["theme"] not in ("auto", "light", "dark"):
        config["theme"] = "auto"
    try:
        config["zoom"] = max(70, min(200, int(config["zoom"])))
    except (TypeError, ValueError):
        config["zoom"] = 100
    if not isinstance(config["recent"], list):
        config["recent"] = []
    return config


def save_config(config: dict) -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# 页面骨架：工具栏常驻，文档区靠 JS 替换，换文件时不重载页面
# --------------------------------------------------------------------------- #
SHELL = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="color-scheme" content="light dark">
<title>mdview</title>
<base id="base" href="__BASE__">
<link id="css-auto" rel="stylesheet" href="__CSS_AUTO__">
<link id="css-light" rel="stylesheet" href="__CSS_LIGHT__" disabled>
<link id="css-dark" rel="stylesheet" href="__CSS_DARK__" disabled>
<style>
  :root {
    --page-bg: #ffffff; --bar-bg: #f6f8fa; --bar-fg: #1f2328;
    --bar-border: #d1d9e0; --btn-bg: #ffffff; --btn-border: #d1d9e0;
    --muted: #59636e; --accent: #0969da; --accent-fg: #ffffff;
    --content-width: 1012px; --zoom: 1;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --page-bg: #0d1117; --bar-bg: #151b23; --bar-fg: #f0f6fc;
      --bar-border: #3d444d; --btn-bg: #212830; --btn-border: #3d444d;
      --muted: #9198a1; --accent: #4493f8; --accent-fg: #ffffff;
    }
  }
  /* 手动指定主题时覆盖系统偏好，所以必须排在上面两段之后 */
  html[data-forced-theme="light"] {
    --page-bg: #ffffff; --bar-bg: #f6f8fa; --bar-fg: #1f2328;
    --bar-border: #d1d9e0; --btn-bg: #ffffff; --btn-border: #d1d9e0;
    --muted: #59636e; --accent: #0969da;
  }
  html[data-forced-theme="dark"] {
    --page-bg: #0d1117; --bar-bg: #151b23; --bar-fg: #f0f6fc;
    --bar-border: #3d444d; --btn-bg: #212830; --btn-border: #3d444d;
    --muted: #9198a1; --accent: #4493f8;
  }

  html, body { height: 100%; margin: 0; }
  body {
    display: flex; flex-direction: column;
    background: var(--page-bg); color: var(--bar-fg);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif;
    overflow: hidden;
  }
  #bar {
    flex: 0 0 auto; display: flex; align-items: center; gap: 6px;
    padding: 8px 12px; background: var(--bar-bg);
    border-bottom: 1px solid var(--bar-border);
    user-select: none;
  }
  #bar .group { display: flex; align-items: center; gap: 4px; }
  #bar .spacer { flex: 1 1 auto; min-width: 8px; }
  button {
    font: inherit; font-size: 14px; line-height: 1;
    min-height: 32px; padding: 0 12px;
    color: var(--bar-fg); background: var(--btn-bg);
    border: 1px solid var(--btn-border); border-radius: 6px;
    cursor: pointer; white-space: nowrap;
  }
  button:hover:not(:disabled) { border-color: var(--accent); color: var(--accent); }
  button:disabled { opacity: .5; cursor: default; }
  button.active { background: var(--accent); border-color: var(--accent); color: var(--accent-fg); }
  button.icon { min-width: 36px; padding: 0 8px; }
  body:not(.ready) #bar button { opacity: .5; pointer-events: none; }

  #filebox {
    flex: 1 1 auto; min-width: 0; display: flex; align-items: baseline; gap: 8px;
    padding: 0 8px; font-size: 13px;
  }
  #file-name { font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  #file-dir {
    color: var(--muted); direction: rtl; text-align: left;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis; flex: 0 1 auto;
  }
  #zoom-label { font-size: 13px; color: var(--muted); min-width: 42px; text-align: center; }

  #scroll { flex: 1 1 auto; overflow: auto; }
  #doc {
    box-sizing: border-box;
    zoom: var(--zoom);
    /* 除以 zoom，放大后视觉宽度保持不变，手感接近浏览器缩放 */
    max-width: calc(var(--content-width) / var(--zoom));
    margin: 0 auto; padding: 40px 45px 90px;
  }
  @media (max-width: 767px) { #doc { padding: 16px 16px 60px; } }
  @media (min-width: 1500px) { :root { --content-width: 1120px; } }
  @media (min-width: 1900px) { :root { --content-width: 1240px; } }

  .welcome { padding-top: 4vh; }
  .welcome h1 { border: 0; }
  #recent-list { list-style: none; padding: 0; }
  #recent-list li { padding: 5px 0; border-bottom: 1px solid var(--bar-border); }
  #recent-list a { color: var(--accent); text-decoration: none; word-break: break-all; }
  #recent-list a:hover { text-decoration: underline; }
</style>
</head>
<body>
<header id="bar">
  <div class="group">
    <button onclick="mdview.open()" title="Ctrl+O">打开…</button>
    <button class="icon" onclick="mdview.reload()" title="重新读取文件 (F5)">刷新</button>
    <button onclick="mdview.browser()" title="在系统默认浏览器里打开 (Ctrl+B)">浏览器</button>
  </div>
  <div class="spacer"></div>
  <div id="filebox">
    <span id="file-name">未打开文件</span>
    <span id="file-dir"></span>
  </div>
  <div class="group">
    <button class="icon" onclick="mdview.zoom(-10)" title="缩小 (Ctrl+-)">−</button>
    <span id="zoom-label">100%</span>
    <button class="icon" onclick="mdview.zoom(10)" title="放大 (Ctrl+=)">+</button>
  </div>
  <div class="group">
    <button data-theme-btn="auto" onclick="mdview.theme('auto')" title="跟随系统">自动</button>
    <button data-theme-btn="light" onclick="mdview.theme('light')">浅色</button>
    <button data-theme-btn="dark" onclick="mdview.theme('dark')">深色</button>
  </div>
</header>

<main id="scroll">
  <article class="markdown-body" id="doc">
    <div class="welcome">
      <h1>mdview</h1>
      <p>用 GitHub 官方渲染器 <b>cmark-gfm</b> 预览 Markdown。点下面的按钮选文件，或直接按 <b>Ctrl+O</b>。</p>
      <p><button onclick="mdview.open()">打开 Markdown 文件…</button></p>
      <div id="recent-box" hidden>
        <h2>最近打开</h2>
        <ul id="recent-list"></ul>
        <p><button onclick="mdview.clearRecent()">清空记录</button></p>
      </div>
    </div>
  </article>
</main>

<script>
"use strict";
const $ = (id) => document.getElementById(id);

window.mdview = (function () {
  const state = { theme: "auto", zoom: 100 };

  function applyTheme(mode) {
    state.theme = mode;
    const root = document.documentElement;
    if (mode === "auto") { root.removeAttribute("data-forced-theme"); }
    else { root.setAttribute("data-forced-theme", mode); }
    $("css-auto").disabled = mode !== "auto";
    $("css-light").disabled = mode !== "light";
    $("css-dark").disabled = mode !== "dark";
    document.querySelectorAll("[data-theme-btn]").forEach((b) => {
      b.classList.toggle("active", b.dataset.themeBtn === mode);
    });
  }

  function applyZoom(percent) {
    state.zoom = Math.max(70, Math.min(200, percent));
    document.documentElement.style.setProperty("--zoom", state.zoom / 100);
    $("zoom-label").textContent = state.zoom + "%";
  }

  function setDocument(d) {
    if (d.base) { $("base").href = d.base; }
    $("doc").innerHTML = d.html;
    $("file-name").textContent = d.title || "未打开文件";
    $("file-dir").textContent = d.dir || "";
    $("file-dir").title = d.dir || "";
    document.title = (d.title || "mdview") + " — mdview";
    $("scroll").scrollTop = 0;
  }

  function setRecent(items) {
    const box = $("recent-box"), list = $("recent-list");
    list.textContent = "";
    if (!items || !items.length) { box.hidden = true; return; }
    box.hidden = false;
    items.forEach((path) => {
      const li = document.createElement("li");
      const a = document.createElement("a");
      a.href = "#"; a.textContent = path; a.title = path;
      a.onclick = (e) => { e.preventDefault(); mdview.openRecent(path); };
      li.appendChild(a);
      list.appendChild(li);
    });
  }

  function api() { return (window.pywebview && window.pywebview.api) || null; }

  function bind() {
    document.body.classList.add("ready");
    const bridge = api();
    if (!bridge) { return; }
    bridge.ready().then((cfg) => {
      applyTheme(cfg.theme);
      applyZoom(cfg.zoom);
      setRecent(cfg.recent);
      return bridge.initial_document();
    }).then((doc) => { if (doc) { setDocument(doc); } })
      .catch((err) => { console.error(err); });
  }

  if (window.pywebview) { bind(); }
  else { window.addEventListener("pywebviewready", bind); }

  window.addEventListener("keydown", (e) => {
    if (e.ctrlKey && !e.shiftKey && e.key.toLowerCase() === "o") { e.preventDefault(); mdview.open(); }
    else if (e.key === "F5") { e.preventDefault(); mdview.reload(); }
    else if (e.ctrlKey && e.key.toLowerCase() === "b") { e.preventDefault(); mdview.browser(); }
    else if (e.ctrlKey && (e.key === "=" || e.key === "+")) { e.preventDefault(); mdview.zoom(10); }
    else if (e.ctrlKey && e.key === "-") { e.preventDefault(); mdview.zoom(-10); }
    else if (e.ctrlKey && e.key === "0") { e.preventDefault(); mdview.resetZoom(); }
  });

  // 页面里的链接一律不在 WebView 内导航，否则目标页面会把整个 GUI 顶掉且退不回来。
  // 页内锚点（#fn-1 这类）除外，那是文档内部的跳转。
  function interceptLink(e) {
    const link = e.target && e.target.closest ? e.target.closest("a[href]") : null;
    if (!link) { return; }
    const href = link.getAttribute("href") || "";
    if (href.startsWith("#")) { return; }
    e.preventDefault();
    const bridge = api();
    if (bridge) { bridge.open_link(link.href); }
  }
  document.addEventListener("click", interceptLink);
  document.addEventListener("auxclick", interceptLink);

  return {
    state,
    open() { const b = api(); if (b) { b.open_file(); } },
    reload() { const b = api(); if (b) { b.reload(); } },
    browser() { const b = api(); if (b) { b.open_in_browser(); } },
    openRecent(p) { const b = api(); if (b) { b.open_path(p); } },
    clearRecent() { const b = api(); if (b) { b.clear_recent(); } },
    zoom(delta) { applyZoom(state.zoom + delta); const b = api(); if (b) { b.set_zoom(state.zoom); } },
    resetZoom() { applyZoom(100); const b = api(); if (b) { b.set_zoom(100); } },
    theme(mode) { applyTheme(mode); const b = api(); if (b) { b.set_theme(mode); } },
    setDocument, setRecent, applyTheme, applyZoom,
  };
})();
</script>
</body>
</html>
"""


def build_shell(base_uri: str) -> str:
    replacements = {
        "__BASE__": base_uri,
        "__CSS_AUTO__": (APP_DIR / "github-markdown.css").as_uri(),
        "__CSS_LIGHT__": (APP_DIR / "github-markdown-light.css").as_uri(),
        "__CSS_DARK__": (APP_DIR / "github-markdown-dark.css").as_uri(),
    }
    shell = SHELL
    for key, value in replacements.items():
        shell = shell.replace(key, value)
    return shell


# --------------------------------------------------------------------------- #
# 前端的 Python 侧：工具栏上的每个动作都落到这里
# --------------------------------------------------------------------------- #
class Api:
    """暴露给页面 JS 的接口。

    注意：所有实例属性都用下划线开头。pywebview 会把 js_api 对象的公开属性
    也一并处理，一旦持有窗口之类的对象引用，它就会顺着 native 树递归下去。
    """

    def __init__(self, initial: Path | None = None):
        self._config = load_config()
        self._initial = initial
        self._current: Path | None = None

    # -- 内部工具 ---------------------------------------------------------- #
    def _push(self, script: str) -> None:
        if _ACTIVE_WINDOW is not None:
            _ACTIVE_WINDOW.evaluate_js(script)

    def _set_title(self, path: Path) -> None:
        if _ACTIVE_WINDOW is not None:
            _ACTIVE_WINDOW.title = f"{path.name} — mdview"

    def _remember(self, path: Path) -> None:
        entry = str(path.resolve())  # 存绝对路径，否则最近列表会随工作目录失效
        recent = [p for p in self._config["recent"] if p != entry]
        recent.insert(0, entry)
        self._config["recent"] = recent[:12]
        self._config["last_dir"] = str(path.parent)
        save_config(self._config)
        self._push(f"window.mdview.setRecent({core.to_js(self._config['recent'])});")

    def _document(self, path: Path) -> dict:
        return {
            "html": core.render(core.read_markdown(path)),
            "base": core.dir_uri(path.parent),
            "title": path.name,
            "dir": str(path.parent),
        }

    # -- 加载 -------------------------------------------------------------- #
    def load_path(self, path: Path) -> bool:
        path = Path(path).expanduser().resolve()
        if not path.is_file():
            print(f"找不到文件：{path}", file=sys.stderr)
            return False
        try:
            document = self._document(path)
        except OSError as exc:
            print(f"读取失败：{exc}", file=sys.stderr)
            return False

        self._push(f"window.mdview.setDocument({core.to_js(document)});")
        self._current = path
        self._set_title(path)
        self._remember(path)
        return True

    # -- 供前端调用的接口 --------------------------------------------------- #
    def ready(self) -> dict:
        return {
            "theme": self._config["theme"],
            "zoom": self._config["zoom"],
            "recent": self._config["recent"],
        }

    def initial_document(self) -> dict | None:
        if self._initial is None or not self._initial.is_file():
            return None
        try:
            document = self._document(self._initial)
        except OSError:
            return None
        self._current = self._initial
        self._set_title(self._initial)
        self._remember(self._initial)
        return document

    def open_file(self) -> None:
        if _ACTIVE_WINDOW is None:
            return
        start_dir = self._config.get("last_dir") or str(Path.home())
        result = _ACTIVE_WINDOW.create_file_dialog(
            _FILE_DIALOG_OPEN,
            allow_multiple=False,
            directory=start_dir if os.path.isdir(start_dir) else str(Path.home()),
            file_types=(
                "Markdown 文件 (*.md;*.markdown;*.mdown;*.mkd;*.mdx)",
                "文本文件 (*.txt)",
                "所有文件 (*.*)",
            ),
        )
        if result:
            self.load_path(Path(result[0]))

    def open_path(self, path: str) -> None:
        self.load_path(Path(path))

    def open_link(self, url: str) -> None:
        """把文档里的链接交给系统处理，绝不让 WebView 自己导航过去。

        本地 Markdown 直接在本窗口里切换，其余文件交给系统默认程序，网页交给浏览器。
        """
        parsed = urlparse(url)
        if parsed.scheme == "file":
            target = Path(url2pathname(parsed.path))
            if target.suffix.lower() in MARKDOWN_SUFFIXES and target.is_file():
                self.load_path(target)
            elif target.exists():
                try:
                    os.startfile(target)  # noqa: S606  交给系统默认程序
                except OSError as exc:
                    print(f"打开文件失败：{exc}", file=sys.stderr)
            else:
                print(f"链接指向的文件不存在：{target}", file=sys.stderr)
            return
        if parsed.scheme in ("http", "https", "mailto"):
            webbrowser.open(url)
            return
        print(f"未处理的链接协议：{url}", file=sys.stderr)

    def reload(self) -> None:
        if self._current is not None:
            self.load_path(self._current)

    def open_in_browser(self) -> None:
        if self._current is None:
            return
        import mdview as cli  # 复用 CLI 的整页模板

        try:
            source = core.read_markdown(self._current)
        except OSError:
            return
        html = cli.build_page(
            source,
            self._current.stem,
            core.dir_uri(self._current.parent),
            (APP_DIR / "github-markdown.css").as_uri(),
        )
        out = Path(tempfile.gettempdir()) / "mdview" / "browser-view.html"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(html, encoding="utf-8")
        webbrowser.open(out.resolve().as_uri())

    def set_theme(self, mode: str) -> None:
        if mode in ("auto", "light", "dark"):
            self._config["theme"] = mode
            save_config(self._config)

    def set_zoom(self, percent: int) -> None:
        try:
            self._config["zoom"] = max(70, min(200, int(percent)))
        except (TypeError, ValueError):
            return
        save_config(self._config)

    def clear_recent(self) -> None:
        self._config["recent"] = []
        save_config(self._config)
        self._push("window.mdview.setRecent([]);")


def main() -> int:
    global _ACTIVE_WINDOW

    argv = [a for a in sys.argv[1:] if not a.startswith("-")]
    initial = Path(argv[0]).expanduser().resolve() if argv else None

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SHELL_PATH.parent.mkdir(parents=True, exist_ok=True)
    SHELL_PATH.write_text(build_shell(core.dir_uri(SHELL_PATH.parent)), encoding="utf-8")

    api = Api(initial)
    width, height = default_window_size()
    window = webview.create_window(
        "mdview",
        url=SHELL_PATH.as_uri(),
        width=width,
        height=height,
        min_size=(640, 420),
        background_color="#0d1117" if system_is_dark() else "#ffffff",
        js_api=api,
        text_select=True,
    )
    _ACTIVE_WINDOW = window
    webview.start(
        private_mode=False,
        storage_path=str(DATA_DIR / "webview"),
        debug=os.environ.get("MDVIEW_DEBUG") == "1",
    )
    _ACTIVE_WINDOW = None
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
