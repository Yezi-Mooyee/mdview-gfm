#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mdview 的双击入口。

Windows 把 .pyw 交给 pythonw.exe（本机是 pyw.exe 启动器）执行，全程不创建控制台
窗口，所以双击不会闪一下黑框。.cmd 做不到这点——它无论怎么写都要先起一个 cmd
宿主，那个窗口必然闪现。命令行场景继续用 mdview-gui.cmd。
"""

from __future__ import annotations

if __name__ == "__main__":
    from mdview_gui import main

    raise SystemExit(main())
