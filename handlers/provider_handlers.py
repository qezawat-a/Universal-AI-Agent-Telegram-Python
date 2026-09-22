"""J-Rock provider handlers: /provider add|list|use|auto|del."""
from telegram import Update
from telegram.ext import ContextTypes

from db.session import SessionLocal
from services.memory_service import MemoryService
from services.llm_client import encrypt_key, fetch_models, rank_models, probe_model
from services.provider_router import user_providers, auto_select
import asyncio

memory = MemoryService()


async def cmd_provider(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = SessionLocal()
    try:
        user = memory.get_or_create_user(db, update.effective_user.id)
        if not context.args:
            await update.message.reply_text(
                "🔌 Providers (auto-switch by probe, no blind fallback)\n"
                "/provider list — همه + مدل‌ها\n"
                "/provider add <name> <base_url> <api_key>\n"
                "/provider use <name> — قفل به پرووایدر\n"
                "/provider auto — انتخاب خودکار بهترین usable\n"
                "/provider del <name>"
            )
            return
        sub = context.args[0].lower()
        if sub == "list":
            lines = ["🔌 Providers:"]
            for p in user_providers(db, user):
                keymask = "✅key" if p.get("api_key") and p["api_key"] != "no-key" else "⛔nokey"
                mark = " ✅active" if (user.active_provider or "") == p["name"] else ""
                lines.append(f"• {p['name']}{mark} — {p['base_url']} [{keymask}]")
            for s in memory.list_providers(db, user):
                lines.append(f"  (db) {s.name}: {s.base_url} enabled={s.enabled}")
            await update.message.reply_text("\n".join(lines))
            return
        if sub == "add" and len(context.args) >= 4:
            name, base_url, api_key = context.args[1], context.args[2], context.args[3]
            try:
                enc = encrypt_key(api_key)
            except Exception:
                enc = api_key
            memory.add_provider(db, user, name, base_url, enc)
            await update.message.reply_text(f"✅ provider {name} saved.")
            return
        if sub == "del" and len(context.args) >= 2:
            if memory.delete_provider(db, user, context.args[1]):
                await update.message.reply_text("✅ deleted.")
            else:
                await update.message.reply_text("⛔ not found.")
            return
        if sub == "use" and len(context.args) >= 2:
            user.active_provider = context.args[1]
            user.active_model = None
            db.commit()
            await update.message.reply_text(f"✅ locked to {context.args[1]}, model will auto-pick on next message.")
            return
        if sub == "auto":
            user.active_provider = None
            user.active_model = None
            db.commit()
            try:
                pname, _, _, model = await asyncio.to_thread(auto_select, user, db)
                user.active_provider = pname
                user.active_model = model
                db.commit()
                await update.message.reply_text(f"✅ auto: {pname} / {model}")
            except Exception as e:
                await update.message.reply_text(f"⚠️ auto failed: {e}")
            return
        await update.message.reply_text("Usage: /provider list|add|use|auto|del")
    finally:
        db.close()
