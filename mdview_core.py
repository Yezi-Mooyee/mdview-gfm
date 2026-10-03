#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mdview 渲染核心：GitHub 官方渲染器 cmark-gfm 的薄封装。

CLI（mdview.py）与 GUI（mdview_gui.py）都走这里，保证两种方式产出的
HTML 完全一致。
"""

from __future__ import annotations

import json
from pathlib import Path

import pycmarkgfm
from pycmarkgfm import options as gfm

# GitHub 网页渲染时启用的选项组合：放行原生 HTML、支持脚注、用 GitHub 的 <pre lang="x"> 写法。
GFM_OPTIONS = (
    gfm.default | gfm.unsafe | gfm.validate_utf8 | gfm.github_pre_lang | gfm.footnotes
)


def render(source: str, safe: bool = False) -> str:
    """把 Markdown 渲染成 GitHub 风格的 HTML 片段。

    safe=True 时退回 cmark-gfm 的严格过滤模式（原生 HTML 一律转义）。
    """
    options = GFM_OPTIONS & ~gfm.unsafe if safe else GFM_OPTIONS
    return pycmarkgfm.gfm_to_html(source, options=options)


def read_markdown(path: Path) -> str:
    """读取 Markdown，容忍 BOM 与 GBK/GB18030 编码的中文文件。"""
    data = path.read_bytes()
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def dir_uri(path: Path) -> str:
    """目录 → 带结尾斜杠的 file:// URI，供页面 <base> 解析相对图片/链接。"""
    uri = path.resolve().as_uri()
    return uri if uri.endswith("/") else uri + "/"


def to_js(value: object) -> str:
    """序列化成可安全内嵌进 <script>/evaluate_js 的字面量。"""
    text = json.dumps(value, ensure_ascii=False)
    return text.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
