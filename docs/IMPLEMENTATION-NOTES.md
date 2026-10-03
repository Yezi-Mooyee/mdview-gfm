# Implementation notes

Design decisions and platform pitfalls behind mdview-gfm. The [README](README.md)
only covers what a *user* sees; this file is for anyone touching the code.

---

## 1. Theme switching: three stylesheets, switched by `media`

`github-markdown-css` ships **three independent files**:

| File | Nature |
|:-----|:-------|
| `github-markdown.css` | variable-driven; light values in `.markdown-body`, dark values inside `@media (prefers-color-scheme: dark)` |
| `github-markdown-light.css` | forced light — **hard-coded colors**, no CSS variables at all |
| `github-markdown-dark.css` | forced dark — same, hard-coded |

They are **not** "one ruleset with different variables". An attempt to merge them
into a single sheet (take the light file as the base, append the dark file's first
rule block as an override) produced a white table with light-grey text in dark
mode: the dark file's *table* rule lives further down its own file and never got
copied over, so `background-color: #ffffff` survived while the text color was
overridden.

The current approach keeps all three attached and flips the `media` attribute:

```html
<link id="css-auto"  rel="stylesheet" href="...">
<link id="css-light" rel="stylesheet" href="..." media="not all">
<link id="css-dark"  rel="stylesheet" href="..." media="not all">
```

`media="not all"` stylesheets are **still downloaded and parsed**, just not
applied. Switching therefore takes effect from memory — there is no window where
the old sheet is gone and the new one is still loading, which is what used to make
the scroll position and layout jump.

The toolbar's own colors are separate and are switched by a `data-forced-theme`
attribute on `<html>`.

---

## 2. Drag & drop: WebView2's file channel

WebView2 in windowless mode consumes external drops itself and does **not** pass
them up to the hosting WinForms window. Turning off `AllowExternalDrop` does not
help either — it only makes WebView2 *refuse* the drop: the cursor turns into a
no-entry sign and the events still never reach the parent. That is why drag & drop
initially only worked over the title bar, which WebView2 does not cover.

The working path is WebView2's official file channel:

```js
chrome.webview.postMessageWithAdditionalObjects("mdview:file-drop", files);
```

The host reads real paths out of `CoreWebView2WebMessageReceivedEventArgs.AdditionalObjects`.
That API accepts **only files that exist on disk** — posting an in-memory `File`
fails with `additional File object is not a file on the disk`, which is also why
the feature cannot be tested without a real drag.

**Threading:** the drop handler runs on the UI thread. Calling `evaluate_js`
synchronously from it deadlocks — the UI thread waits for a JavaScript result that
can only be delivered by the UI thread. Because OLE drag & drop is synchronous, the
source Explorer window freezes too and only recovers when the window is force
closed. All loading therefore happens on a background thread.

---

## 3. Links: never navigate in-window

Navigating the WebView replaces the entire GUI with the target page and leaves no
way back. Clicks are intercepted and handed to Python:

- Only the **primary button** opens anything. `auxclick` covers middle *and* right
  click, so `e.button !== 1` would still let right-click through; instead any
  `auxclick` is swallowed (after `preventDefault`, so WebView2 does not open a new
  window) without opening anything. Right-click's own context menu is unaffected,
  since it comes from the `contextmenu` event.
- Anchors starting with `#` are left alone — those are in-document jumps (footnotes).
- Local Markdown goes through `load_path`; web links go to the system browser;
  anything else goes to `os.startfile`.

---

## 4. Navigation history

A plain list plus an index, with `None` representing the home page.

- The home button **pushes a new entry** rather than jumping to the bottom of the
  stack, so Back still returns to the document you were reading.
- Pressing Home while already there does not stack another `None`, but still
  re-renders the recent list.
- Same page in a row is not recorded twice.
- Opening from the middle discards the forward branch.
- Capped at `MAX_HISTORY` (100); overflow is trimmed from the head and the cursor
  is shifted to match.

---

## 5. Recent files and multiple instances

`_remember()` re-reads `config.json` from disk *before* writing, and merges, so two
instances running side by side do not swallow each other's entries. Entering the
home page calls `_reload_recent()`, which re-reads the file and drops paths that no
longer exist.

The config file is read as `utf-8-sig`. Notepad (and Windows PowerShell 5.1's
`-Encoding UTF8`) writes a BOM; reading it as plain `utf-8` yields invalid JSON, and
the loader would silently fall back to defaults and then overwrite the user's
settings.

---

## 6. The `js_api` return-value pitfall

pywebview delivers a `js_api` method's return value to JavaScript. Calling
`evaluate_js` **inside** such a method breaks that delivery: the value never
arrives, and the front end sees `null`.

This showed up as "launching with a file argument does not open the file, but the
title bar updates anyway". `initial_document()` used to call `_remember()`, which
pushed a UI update via `evaluate_js`.

Rule followed from then on: methods with a meaningful return value only read state;
anything that needs to push to the page returns `None`.

---

## 7. DPI handling

`SetProcessDpiAwareness(2)` must run **before any window is created** — otherwise
the process is treated as DPI-unaware and the whole WebView is bitmap-stretched,
which is visibly blurry at 200% scaling.

Window size is computed from the screen's *logical* resolution (physical size
divided by the system DPI scale) and handed to pywebview, which converts back to
physical pixels. On a 2736×1824 @200% display (logical 1368×912) this yields
roughly 1180×820 logical pixels.

---

## 8. Startup time measurements

Instrumented cold/warm starts:

| Stage | Time |
|:------|:-----|
| DPI declaration + pythonnet/CLR + imports | ~1.3 s |
| `create_window` | +0.03 s |
| → page `loaded` event | **+6 to 20 s, highly variable** |

A control run with a trivial `<h1>hi</h1>` page was *slower* than the real
document, so the cost is WebView2 runtime initialisation, not page content or
stylesheet count. Passing `WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--disable-features=msSmartScreenProtection,RendererCodeIntegrity`
made no measurable difference (it measured slower), so no flags are used.

---

## 9. PostMessage-only UI updates

All page updates go through `window.mdview.*` functions injected by
`evaluate_js`. The shell page is loaded once; switching documents replaces
`#doc.innerHTML` rather than reloading, which keeps scroll, zoom, theme and history
state intact. Because of that, the welcome page's markup is captured into
`WELCOME_HTML` at startup so the Home button can restore it verbatim.

`setRecent()` must guard against missing elements: once a document is open the
welcome markup is gone, and `#recent-list` no longer exists.
