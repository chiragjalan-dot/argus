"""
Browser agent — connects to your existing Chrome via CDP.
Uses Gemini 2.5 Flash with full DOM access: no coordinate guessing, no phantom browser.

Workflow:
  1. python launch_chrome_cdp.py     (once per session)
  2. python browser_agent.py         (interactive)
  or:
  2. python test_agent.py            (single task test)
"""

import asyncio
import base64
import json
import sys
import os
from urllib.request import urlopen
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from google import genai
from google.genai import types
from playwright.async_api import async_playwright
from memory.memory import build_context, update_site, update_framework, save_task_pattern, domain_of

CDP_URL  = "http://localhost:9222"
MODEL    = "gemini-2.5-flash"
PROJECT  = "project-7107f92a-86d1-4161-a11"
LOCATION = "us-central1"


# ── Chrome CDP guard ─────────────────────────────────────────────────────────

def _get_targets(cdp_url=CDP_URL):
    http_base = cdp_url.replace("ws://", "http://")
    try:
        return json.loads(urlopen(f"{http_base}/json/list", timeout=3).read())
    except Exception:
        return []

def _omnibox_open(cdp_url=CDP_URL):
    # Only the base omnibox-popup.top-chrome/ blocks CDP — the aim.html variant is a permanent
    # Chrome 149 AI Mode background target and does NOT block connections.
    return any(
        t.get("url", "") == "chrome://omnibox-popup.top-chrome/"
        for t in _get_targets(cdp_url)
    )

async def _dismiss_omnibox(cdp_url=CDP_URL):
    """
    Two-strategy dismissal:
    1. HTTP /json/close on each Omnibox target (no focus needed)
    2. WScript.Shell AppActivate + SendKeys Escape (brings Chrome to front, sends key)
    """
    http_base = cdp_url.replace("ws://", "http://")
    targets   = _get_targets(cdp_url)

    # Strategy 1: try /json/close on each Omnibox target
    for t in targets:
        if "Omnibox" in t.get("title", ""):
            try:
                urlopen(f"{http_base}/json/close/{t['id']}", timeout=2)
            except Exception:
                pass

    await asyncio.sleep(0.4)
    if not _omnibox_open(cdp_url):
        return  # Strategy 1 worked

    # Strategy 2: AppActivate the GCP page by title, then SendKeys Escape
    page_title = next(
        (t["title"] for t in targets if t.get("type") == "page" and "google" in t.get("url", "")),
        "Google Cloud",
    )
    # Escape single quotes for the PowerShell string
    safe_title = page_title.replace("'", "''")[:60]
    ps = (
        f"$w = New-Object -ComObject wscript.shell;"
        f"if ($w.AppActivate('{safe_title}')) {{"
        f"  Start-Sleep -Milliseconds 400;"
        f"  $w.SendKeys('{{ESC}}');"
        f"}} else {{"
        f"  $w.AppActivate('Chrome');"
        f"  Start-Sleep -Milliseconds 400;"
        f"  $w.SendKeys('{{ESC}}');"
        f"}}"
    )
    proc = await asyncio.create_subprocess_exec(
        "powershell", "-Command", ps,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.wait()
    await asyncio.sleep(0.6)

async def connect_chrome(p, cdp_url=CDP_URL, retries=3):
    """Connect to Chrome CDP with automatic Omnibox Popup detection and dismissal."""
    for attempt in range(retries):
        if _omnibox_open(cdp_url):
            print(f"[CDP] Omnibox popup detected (attempt {attempt+1}) — dismissing...")
            await _dismiss_omnibox(cdp_url)
            if _omnibox_open(cdp_url):
                print("[CDP] Omnibox still present — will attempt connect anyway")

        try:
            browser = await p.chromium.connect_over_cdp(cdp_url, timeout=15000)
            return browser
        except Exception as e:
            if attempt < retries - 1:
                print(f"[CDP] Connect failed ({e.__class__.__name__}), retrying...")
                await asyncio.sleep(1)
            else:
                raise


# ── Tool definitions ─────────────────────────────────────────────────────────

TOOL_DECLARATIONS = [
    types.FunctionDeclaration(
        name="list_tabs",
        description="List all open tabs in Chrome. Returns index, title, URL.",
        parameters={"type": "object", "properties": {}, "required": []},
    ),
    types.FunctionDeclaration(
        name="go_to_tab",
        description="Switch to a tab by its index number from list_tabs.",
        parameters={"type": "object", "properties": {
            "index": {"type": "integer", "description": "Tab index"},
        }, "required": ["index"]},
    ),
    types.FunctionDeclaration(
        name="navigate",
        description="Navigate the active tab to a URL.",
        parameters={"type": "object", "properties": {
            "url": {"type": "string"},
        }, "required": ["url"]},
    ),
    types.FunctionDeclaration(
        name="screenshot",
        description="Take a screenshot of the active tab to see its current state.",
        parameters={"type": "object", "properties": {}, "required": []},
    ),
    types.FunctionDeclaration(
        name="read_page",
        description="Read the visible text content of the active tab (up to 3000 chars).",
        parameters={"type": "object", "properties": {}, "required": []},
    ),
    types.FunctionDeclaration(
        name="click",
        description="Click an element. Use selector (CSS) or text (visible label).",
        parameters={"type": "object", "properties": {
            "selector": {"type": "string", "description": "CSS selector"},
            "text":     {"type": "string", "description": "Click element whose text contains this"},
        }},
    ),
    types.FunctionDeclaration(
        name="type_text",
        description="Type text into an input field. Optionally clear it first.",
        parameters={"type": "object", "properties": {
            "selector":    {"type": "string", "description": "CSS selector of the input"},
            "text":        {"type": "string"},
            "clear_first": {"type": "boolean"},
        }, "required": ["text"]},
    ),
    types.FunctionDeclaration(
        name="press_key",
        description="Press a keyboard key, e.g. Enter, Escape, Tab.",
        parameters={"type": "object", "properties": {
            "key": {"type": "string"},
        }, "required": ["key"]},
    ),
    types.FunctionDeclaration(
        name="get_element_text",
        description="Get text content of elements matching a CSS selector.",
        parameters={"type": "object", "properties": {
            "selector": {"type": "string"},
        }, "required": ["selector"]},
    ),
    types.FunctionDeclaration(
        name="wait_for",
        description="Wait for an element to appear on the page.",
        parameters={"type": "object", "properties": {
            "selector":   {"type": "string"},
            "timeout_ms": {"type": "integer"},
        }, "required": ["selector"]},
    ),
    types.FunctionDeclaration(
        name="scroll",
        description="Scroll the page up or down by a number of pixels (default 400).",
        parameters={"type": "object", "properties": {
            "direction": {"type": "string", "description": "up or down"},
            "pixels":    {"type": "integer", "description": "How many pixels to scroll (default 400)"},
        }, "required": ["direction"]},
    ),
    types.FunctionDeclaration(
        name="fill_by_label",
        description="Fill an input field found by its visible label or placeholder text. More reliable than CSS selectors on Angular/GCP pages.",
        parameters={"type": "object", "properties": {
            "label": {"type": "string", "description": "Visible label or placeholder text of the input"},
            "value": {"type": "string", "description": "Text to enter"},
        }, "required": ["label", "value"]},
    ),
    types.FunctionDeclaration(
        name="run_js",
        description="Run JavaScript on the page and return the result. Use for Angular Material components, virtual scroll, or any element that standard tools cannot reach.",
        parameters={"type": "object", "properties": {
            "code": {"type": "string", "description": "JavaScript expression or function body to execute"},
        }, "required": ["code"]},
    ),
    types.FunctionDeclaration(
        name="wait_seconds",
        description="Pause for a fixed number of seconds (max 10) to let animations or async loads complete.",
        parameters={"type": "object", "properties": {
            "seconds": {"type": "number"},
        }, "required": ["seconds"]},
    ),
    types.FunctionDeclaration(
        name="hover",
        description="Hover over an element to reveal tooltips, dropdown arrows, or context menus.",
        parameters={"type": "object", "properties": {
            "selector": {"type": "string", "description": "CSS selector of element to hover"},
            "text":     {"type": "string", "description": "Hover element whose text contains this"},
        }},
    ),
    types.FunctionDeclaration(
        name="dismiss_overlays",
        description=(
            "Force-remove modal overlays, login walls, cookie banners, and popup friction via JS. "
            "Use this when close buttons fail or can't be found — it nukes the overlay from the DOM entirely. "
            "Also restores page scroll if the modal locked it."
        ),
        parameters={"type": "object", "properties": {}},
    ),
    types.FunctionDeclaration(
        name="save_observation",
        description=(
            "Save a useful observation about this site or task to persistent memory. "
            "Call this whenever you discover something reusable: a tricky interaction pattern, "
            "a JS workaround that worked, a selector that's reliable, or a pitfall to avoid. "
            "Do NOT wait until the end — save as soon as you learn something."
        ),
        parameters={"type": "object", "properties": {
            "note":     {"type": "string", "description": "The observation to remember"},
            "category": {"type": "string", "description": "One of: interaction, pitfall, friction, failure"},
        }, "required": ["note", "category"]},
    ),
]

GEMINI_TOOLS = types.Tool(function_declarations=TOOL_DECLARATIONS)


# ── Tool execution ───────────────────────────────────────────────────────────

async def run_tool(name, inputs, page, ctx, session):
    try:
        if name == "list_tabs":
            lines = []
            for i, p in enumerate(ctx.pages):
                lines.append(f"[{i}] {await p.title()!r}  {p.url}")
            return "\n".join(lines) or "No tabs."

        elif name == "go_to_tab":
            pages = ctx.pages
            idx = int(inputs["index"])
            if idx >= len(pages):
                return f"Error: only {len(pages)} tabs exist."
            await pages[idx].bring_to_front()
            return f"Switched to tab {idx}: {await pages[idx].title()!r}"

        elif name == "navigate":
            await page.goto(inputs["url"], wait_until="domcontentloaded", timeout=15000)
            return f"Navigated to {page.url}  title={await page.title()!r}"

        elif name == "screenshot":
            png = await page.screenshot(type="png")
            return {"type": "image", "data": base64.b64encode(png).decode(), "media_type": "image/png"}

        elif name == "read_page":
            text = await page.inner_text("body")
            return text[:3000] + ("...[truncated]" if len(text) > 3000 else "")

        elif name == "click":
            if inputs.get("selector"):
                await page.click(inputs["selector"], timeout=5000)
                return f"Clicked {inputs['selector']}"
            elif inputs.get("text"):
                await page.get_by_text(inputs["text"]).first.click(timeout=5000)
                return f"Clicked element with text {inputs['text']!r}"
            return "Error: provide selector or text."

        elif name == "type_text":
            sel  = inputs.get("selector", "")
            text = inputs["text"]
            if sel:
                if inputs.get("clear_first", True):
                    await page.fill(sel, text, timeout=5000)
                else:
                    await page.type(sel, text, timeout=5000)
            else:
                await page.keyboard.type(text)
            return f"Typed {text!r}" + (f" into {sel}" if sel else "")

        elif name == "press_key":
            await page.keyboard.press(inputs["key"])
            return f"Pressed {inputs['key']}"

        elif name == "get_element_text":
            els = await page.query_selector_all(inputs["selector"])
            texts = [await el.inner_text() for el in els[:10]]
            return json.dumps(texts, ensure_ascii=False)

        elif name == "wait_for":
            await page.wait_for_selector(inputs["selector"], timeout=inputs.get("timeout_ms", 5000))
            return f"Element {inputs['selector']!r} found."

        elif name == "scroll":
            px = int(inputs.get("pixels", 400))
            delta = px if inputs["direction"] == "down" else -px
            await page.evaluate(f"window.scrollBy(0, {delta})")
            return f"Scrolled {inputs['direction']} {px}px"

        elif name == "fill_by_label":
            label = inputs["label"]
            value = inputs["value"]
            # Try Playwright label/placeholder first, then JS fallback
            for loc in (page.get_by_label(label, exact=False),
                        page.get_by_placeholder(label, exact=False)):
                try:
                    if await loc.count() > 0:
                        await loc.first.fill(value)
                        return f"Filled '{label}' = '{value}'"
                except Exception:
                    pass
            filled = await page.evaluate("""([lbl, val]) => {
                for (var inp of document.querySelectorAll('input:not([type=hidden]),textarea')) {
                    var l = (inp.labels && inp.labels[0] ? inp.labels[0].innerText :
                             inp.getAttribute('aria-label') || inp.placeholder || inp.name || '').trim();
                    if (l.toLowerCase().includes(lbl.toLowerCase())) {
                        inp.focus(); inp.value = val;
                        ['input','change'].forEach(e => inp.dispatchEvent(new Event(e,{bubbles:true})));
                        return l;
                    }
                }
                return null;
            }""", [label, value])
            if not filled:
                return f"Error: no input found with label/placeholder '{label}'"
            return f"Filled (js) '{label}' = '{value}'"

        elif name == "run_js":
            result = await page.evaluate(inputs["code"])
            return str(result)[:500] if result is not None else "null"

        elif name == "wait_seconds":
            secs = min(float(inputs.get("seconds", 2)), 10)
            await asyncio.sleep(secs)
            return f"Waited {secs}s"

        elif name == "hover":
            if inputs.get("selector"):
                await page.hover(inputs["selector"], timeout=5000)
                return f"Hovered {inputs['selector']}"
            elif inputs.get("text"):
                await page.get_by_text(inputs["text"]).first.hover(timeout=5000)
                return f"Hovered element with text {inputs['text']!r}"
            return "Error: provide selector or text."

        elif name == "dismiss_overlays":
            removed = await page.evaluate("""() => {
                let count = 0;
                const selectors = [
                    '[role="dialog"]',
                    '[class*="modal"]',
                    '[class*="overlay"]',
                    '[class*="popup"]',
                    '[class*="banner"]',
                    '[id*="modal"]',
                    '[id*="overlay"]',
                    '[class*="cookie"]',
                    '[class*="consent"]',
                    '[class*="paywall"]',
                    '[class*="gate"]',
                    'artdeco-modal',
                ];
                selectors.forEach(sel => {
                    document.querySelectorAll(sel).forEach(el => {
                        el.remove(); count++;
                    });
                });
                // Unlock scroll if modal froze the page
                document.body.style.overflow = '';
                document.documentElement.style.overflow = '';
                document.body.style.position = '';
                return count;
            }""")
            return f"Removed {removed} overlay element(s) and restored scroll."

        elif name == "save_observation":
            note     = inputs.get("note", "")
            category = inputs.get("category", "interaction")
            session.setdefault("notes", []).append({"note": note, "category": category})
            return f"Observation saved ({category}): {note[:80]}"

        return f"Unknown tool: {name}"

    except Exception as e:
        return f"Tool error ({name}): {e}"


# ── Post-task memory flush ────────────────────────────────────────────────────

async def _flush_memory(client, domain, task, session, success):
    """Ask Gemini to extract structured learnings from the session log, write to disk."""
    notes   = session.get("notes", [])
    calls   = session.get("calls", [])
    if not calls and not notes:
        return

    calls_text = "\n".join(f"  {c}" for c in calls[-40:])  # last 40 tool calls
    notes_text = "\n".join(f"  [{n['category']}] {n['note']}" for n in notes)

    prompt = f"""You are analyzing a browser automation session to extract reusable memory.

Domain: {domain}
Task: {task}
Success: {success}

Tool calls made (in order):
{calls_text or '  (none)'}

Agent observations saved during session:
{notes_text or '  (none)'}

Extract a JSON object with exactly these fields:
{{
  "framework": "e.g. Angular Material, React, vanilla HTML, or null if unknown",
  "framework_notes": ["tips that apply to ANY site built with this framework, not just this domain — e.g. Angular Material CDK overlay patterns, React hydration quirks"],
  "site_notes": ["tips specific to THIS domain only — e.g. GCP-specific URL param state, this site's auth flow"],
  "pitfalls": ["things that went wrong or should be avoided on this site"],
  "known_failures": ["approaches that definitively do not work on this site"],
  "friction": ["bot-detection or auth friction observed"],
  "task_type": "short snake_case label for this type of task, e.g. quota_edit",
  "keywords": ["2-5 keywords a future task description would contain to match this pattern"],
  "steps": [
    {{"description": "plain English step that was taken"}}
  ]
}}

Return ONLY the JSON. No prose."""

    try:
        resp = client.models.generate_content(
            model=MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
            ),
        )
        raw = resp.text.strip()
        data = json.loads(raw)
    except Exception as e:
        print(f"[Memory] Summarisation failed: {e}")
        # Fall back: flush raw notes only, all treated as site-specific
        interactions = [n["note"] for n in notes if n["category"] == "interaction"]
        failures     = [n["note"] for n in notes if n["category"] == "failure"]
        update_site(domain, interactions,
                    friction=[n["note"] for n in notes if n["category"] == "friction"],
                    known_failures=failures)
        return

    framework = data.get("framework") or None

    # Framework-level notes go to the shared pool
    if framework and data.get("framework_notes"):
        update_framework(framework, data["framework_notes"])

    # Site-specific notes go to the domain profile
    update_site(
        domain,
        observations   = data.get("site_notes", []),
        framework      = framework,
        friction       = data.get("friction", []),
        known_failures = data.get("known_failures", []),
    )

    if data.get("task_type") and data.get("steps"):
        save_task_pattern(
            domain    = domain,
            task_type = data["task_type"],
            keywords  = data.get("keywords", []),
            steps     = data["steps"],
            pitfalls  = data.get("pitfalls", []),
            success   = success,
        )
        print(f"[Memory] Pattern '{data['task_type']}' saved for {domain}")

    print(f"[Memory] Site profile updated for {domain}")


# ── Agent loop ───────────────────────────────────────────────────────────────

async def agent_loop(task: str, page, ctx):
    client = genai.Client(vertexai=True, project=PROJECT, location=LOCATION)

    # Load memory context for this domain
    domain      = domain_of(page.url)
    mem_context = build_context(domain, task)
    sys_prefix  = f"{mem_context}\n\n" if mem_context else ""
    if mem_context:
        print(f"[Memory] Loaded context for {domain}")

    config = types.GenerateContentConfig(
        system_instruction=(
            sys_prefix +
            "You are a browser automation agent with full access to the user's real Chrome browser. "
            "Use CSS selectors to click and type — not pixel coordinates. "
            "Always take a screenshot first to see the current page state. "
            "When you discover a useful interaction pattern, pitfall, or workaround, "
            "call save_observation immediately — do not wait until the end. "
            "Think step by step. When done, say DONE and summarise what happened."
        ),
        tools=[GEMINI_TOOLS],
    )

    print(f"\n{'-'*60}")
    print(f"Task: {task}")
    print(f"{'-'*60}\n")

    session  = {"notes": [], "calls": []}
    success  = False

    chat     = client.chats.create(model=MODEL, config=config)
    response = chat.send_message(task)

    step = 0
    while True:
        fn_calls = [
            part.function_call
            for part in response.candidates[0].content.parts
            if part.function_call
        ]
        text_parts = [
            part.text
            for part in response.candidates[0].content.parts
            if hasattr(part, "text") and part.text
        ]

        if text_parts:
            combined = " ".join(text_parts).strip()
            if combined:
                print(f"\n[Agent] {combined}")
                session["calls"].append(f"[agent] {combined[:200]}")

        if not fn_calls:
            # Agent gave a final text response with no more tool calls = task done
            success = True
            break

        step += 1
        tool_responses = []
        img_data = None

        for fc in fn_calls:
            inputs = dict(fc.args) if fc.args else {}
            print(f"[Step {step}] {fc.name}({json.dumps(inputs)[:80]})")
            result = await run_tool(fc.name, inputs, page, ctx, session)

            if isinstance(result, dict) and result.get("type") == "image":
                print(f"         -> [screenshot]")
                session["calls"].append(f"screenshot() -> [image captured, url={page.url}]")
                tool_responses.append(
                    types.Part.from_function_response(
                        name=fc.name,
                        response={"result": "Screenshot taken. See image."},
                    )
                )
                img_data = result["data"]
            else:
                preview = str(result)[:120].replace("\n", " ")
                print(f"         -> {preview}")
                session["calls"].append(f"{fc.name}({json.dumps(inputs)[:60]}) -> {preview}")
                tool_responses.append(
                    types.Part.from_function_response(
                        name=fc.name,
                        response={"result": str(result)},
                    )
                )

        if img_data:
            response = chat.send_message([
                *tool_responses,
                types.Part.from_bytes(
                    data=base64.b64decode(img_data),
                    mime_type="image/png",
                ),
            ])
        else:
            response = chat.send_message(tool_responses)

    # Use the page's current URL at flush time — may differ from starting domain
    await _flush_memory(client, domain_of(page.url), task, session, success)


# ── Interactive entry point ──────────────────────────────────────────────────

async def main():
    async with async_playwright() as p:
        try:
            browser = await connect_chrome(p)
        except Exception as e:
            print(f"Cannot connect to Chrome at {CDP_URL}: {e}")
            print("Run: python launch_chrome_cdp.py")
            return

        ctx   = browser.contexts[0]
        pages = [pg for pg in ctx.pages if pg.url != "about:blank"] or ctx.pages
        page  = pages[0]
        await page.bring_to_front()
        print(f"Connected. Active: {page.title()!r}  {page.url}")
        print("Type task (or 'quit'):\n")

        while True:
            task = input("Task > ").strip()
            if task.lower() in ("quit", "exit", "q"):
                break
            if task:
                await agent_loop(task, page, ctx)

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
