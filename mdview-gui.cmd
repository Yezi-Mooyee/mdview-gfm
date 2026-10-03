@echo off
rem mdview GUI - native window Markdown viewer powered by GitHub's cmark-gfm
rem Usage: mdview-gui [file.md]     (double-click this file to start)
start "" pythonw "%~dp0mdview_gui.py" %*
