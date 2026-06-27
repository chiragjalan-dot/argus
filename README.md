# Argus — Browser Automation Agent

A vision-first browser agent that connects to your **real, already-open Chrome session** via CDP. Uses Gemini 2.5 Flash multimodal for perception and decision-making. Learns from every task and builds persistent memory across sessions.

---

## What Makes Argus Different

| Feature | Argus | Typical agents |
|---|---|---|
| Browser session | Your real logged-in Chrome | Fresh isolated browser |
| Cross-session memory | Site profiles, task patterns, framework pool | None or basic |
| Framework awareness | Learns Angular Material, React, etc. across all sites | Per-run only |
| Authentication | Your real cookies — no re-login | Manual login each run |
| LLM backend | Gemini 2.5 Flash on Vertex AI (GCP credits) | Various |

---

## Quick Start

```powershell
# 1. Install dependencies
.\venv\Scripts\pip.exe install google-genai playwright
.\venv\Scripts\playwright.exe install chromium

# 2. Launch Chrome with your real profile (first run copies profile ~30s)
.\venv\Scripts\python.exe launch_chrome_cdp.py

# 3. Run the agent
.\venv\Scripts\python.exe browser_agent.py
```

## Requirements

- Python 3.12+
- GCP project with Vertex AI enabled (`gemini-2.5-flash` access)
- Application Default Credentials: `gcloud auth application-default login`
- Google Chrome installed

---

## Architecture

```
Task input
    ↓
Memory lookup (domain + task → inject site profile + task pattern)
    ↓
Gemini 2.5 Flash (vision + function calling)
    ↓
Tool execution (Playwright CDP)
    ↓ (loop until no more tool calls)
Post-task summarisation (Gemini extracts learnings)
    ↓
Memory write (sites.json, tasks.json, frameworks.json)
```

---

## Tool Inventory

See [AGENT_CONTEXT.md](AGENT_CONTEXT.md) for complete tool signatures and usage guidance.

| Tool | Purpose |
|---|---|
| `screenshot` | Capture current page as image |
| `read_page` | Read visible text content |
| `navigate` | Go to URL |
| `click` | Click by CSS selector or visible text |
| `type_text` | Type into an input field |
| `fill_by_label` | Fill input by label/placeholder (Angular-safe) |
| `press_key` | Send keyboard key |
| `scroll` | Scroll up or down |
| `hover` | Hover to reveal dropdowns/tooltips |
| `run_js` | Execute arbitrary JavaScript |
| `get_element_text` | Get text from matched elements |
| `wait_for` | Wait for element to appear |
| `wait_seconds` | Fixed pause for animations |
| `handle_file_dialog` | Handle native Windows Save/Open file dialogs |
| `dismiss_overlays` | Force-remove modals, cookie banners, login walls via JS |
| `click_at` | Click at pixel coordinates — bypasses DOM entirely |
| `wait_for_network_idle` | Wait until SPA has no pending requests |
| `wait_for_response` | Wait for a specific API response URL |
| `download_file` | Trigger and save a file download |
| `upload_file` | Upload a file to `<input type="file">` |
| `extract` | Extract structured JSON from page via Gemini vision |
| `list_tabs` | List all open tabs |
| `go_to_tab` | Switch to tab by index |
| `save_observation` | Save reusable note to persistent memory |

---

## Memory System

Three JSON stores, all in `memory/`:

- **`sites.json`** — per-domain profiles: framework, interaction notes, known failures, friction
- **`tasks.json`** — per-domain task patterns: step sequences, keywords, confidence score (0.5→1.0)
- **`frameworks.json`** — shared framework pool: Angular Material patterns apply to *all* Angular sites

After every task, Gemini summarises what happened and writes structured learnings to all three stores. Next time the agent visits the same site, it gets that knowledge injected into its system prompt before it starts.

---

## Planned Upgrades

| # | Feature | Status |
|---|---|---|
| 1 | `click_at(x, y)` — coordinate clicking from vision | ✅ Done |
| 2 | `wait_for_network_idle` / `wait_for_response` | ✅ Done |
| 3 | `download_file` / `upload_file` | ✅ Done |
| 4 | `extract(description, schema?)` — structured JSON | ✅ Done |
| 5 | `handle_file_dialog` — native Windows Save/Open dialog | ✅ Done |

---

## File Structure

```
argus/
  browser_agent.py          ← core agent loop, all tools, memory integration
  launch_chrome_cdp.py      ← Chrome launcher with real profile copy
  test_agent.py             ← single-task test runner
  memory/
    memory.py               ← read/write for all three stores
    sites.json              ← auto-generated, gitignored
    tasks.json              ← auto-generated, gitignored
    frameworks.json         ← auto-generated, gitignored
  AGENT_CONTEXT.md          ← complete context doc for AI sessions
  README.md                 ← this file
```
