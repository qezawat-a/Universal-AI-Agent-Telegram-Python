"""J-Rock soul handlers: /soul show|set|reset."""
from telegram import Update
from telegram.ext import ContextTypes

from db.session import SessionLocal
from services.memory_service import MemoryService
from services.soul_service import load_default_soul

memory = MemoryService()


async def cmd_soul(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = SessionLocal()
    try:
        user = memory.get_or_create_user(db, update.effective_user.id)
        if not context.args:
            await update.message.reply_text(
                "🧬 Soul = prompt + persona + style\n"
                "/soul show — نمایش\n"
                "/soul set prompt <text> | persona <text> | style <text>\n"
                "/soul reset [prompt|persona|style|all]"
            )
            return
        sub = context.args[0].lower()
        if sub == "show":
            default = load_default_soul()
            prompt = user.system_prompt or "(default file)"
            persona = user.soul_persona or "(none)"
            style = user.soul_style or "(none)"
            preview = (user.system_prompt or default)[:1500]
            await update.message.reply_text(
                f"🧬 J-Rock soul\n\nPROMPT:\n{preview}\n\nPERSONA:\n{persona[:1000]}\n\nSTYLE:\n{style[:1000]}"
            )
            return
        if sub == "set" and len(context.args) >= 3:
            field = context.args[1].lower()
            text = " ".join(context.args[2:])
            if field == "prompt":
                user.system_prompt = text
            elif field == "persona":
                user.soul_persona = text
            elif field == "style":
                user.soul_style = text
            else:
                await update.message.reply_text("field must be prompt|persona|style")
                return
            db.commit()
            await update.message.reply_text(f"✅ soul {field} updated.")
            return
        if sub == "reset":
            target = (context.args[1].lower() if len(context.args) > 1 else "all")
            if target in ("prompt", "all"):
                user.system_prompt = None
            if target in ("persona", "all"):
                user.soul_persona = None
            if target in ("style", "all"):
                user.soul_style = None
            db.commit()
            await update.message.reply_text(f"✅ soul {target} reset to Fable default.")
            return
        await update.message.reply_text("Usage: /soul show|set|reset")
    finally:
        db.close()
