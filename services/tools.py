"""Tool/function definitions the model can call during an agentic chat turn.

Each tool has:
  - a JSON-schema description (TOOL_DEFINITIONS) sent to the model, and
  - a python implementation in TOOL_REGISTRY (name -> callable(user, **args) -> str).

To add a new "skill", just append a definition here + a function — the model
will then be able to use it automatically, no new Telegram command needed.
"""
from services.router_client import web_search, web_fetch

try:
    from services.mcp_client import MCP_TOOL_DEFINITIONS, MCP_TOOL_REGISTRY
except Exception:
    MCP_TOOL_DEFINITIONS, MCP_TOOL_REGISTRY = [], {}


def _parse_skill_file(path: str, fallback_name: str) -> dict | None:
    """Parse a skill md file. Supports YAML frontmatter (name:/description:).
    Returns {name, file, instructions, description} or None on failure."""
    import os
    try:
        with open(path, "r", encoding="utf-8") as f:
            txt = f.read()
    except Exception:
        return None
    name, desc = "", ""
    body = txt
    if txt.startswith("---"):
        end = txt.find("---", 3)
        if end != -1:
            fm = txt[3:end]
            body = txt[end + 3:].strip()
            for line in fm.splitlines():
                if ":" in line:
                    k, _, v = line.partition(":")
                    k = k.strip().lower()
                    v = v.strip().strip("'\"")
                    if k == "name":
                        name = v
                    elif k == "description":
                        desc = v
    if not name:
        for line in body.splitlines():
            s = line.strip()
            if s.startswith("#"):
                t = s.lstrip("# ").strip()
                if t and t.lower() not in ("skill", "skills"):
                    name = t
                break
    if not name:
        name = fallback_name
    return {"name": name, "file": os.path.relpath(path),
            "instructions": txt, "description": desc}


def load_skill_files() -> list[dict]:
    """Load skills from skills/ dir. Supports both layouts:
    - skills/<name>.md (top-level file)
    - skills/<folder>/SKILL.md (folder convention, name: from frontmatter or folder)
    """
    import os
    out = []
    d = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "skills")
    if not os.path.isdir(d):
        return out
    for fn in sorted(os.listdir(d)):
        full = os.path.join(d, fn)
        if os.path.isfile(full) and fn.endswith(".md") and fn != "README.md":
            s = _parse_skill_file(full, fn[:-3])
            if s:
                out.append(s)
    for fn in sorted(os.listdir(d)):
        full = os.path.join(d, fn)
        if not os.path.isdir(full) or fn.startswith((".", "_")):
            continue
        for cand in ("SKILL.md", "skill.md", f"{fn}.md"):
            p = os.path.join(full, cand)
            if os.path.isfile(p):
                s = _parse_skill_file(p, fn)
                if s:
                    out.append(s)
                break
    # dedupe by name, keep first
    seen, uniq = set(), []
    for s in out:
        if s["name"] in seen:
            continue
        seen.add(s["name"])
        uniq.append(s)
    return uniq


TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Search the web for up-to-date information, news, or any query. "
                "Returns a list of results with titles, URLs, and snippets."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The search query."},
                    "max_results": {
                        "type": "integer",
                        "description": "Number of results to return (default 5).",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": (
                "Fetch the content of a web page URL and return it as markdown text. "
                "Use to read articles, documentation, or any webpage the user mentions."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Full URL to fetch."},
                },
                "required": ["url"],
            },
        },
    },
]


def _web_search(user, query: str, max_results: int = 5) -> str:
    data = web_search(user, query, int(max_results))
    results = data.get("results") or []
    if not results:
        return "No results found."
    lines = []
    for i, r in enumerate(results[:8], 1):
        lines.append(f"{i}. {r.get('title', '')}\n{r.get('url', '')}\n{(r.get('snippet') or '').strip()}")
    return "\n\n".join(lines)


def _web_fetch(user, url: str) -> str:
    data = web_fetch(user, url, "markdown", 8000)
    content = data.get("content") or {}
    text = content.get("text") if isinstance(content, dict) else None
    if not text:
        text = data.get("content") or ""
    if not text:
        return "No content could be extracted from that URL."
    return f"Title: {data.get('title', '')}\n\n{text}"


TOOL_REGISTRY = {
    "web_search": _web_search,
    "web_fetch": _web_fetch,
    **MCP_TOOL_REGISTRY,
}

TOOL_DEFINITIONS = TOOL_DEFINITIONS + MCP_TOOL_DEFINITIONS
