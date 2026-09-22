"""J-Rock MCP handlers: /mcp list|tools|call|fs|read|sql."""
from telegram import Update
from telegram.ext import ContextTypes

from services.mcp_client import (
    list_servers, fs_list, fs_read, sqlite_query, mcp_list_tools, mcp_call_tool,
)


async def cmd_mcp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "🧩 MCP (filesystem + fetch + sqlite + bitunix)\n"
            "/mcp list\n"
            "/mcp tools <server>  (مثلا bitunix)\n"
            "/mcp call <server> <tool> [json]  (مثلا: /mcp call bitunix spot_get_last_price {\"symbol\":\"BTCUSDT\"})\n"
            "/mcp fs <path>\n"
            "/mcp read <file>\n"
            "/mcp sql <SELECT ...>"
        )
        return
    sub = context.args[0].lower()
    if sub == "list":
        servers = list_servers()
        lines = ["🧩 MCP servers:"] + [f"• {s['name']} — {s.get('description','')}" for s in servers]
        await update.message.reply_text("\n".join(lines) or "No MCP servers.")
        return
    if sub == "tools":
        if len(context.args) < 2:
            await update.message.reply_text("Usage: /mcp tools <server>  (مثلا /mcp tools bitunix)")
            return
        try:
            tools = mcp_list_tools(context.args[1])
        except Exception as e:
            await update.message.reply_text(f"⚠️ {e}")
            return
        if not tools:
            await update.message.reply_text(f"(no tools on {context.args[1]})")
            return
        lines = [f"🧩 {context.args[1]} tools ({len(tools)}):"]
        for t in tools:
            desc = (t.get("description") or "")[:100]
            lines.append(f"• {t.get('name')}" + (f" — {desc}" if desc else ""))
        await update.message.reply_text("\n".join(lines)[:4000])
        return
    if sub == "call":
        if len(context.args) < 3:
            await update.message.reply_text(
                "Usage: /mcp call <server> <tool> [json]\n"
                "مثلا: /mcp call bitunix spot_get_last_price {\"symbol\":\"BTCUSDT\"}"
            )
            return
        import json as _json
        server, tool = context.args[1], context.args[2]
        raw = " ".join(context.args[3:]).strip() or "{}"
        try:
            args = _json.loads(raw)
        except Exception:
            await update.message.reply_text("⛔ arguments باید JSON معتبر باشه. مثلا {\"symbol\":\"BTCUSDT\"}")
            return
        await update.message.chat.send_action("typing")
        import asyncio as _aio
        out = await _aio.to_thread(mcp_call_tool, server, tool, args)
        await update.message.reply_text(out[:4000] or "(empty)")
        return
    if sub == "fs":
        path = context.args[1] if len(context.args) > 1 else "."
        await update.message.reply_text(fs_list(path)[:4000] or "(empty)")
        return
    if sub == "read":
        if len(context.args) < 2:
            await update.message.reply_text("Usage: /mcp read <file>")
            return
        await update.message.reply_text(fs_read(context.args[1])[:4000] or "(empty)")
        return
    if sub == "sql":
        sql = " ".join(context.args[1:])
        await update.message.reply_text(sqlite_query(sql)[:4000])
        return
    await update.message.reply_text("Usage: /mcp list|tools|call|fs|read|sql")
