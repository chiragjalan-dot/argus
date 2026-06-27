"""
Persistent memory for the browser agent.

Two stores:
  sites.json  — domain-keyed site profiles (framework, interaction notes, friction)
  tasks.json  — domain-keyed task patterns with replay steps and confidence

Confidence scale:
  0.5  = first successful run (treat as hint, not gospel)
  +0.1 per additional success, capped at 1.0
  >=0.7 = shown as a replay sequence to follow
  <0.7  = shown as "previous attempt notes" only
"""

import json, re
from datetime import date
from pathlib import Path

def _today(): return str(date.today())

_DIR       = Path(__file__).parent
SITES      = _DIR / "sites.json"
TASKS      = _DIR / "tasks.json"
FRAMEWORKS = _DIR / "frameworks.json"


# ── I/O ──────────────────────────────────────────────────────────────────────

def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}

def _save(path: Path, data: dict):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


# ── Domain extraction ─────────────────────────────────────────────────────────

def domain_of(url: str) -> str:
    m = re.match(r"https?://([^/?#]+)", url)
    return m.group(1) if m else url


# ── Site profile ──────────────────────────────────────────────────────────────

def get_site(domain: str) -> dict:
    return _load(SITES).get(domain, {})

def update_site(domain: str, observations: list, framework: str = None,
                friction: list = None, known_failures: list = None):
    """Merge new observations into the site profile. Deduplicates automatically."""
    data = _load(SITES)
    site = data.get(domain, {
        "framework": None,
        "interaction_notes": [],
        "friction": [],
        "known_failures": [],
        "last_updated": None,
    })

    if framework:
        site["framework"] = framework

    for field, incoming in [
        ("interaction_notes", observations or []),
        ("friction",         friction or []),
        ("known_failures",   known_failures or []),
    ]:
        existing = set(site.get(field, []))
        for item in incoming:
            if item and item not in existing:
                site.setdefault(field, []).append(item)
                existing.add(item)

    site["last_updated"] = _today()
    data[domain] = site
    _save(SITES, data)


# ── Framework pool ───────────────────────────────────────────────────────────

def get_framework(name: str) -> dict:
    return _load(FRAMEWORKS).get(name, {})

def update_framework(name: str, notes: list):
    """Merge notes into the shared framework pool. Deduplicates automatically."""
    if not name or not notes:
        return
    data = _load(FRAMEWORKS)
    fw   = data.get(name, {"interaction_notes": [], "last_updated": None})
    existing = set(fw.get("interaction_notes", []))
    for note in notes:
        if note and note not in existing:
            fw["interaction_notes"].append(note)
            existing.add(note)
    fw["last_updated"] = _today()
    data[name] = fw
    _save(FRAMEWORKS, data)


# ── Escalation table ──────────────────────────────────────────────────────────

def get_escalation_hint(framework: str, failed_tool: str) -> str:
    """Return a hint string for what to try next after a tool fails on a given framework."""
    if not framework:
        return ""
    esc = _load(FRAMEWORKS).get(framework, {}).get("escalation", {})
    nexts = esc.get(f"{failed_tool}_failed", [])
    if nexts:
        return f"On {framework}: after {failed_tool} fails, try: {' → '.join(nexts)}"
    return ""

def save_escalation(framework: str, failed_tool: str, worked_tool: str):
    """Record that worked_tool succeeded after failed_tool failed on this framework."""
    if not framework or not failed_tool or not worked_tool or failed_tool == worked_tool:
        return
    data = _load(FRAMEWORKS)
    fw   = data.setdefault(framework, {})
    esc  = fw.setdefault("escalation", {})
    key  = f"{failed_tool}_failed"
    lst  = esc.get(key, [])
    if worked_tool not in lst:
        lst.insert(0, worked_tool)
    esc[key] = lst[:5]
    fw["last_updated"] = _today()
    data[framework] = fw
    _save(FRAMEWORKS, data)


# ── Task patterns ─────────────────────────────────────────────────────────────

def get_task_pattern(domain: str, task_hint: str) -> dict | None:
    """Return the best-matching task pattern, or None."""
    patterns = _load(TASKS).get(domain, [])
    hint_lower = task_hint.lower()
    scored = []
    for p in patterns:
        kw_hits = sum(1 for kw in p.get("keywords", []) if kw in hint_lower)
        if kw_hits > 0:
            scored.append((kw_hits * p.get("confidence", 0), p))
    if not scored:
        return None
    return max(scored, key=lambda x: x[0])[1]

def save_task_pattern(domain: str, task_type: str, keywords: list,
                      steps: list, pitfalls: list, success: bool = True):
    """
    Upsert a task pattern.
    steps: list of dicts {"description": str, "tool": str (optional), "params": dict (optional)}
    pitfalls: list of strings describing what to avoid
    """
    data  = _load(TASKS)
    patterns = data.get(domain, [])

    for p in patterns:
        if p.get("task_type") == task_type:
            if success:
                p["confidence"] = min(1.0, p.get("confidence", 0.5) + 0.1)
                p["uses"]       = p.get("uses", 0) + 1
                p["steps"]      = steps    # update with latest successful sequence
                p["pitfalls"]   = pitfalls
            else:
                p["confidence"] = max(0.1, p.get("confidence", 0.5) - 0.1)
                for pit in pitfalls:
                    if pit not in p.get("pitfalls", []):
                        p.setdefault("pitfalls", []).append(pit)
            p["last_used"] = str(date.today())
            data[domain]   = patterns
            _save(TASKS, data)
            return

    # New pattern
    patterns.append({
        "task_type":  task_type,
        "keywords":   keywords,
        "steps":      steps,
        "pitfalls":   pitfalls,
        "confidence": 0.5 if success else 0.1,
        "uses":       1 if success else 0,
        "last_used":  str(date.today()),
    })
    data[domain] = patterns
    _save(TASKS, data)


# ── Context builder ───────────────────────────────────────────────────────────

def build_context(domain: str, task: str) -> str:
    """
    Return a block of text to prepend to the agent's system prompt.
    Empty string if nothing is known yet.
    """
    site    = get_site(domain)
    pattern = get_task_pattern(domain, task)
    lines   = []

    # Framework-level notes (shared across all sites with the same framework)
    fw_name = site.get("framework") if site else None
    if fw_name:
        fw = get_framework(fw_name)
        has_content = fw.get("interaction_notes") or fw.get("escalation")
        if has_content:
            lines.append(f"=== FRAMEWORK: {fw_name} (applies to all sites using this framework) ===")
            for n in fw.get("interaction_notes", []):
                lines.append(f"  - {n}")
            if fw.get("escalation"):
                lines.append("  Escalation table (learned from past failures — follow exactly):")
                for key, nexts in fw["escalation"].items():
                    tool = key.replace("_failed", "")
                    lines.append(f"    After {tool} fails → try: {' → '.join(nexts)}")

    # Site-specific notes
    if site:
        lines.append(f"\n=== SITE: {domain} ===")
        if site.get("interaction_notes"):
            lines.append("Site-specific notes:")
            for n in site["interaction_notes"]:
                lines.append(f"  - {n}")
        if site.get("known_failures"):
            lines.append("Known failures (do NOT try these):")
            for f in site["known_failures"]:
                lines.append(f"  - {f}")
        if site.get("friction"):
            lines.append("Bot friction observed:")
            for f in site["friction"]:
                lines.append(f"  - {f}")

    if pattern:
        conf = pattern.get("confidence", 0)
        lines.append(f"\n=== TASK PATTERN: {pattern['task_type']} (confidence {conf:.0%}) ===")
        if pattern.get("pitfalls"):
            lines.append("Pitfalls to avoid:")
            for p in pattern["pitfalls"]:
                lines.append(f"  - {p}")
        if pattern.get("steps"):
            if conf >= 0.7:
                lines.append("Proven step sequence — follow this order:")
            else:
                lines.append("Previous attempt sequence (confidence low — use as a hint only):")
            for i, s in enumerate(pattern["steps"], 1):
                lines.append(f"  {i}. {s['description']}")

    return "\n".join(lines)
