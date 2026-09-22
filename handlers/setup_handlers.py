from telegram.ext import CallbackQueryHandler, CommandHandler, MessageHandler, filters

import os


def _user_filter():
    ids = {x.strip() for x in
           (os.getenv("ALLOWED_USER_ID", "") + "," + os.getenv("ADMIN_IDS", "")).split(",")
           if x.strip().isdigit()}
    if not ids:
        return None
    return filters.User(user_id={int(i) for i in ids})

from .chat_handlers import (
    cmd_start,
    cmd_help,
    cmd_setapi,
    cmd_models,
    cmd_setmodel,
    cmd_setsystem,
    cmd_setmemory,
    cmd_tts,
    cmd_profile,
    cmd_research,
    cmd_broadcast,
    cmd_verbose,
    cmd_theme,
    cmd_think,
    cmd_autocompact,
    cmd_status,
    handle_text,
)
from .media_handlers import handle_photo, handle_voice
from .memory_handlers import cmd_history, cmd_forget, cmd_setpref, cmd_sessions, cmd_newchat, cmd_resume, cmd_clearprefs, cmd_skill
from .router_handlers import cmd_image, cmd_web, cmd_fetch
from .soul_handlers import cmd_soul
from .provider_handlers import cmd_provider
from .mcp_handlers import cmd_mcp
from .settings_handlers import cmd_menu, cmd_settings, cmd_gateway, cmd_gen, on_menu_button


def register_handlers(app):
    uf = _user_filter()
    kw = {"filters": uf} if uf is not None else {}
    for cmd, fn in [
        ("start", cmd_start), ("help", cmd_help), ("menu", cmd_menu),
        ("settings", cmd_settings), ("gateway", cmd_gateway), ("gen", cmd_gen),
        ("soul", cmd_soul), ("provider", cmd_provider), ("mcp", cmd_mcp),
        ("setapi", cmd_setapi), ("models", cmd_models), ("setmodel", cmd_setmodel),
        ("setsystem", cmd_setsystem), ("setmemory", cmd_setmemory), ("tts", cmd_tts),
        ("profile", cmd_profile), ("verbose", cmd_verbose), ("theme", cmd_theme),
        ("think", cmd_think), ("autocompact", cmd_autocompact),
        ("status", cmd_status), ("research", cmd_research), ("history", cmd_history),
        ("sessions", cmd_sessions), ("newchat", cmd_newchat), ("resume", cmd_resume),
        ("forget", cmd_forget), ("setpref", cmd_setpref), ("clearprefs", cmd_clearprefs),
        ("skill", cmd_skill), ("broadcast", cmd_broadcast), ("image", cmd_image),
        ("web", cmd_web), ("fetch", cmd_fetch),
    ]:
        app.add_handler(CommandHandler(cmd, fn, **kw))
    app.add_handler(CallbackQueryHandler(on_menu_button, pattern=r"^m:"))

    msg_kw = {"filters": (filters.PHOTO & uf)} if uf is not None else {"filters": filters.PHOTO}
    voice_kw = {"filters": (filters.VOICE & uf)} if uf is not None else {"filters": filters.VOICE}
    text_kw = {"filters": ((filters.TEXT & ~filters.COMMAND) & uf)} if uf is not None else {"filters": filters.TEXT & ~filters.COMMAND}
    app.add_handler(MessageHandler(msg_kw["filters"], handle_photo))
    app.add_handler(MessageHandler(voice_kw["filters"], handle_voice))
    app.add_handler(MessageHandler(text_kw["filters"], handle_text))
