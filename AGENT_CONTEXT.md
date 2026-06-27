# Argus — Agent Context Document

**For AI models:** This document gives you complete context to work on or with the Argus browser agent. Read this before touching any code. It covers architecture, all tools, memory system, known limitations, and planned upgrades.

---

## What Argus Is

Argus is a browser automation agent that:
- Connects to the user's **real, already-open Chrome browser** via CDP (no isolated browser, real cookies and sessions)
- Uses **Gemini 2.5 Flash on Vertex AI** for vision and decision-making (multimodal function calling)
- Runs an **autonomous perceive → decide → act loop** until the task is complete
- **Learns persistently** — writes structured site knowledge and task patterns to disk after every run
- On the next run for the same site, injects previously learned context into the system prompt before the agent starts

---

## Key Files

| File | Role |
|---|---|
| `browser_agent.py` | Core agent: tool declarations, `run_tool()`, `agent_loop()`, `_flush_memory()`, CDP guard |
| `launch_chrome_cdp.py` | Launches Chrome with CDP on port 9222, copies real user profile on first run |
| `test_agent.py` | Connects to Chrome, runs one task through `agent_loop()`, closes |
| `memory/memory.py` | All memory read/write: `get_site`, `update_site`, `get_framework`, `update_framework`, `get_task_pattern`, `save_task_pattern`, `build_context` |
| `memory/sites.json` | Domain-keyed site profiles (auto-generated, gitignored) |
| `memory/tasks.json` | Domain-keyed task patterns with confidence scores (auto-generated, gitignored) |
| `memory/frameworks.json` | Framework-level shared knowledge pool (auto-generated, gitignored) |

---

## Runtime Configuration

```python
# browser_agent.py top-level constants
CDP_URL  = "http://localhost:9222"
MODEL    = "gemini-2.5-flash"
PROJECT  = "project-7107f92a-86d1-4161-a11"   # GCP project ID
LOCATION = "us-central1"
```

Auth: Vertex AI Application Default Credentials (`gcloud auth application-default login`). No API key required.

---

## Agent Loop Flow

```
agent_loop(task, page, ctx)
  1. Extract domain from page.url
  2. build_context(domain, task) → inject site profile + task pattern into system prompt
  3. client.chats.create() with GEMINI_TOOLS
  4. Loop:
       a. send_message → get response
       b. extract function_calls and text_parts
       c. For each function_call: run_tool(name, inputs, page, ctx, session)
       d. Log tool call to session["calls"]
       e. Screenshot results sent as image Part; other results as function_response Part
       f. If no function_calls in response → task done, break
  5. _flush_memory(client, domain_of(page.url), task, session, success)
       → one Gemini call to summarise session
       → update_framework() + update_site() + save_task_pattern()
```

---

## Complete Tool Inventory (23 tools)

### Perception

**`screenshot()`**
- Takes PNG of current page, sends to Gemini as an image Part
- Returns: `{"type": "image", "data": base64_png, "media_type": "image/png"}`
- Use first on every task to see the current state

**`read_page()`**
- Returns: `inner_text("body")` truncated to 3000 chars
- Use when you need text content without visual context
- Note: Angular Material virtual-scroll tables may not appear here — use `run_js` for those

**`get_element_text(selector)`**
- Runs `query_selector_all(selector)`, returns list of up to 10 element texts as JSON
- Use to extract text from specific matched elements

---

### Navigation

**`navigate(url)`**
- `page.goto(url, wait_until="domcontentloaded", timeout=15000)`
- Returns: `"Navigated to {url} title={title}"`

**`list_tabs()`**
- Returns numbered list of all open tabs with title and URL
- Use when you need to find or switch to a specific tab

**`go_to_tab(index)`**
- `ctx.pages[index].bring_to_front()`
- Use the index from `list_tabs()`

---

### Interaction

**`click(selector?, text?)`**
- Provide either `selector` (CSS) or `text` (visible label)
- `selector`: `page.click(selector, timeout=5000)`
- `text`: `page.get_by_text(text).first.click(timeout=5000)`
- Prefer `text` for buttons with clear labels; prefer `selector` for precise targeting
- Timeout: 5 seconds

**`type_text(selector?, text, clear_first?)`**
- `selector` given: `page.fill(selector, text)` if clear_first (default True), else `page.type()`
- `selector` absent: `page.keyboard.type(text)` — types into whatever has focus
- Default: clears existing content before typing

**`fill_by_label(label, value)`**
- Finds input by visible label or placeholder text, not CSS selector
- Tries: `page.get_by_label()` → `page.get_by_placeholder()` → JS DOM scan
- **Use on Angular Material and GCP pages** where standard selectors fail
- JS fallback uses `aria-label`, `placeholder`, or associated `<label>` text

**`press_key(key)`**
- `page.keyboard.press(key)` — e.g. `"Enter"`, `"Escape"`, `"Tab"`, `"ArrowDown"`

**`hover(selector?, text?)`**
- Hover to reveal hidden dropdowns, tooltip arrows, or context menus
- Provide either `selector` (CSS) or `text` (element text)
- Timeout: 5 seconds

---

### DOM Manipulation

**`scroll(direction, pixels?)`**
- `direction`: `"up"` or `"down"`; `pixels`: default 400
- `window.scrollBy(0, delta)` — use for virtual-scroll tables on GCP

**`run_js(code)`**
- `page.evaluate(code)` — executes arbitrary JavaScript, returns result (max 500 chars)
- **Primary tool for Angular Material / GCP** — virtual scroll tables, CDK overlay portals, mat-select
- Example: `Array.from(document.querySelectorAll('[role=row]')).find(r => r.innerText.includes('GPU')).click()`

**`dismiss_overlays()`**
- Removes modal dialogs, login walls, cookie banners, paywalls via JS DOM removal
- Targets: `[role=dialog]`, `[class*=modal]`, `[class*=overlay]`, `[class*=popup]`, `[class*=banner]`, `[class*=cookie]`, `[class*=consent]`, `[class*=paywall]`, `artdeco-modal`, etc.
- Also restores `body.style.overflow` if a modal froze the page scroll
- Use when close buttons can't be found or fail — nukes the overlay entirely

---

### Timing

**`wait_for(selector, timeout_ms?)`**
- `page.wait_for_selector(selector, timeout=timeout_ms)`
- Default timeout: 5000ms
- Use before interacting with elements that load asynchronously

**`wait_seconds(seconds)`**
- `asyncio.sleep(min(seconds, 10))`
- Max 10 seconds. Use for animations or async content that `wait_for` can't target

---

### Coordinate & Network

**`click_at(x, y, description?)`**
- `page.mouse.click(x, y)` — bypasses DOM entirely
- Use ONLY when an element is visible in a screenshot but unreachable via selector, text, `fill_by_label`, or `run_js`
- The main agent loop already has the screenshot in context — estimate coordinates from it directly
- `description` is for logging only

**`wait_for_network_idle(timeout_ms?)`**
- `page.wait_for_load_state("networkidle")` — waits until no pending requests for 500ms
- Default timeout: 10000ms
- **Prefer over `wait_seconds` on any SPA** (Angular, React, GCP Console)

**`wait_for_response(url_pattern, timeout_ms?)`**
- `page.wait_for_response(lambda r: pattern in r.url)`
- Use when you know which API endpoint signals content has loaded
- Default timeout: 15000ms

---

### File Operations

**`download_file(selector?, text?, save_as?)`**
- Triggers download via `page.expect_download()` then `download.save_as()`
- Files saved to `downloads/` directory next to `browser_agent.py`
- Provide either `selector` (CSS) or `text` (visible label) to identify the trigger
- `save_as`: optional filename; uses server-suggested name if omitted
- Returns: absolute path to saved file

**`upload_file(selector, path)`**
- `page.set_input_files(selector, path)` — works on `<input type="file">` elements
- `path`: absolute path to file on disk
- Does NOT handle native Windows Save/Open dialogs (those require win32gui)

---

### Structured Extraction

**`extract(description, schema?)`**
- Sends current screenshot + page text to Gemini with JSON response mode
- Returns structured JSON — more reliable than `read_page` + manual LLM parsing
- `description`: natural language description of what to extract
- `schema`: optional JSON schema hint string
- Max output: 3000 chars
- Uses a separate Gemini call (one extra LLM call per invocation)
- Example: `extract("all rows in the quota table", '{"rows":[{"name":"string","current":"number","limit":"number"}]}')`

---

### Memory

**`save_observation(note, category)`**
- Saves a note to the session buffer; flushed to `sites.json` after task completes
- `category`: one of `interaction`, `pitfall`, `friction`, `failure`
- **Call immediately when you discover something reusable** — don't wait until the end
- Examples:
  - `interaction`: "GCP quota table uses virtual scroll — use run_js to find rows"
  - `pitfall`: "mat-select requires 500ms wait before the CDK portal opens"
  - `failure`: "button.artdeco-modal__dismiss does not exist on LinkedIn job pages"
  - `friction`: "LinkedIn redirects to login if no session cookie present"

---

## Memory System

### Three Stores

**`memory/sites.json`** — keyed by domain
```json
{
  "console.cloud.google.com": {
    "framework": "Angular Material",
    "interaction_notes": ["tip1", "tip2"],
    "known_failures": ["selector that never works"],
    "friction": ["rate limit on rapid clicks observed"],
    "last_updated": "2026-06-27"
  }
}
```

**`memory/tasks.json`** — keyed by domain, list of patterns
```json
{
  "console.cloud.google.com": [
    {
      "task_type": "quota_edit",
      "keywords": ["quota", "increase", "GPU", "limit"],
      "steps": [{"description": "Filter quota table by metric name"}, ...],
      "pitfalls": ["Done button closes panel if justification is empty"],
      "confidence": 0.7,
      "uses": 3,
      "last_used": "2026-06-27"
    }
  ]
}
```

**`memory/frameworks.json`** — keyed by framework name
```json
{
  "Angular Material": {
    "interaction_notes": [
      "Virtual scroll tables: rows not in DOM — use run_js to find by text",
      "mat-select: click trigger, wait 500ms, then click option inside CDK overlay portal",
      "Sidebar panels render in CDK overlay — not in main DOM tree"
    ],
    "last_updated": "2026-06-27"
  }
}
```

### Confidence Scoring

- `0.5` — first successful run (used as hint, not authoritative)
- `+0.1` per additional success, capped at `1.0`
- `≥0.7` — step sequence shown as "follow this order" (replay hint)
- `<0.7` — shown as "previous attempt, low confidence"
- `-0.1` per failure

### Context Injection

At the start of every `agent_loop()`, `build_context(domain, task)` produces a block prepended to the system prompt:

```
=== FRAMEWORK: Angular Material ===       ← shared, applies to any Angular site
  - Virtual scroll tables: use run_js...
  - mat-select: click trigger, wait...

=== SITE: console.cloud.google.com ===    ← site-specific
  - URL params control page state...
  - Known failures: [selector list]

=== TASK PATTERN: quota_edit (70%) ===    ← task-specific, shown if ≥0.7 confidence
  Proven step sequence — follow this order:
  1. Filter quota table by metric name
  2. ...
```

---

## Chrome CDP Setup

### First Run
`launch_chrome_cdp.py` copies the user's real Chrome profile to `TEMP/chrome-cdp-session-profile`. This takes ~30 seconds and happens once. All subsequent runs reuse the copy.

Real profile path: `%LOCALAPPDATA%\Google\Chrome\User Data`

To force a fresh copy (e.g. after logging into new sites):
```powershell
python launch_chrome_cdp.py --fresh
```

### Omnibox Popup Issue (Chrome 149)

Chrome 149 keeps two Omnibox-related targets in the CDP target list:
- `chrome://omnibox-popup.top-chrome/` — **blocking** (opens when address bar focused)
- `chrome://omnibox-popup.top-chrome/omnibox_popup_aim.html` — **not blocking** (permanent AI Mode background target)

`connect_chrome()` in `browser_agent.py` detects only the blocking URL and attempts dismissal via `/json/close` before connecting. If the CDP session becomes stale (same session ID across repeated failures), restart Chrome:

```powershell
Stop-Process -Name chrome -ErrorAction SilentlyContinue
python launch_chrome_cdp.py
```

---

## Known Limitations (Current Gaps)

### Gap 1 — Native Windows file dialogs
`upload_file` handles `<input type="file">` elements cleanly. But some sites open a native Windows Save/Open dialog that Playwright cannot interact with (it's outside the browser process).

**Workaround:** Most modern sites use `<input type="file">` — use `upload_file`. For native dialogs, manual interaction is required.

**Planned fix:** `pywin32` or `pyautogui` to find and interact with the Windows dialog handle.

---

## Planned Upgrades

| Priority | Tool/Feature | Description | Status |
|---|---|---|---|
| 1 | `click_at(x, y)` | Coordinate clicking from vision | ✅ Done |
| 2 | `wait_for_network_idle()` | Wait for SPA API calls to complete | ✅ Done |
| 3 | `wait_for_response(url_pattern)` | Wait for specific API response | ✅ Done |
| 4 | `download_file` | Trigger and save file downloads | ✅ Done |
| 5 | `upload_file` | Upload files to `<input type="file">` | ✅ Done |
| 6 | `extract(description, schema?)` | Structured JSON extraction via Gemini vision | ✅ Done |
| 7 | Native Windows file dialog | Handle OS-level Save/Open dialogs | Planned |

---

## What NOT to Do

- Do not modify `PROJECT`, `MODEL`, or `LOCATION` constants without updating the memory system — domain lookups depend on consistent URL patterns
- Do not add a new tool without adding both the `FunctionDeclaration` in `TOOL_DECLARATIONS` and the `elif` branch in `run_tool()`
- Do not write to `sites.json`, `tasks.json`, or `frameworks.json` directly — always use the functions in `memory/memory.py` which deduplicate and preserve existing data
- Do not change the memory JSON schema without updating `build_context()`, `update_site()`, and `_flush_memory()` together
- The `session` dict in `agent_loop()` is the only mutable state shared between `agent_loop` and `run_tool` — do not pass additional mutable state through `ctx` or `page`

---

## Adding a New Tool (Checklist)

1. Add `types.FunctionDeclaration(name=..., description=..., parameters=...)` to `TOOL_DECLARATIONS` list in `browser_agent.py`
2. Add `elif name == "your_tool":` branch in `run_tool()` before `return f"Unknown tool: {name}"`
3. Add a row to the Tool Inventory section of this file
4. If the tool interacts with the browser in a new way, test it with `test_agent.py` before committing
5. Update the README.md tool table
