"""J-Rock MCP client: filesystem + fetch + sqlite as callable tools.

Reads mcp.json for config but runs lightweight local implementations so no
extra MCP server processes are required on Termux/Railway.
"""
import json
import logging
import os
import sqlite3

logger = logging.getLogger(__name__)


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


MCP_TOOL_DEFINITIONS = [
    {"type": "function", "function": {
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
}
