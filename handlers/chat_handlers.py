"""Core chat + setup handlers for J-Rock."""
import asyncio
import logging
import os

from telegram import Update
from telegram.ext import ContextTypes

from db.session import SessionLocal

logger = logging.getLogger(__name__)
from services.llm_client import encrypt_key, fetch_models, chat_completion, DEFAULT_SYSTEM_PROMPT, run_agentic, rank_models, probe_model
from services.memory_service import MemoryService
from services.message_utils import split_message
from services.soul_service import build_system_prompt, load_default_soul
from services.tts_service import text_to_speech
from services.tools import TOOL_DEFINITIONS, TOOL_REGISTRY

memory = MemoryService()
_JROCK_SOUL = None


def _default_soul() -> str:
    global _JROCK_SOUL
    if _JROCK_SOUL is None:
        try:
            _JROCK_SOUL = load_default_soul()
        except Exception:
            _JROCK_SOUL = DEFAULT_SYSTEM_PROMPT
    return _JROCK_SOUL


def _admin_ids():
    return {int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()}


def _db():
    return SessionLocal()


def _agentic_reply(user, messages: list[dict], cfg: dict | None = None) -> tuple:
    """Run an agentic chat turn. Falls back to plain chat if tool-use is
    disabled or the model/endpoint doesn't support function calling.
    Returns (text, steps) where steps is the list of tool names used."""
    from services.llm_client import think_config
    cfg = cfg or think_config("low")
    if os.getenv("AGENTIC_TOOLS", "true").lower() == "false":
        return chat_completion(user, messages,
                               temperature=cfg["temperature"],
                               max_tokens=cfg["max_tokens"]), []
    try:
        return run_agentic(user, messages, TOOL_DEFINITIONS, TOOL_REGISTRY,
                           temperature=cfg["temperature"],
                           max_tokens=cfg["max_tokens"])
    except Exception as e:
        logger.warning("agentic tool-call failed, falling back to plain chat: %s", e)
        return chat_completion(user, messages,
                               temperature=cfg["temperature"],
                               max_tokens=cfg["max_tokens"]), []


def _apply_theme(theme: str, text: str) -> str:
    """Apply a display theme to the assistant reply.

    Themes:
      default  -> unchanged
      compact  -> collapse extra blank lines, trim
      emoji    -> prepend a spark to bare replies
      markdown -> lightly prettify (fenced headers get highlighted)
    """
    theme = (theme or "default").lower()
    if theme == "compact":
        import re
        return re.sub(r"\n{3,}", "\n\n", text).strip()
    if theme == "emoji":
        if text and not text[0].isspace() and not any(
            text.startswith(p) for p in ("✨", "🤖", "💡", "⚠️", "🔍", "🎨", "📄", "📢")
        ):
            return "✨ " + text
        return text
    if theme == "markdown":
        # leave the model's own markdown intact; just ensure a trailing newline
        return text.rstrip() + "\n" if text else text
    return text


async def _ensure_model(db, user) -> None:
    """Auto-pick best usable (provider, model) by real probe. No blind fallback."""
    if user.active_model:
        return
    try:
        from services.provider_router import auto_select
        try:
            pname, base_url, api_key, model = await asyncio.to_thread(auto_select, user, db)
        except Exception as e:
            logger.warning("provider-router auto failed, single-provider fallback: %s", e)
            models = await asyncio.to_thread(fetch_models, user)
            if not models:
                return
            ranked = rank_models(models)
            default = os.getenv("DEFAULT_MODEL")
            if default and default in models:
                ranked = [default] + ranked
            for candidate in ranked[:8]:
                try:
                    await asyncio.to_thread(probe_model, user, candidate)
                    user.active_model = candidate
                    db.commit()
                    return
                except Exception as ex:
                    logger.warning("model %s not usable: %s", candidate, ex)
            return
        # router succeeded: pin provider URL/key to user so get_client uses it
        user.base_url = base_url
        try:
            user.api_key_encrypted = encrypt_key(api_key)
        except Exception:
            pass
        user.active_provider = pname
        user.active_model = model
        db.commit()
        logger.info("auto-selected %s / %s for %s", pname, model, user.telegram_id)
    except Exception as e:
        logger.warning("auto model selection failed for %s: %s", user.telegram_id, e)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = _db()
    try:
        memory.get_or_create_user(
            db, update.effective_user.id, update.effective_user.username, update.effective_user.full_name
        )
        await update.message.reply_text(
            "👋 سلام! من J-Rock‌ـم.\n"
            "۱) با /setapi <base_url> <api_key> یا /provider add اندپوینتت رو تنظیم کن\n"
            "۲) با /models یا /provider list مدل‌ها رو ببین (auto انتخاب می‌کنم)\n"
            "۳) با /soul show پرسونای Fable منو ببین\n"
            "بعد هرچی خواستی بپرس. با /menu یا /help همه گزینه‌ها."
        )
    finally:
        db.close()


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 J-Rock — type / to see all\n"
        "/menu - منوی دکمه‌ای\n"
        "/settings - تنظیمات\n"
        "/gateway - وضعیت گیت‌وی\n"
        "/soul show|set|reset - پرامپت/پرسونا/استایل\n"
        "/provider list|add|use|auto|del - پرووایدرها\n"
        "/mcp list|fs|read|sql - ابزارهای MCP\n"
        "/gen <text|image|code> <prompt> - تولید هرچی مدل ساپورت کنه\n"
        "/setapi <base_url> <api_key> - تنظیم سریع اندپوینت\n"
        "/models - لیست مدل‌ها\n"
        "/setmodel <name>|auto - انتخاب مدل\n"
        "/setsystem <prompt>|reset - سیستم‌پرامپت (قدیمی، /soul جدید)\n"
        "/setmemory <n> - تعداد پیام‌های حافظه\n"
        "/tts on|off - صدای خروجی\n"
        "/profile - پروفایل\n"
        "/status - وضعیت فعلی بات\n"
        "/verbose 0|1|2 - سطح نمایش ابزارها\n"
        "/think off|low|medium|high - سطح تفکر\n"
        "/autocompact on|off - فشرده‌سازی خودکار سشن طولانی\n"
        "/theme default|compact|emoji|markdown - تم نمایش\n"
        "/skill add|list|use|del - اسکیل/پرسونای شخصی\n"
        "/history - تاریخچه سشن فعلی\n"
        "/sessions - لیست سشن‌ها\n"
        "/newchat - شروع سشن جدید (قبلی محفوظ)\n"
        "/resume <id> - بازگشت به سشن قبلی\n"
        "/forget - پاک کردن سشن فعلی\n"
        "/research <topic> - جستجوی عمیق\n"
        "/image <prompt> - ساخت عکس (مدل: DEFAULT_IMAGE_MODEL)\n"
        "/web <query> - جستجوی وب\n"
        "/fetch <url> - خواندن محتوای صفحه\n"
        "/setpref <k> <v> - تنظیم دلخواه (فقط <k> = حذف)\n"
        "/clearprefs - پاک کردن همهٔ preferenceها\n"
        "💡 در چت معمولی هم بات خودش برای جستجو/خواندن صفحه از وب استفاده می‌کند."
    )


async def cmd_setapi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /setapi <base_url> <api_key>")
        return
    base_url, api_key = context.args[0], context.args[1]
    db = _db()
    try:
        user = memory.get_or_create_user(db, update.effective_user.id)
        user.base_url = base_url
        user.api_key_encrypted = encrypt_key(api_key)
        db.commit()
        await update.message.reply_text("✅ اندپوینت و کلید ذخیره شد (رمزنگاری‌شده).")
    finally:
        db.close()


async def cmd_models(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = _db()
    try:
        user = memory.get_or_create_user(db, update.effective_user.id)
        try:
            models = await asyncio.to_thread(fetch_models, user)
        except Exception as e:
            await update.message.reply_text(f"⚠️ نتونستم مدل‌ها رو بگیرم: {e}")
            return
        if not models:
            await update.message.reply_text("هیچ مدلی پیدا نشد. اندپوینت/کلید رو چک کن.")
            return
        current = user.active_model or os.getenv("DEFAULT_MODEL", "(تنظیم نشده)")
        text = f"مدل فعلی: {current}\n\nمدل‌های موجود:\n" + "\n".join(models)
        for part in split_message(text):
            await update.message.reply_text(part)
    finally:
        db.close()


async def cmd_setmodel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /setmodel <model_name>  (یا /setmodel auto برای انتخاب خودکار)")
        return
    if context.args[0].lower() in {"auto", "reset", "-"}:
        db = _db()
        try:
            user = memory.get_or_create_user(db, update.effective_user.id)
            user.active_model = None
            db.commit()
            await update.message.reply_text("✅ مدل پاک شد؛ روی اولین پیام بات خودش یکی رو انتخاب می‌کند (با تست).")
        finally:
            db.close()
        return
    model = " ".join(context.args)
    db = _db()
    try:
        user = memory.get_or_create_user(db, update.effective_user.id)
        user.active_model = model
        db.commit()
        await update.message.reply_text(f"✅ مدل تنظیم شد: {model}")
    finally:
        db.close()


async def cmd_setsystem(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /setsystem <prompt text>  (یا /setsystem reset برای بازگشت به پیش‌فرض)")
        return
    if context.args[0].lower() in {"reset", "default", "-"}:
        db = _db()
        try:
            user = memory.get_or_create_user(db, update.effective_user.id)
            user.system_prompt = None
            db.commit()
            await update.message.reply_text("✅ سیستم‌پرامپت به پیش‌فرض (DEFAULT_SYSTEM_PROMPT) برگشت.")
        finally:
            db.close()
        return
    prompt = " ".join(context.args)
    db = _db()
    try:
        user = memory.get_or_create_user(db, update.effective_user.id)
        user.system_prompt = prompt
        db.commit()
        await update.message.reply_text("✅ سیستم‌پرامپت تنظیم شد.")
    finally:
        db.close()


async def cmd_setmemory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /setmemory <n>")
        return
    n = int(context.args[0])
    db = _db()
    try:
        user = memory.get_or_create_user(db, update.effective_user.id)
        user.memory_window = n
        db.commit()
        await update.message.reply_text(f"✅ پنجره‌ی حافظه = {n} پیام.")
    finally:
        db.close()


async def cmd_tts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /tts on|off")
        return
    val = context.args[0].lower()
    db = _db()
    try:
        user = memory.get_or_create_user(db, update.effective_user.id)
        user.tts_enabled = (val == "on")
        db.commit()
        await update.message.reply_text(f"✅ TTS = {'روشن' if user.tts_enabled else 'خاموش'}")
    finally:
        db.close()


async def cmd_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = _db()
    try:
        user = memory.get_or_create_user(db, update.effective_user.id)
        prefs = memory.get_all_preferences(db, user)
        lines = [
            f"telegram_id: {user.telegram_id}",
            f"base_url: {user.base_url or '(پیش‌فرض)'}" ,
            f"active_model: {user.active_model or '(پیش‌فرض)'}",
            f"tts: {'روشن' if user.tts_enabled else 'خاموش'} ({user.tts_voice})",
            f"memory_window: {user.memory_window}",
            f"verbose: {prefs.get('verbose', '1')}",
            f"theme: {prefs.get('theme', 'default')}",
            f"active_skill: {prefs.get('active_skill', '(هیچ‌کدام)')}",
            f"skills: {len(memory.list_skills(db, user))} ذخیره‌شده",
            f"system_prompt: {user.system_prompt[:200] if user.system_prompt else '(پیش‌فرض)'}",
        ]
        if prefs:
            lines.append("preferences:")
            lines += [f"  {k}: {v}" for k, v in prefs.items()]
        for part in split_message("\n".join(lines)):
            await update.message.reply_text(part)
    finally:
        db.close()


async def cmd_verbose(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text(
            "Usage: /verbose 0|1|2\n"
            "0 = فقط پاسخ نهایی (بدون لاگ ابزار)\n"
            "1 = نام ابزارهای استفاده‌شده (پیش‌فرض)\n"
            "2 = جزئیات بیشتر (تعداد فراخوانی)"
        )
        return
    level = int(context.args[0])
    if level not in (0, 1, 2):
        await update.message.reply_text("فقط 0، 1 یا 2 مجازه.")
        return
    db = _db()
    try:
        user = memory.get_or_create_user(db, update.effective_user.id)
        memory.set_preference(db, user, "verbose", str(level))
        await update.message.reply_text(f"✅ سطح نمایش ابزار = {level}")
    finally:
        db.close()


async def cmd_theme(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "Usage: /theme <default|compact|emoji|markdown>\n"
            "default  = بدون تغییر\n"
            "compact  = حذف خطوط خالی اضافه\n"
            "emoji    = افزودن ✨ به ابتدای پاسخ\n"
            "markdown = رعایت فرمت‌بندی مارک‌داون"
        )
        return
    theme = context.args[0].lower()
    if theme not in ("default", "compact", "emoji", "markdown"):
        await update.message.reply_text("تم معتبر: default, compact, emoji, markdown")
        return
    db = _db()
    try:
        user = memory.get_or_create_user(db, update.effective_user.id)
        memory.set_preference(db, user, "theme", theme)
        await update.message.reply_text(f"✅ تم = {theme}")
    finally:
        db.close()


async def cmd_think(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from services.llm_client import THINK_LEVELS
    db = _db()
    try:
        user = memory.get_or_create_user(db, update.effective_user.id)
        if not context.args:
            cur = memory.get_preference(db, user, "think", "low")
            await update.message.reply_text(
                f"🧠 thinking level = {cur}\n"
                "off = سریع/کم‌حرف (بدون نمایش ابزار)\n"
                "low = معمولی (پیش‌فرض)\n"
                "medium = دقیق‌تر، reasoning بیشتر\n"
                "high = عمیق، step-by-step\n"
                "مثال: /think high"
            )
            return
        level = context.args[0].lower()
        if level not in THINK_LEVELS:
            await update.message.reply_text("⛔ فقط: off | low | medium | high")
            return
        memory.set_preference(db, user, "think", level)
        await update.message.reply_text(f"✅ thinking level = {level}")
    finally:
        db.close()


async def cmd_autocompact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = _db()
    try:
        user = memory.get_or_create_user(db, update.effective_user.id)
        if not context.args:
            cur = memory.get_preference(db, user, "autocompact", "on")
            await update.message.reply_text(
                f"🗜 autocompact = {cur}\n"
                "وقتی سشن از ۲ برابر پنجره حافظه رد بشه، پیام‌های قدیمی خلاصه و حذف می‌شن.\n"
                "مثال: /autocompact off"
            )
            return
        val = context.args[0].lower()
        if val not in ("on", "off"):
            await update.message.reply_text("⛔ فقط: on | off")
            return
        memory.set_preference(db, user, "autocompact", val)
        await update.message.reply_text(f"✅ autocompact = {val}")
    finally:
        db.close()


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = _db()
    try:
        user = memory.get_or_create_user(db, update.effective_user.id)
        sess = memory.get_current_session(db, user)
        prefs = memory.get_all_preferences(db, user)
        agentic = os.getenv("AGENTIC_TOOLS", "true").lower() != "false"
        tools = list(TOOL_REGISTRY.keys()) if agentic else []
        skill = prefs.get("active_skill")
        if skill:
            skill_mark = f"{skill} {'✅' if memory.get_skill(db, user, skill) else '(حذف شده!)'}"
        else:
            skill_mark = "(هیچ‌کدام)"
        lines = [
            "📊 وضعیت J-Rock:",
            f"provider: {user.active_provider or 'auto'}",
            f"think: {prefs.get('think', 'low')} | autocompact: {prefs.get('autocompact', 'on')}",
            f"model: {user.active_model or os.getenv('DEFAULT_MODEL', '(انتخاب خودکار)')}",
            f"session: #{sess.id} ({sess.message_count} پیام)",
            f"verbose: {prefs.get('verbose', '1')}",
            f"theme: {prefs.get('theme', 'default')}",
            f"active_skill: {skill_mark}",
            f"agentic tools: {'روشن' if agentic else 'خاموش'} ({', '.join(tools) or '—'})",
            f"tts: {'روشن' if user.tts_enabled else 'خاموش'} ({user.tts_voice})",
            f"memory_window: {user.memory_window}",
            f"skills ذخیره‌شده: {len(memory.list_skills(db, user))}",
        ]
        await update.message.reply_text("\n".join(lines))
    finally:
        db.close()


async def cmd_research(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /research <topic>")
        return
    topic = " ".join(context.args)
    db = _db()
    try:
        user = memory.get_or_create_user(db, update.effective_user.id)
        await _ensure_model(db, user)
        from services.llm_client import think_config as _tc
        _cfg = _tc(memory.get_preference(db, user, "think", "low"))
        system = user.system_prompt or "You are a deep research assistant."
        if _cfg.get("hint"):
            system += f"\n\n{_cfg['hint']}"
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": f"Do deep research on: {topic}. Provide a structured report with cited points and a conclusion."},
        ]
        await update.message.reply_text("🔍 در حال پژوهش...")
        reply = await asyncio.to_thread(chat_completion, user, messages,
                                        _cfg["temperature"], _cfg["max_tokens"])
        memory.add_message(db, user, "user", f"/research {topic}")
        memory.add_message(db, user, "assistant", reply, model_used=user.active_model)
        for part in split_message(reply):
            await update.message.reply_text(part)
    except Exception as e:
        await update.message.reply_text(f"⚠️ خطا: {e}")
    finally:
        db.close()


async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in _admin_ids():
        await update.message.reply_text("⛔ فقط ادمین.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /broadcast <msg>")
        return
    msg = " ".join(context.args)
    db = _db()
    try:
        sent = 0
        for u in memory.all_users(db):
            try:
                await context.bot.send_message(u.telegram_id, f"📢 {msg}")
                sent += 1
            except Exception:
                pass
        await update.message.reply_text(f"✅ ارسال شد به {sent} نفر.")
    finally:
        db.close()


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    db = _db()
    try:
        user = memory.get_or_create_user(db, update.effective_user.id)
        await _ensure_model(db, user)

        verbose = int(memory.get_preference(db, user, "verbose", "1") or "1")
        theme = memory.get_preference(db, user, "theme", "default") or "default"
        from services.llm_client import think_config
        cfg = think_config(memory.get_preference(db, user, "think", "low"))

        # active skill (if any) is appended to the soul system prompt so it shapes
        # every reply without overwriting the user's base persona.
        system = build_system_prompt(user, _default_soul())
        if cfg.get("hint"):
            system += f"\n\n{cfg['hint']}"
        skill_name = memory.get_preference(db, user, "active_skill")
        if skill_name:
            skill = memory.get_skill(db, user, skill_name)
            if skill:
                system = f"{system}\n\n=== ACTIVE SKILL: {skill.name} ===\n{skill.instructions}"

        sess = memory.get_current_session(db, user)
        history = memory.get_history(db, user, session=sess)
        context_msgs = list(history)
        summary = memory.get_compact_summary(db, sess.id)
        if summary:
            context_msgs = [{"role": "system",
                             "content": f"Earlier in this session (compacted):\n{summary}"}] + context_msgs
        messages = [{"role": "system", "content": system}] + context_msgs + [{"role": "user", "content": text}]
        await update.message.chat.send_action("typing")
        try:
            reply, steps = await asyncio.to_thread(_agentic_reply, user, messages, cfg)
        except Exception as e:
            await update.message.reply_text(f"⚠️ خطا: {e}")
            return
        memory.add_message(db, user, "user", text, session=sess)
        memory.add_message(db, user, "assistant", reply, model_used=user.active_model, session=sess)
        if memory.maybe_compact(db, user, sess):
            try:
                await update.message.reply_text("🗜 سشن طولانی شد — قدیمی‌ها خلاصه و فشرده شد (autocompact).")
            except Exception:
                pass

        # show the tools the agent used (verbose 1 = names, 2 = names + short note)
        if verbose >= 1 and steps and cfg.get("show_steps", True):
            seen = []
            for s in steps:
                if s not in seen:
                    seen.append(s)
            note = "🛠 استفاده شد: " + ", ".join(seen)
            if verbose >= 2:
                note += f"\n🔢 تعداد فراخوانی ابزار: {len(steps)}"
            try:
                await update.message.reply_text(note)
            except Exception:
                pass

        reply = _apply_theme(theme, reply)
        for part in split_message(reply):
            await update.message.reply_text(part)
        if user.tts_enabled:
            try:
                ogg = await asyncio.to_thread(text_to_speech, user, reply)
                with open(ogg, "rb") as f:
                    await update.message.reply_voice(f)
                os.remove(ogg)
            except Exception as e:
                print(f"[tts] failed: {e}")
    finally:
        db.close()
