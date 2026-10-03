# Third-party notices

mdview itself is licensed under the MIT License — see [LICENSE](LICENSE).

This repository **redistributes** the following third-party files, which remain
under their own licenses. The runtime Python dependencies are *not* bundled;
users install them separately, so their licenses apply to the installed
packages rather than to this repository.

---

## github-markdown-css

Redistributed files:

- `github-markdown.css`
- `github-markdown-light.css`
- `github-markdown-dark.css`

Source: <https://github.com/sindresorhus/github-markdown-css>

```
MIT License

Copyright (c) Sindre Sorhus <sindresorhus@gmail.com> (https://sindresorhus.com)

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

---

## Runtime dependencies (installed by the user, not bundled)

| Package | License | Used for |
|:--------|:--------|:---------|
| [pycmarkgfm](https://github.com/thechampion/pycmarkgfm) | MIT | Python binding for `cmark-gfm` |
| [cmark-gfm](https://github.com/github/cmark-gfm) | BSD-2-Clause | GitHub's Markdown parser/renderer (linked into `pycmarkgfm`) |
| [pywebview](https://github.com/r0x0r/pywebview) | BSD-3-Clause | Native window + Edge WebView2 hosting (GUI only) |
| [pythonnet](https://github.com/pythonnet/pythonnet) | MIT | CLR bridge used by pywebview on Windows |
