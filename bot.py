"""J-Rock — self-hosted Telegram AI agent (entry point)."""
import asyncio
import logging
import os
import traceback

from dotenv import load_dotenv
from telegram import Bot, BotCommand
from telegram.ext import ApplicationBuilder, ContextTypes

from db.session import init_db
from handlers import register_handlers
from services.message_utils import split_message

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

COMMANDS = [
    ("start", "شروع J-Rock"),
    ("menu", "منوی همه گزینه‌ها"),
    ("help", "راهنما"),
    ("settings", "تنظیمات"),
    ("gateway", "وضعیت گیت‌وی"),
    ("soul", "پرامپت/پرسونا/استایل"),
    ("skill", "اسکیل‌ها"),
    ("provider", "پرووایدرها + auto"),
    ("mcp", "ابزارهای MCP"),
    ("gen", "تولید هرچیزی"),
    ("setapi", "اندپوینت + کلید"),
    ("models", "لیست مدل‌ها"),
    ("setmodel", "انتخاب مدل"),
    ("setsystem", "سیستم‌پرامپت"),
    ("setmemory", "پنجره حافظه"),
    ("tts", "صدا on/off"),
    ("profile", "پروفایل"),
    ("status", "وضعیت بات"),
    ("verbose", "سطح نمایش ابزار"),
    ("think", "سطح تفکر"),
    ("autocompact", "فشرده‌سازی خودکار"),
    ("theme", "تم نمایش"),
    ("history", "تاریخچه سشن فعلی"),
    ("sessions", "لیست سشن‌ها"),
    ("newchat", "سشن جدید"),
    ("resume", "بازگشت به سشن"),
    ("forget", "پاک کردن سشن فعلی"),
    ("research", "جستجوی عمیق"),
    ("image", "ساخت عکس"),
    ("web", "جستجوی وب"),
    ("fetch", "خواندن URL"),
    ("setpref", "تنظیم دلخواه"),
    ("clearprefs", "پاک کردن تنظیمات"),
]


def _admin_ids():
    return {int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()}


async def _post_init(app):
    try:
        await app.bot.set_my_commands([BotCommand(c, d) for c, d in COMMANDS])
    except Exception as e:  # bad token / no network at boot shouldn't kill the bot
        logger.warning("set_my_commands failed (bot still running): %s", e)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Exception while handling an update:", exc_info=context.error)
    text = f"❌ خطا:\n{type(context.error).__name__}: {context.error}"
    for aid in _admin_ids():
        try:
            for part in split_message(text):
                await context.application.bot.send_message(aid, part)
        except Exception:
            pass


def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN not set. Get one from @BotFather.")

    try:
        init_db()
        logger.info("Database initialized.")
    except Exception as e:
        logger.error("init_db failed (bot will still start, but DB calls will error): %s", e, exc_info=True)

    # Webhook mode when we have a public URL (Railway sets RAILWAY_PUBLIC_DOMAIN).
    # Polling otherwise (local dev with no public URL). Webhook avoids the Telegram
    # 409 Conflict you get with polling whenever more than one container is alive
    # (Railway's zero-downtime deploys, or two services) — Telegram pushes updates
    # to one URL instead of two instances fighting over getUpdates.
    public_domain = os.getenv("RAILWAY_PUBLIC_DOMAIN") or os.getenv("WEBHOOK_DOMAIN")
    webhook_url = os.getenv("WEBHOOK_URL") or (
        f"https://{public_domain}/{token}" if public_domain else None
    )
    port = int(os.getenv("PORT", "8080"))

    mode = "webhook" if webhook_url else "polling"
    logger.info("J-Rock starting (mode: %s)...", mode)
    # NOTE: single run per process. Do NOT loop-and-rebuild the Application
    # in-process: PTB closes the event loop on shutdown, so a second
    # run_webhook()/run_polling() in the same process dies with
    # "RuntimeError: Event loop is closed". On crash, notify admins and exit
    # non-zero — Railway/Docker restarts the worker with a fresh interpreter.
    app = ApplicationBuilder().token(token).post_init(_post_init).build()
    register_handlers(app)
    app.add_error_handler(error_handler)
    try:
        if webhook_url:
            app.run_webhook(
                listen="0.0.0.0",
                port=port,
                url_path=token,
                webhook_url=webhook_url,
                allowed_updates=["message"],
                drop_pending_updates=True,
            )
        else:
            app.run_polling(
                allowed_updates=["message"],
                bootstrap_retries=10,
            )
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error("Fatal, exiting for platform restart: %s", e, exc_info=True)
        # surface fatal errors to admins via a direct API call
        try:
            b = Bot(token)
            tb = "".join(traceback.format_exception(type(e), e, e.__traceback__))
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            for aid in _admin_ids():
                try:
                    loop.run_until_complete(b.send_message(aid, f"❌ Crash:\n{tb[-3500:]}"))
                except Exception:
                    pass
            loop.close()
        except Exception:
            pass
        raise SystemExit(1)


if __name__ == "__main__":
    main()
