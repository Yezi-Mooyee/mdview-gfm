#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mdview —— 用 GitHub 官方渲染器（cmark-gfm）在浏览器里预览 Markdown。

渲染逻辑见 mdview_core.py，原生窗口版本见 mdview_gui.py。

用法：
    python mdview.py README.md            # 渲染并用默认浏览器打开
    python mdview.py README.md --no-open  # 只生成 HTML
    type a.md | python mdview.py -        # 从标准输入读取
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import tempfile
import webbrowser
from pathlib import Path

import mdview_core as core

HERE = Path(__file__).resolve().parent
DEFAULT_CSS = HERE / "github-markdown.css"

PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<meta name="generator" content="mdview + cmark-gfm">
<title>{title}</title>
{base}<link rel="stylesheet" href="{css_uri}">
<style>
html {{ scroll-behavior: smooth; }}
body {{ margin: 0; background-color: #ffffff; }}
@media (prefers-color-scheme: dark) {{ body {{ background-color: #0d1117; }} }}
.markdown-body {{
  box-sizing: border-box;
  min-width: 200px;
  max-width: 1120px;
  margin: 0 auto;
  padding: 45px;
}}
@media (max-width: 767px) {{ .markdown-body {{ padding: 15px; }} }}
</style>
</head>
<body>
<article class="markdown-body">
{body}
</article>
</body>
</html>
"""


def escape_attr(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def build_page(source: str, title: str, base_uri: str, css_uri: str, safe: bool = False) -> str:
    """拼出一张完整的、可在浏览器里直接打开的 HTML 页面。"""
    base_tag = f'<base href="{escape_attr(base_uri)}">\n' if base_uri else ""
    return PAGE.format(
        title=escape_attr(title),
        base=base_tag,
        css_uri=escape_attr(css_uri),
        body=core.render(source, safe=safe),
    )


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        # 输出被重定向/捕获时统一切到 UTF-8；真实控制台交给 Python 自己处理，
        # 免得 Windows 控制台代码页把中文路径变成乱码。
        try:
            if not stream.isatty():
                stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(
        prog="mdview",
        description="用 GitHub 同款渲染器（cmark-gfm）在浏览器里查看 Markdown。",
    )
    parser.add_argument("file", help="Markdown 文件路径，用 - 表示从标准输入读取")
    parser.add_argument("--out", help="生成的 HTML 路径（默认放在临时目录）")
    parser.add_argument("--no-open", action="store_true", help="只生成 HTML，不打开浏览器")
    parser.add_argument("--safe", action="store_true", help="严格过滤原生 HTML（与 cmark-gfm 默认一致）")
    parser.add_argument("--css", help="自定义样式表路径")
    parser.add_argument("--title", help="页面标题")
    args = parser.parse_args(argv)

    if args.file == "-":
        source = sys.stdin.buffer.read().decode("utf-8", errors="replace")
        base_uri = ""
        stem = "stdin"
        key = source.encode("utf-8")
    else:
        md = Path(args.file).expanduser()
        if not md.is_file():
            sys.stderr.write(f"找不到文件：{md}\n")
            return 1
        source = core.read_markdown(md)
        base_uri = core.dir_uri(md.parent)
        stem = md.stem or "document"
        key = str(md.resolve()).encode("utf-8")

    css = Path(args.css).expanduser() if args.css else DEFAULT_CSS
    if not css.is_file():
        sys.stderr.write(f"找不到样式表：{css}\n")
        return 1

    html = build_page(source, args.title or stem, base_uri, css.resolve().as_uri(), args.safe)

    if args.out:
        out = Path(args.out).expanduser()
    else:
        digest = hashlib.sha1(key).hexdigest()[:10]
        safe_stem = "".join(c if c.isalnum() or c in "-_" else "_" for c in stem)[:40] or "document"
        out = Path(tempfile.gettempdir()) / "mdview" / f"{safe_stem}-{digest}.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")

    print(out)
    if not args.no_open:
        webbrowser.open(out.resolve().as_uri())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
