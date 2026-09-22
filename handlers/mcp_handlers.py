"""J-Rock MCP handlers: /mcp list|call|reload."""
from telegram import Update
from telegram.ext import ContextTypes

from services.mcp_client import list_servers, fs_list, fs_read, sqlite_query


async def cmd_mcp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "🧩 MCP (filesystem + fetch + sqlite)\n"
            "/mcp list\n"
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
    await update.message.reply_text("Usage: /mcp list|fs|read|sql")
