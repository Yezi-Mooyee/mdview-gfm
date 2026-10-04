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
import threading
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
# 历史栈上限：长时间浏览也不至于让栈无限涨下去
MAX_HISTORY = 100

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
<!-- 官方给的是「硬编码浅色 / 硬编码深色 / 变量驱动自动」三套独立样式表，没法合并，
     只能整体切换。这里用 media 而不是 disabled：media 不匹配时浏览器照样会下载，
     切换时样式已经在内存里，不会出现「旧的已失效、新的还在加载」的空窗。 -->
<link id="css-auto" rel="stylesheet" href="__CSS_AUTO__">
<link id="css-light" rel="stylesheet" href="__CSS_LIGHT__" media="not all">
<link id="css-dark" rel="stylesheet" href="__CSS_DARK__" media="not all">
<style>
  :root {
    --page-bg: #ffffff; --bar-bg: #f6f8fa; --bar-fg: #1f2328;
    --bar-border: #d1d9e0; --btn-bg: #ffffff; --btn-border: #d1d9e0;
    --muted: #59636e; --accent: #0969da; --accent-fg: #ffffff;
    --err-bg: #fff0ee; --err-fg: #d1242f; --err-border: #ffc9c4;
    --content-width: 1012px; --zoom: 1;
  }
  /* 系统深色时生效，但「强制浅色」要能压过它 */
  @media (prefers-color-scheme: dark) {
    html:not([data-forced-theme="light"]) {
      --page-bg: #0d1117; --bar-bg: #151b23; --bar-fg: #f0f6fc;
      --bar-border: #3d444d; --btn-bg: #212830; --btn-border: #3d444d;
      --muted: #9198a1; --accent: #4493f8; --accent-fg: #ffffff;
      --err-bg: #2d1416; --err-fg: #ff7b72; --err-border: #6e2a2a;
    }
  }
  /* 手动指定主题时覆盖系统偏好，所以必须排在上面两段之后 */
  html[data-forced-theme="light"] {
    --page-bg: #ffffff; --bar-bg: #f6f8fa; --bar-fg: #1f2328;
    --bar-border: #d1d9e0; --btn-bg: #ffffff; --btn-border: #d1d9e0;
    --muted: #59636e; --accent: #0969da;
    --err-bg: #fff0ee; --err-fg: #d1242f; --err-border: #ffc9c4;
  }
  html[data-forced-theme="dark"] {
    --page-bg: #0d1117; --bar-bg: #151b23; --bar-fg: #f0f6fc;
    --bar-border: #3d444d; --btn-bg: #212830; --btn-border: #3d444d;
    --muted: #9198a1; --accent: #4493f8;
    --err-bg: #2d1416; --err-fg: #ff7b72; --err-border: #6e2a2a;
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
  button:hover:not(:disabled):not(.active) { border-color: var(--accent); color: var(--accent); }
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
  /* 打不开文件时的提示就长在文件名/路径这一格上：工具栏高度不变，正文区域的
     尺寸和位置也一点不动，不会顶得内容跳一下。边框和内边距常驻，悬停时只换颜色，
     所以框亮起来的时候文字一个像素都不挪。 */
  #file-error {
    flex: 1 1 auto; min-width: 0;
    display: flex; align-items: center; gap: 6px;
    padding: 1px 6px;
    border: 1px solid transparent; border-radius: 6px;
    color: var(--err-fg);
  }
  #file-error[hidden] { display: none; }
  #file-error:hover { border-color: var(--err-fg); background: var(--err-bg); }
  #file-error-text {
    flex: 0 1 auto; min-width: 0;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    user-select: text;
  }
  /* 定宽右对齐：数字从 3 走到 1 也推不动旁边的文字 */
  #file-countdown {
    flex: 0 0 auto; min-width: 18px;
    text-align: right; opacity: .7;
    font-variant-numeric: tabular-nums;
  }
  /* 鼠标停在提示上时计时是停住的，数字就没必要还挂在那儿占眼睛 */
  #file-error:hover #file-countdown { visibility: hidden; }
  #file-error-close {
    flex: 0 0 auto; min-height: 20px; min-width: 20px; padding: 0;
    font-size: 12px; line-height: 1;
    color: var(--err-fg); background: transparent; border-color: transparent;
  }
  #file-error-close:hover { color: var(--err-fg); border-color: var(--err-fg); }
  #zoom-label { font-size: 13px; color: var(--muted); min-width: 42px; text-align: center; }

  #scroll { flex: 1 1 auto; overflow: auto; }
  #statusbar {
    flex: 0 0 auto; height: 22px; padding: 0 12px;
    font-size: 12px; line-height: 22px;
    color: var(--muted); background: var(--bar-bg);
    border-top: 1px solid var(--bar-border);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    user-select: text;
  }
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
  /* 历史里的文件已经没了时占满正文区，明确表示“这页打不开”，而不是留着上一页的内容 */
  .load-error { padding-top: 4vh; }
  .load-error h1 { border: 0; }
  .load-error-path {
    padding: 8px 12px; border-radius: 6px;
    background: var(--err-bg); color: var(--err-fg);
    border: 1px solid var(--err-border);
    font-family: ui-monospace, SFMono-Regular, "Cascadia Mono", Consolas, monospace;
    font-size: 13px; word-break: break-all;
  }
  .load-error .load-error-hint { color: var(--muted); }
  #recent-list { list-style: none; padding: 0; }
  #recent-list li { padding: 5px 0; border-bottom: 1px solid var(--bar-border); }
  #recent-list a { color: var(--accent); text-decoration: none; word-break: break-all; }
  #recent-list a:hover { text-decoration: underline; }
  /* 已经不在磁盘上的条目照样留在列表里，但一眼要能看出它点不开 */
  #recent-list li.missing a { color: var(--muted); }
  .recent-tag {
    display: inline-block; margin-left: 8px; padding: 0 6px;
    font-size: 11px; line-height: 17px; vertical-align: 1px;
    color: var(--err-fg); background: var(--err-bg);
    border: 1px solid var(--err-border); border-radius: 999px;
    white-space: nowrap;
  }
</style>
</head>
<body>
<header id="bar">
  <div class="group">
    <button class="icon" id="btn-back" onclick="mdview.back()" title="后退 (Alt+←)" disabled>←</button>
    <button class="icon" id="btn-forward" onclick="mdview.forward()" title="前进 (Alt+→)" disabled>→</button>
    <button onclick="mdview.home()" title="回到主页 (Alt+Home)">主页</button>
  </div>
  <div class="group">
    <button onclick="mdview.open()" title="Ctrl+O">打开…</button>
    <button class="icon" onclick="mdview.reload()" title="重新读取文件 (F5)">刷新</button>
    <button onclick="mdview.browser()" title="在系统默认浏览器里打开 (Ctrl+B)">浏览器</button>
  </div>
  <div class="spacer"></div>
  <div id="filebox">
    <span id="file-name">未打开文件</span>
    <span id="file-dir"></span>
    <!-- 打不开文件时换成这一格显示：位置、高度都和文件名/路径一样，正文不会被挤动 -->
    <span id="file-error" hidden>
      <span id="file-error-text"></span>
      <span id="file-countdown"></span>
      <button id="file-error-close" type="button" onclick="mdview.dismissBanner()" title="关闭提示" aria-label="关闭提示">✕</button>
    </span>
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

<footer id="statusbar"><span id="status-text"></span></footer>

<script>
"use strict";
const $ = (id) => document.getElementById(id);

window.mdview = (function () {
  const state = { theme: "auto", zoom: 100, recent: [] };
  // 欢迎页的原始内容，回主页时照原样恢复
  const WELCOME_HTML = $("doc").innerHTML;

  function applyTheme(mode) {
    state.theme = mode;
    // 工具栏自己的配色靠 data-forced-theme 属性切换
    const root = document.documentElement;
    if (mode === "auto") { root.removeAttribute("data-forced-theme"); }
    else { root.setAttribute("data-forced-theme", mode); }
    // Markdown 正文换的是官方那三份样式表。用 media 而不是 disabled：media 不匹配时
    // 浏览器仍会下载，所以切换瞬间样式已在内存里，不会带着滚动和排版一起跳。
    $("css-auto").media = mode === "auto" ? "all" : "not all";
    $("css-light").media = mode === "light" ? "all" : "not all";
    $("css-dark").media = mode === "dark" ? "all" : "not all";
    document.querySelectorAll("[data-theme-btn]").forEach((b) => {
      b.classList.toggle("active", b.dataset.themeBtn === mode);
    });
  }

  function applyZoom(percent) {
    state.zoom = Math.max(70, Math.min(200, percent));
    document.documentElement.style.setProperty("--zoom", state.zoom / 100);
    $("zoom-label").textContent = state.zoom + "%";
  }

  function setDocument(d, keepScroll) {
    const scroller = $("scroll");
    hideBanner();
    // 先按比例记住当前位置，替换内容后再换算回去，这样刷新不会跳回顶部
    const span = scroller.scrollHeight - scroller.clientHeight;
    const ratio = span > 0 ? scroller.scrollTop / span : 0;
    if (d.base) { $("base").href = d.base; }
    $("doc").innerHTML = d.html;
    $("file-name").textContent = d.title || "未打开文件";
    $("file-dir").textContent = d.dir || "";
    $("file-dir").title = d.dir || "";
    document.title = (d.title || "mdview") + " — mdview";
    const newSpan = scroller.scrollHeight - scroller.clientHeight;
    scroller.scrollTop = keepScroll && newSpan > 0 ? ratio * newSpan : 0;
  }

  // 打不开文件时的提示，占用的正是文件名/路径那一格：工具栏高度不变，正文区域不会
  // 被顶得跳一下。autoClose 只在提示由用户当场操作触发时才为 true——启动时加载失败的
  // 提示要一直留着，否则用户只看到一个空窗口，说不清文件为什么没打开。
  const BANNER_TIMEOUT = 3000;
  let bannerTickTimer = 0, bannerAutoClose = false;

  function stopBannerTimers() {
    if (bannerTickTimer) { clearInterval(bannerTickTimer); bannerTickTimer = 0; }
  }

  function setCountdown(seconds) {
    const label = $("file-countdown");
    if (label) { label.textContent = seconds > 0 ? seconds + "s" : ""; }
  }

  // 数字只是给人看的，元素宽度定死，所以从 3 走到 1 也不会带动旁边的文字。
  // 关闭也交给这个表：另外再挂一个 setTimeout 会和它同一时刻到点，谁先跑还看调度，
  // 先跑的那个会把另一个清掉，于是提示就永远不关了。
  function armBannerTimers() {
    stopBannerTimers();
    let left = Math.round(BANNER_TIMEOUT / 1000);
    setCountdown(left);
    bannerTickTimer = setInterval(() => {
      left -= 1;
      if (left <= 0) { hideBanner(); return; }
      setCountdown(left);
    }, 1000);
  }

  function hideBanner() {
    stopBannerTimers();
    bannerAutoClose = false;
    setCountdown(0);
    const box = $("file-error");
    if (box) { box.hidden = true; box.removeAttribute("title"); }
    const name = $("file-name"), dir = $("file-dir");
    if (name) { name.hidden = false; }
    if (dir) { dir.hidden = false; }
  }

  // 系统给的那个悬停标签是整行画出来的，长路径会顶出屏幕被裁掉；在分隔符后面折行，
  // 才保证整条路径都看得见
  function tooltipFor(text) {
    return text.replace(/([\\/])/g, "$1\n");
  }

  function showBanner(text, autoClose) {
    const box = $("file-error"), label = $("file-error-text");
    if (!box || !label) { return; }
    label.textContent = text;
    // 悬停标签挂在整格上，鼠标落在这一格的任何位置（不只文字上）都能看到完整路径
    box.title = tooltipFor(text);
    box.hidden = false;
    $("file-name").hidden = true;
    $("file-dir").hidden = true;
    bannerAutoClose = !!autoClose;
    if (bannerAutoClose) { armBannerTimers(); }
    else { stopBannerTimers(); setCountdown(0); }
  }

  function hoverBanner(inside) {
    // 鼠标压上来就停表，移开再给满 3 秒：正盯着看的时候消失最恼人
    if (inside) { stopBannerTimers(); return; }
    if (bannerAutoClose && !$("file-error").hidden) { armBannerTimers(); }
  }

  // 前后翻历史时每一页都是现从硬盘读的，读不到就不能留着上一页的内容：
  // 留在屏幕上会被当成「这页的内容」，实际那是另一个文件。
  function showErrorPage(d) {
    hideBanner();
    const doc = $("doc");
    doc.textContent = "";
    const box = document.createElement("div");
    box.className = "load-error";
    const heading = document.createElement("h1");
    heading.textContent = "打不开这个文件";
    const reason = document.createElement("p");
    reason.textContent = d.message || "原因不明";
    const target = document.createElement("p");
    target.className = "load-error-path";
    target.textContent = d.path || "";
    const hint = document.createElement("p");
    hint.className = "load-error-hint";
    hint.textContent = "文件可能已被删除、改名或移动。恢复后按 F5 重新读取，也可以用前进/后退去看别的文件。";
    box.append(heading, reason, target, hint);
    doc.appendChild(box);
    $("file-name").textContent = d.title || "打不开的文件";
    $("file-dir").textContent = d.dir || "";
    $("file-dir").title = d.dir || "";
    document.title = (d.title || "mdview") + " — mdview";
    $("scroll").scrollTop = 0;
  }

  function setRecent(items) {
    state.recent = items || [];
    const box = $("recent-box"), list = $("recent-list");
    // 打开文件后欢迎页已被文档内容取代，这两个元素不复存在，直接跳过
    if (!box || !list) { return; }
    list.textContent = "";
    if (!items || !items.length) { box.hidden = true; return; }
    box.hidden = false;
    items.forEach((item) => {
      // 正常情况下传进来的是 {path, exists}；纯字符串也照收，省得调用方挑格式
      const path = typeof item === "string" ? item : item.path;
      const exists = typeof item === "string" ? true : item.exists !== false;
      const li = document.createElement("li");
      const a = document.createElement("a");
      a.href = "#"; a.textContent = path; a.title = path;
      a.onclick = (e) => { e.preventDefault(); mdview.openRecent(path); };
      li.appendChild(a);
      if (!exists) {
        // 不删掉这条：路径本身就是线索（文件多半只是被挪走了），点开时会有横幅说明原因
        li.classList.add("missing");
        const tag = document.createElement("span");
        tag.className = "recent-tag";
        tag.textContent = "已失效";
        li.appendChild(tag);
      }
      list.appendChild(li);
    });
  }

  function showWelcome(items) {
    hideBanner();
    $("doc").innerHTML = WELCOME_HTML;
    $("file-name").textContent = "未打开文件";
    $("file-dir").textContent = "";
    $("file-dir").title = "";
    document.title = "mdview";
    $("scroll").scrollTop = 0;
    setRecent(items || state.recent);
  }

  function setNav(canBack, canForward) {
    $("btn-back").disabled = !canBack;
    $("btn-forward").disabled = !canForward;
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
    }).then((doc) => {
      // 启动参数指向的文件打不开时，后端只能把原因塞在返回值里（带返回值的方法里
      // 不能反过来推脚本给页面），所以这里自己把提示条亮起来，并且不自动关闭
      if (doc && doc.error) { showBanner(doc.error, false); }
      else if (doc) { setDocument(doc); }
      return bridge.nav_state();
    }).then((nav) => { if (nav) { setNav(nav[0], nav[1]); } })
      .catch((err) => { console.error(err); });
  }

  if (window.pywebview) { bind(); }
  else { window.addEventListener("pywebviewready", bind); }

  // 提示那一格的悬停监听放在这里而不是 bind() 里：元素一直都在，绑在 bind() 里就等于
  // 把「鼠标压上去就不自动关」这条行为挂在了 pywebview 就绪上，没就绪时它会照关不误。
  const bannerBox = $("file-error");
  if (bannerBox) {
    bannerBox.addEventListener("mouseenter", () => hoverBanner(true));
    bannerBox.addEventListener("mouseleave", () => hoverBanner(false));
  }

  window.addEventListener("keydown", (e) => {
    if (e.ctrlKey && !e.shiftKey && e.key.toLowerCase() === "o") { e.preventDefault(); mdview.open(); }
    else if (e.key === "F5") { e.preventDefault(); mdview.reload(); }
    else if (e.ctrlKey && e.key.toLowerCase() === "b") { e.preventDefault(); mdview.browser(); }
    else if (e.ctrlKey && (e.key === "=" || e.key === "+")) { e.preventDefault(); mdview.zoom(10); }
    else if (e.ctrlKey && e.key === "-") { e.preventDefault(); mdview.zoom(-10); }
    else if (e.ctrlKey && e.key === "0") { e.preventDefault(); mdview.resetZoom(); }
    else if (e.altKey && e.key === "ArrowLeft") { e.preventDefault(); mdview.back(); }
    else if (e.altKey && e.key === "ArrowRight") { e.preventDefault(); mdview.forward(); }
    else if (e.altKey && e.key === "Home") { e.preventDefault(); mdview.home(); }
  });

  // 页面里的链接一律不在 WebView 内导航，否则目标页面会把整个 GUI 顶掉且退不回来。
  // 页内锚点（#fn-1 这类）除外，那是文档内部的跳转。
  function interceptLink(e) {
    const link = e.target && e.target.closest ? e.target.closest("a[href]") : null;
    if (!link) { return; }
    const href = link.getAttribute("href") || "";
    if (href.startsWith("#")) { return; }
    e.preventDefault();
    // 只有主键（左键）才真的打开。中键和右键都只吞掉默认行为——不吞的话 WebView2
    // 可能自己开新窗口或导航过去；右键的上下文菜单不受 auxclick 影响，照常弹出。
    if (e.type === "auxclick") { return; }
    const bridge = api();
    if (bridge) { bridge.open_link(link.href); }
  }
  document.addEventListener("click", interceptLink);
  document.addEventListener("auxclick", interceptLink);

  // 悬停链接时把目标地址显示到底部状态栏，和浏览器的习惯一致
  function linkOf(target) {
    return target && target.closest ? target.closest("a[href]") : null;
  }
  function showLink(href) {
    const text = $("status-text");
    if (!text) { return; }
    if (!href) { text.textContent = ""; return; }
    // file:///C:/x/y.md 显示成本地路径更好读
    text.textContent = href.startsWith("file:///")
      ? decodeURIComponent(href.slice(8)).replace(/\//g, "\\")
      : href;
  }
  document.addEventListener("mouseover", (e) => {
    const link = linkOf(e.target);
    showLink(link ? link.href : null);
  });
  document.addEventListener("mouseout", (e) => {
    const link = linkOf(e.relatedTarget);
    showLink(link ? link.href : null);
  });

  // 拖文件进来：外部拖放被 WebView2 自己吃掉了，父窗体既收不到也没法让它透传，
  // 只能让页面把 File 对象交给宿主，由宿主从 AdditionalObjects 里取出真实路径。
  window.addEventListener("dragover", (e) => { e.preventDefault(); }, false);
  window.addEventListener("drop", (e) => {
    e.preventDefault();
    const files = e.dataTransfer ? Array.from(e.dataTransfer.files || []) : [];
    const bridge = window.chrome && window.chrome.webview;
    if (files.length && bridge && bridge.postMessageWithAdditionalObjects) {
      bridge.postMessageWithAdditionalObjects("mdview:file-drop", files);
    }
  }, false);

  return {
    state,
    open() { const b = api(); if (b) { b.open_file(); } },
    reload() { const b = api(); if (b) { b.reload(); } },
    browser() { const b = api(); if (b) { b.open_in_browser(); } },
    openRecent(p) { const b = api(); if (b) { b.open_path(p); } },
    clearRecent() { const b = api(); if (b) { b.clear_recent(); } setRecent([]); },
    zoom(delta) { applyZoom(state.zoom + delta); const b = api(); if (b) { b.set_zoom(state.zoom); } },
    resetZoom() { applyZoom(100); const b = api(); if (b) { b.set_zoom(100); } },
    theme(mode) { applyTheme(mode); const b = api(); if (b) { b.set_theme(mode); } },
    back() { const b = api(); if (b) { b.go_back(); } },
    forward() { const b = api(); if (b) { b.go_forward(); } },
    home() { const b = api(); if (b) { b.go_home(); } },
    dismissBanner() { hideBanner(); },
    setDocument, setRecent, setNav, showWelcome, applyTheme, applyZoom,
    showBanner, hideBanner, showErrorPage,
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
        # 历史栈里的 None 代表主页
        self._history: list[Path | None] = [None]
        self._index = 0
        # 最近列表里已经不存在的条目照样留着：那个路径对用户仍是线索（文件可能只是
        # 被挪走了），列表里给它标一个「已失效」比默默删掉更容易理解
        self._config["recent"] = [str(p) for p in self._config["recent"]]

    def _recent_items(self) -> list[dict]:
        """最近列表的推送格式：路径 + 当前还在不在。

        存在与否每次现算，列表每次推送前都是最新的。存储格式仍是纯字符串列表，
        免得给配置文件加没必要的结构。
        """
        return [
            {"path": entry, "exists": Path(entry).is_file()}
            for entry in self._config["recent"]
        ]

    # -- 内部工具 ---------------------------------------------------------- #
    def _push(self, script: str) -> None:
        """把脚本推给页面。页面侧报错不该拖垮 Python 侧，所以这里吞掉异常。"""
        if _ACTIVE_WINDOW is None:
            return
        try:
            _ACTIVE_WINDOW.evaluate_js(script)
        except Exception as exc:  # noqa: BLE001  JS 出错只记一笔，不中断 Python 流程
            print(f"页面脚本执行失败：{exc}", file=sys.stderr)

    def _set_title(self, path: Path | None) -> None:
        if _ACTIVE_WINDOW is not None:
            _ACTIVE_WINDOW.title = f"{path.name} — mdview" if path is not None else "mdview"

    def _push_nav(self) -> None:
        """把前进/后退按钮的可用状态推给前端。"""
        self._push(
            "window.mdview.setNav("
            f"{'true' if self._index > 0 else 'false'}, "
            f"{'true' if self._index < len(self._history) - 1 else 'false'});"
        )

    def _push_history(self, entry: Path | None) -> None:
        """把一页压入历史。

        三件事：同一页不重复入栈（连点同一个文件不该堆一串）、从历史中间岔开时丢弃
        后面那条分支、超过 MAX_HISTORY 就从头部截掉并同步挪动游标，避免栈无限增长。
        """
        if self._history[self._index] == entry:
            return
        del self._history[self._index + 1 :]
        self._history.append(entry)
        self._index = len(self._history) - 1
        overflow = len(self._history) - MAX_HISTORY
        if overflow > 0:
            del self._history[:overflow]
            self._index -= overflow

    def _remember(self, path: Path) -> None:
        """把文件记入最近列表并落盘。

        这里刻意不推送 UI 更新：本方法会在 initial_document() 内部被调用，而带返回值
        的 js_api 方法里再调 evaluate_js，会干扰返回值回传前端，表现为「启动时带文件
        参数却打不开」。最近列表只在欢迎页用到，前端拿 ready() 的返回值就够了。
        """
        entry = str(path.resolve())  # 存绝对路径，否则最近列表会随工作目录失效
        # 落盘前重读一次：多个实例同时开着，各写各的会把对方刚记的条目吞掉
        stored = load_config()
        recent = [p for p in stored.get("recent", []) if p != entry]
        recent.insert(0, entry)
        self._config["recent"] = recent[:12]
        self._config["last_dir"] = str(path.parent)
        save_config(self._config)

    def _document(self, path: Path) -> dict:
        return {
            "html": core.render(core.read_markdown(path)),
            "base": core.dir_uri(path.parent),
            "title": path.name,
            "dir": str(path.parent),
        }

    # -- 打不开文件时的反馈 ------------------------------------------------- #
    def _banner(self, message: str, auto_close: bool = True) -> None:
        """在工具栏下面亮一条提示。

        auto_close 只在提示由用户当场操作触发时才为真；启动时加载失败的提示要留着，
        否则用户只看到一个空窗口，不知道文件为什么没打开。
        """
        self._push(
            f"window.mdview.showBanner({core.to_js(message)}, "
            f"{'true' if auto_close else 'false'});"
        )

    @staticmethod
    def _load_problem(path: Path) -> str | None:
        """返回不能按文件打开的原因，能打开就返回 None。"""
        if path.is_dir():
            return "这是一个文件夹，不是文件"
        if not path.is_file():
            return "找不到文件"
        return None

    @staticmethod
    def _report_failure(problem: str, path: Path) -> str:
        """打不开文件时写一行 stderr，并返回给界面用的那句话。

        界面这半边不能省：开始菜单快捷方式走 pythonw，没有控制台可写，
        只留日志等于什么也没说。
        """
        message = f"{problem}：{path}"
        print(message, file=sys.stderr)
        return message

    def _show_error_page(self, path: Path, problem: str) -> None:
        """历史里的文件没了时改显示错误页。

        前后翻页每次都是现从硬盘读，没有缓存，所以读不到就不能把上一页的内容留在
        屏幕上——那看上去就是「这一页的内容」，实际却是另一个文件。
        """
        self._current = path  # 留着路径：文件回来了按 F5 就能直接重读
        self._set_title(path)
        payload = {
            "path": str(path),
            "title": path.name,
            "dir": str(path.parent),
            "message": problem,
        }
        self._push(f"window.mdview.showErrorPage({core.to_js(payload)});")

    # -- 加载 -------------------------------------------------------------- #
    def load_path(
        self,
        path: Path,
        keep_scroll: bool = False,
        record_history: bool = True,
        page_on_error: bool = False,
    ) -> bool:
        """加载一个文件。

        page_on_error=True 用于前后翻历史：读不到就整页换成错误页。其余入口
        （打开文件、刷新、拖放、最近列表）都保留当前内容，只在顶部亮一条提示，
        免得用户正在看的文档被一次失手点掉的路径顶掉。
        """
        path = Path(path).expanduser().resolve()
        document: dict | None = None
        problem = self._load_problem(path)
        if problem is None:
            try:
                document = self._document(path)
            except OSError as exc:
                problem = f"读取失败：{exc}"
        if document is None:
            assert problem is not None
            message = self._report_failure(problem, path)
            if page_on_error:
                self._show_error_page(path, problem)
            else:
                self._banner(message)
            return False

        self._push(
            f"window.mdview.setDocument({core.to_js(document)}, "
            f"{'true' if keep_scroll else 'false'});"
        )
        self._current = path
        self._set_title(path)
        self._remember(path)
        if record_history:
            self._push_history(path)
        self._push_nav()
        return True

    # -- 供前端调用的接口 --------------------------------------------------- #
    def ready(self) -> dict:
        return {
            "theme": self._config["theme"],
            "zoom": self._config["zoom"],
            "recent": self._recent_items(),
        }

    def nav_state(self) -> list[bool]:
        """前端启动完成后来问一次导航按钮状态。

        初始文档是在 initial_document() 里入栈的，而那个方法带返回值，不能在里面推送
        （会干扰返回值回传），所以这里让前端自己来取。
        """
        return [self._index > 0, self._index < len(self._history) - 1]

    def initial_document(self) -> dict | None:
        """启动时要显示的内容。

        这个方法带返回值，不能反过来推脚本给页面（会打断返回值回传），所以「文件没找到」
        只能塞进返回值，让前端自己把提示条亮起来。

        只有「压根没带启动参数」才返回 None；带了参数却打不开必须把原因说明白，
        否则用户面对的就是一个空窗口，看不出文件到底打开没有。
        """
        if self._initial is None:
            return None
        document: dict | None = None
        problem = self._load_problem(self._initial)
        if problem is None:
            try:
                document = self._document(self._initial)
            except OSError as exc:
                problem = f"读取失败：{exc}"
        if document is None:
            assert problem is not None
            return {"error": self._report_failure(problem, self._initial)}
        self._current = self._initial
        self._set_title(self._initial)
        self._remember(self._initial)
        self._history.append(self._initial)
        self._index = len(self._history) - 1
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
                    message = f"打开文件失败：{exc}"
                    print(message, file=sys.stderr)
                    self._banner(message)
            else:
                message = f"找不到文件：{target}"
                print(message, file=sys.stderr)
                self._banner(message)
            return
        if parsed.scheme in ("http", "https", "mailto"):
            webbrowser.open(url)
            return
        print(f"未处理的链接协议：{url}", file=sys.stderr)

    def reload(self) -> None:
        if self._current is not None:
            # 刷新要停在原处，否则读长文档时每次刷新都被弹回顶部
            self.load_path(self._current, keep_scroll=True)

    def _reload_recent(self) -> None:
        """进主页时重读最近列表。

        同时开着两个实例是常态，所以每次都从磁盘取最新的。已经不存在的条目不再剔除，
        列表里会标成「已失效」，点开时再给一条横幅。
        """
        stored = load_config()
        self._config["recent"] = [str(p) for p in stored.get("recent", [])]
        self._config["last_dir"] = stored.get("last_dir") or self._config.get("last_dir", "")

    def _show_entry(self) -> None:
        entry = self._history[self._index]
        if entry is None:
            self._current = None
            self._set_title(None)
            self._reload_recent()
            self._push(
                f"window.mdview.showWelcome({core.to_js(self._recent_items())});"
            )
        else:
            self.load_path(entry, record_history=False, page_on_error=True)
        self._push_nav()

    def go_back(self) -> None:
        if self._index > 0:
            self._index -= 1
            self._show_entry()

    def go_forward(self) -> None:
        if self._index < len(self._history) - 1:
            self._index += 1
            self._show_entry()

    def go_home(self) -> None:
        # 主页按新的一页入栈，这样「后退」还能回到刚才那份文档；已经在主页时不再叠层，
        # 但仍然走一遍 _show_entry()，好把最近列表重新刷一次
        if self._history[self._index] is not None:
            self._push_history(None)
        self._show_entry()

    def open_in_browser(self) -> None:
        if self._current is None:
            return
        import mdview as cli  # 复用 CLI 的整页模板

        try:
            source = core.read_markdown(self._current)
        except OSError as exc:
            message = f"读取失败：{exc}"
            print(message, file=sys.stderr)
            self._banner(message)
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


def _install_file_drop(window: webview.Window, api: "Api") -> None:
    """接管文件拖放，让拖进来的文件在本窗口打开。

    两个坑都踩过了，所以这里这么写：

    1. WebView2 在无窗口模式下自己吃掉了外部拖放，父窗体既收不到事件、也没法让它透传
       （关掉 AllowExternalDrop 只会让它变成「拒绝接收」：内容区光标变禁止样式，事件
       照样不上传，只有 WebView 盖不到的标题栏才落到窗体上）。所以只能走 WebView2 自己
       的通道：页面在 drop 事件里用 postMessageWithAdditionalObjects 把 File 对象交给
       宿主，宿主再从 AdditionalObjects 里读出真实路径。
    2. 拖放事件本身在 UI 线程上跑。处理器里一旦同步等 JS 执行（evaluate_js），UI 线程
       和 OLE 拖放的源进程会一起卡死——表现就是窗口「未响应」，连资源管理器也跟着僵住，
       直到强关窗口才把积压的操作一次性放出来。所以加载动作必须丢给后台线程后立刻返回。
    """
    try:
        import clr

        clr.AddReference("System.Windows.Forms")
        from System import Action
    except Exception as exc:  # noqa: BLE001
        print(f"拖放不可用（加载 WinForms 失败）：{exc}", file=sys.stderr)
        return

    try:
        form = window.native
    except Exception as exc:  # noqa: BLE001
        print(f"拖放不可用（拿不到窗口对象）：{exc}", file=sys.stderr)
        return

    def on_web_message(sender, event):
        try:
            if event.WebMessageAsJson != '"mdview:file-drop"':
                return  # 不是拖放消息，留给 pywebview 自己处理
        except Exception:  # noqa: BLE001
            return
        try:
            items = list(event.AdditionalObjects or [])
        except Exception as exc:  # noqa: BLE001
            print(f"读取拖入的文件失败：{exc}", file=sys.stderr)
            return
        for item in items:
            try:
                path = item.Path
            except Exception:  # noqa: BLE001
                continue
            if not path:
                continue
            # 此刻仍在 UI 线程上，同步加载会死锁，必须甩出去
            threading.Thread(
                target=api.load_path, args=(Path(path),), daemon=True
            ).start()
            return

    def attach():
        try:
            form.browser.webview.CoreWebView2.WebMessageReceived += on_web_message
            print("文件拖放已就绪", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001
            print(f"挂载拖放通道失败：{exc}", file=sys.stderr)

    try:
        form.BeginInvoke(Action(attach))
    except Exception as exc:  # noqa: BLE001
        print(f"拖放不可用（切 UI 线程失败）：{exc}", file=sys.stderr)


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
    # 拖放要等页面加载完再挂：那会儿 WebView2 控件才真正存在
    window.events.loaded += lambda *_: _install_file_drop(window, api)
    webview.start(
        private_mode=False,
        storage_path=str(DATA_DIR / "webview"),
        debug=os.environ.get("MDVIEW_DEBUG") == "1",
    )
    _ACTIVE_WINDOW = None
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
