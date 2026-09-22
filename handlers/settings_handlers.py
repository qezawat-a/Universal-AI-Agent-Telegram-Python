"""J-Rock settings hub: /settings /menu /gateway /gen."""
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from db.session import SessionLocal
from services.memory_service import MemoryService
from services.message_utils import split_message
from services.llm_client import chat_completion, generate_image
from services.soul_service import build_system_prompt, load_default_soul

memory = MemoryService()
_DEFAULT_SOUL = None


def _soul():
    global _DEFAULT_SOUL
    if _DEFAULT_SOUL is None:
        _DEFAULT_SOUL = load_default_soul()
    return _DEFAULT_SOUL


async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton("🧬 soul", callback_data="m:soul"),
         InlineKeyboardButton("🧠 skill", callback_data="m:skill")],
        [InlineKeyboardButton("🔌 provider", callback_data="m:provider"),
         InlineKeyboardButton("🧩 mcp", callback_data="m:mcp")],
        [InlineKeyboardButton("📊 status", callback_data="m:status"),
         InlineKeyboardButton("⚙️ settings", callback_data="m:settings")],
    ]
    await update.message.reply_text(
        "🤖 J-Rock — pick an option or type /:\n"
        "/soul /skill /provider /mcp /gateway /settings /gen /image /web /fetch\n"
        "/models /setmodel /setapi /profile /status /theme /verbose /think /autocompact",
        reply_markup=InlineKeyboardMarkup(kb),
    )


async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = SessionLocal()
    try:
        user = memory.get_or_create_user(db, update.effective_user.id)
        prefs = memory.get_all_preferences(db, user)
        await update.message.reply_text(
            "⚙️ J-Rock settings\n"
            f"provider: {user.active_provider or 'auto'} | model: {user.active_model or 'auto'}\n"
            f"think={prefs.get('think','low')} autocompact={prefs.get('autocompact','on')} "
            f"theme={prefs.get('theme','default')} verbose={prefs.get('verbose','1')} "
            f"tts={'on' if user.tts_enabled else 'off'} mem={user.memory_window}\n\n"
            "/provider /setmodel /soul /skill /mcp /theme /verbose /think /autocompact /tts /setmemory /setapi"
        )
    finally:
        db.close()


async def cmd_gateway(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import os
    mode = "webhook" if (os.getenv("RAILWAY_PUBLIC_DOMAIN") or os.getenv("WEBHOOK_URL")) else "polling"
    await update.message.reply_text(
        f"🌐 J-Rock gateway: telegram ({mode})\n"
        "Terminal: `python jrock_cli.py` (REPL) | `python jrock_tui.py` (graphical)\n"
        "All `/` commands work in Telegram + terminal."
    )


async def on_menu_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = (q.data or "")[2:]
    hints = {
        "soul": "/soul show",
        "skill": "/skill list",
        "provider": "/provider list",
        "mcp": "/mcp list",
        "status": "/status",
        "settings": "/settings",
    }
    await q.message.reply_text(hints.get(data, "/help"))


async def cmd_gen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generate anything the model supports: /gen <text|image|code> <prompt>."""
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /gen <text|image|code> <prompt>")
        return
    kind = context.args[0].lower()
    prompt = " ".join(context.args[1:])
    db = SessionLocal()
    try:
        user = memory.get_or_create_user(db, update.effective_user.id)
        if kind == "image":
            await update.message.reply_text("🎨 generating…")
            try:
                r = await asyncio.to_thread(generate_image, user, prompt)
                if r.get("url"):
                    await update.message.reply_photo(r["url"], caption=prompt[:200])
                else:
                    await update.message.reply_text("⚠️ no image returned (model may not support it).")
            except Exception as e:
                await update.message.reply_text(f"⚠️ image failed (unsupported?): {e}")
            return
        system = build_system_prompt(user, _soul())
        from services.llm_client import think_config as _tc2
        _cfg2 = _tc2(memory.get_preference(db, user, "think", "low"))
        if _cfg2.get("hint"):
            system += f"\n\n{_cfg2['hint']}"
        if kind == "code":
            system += "\n\nReturn ONLY code with minimal comments."
        msgs = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
        await update.message.chat.send_action("typing")
        try:
            reply = await asyncio.to_thread(chat_completion, user, msgs,
                                            _cfg2["temperature"], _cfg2["max_tokens"])
        except Exception as e:
            await update.message.reply_text(f"⚠️ gen failed: {e}")
            return
        memory.add_message(db, user, "user", f"/gen {kind} {prompt}")
        memory.add_message(db, user, "assistant", reply, model_used=user.active_model)
        for part in split_message(reply):
            await update.message.reply_text(part)
    finally:
        db.close()
