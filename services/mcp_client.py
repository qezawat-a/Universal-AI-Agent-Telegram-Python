"""J-Rock MCP client: filesystem + fetch + sqlite as callable tools,
plus a generic stdio bridge for external MCP servers (e.g. bitunix-mcp)
declared in mcp.json.

Local tools run in-process (no extra daemons needed on Termux/Railway).
External servers are spawned per call over newline-delimited JSON-RPC stdio.
"""
import json
import logging
import os
import sqlite3
import subprocess
import time

logger = logging.getLogger(__name__)

MCP_SPAWN_TIMEOUT = float(os.getenv("MCP_SPAWN_TIMEOUT", "25"))
_MCP_TOOLS_CACHE: dict = {}  # server -> (timestamp, [tool dicts])
_MCP_TOOLS_TTL = 300

_LOCAL_IMPLS = {"filesystem", "fetch", "sqlite"}


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_config() -> dict:
    path = os.getenv("MCP_CONFIG", os.path.join(_repo_root(), "mcp.json"))
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning("mcp.json not readable: %s", e)
        return {"mcpServers": {}}


def list_servers() -> list[dict]:
    cfg = load_config().get("mcpServers", {})
    return [{"name": k, **(v or {})} for k, v in cfg.items()]


def fs_list(path: str = ".", limit: int = 50) -> str:
    base = os.path.abspath(os.path.join(_repo_root(), path)) if not os.path.isabs(path) else path
    root = _repo_root()
    if not base.startswith(root):
        return "⛔ path outside workspace"
    try:
        items = sorted(os.listdir(base))[:limit]
        return "\n".join(items) or "(empty)"
    except Exception as e:
        return f"Error: {e}"


def fs_read(path: str, limit: int = 8000) -> str:
    base = os.path.abspath(os.path.join(_repo_root(), path)) if not os.path.isabs(path) else path
    if not base.startswith(_repo_root()):
        return "⛔ path outside workspace"
    try:
        with open(base, "r", encoding="utf-8", errors="replace") as f:
            return f.read()[:limit]
    except Exception as e:
        return f"Error: {e}"


def sqlite_query(sql: str, limit: int = 50) -> str:
    db_path = os.path.join(_repo_root(), "agent.db")
    if not os.path.isfile(db_path):
        return "No agent.db yet (run bot once to init DB)."
    if not sql.strip().lower().startswith("select"):
        return "⛔ only SELECT allowed via MCP sqlite"
    try:
        con = sqlite3.connect(db_path)
        cur = con.cursor()
        cur.execute(sql)
        rows = cur.fetchmany(limit)
        cols = [d[0] for d in cur.description or []]
        con.close()
        out = [", ".join(cols)] + [", ".join(str(c) for c in r) for r in rows]
        return "\n".join(out) or "(no rows)"
    except Exception as e:
        return f"Error: {e}"


MCP_TOOL_DEFINITIONS = [    {"type": "function", "function": {
        "name": "mcp_fs_list",
        "description": "List workspace files (MCP filesystem).",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "Relative path, default '.'"}}, "required": []}}},
    {"type": "function", "function": {
        "name": "mcp_fs_read",
        "description": "Read a workspace file (MCP filesystem).",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "Relative file path"}}, "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "mcp_sqlite_query",
        "description": "Run read-only SELECT on agent.db (MCP sqlite).",
        "parameters": {"type": "object", "properties": {
            "sql": {"type": "string", "description": "SELECT statement"}}, "required": ["sql"]}}},
]


def _mcp_fs_list(user, path: str = ".") -> str:
    return fs_list(path)


def _mcp_fs_read(user, path: str) -> str:
    return fs_read(path)


def _mcp_sqlite_query(user, sql: str) -> str:
    return sqlite_query(sql)


MCP_TOOL_REGISTRY = {
    "mcp_fs_list": _mcp_fs_list,
    "mcp_fs_read": _mcp_fs_read,
    "mcp_sqlite_query": _mcp_sqlite_query,
    "mcp_tools": None,  # set below
    "mcp_call": None,  # set below
}


# ---- generic stdio MCP bridge (external servers from mcp.json) ----

def _server_cfg(name: str) -> dict | None:
    cfg = load_config().get("mcpServers", {}).get(name)
    if not cfg or cfg.get("enabled") is False or not cfg.get("command"):
        return None
    return cfg


def _server_env(cfg: dict) -> dict:
    """Merge process env with per-server env (supports $VAR expansion).
    Empty values are dropped so servers see 'unset' (e.g. public-only mode)."""
    env = dict(os.environ)
    for k, v in (cfg.get("env") or {}).items():
        if isinstance(v, str) and v.startswith("$"):
            v = os.environ.get(v[1:], "")
        if str(v) == "":
            env.pop(str(k), None)
        else:
            env[str(k)] = str(v)
    return env


def _spawn_stdio(cfg: dict) -> subprocess.Popen:
    cmd = [cfg["command"], *(cfg.get("args") or [])]
    try:
        return subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, bufsize=1,
            env=_server_env(cfg),
        )
    except FileNotFoundError:
        raise RuntimeError(f"command not found: {cfg['command']} (is it installed?)")


def _rpc_exchange(proc: subprocess.Popen, requests: list[dict],
                  timeout: float = MCP_SPAWN_TIMEOUT) -> dict:
    """Send JSON-RPC requests (notifications have no id), return {id: response}."""
    assert proc.stdin and proc.stdout
    want = {}
    for r in requests:
        proc.stdin.write(json.dumps(r) + "\n")
        if "id" in r:
            want[r["id"]] = r.get("method", "?")
    proc.stdin.flush()
    out: dict = {}
    deadline = time.time() + timeout
    for mid in want:
        rest = deadline - time.time()
        if rest <= 0:
            raise TimeoutError("MCP server response timeout")
        line = _readline_timeout(proc, rest)
        if not line:
            raise RuntimeError("MCP server closed stdout (crashed?)")
        try:
            resp = json.loads(line)
        except Exception:
            raise RuntimeError(f"MCP server sent non-JSON: {line[:200]}")
        out[resp.get("id", mid)] = resp
    return out


def _readline_timeout(proc: subprocess.Popen, timeout: float) -> str:
    import select
    fd = proc.stdout.fileno()
    r, _, _ = select.select([fd], [], [], timeout)
    if not r:
        return ""
    return proc.stdout.readline()


def mcp_list_tools(server: str) -> list[dict]:
    """List tools of an external MCP server (cached 5 min)."""
    now = time.time()
    hit = _MCP_TOOLS_CACHE.get(server)
    if hit and now - hit[0] < _MCP_TOOLS_TTL:
        return hit[1]
    cfg = _server_cfg(server)
    if cfg is None:
        raise RuntimeError(f"unknown/disabled MCP server: {server}")
    if server in _LOCAL_IMPLS:
        raise RuntimeError(f"{server} is a built-in local tool, not a stdio server")
    proc = _spawn_stdio(cfg)
    try:
        resps = _rpc_exchange(proc, [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                        "clientInfo": {"name": "jrock", "version": "1.0"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        ])
        if "error" in resps.get(1, {}):
            raise RuntimeError(f"MCP initialize failed: {resps[1]['error']}")
        tools = ((resps.get(2, {}).get("result") or {}).get("tools")) or []
        if "error" in resps.get(2, {}):
            raise RuntimeError(f"MCP tools/list failed: {resps[2]['error']}")
        _MCP_TOOLS_CACHE[server] = (now, tools)
        return tools
    finally:
        try:
            proc.stdin.close()
        except Exception:
            pass
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


def mcp_call_tool(server: str, tool: str, args: dict | None = None) -> str:
    """Call one tool on an external MCP server; returns text result."""
    cfg = _server_cfg(server)
    if cfg is None:
        return f"⛔ unknown/disabled MCP server: {server}"
    if not isinstance(args, dict):
        return "⛔ args must be a JSON object"
    try:
        known = {t.get("name") for t in mcp_list_tools(server)}
    except Exception as e:
        return f"⚠️ MCP {server} tools/list failed: {e}"
    if tool not in known:
        return f"⛔ tool «{tool}» not on {server}. Use mcp_tools to list."
    proc = _spawn_stdio(cfg)
    try:
        resps = _rpc_exchange(proc, [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                        "clientInfo": {"name": "jrock", "version": "1.0"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": tool, "arguments": args or {}}},
        ])
        r2 = resps.get(2, {})
        if "error" in r2:
            return f"⚠️ MCP {tool} error: {r2['error']}"
        content = (r2.get("result") or {}).get("content") or []
        texts = [c.get("text", "") for c in content if isinstance(c, dict)]
        return "\n".join(texts)[:6000] or "(empty result)"
    except Exception as e:
        return f"⚠️ MCP call failed: {e}"
    finally:
        try:
            proc.stdin.close()
        except Exception:
            pass
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


def _mcp_tools(user, server: str) -> str:
    try:
        tools = mcp_list_tools(server)
    except Exception as e:
        return f"⚠️ {e}"
    if not tools:
        return f"(no tools on {server})"
    lines = [f"🧩 {server} tools ({len(tools)}):"]
    for t in tools:
        desc = (t.get("description") or "")[:100]
        lines.append(f"• {t.get('name')}" + (f" — {desc}" if desc else ""))
    return "\n".join(lines)


def _mcp_call(user, server: str, tool: str, arguments: str = "{}") -> str:
    try:
        args = json.loads(arguments) if isinstance(arguments, str) else (arguments or {})
    except Exception:
        return "⛔ arguments must be valid JSON object, e.g. {\"symbol\": \"BTCUSDT\"}"
    return mcp_call_tool(server, tool, args)


MCP_TOOL_DEFINITIONS += [
    {"type": "function", "function": {
        "name": "mcp_tools",
        "description": "List tools of an external MCP server (e.g. bitunix). Call first to discover names.",
        "parameters": {"type": "object", "properties": {
            "server": {"type": "string", "description": "Server name from mcp.json"}}, "required": ["server"]}}},
    {"type": "function", "function": {
        "name": "mcp_call",
        "description": "Call a tool on an external MCP server (e.g. bitunix market data).",
        "parameters": {"type": "object", "properties": {
            "server": {"type": "string"},
            "tool": {"type": "string"},
            "arguments": {"type": "string", "description": "JSON object string, e.g. {\"symbol\": \"BTCUSDT\"}"}},
         "required": ["server", "tool"]}}},
]
MCP_TOOL_REGISTRY["mcp_tools"] = _mcp_tools
MCP_TOOL_REGISTRY["mcp_call"] = _mcp_call
