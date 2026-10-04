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

Hovering a link shows its target in the status bar, and that target comes from
`data-href` first. The recent list hangs its entries on JavaScript and only carries
`href="#"` as a placeholder, so the resolved `link.href` is the shell page's own
address with a `#` on the end — which is exactly what the status bar used to show on
the welcome page. In-document anchors (`#fn-1`) now show nothing, since there is no
address worth printing.

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
home page calls `_reload_recent()`, which re-reads the file.

Entries whose file has gone are **kept, not dropped**. The path is still the clue
the user needs — the file usually just moved — and silently rewriting someone's
history is worse than showing a dead entry. `_recent_items()` computes
`{"path", "exists"}` at push time and the list marks the missing ones; the stored
format stays a plain list of strings, so `config.json` is unchanged. Clicking a
missing entry is an ordinary failed load and reports itself like any other (§10).

Reload works on the welcome page too. There `_current` is `None`, which used to mean
`reload()` returned immediately and F5 did nothing at all; now it re-runs
`_show_entry()`, so the existence flags are recomputed — useful both for files that
came back and for ones that were moved away after the list was drawn. For the same
reason `open_path()` re-pushes the list when the load fails: the entry the user just
clicked is exactly the one that turned out to be stale.

Clearing the list takes two clicks. The first only turns the button red and prints
"click again" beside it; the second does the work. The confirmation is dropped by a
click anywhere else — on **any** mouse button, which is why it listens for
`pointerdown` and not `click` (middle and right clicks produce no `click` at all) — by
the window losing focus, and by the pointer leaving the document entirely.

That last case is what covers the title bar: it belongs to Windows, not to the page,
so no DOM event lands there and the window does not lose focus either. The signal that
does arrive is `mouseout` with an empty `relatedTarget`; moving between elements leaves
`relatedTarget` set, so the two are easy to tell apart. `mouseleave` is **not** usable
here — it does not bubble, so a listener on `document` or `<html>` never fires, which
cost a detour to notice because a synthetic `dispatchEvent` on `document` happily
triggers it. Only a real pointer leaving the window exposes the difference.

That state lives entirely in the front end — the welcome markup is rebuilt from
`WELCOME_HTML` and the confirmation resets with it, so nothing can be left armed behind
the user's back.

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

---

## 10. When the file is gone

Loading used to fail silently: `load_path()` wrote to stderr and returned `False`,
and the page was never told. Started from the Start menu the process is `pythonw`,
which has no console to write to, so "file not found" was invisible while the
previous document stayed on screen as if nothing had happened. Back/forward were
worse: the cursor moved, the buttons updated, and the view did not.

**Feedback must not change the layout.** The message takes over the toolbar cell
that shows the file name and directory (`#file-error` replaces `#file-name` /
`#file-dir`) instead of occupying a row of its own. Rendering the same page with and
without the message at 1200×620 gives zero differing pixels below the toolbar and an
identical `#scroll` height (653 px). A strip that pushes the document down would
move the text out from under the reader's eyes.

**Who closes it.** The whole message is the close button — clicking anywhere on it
dismisses it (Enter or Space too, since it is `role="button"`); the ✕ is only a glyph
and takes no pointer events, because a ✕ that is the sole hit target tells people the
rest of the strip is dead. Messages triggered by a user
action (open, reload, drop, recent entry) also count down from 3 s and then fade. The
one produced by a failed *startup* load stays until it is dismissed or a document
loads successfully: at that point the window is empty and nothing else explains
itself.

The countdown exists because a message that vanishes unannounced is worse than one
that waits. It sits in a fixed-width box so 3 → 2 → 1 cannot nudge the text beside
it. Hovering the message stops the clock and hides the digits (`#file-error:hover`
hides `#file-countdown`); leaving resets it to a full 3 s. Closing is driven by that
same interval rather than a second `setTimeout`: both timers came due in the same
tick, whichever ran first cleared the other, and the message sometimes never closed
at all.

The hover listeners are attached at the top of the IIFE, **not** inside `bind()`.
Binding them in `bind()` tied "hovering keeps it open" to pywebview being ready,
and the message closed on schedule whenever it was not.

Hovering also outlines the whole cell (a transparent 1 px border is always there,
so only the colour changes and nothing shifts) and the native tooltip is attached to
the cell instead of the text, so the full path is reachable anywhere on it. That
tooltip is drawn as a single line by the OS; long paths are wrapped at separators or
they run off the screen and get clipped.

**History re-reads, so a missing page needs a page.** Open, reload, drop and recent
entries all load into a document the user is already reading — the content stays and
only the message changes. Back/forward is a different case: every step re-reads that
file from disk, there is no cache, so leaving the previous document on screen would
present another file's content as this page's. It gets an error page instead, while
the cursor keeps moving normally so both directions stay usable.

Startup adds the constraint from §6: `initial_document()` carries the failure back
as `{"error": ...}` and the page raises the message itself, because a `js_api` method
with a return value must not call `evaluate_js`.

The load failure paths are exercised by a driver that opens a real pywebview window
and reads the resulting DOM state back (button `disabled` flags, whether the message
is shown, `#scroll` height), which is more reliable than sending keystrokes to a
WebView2 window.

