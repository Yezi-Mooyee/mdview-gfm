@echo off
rem mdview - preview Markdown in your browser with GitHub's cmark-gfm renderer
rem Usage: mdview README.md      (or drag a .md file onto this file)
python "%~dp0mdview.py" %*
