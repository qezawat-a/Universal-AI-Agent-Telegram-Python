"""History / memory / preference handlers for OmniAgent."""
from telegram import Update
from telegram.ext import ContextTypes

from db.session import SessionLocal
from services.memory_service import MemoryService
from services.message_utils import split_message

memory = MemoryService()


async def cmd_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = SessionLocal()
    try:
        user = memory.get_or_create_user(db, update.effective_user.id)
        sess = memory.get_current_session(db, user)
        rows = memory.get_history(db, user, limit=10)
        if not rows:
            await update.message.reply_text(f"تاریخچه‌ای در سشن #{sess.id} نیست.")
            return
        text = "\n\n".join(f"{m['role']}: {m['content']}" for m in rows)
        for part in split_message(text):
            await update.message.reply_text(part)
    finally:
        db.close()


async def _session_lines(db, user, limit: int = 15) -> list[str]:
    """Sessions newest-first with msg count + last-message preview."""
    from db.models import ConversationHistory
    sessions = memory.list_sessions(db, user)[:limit]
    lines = []
    for s in sessions:
        mark = " ✅" if s.id == user.current_session_id else ""
        last = (
            db.query(ConversationHistory)
            .filter(ConversationHistory.session_id == s.id)
            .order_by(ConversationHistory.id.desc())
            .first()
        )
        preview = ""
        if last and last.content:
            preview = " — " + last.content.strip().replace("\n", " ")[:60]
        lines.append(f"#{s.id}{mark} — {s.message_count} پیام{preview}")
    return lines


async def cmd_sessions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = SessionLocal()
    try:
        user = memory.get_or_create_user(db, update.effective_user.id)
        lines = await _session_lines(db, user)
        if not lines:
            await update.message.reply_text("هیچ سشنی نیست. با /newchat شروع کن.")
            return
        await update.message.reply_text(
            "سشن‌ها (جدید به قدیم):\n" + "\n".join(lines)
            + "\n\nبرگرد با: /resume <id>"
        )
    finally:
        db.close()


async def cmd_newchat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = SessionLocal()
    try:
        user = memory.get_or_create_user(db, update.effective_user.id)
        old = user.current_session_id
        sess = memory.new_session(db, user)
        await update.message.reply_text(f"✅ سشن جدید #{sess.id} ساخته شد (سشن قبلی #{old} محفوظه).")
    finally:
        db.close()


async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = SessionLocal()
    try:
        user = memory.get_or_create_user(db, update.effective_user.id)
        # No id given -> show pick-from-list instead of bare usage.
        if not context.args or not context.args[0].isdigit():
            lines = await _session_lines(db, user)
            if not lines:
                await update.message.reply_text("هیچ سشنی نیست. با /newchat شروع کن.")
                return
            await update.message.reply_text(
                "کدوم سشن؟\n" + "\n".join(lines) + "\n\nمثال: /resume 3"
            )
            return
        sid = int(context.args[0])
        sess = memory.resume_session(db, user, sid)
        if sess:
            hist = memory.get_history(db, user, limit=3)
            tail = ""
            if hist:
                tail = "\nآخرین پیام‌ها:\n" + "\n".join(
                    f"{m['role']}: {m['content'][:120]}" for m in hist[-3:]
                )
            await update.message.reply_text(f"✅ برگشتی به سشن #{sess.id} ({sess.message_count} پیام).{tail}")
        else:
            await update.message.reply_text("⛔ اون سشن متعلق به تو نیست یا وجود ندارد.")
    finally:
        db.close()


async def cmd_forget(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = SessionLocal()
    try:
        user = memory.get_or_create_user(db, update.effective_user.id)
        sess = memory.get_current_session(db, user)
        memory.clear_session(db, user, sess)
        await update.message.reply_text(f"✅ سشن فعلی #{sess.id} پاک شد (بقیه سشن‌ها می‌مونن).")
    finally:
        db.close()


async def cmd_setpref(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 1:
        await update.message.reply_text("Usage: /setpref <key> <value>  (یا /setpref <key> برای حذف)")
        return
    key = context.args[0]
    # single-arg form -> delete that preference
    if len(context.args) == 1:
        db = SessionLocal()
        try:
            user = memory.get_or_create_user(db, update.effective_user.id)
            if memory.delete_preference(db, user, key):
                await update.message.reply_text(f"✅ preference «{key}» پاک شد.")
            else:
                await update.message.reply_text(f"⚠️ preference «{key}» پیدا نشد.")
        finally:
            db.close()
        return
    value = " ".join(context.args[1:])
    db = SessionLocal()
    try:
        user = memory.get_or_create_user(db, update.effective_user.id)
        memory.set_preference(db, user, key, value)
        await update.message.reply_text(f"✅ {key} = {value}")
    finally:
        db.close()


async def cmd_clearprefs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = SessionLocal()
    try:
        user = memory.get_or_create_user(db, update.effective_user.id)
        n = memory.clear_preferences(db, user)
        await update.message.reply_text(f"✅ {n} preference پاک شد.")
    finally:
        db.close()


async def cmd_skill(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "Skill = تکه‌پرامپت / پرسونای ذخیره‌شده که لایو به چت اضافه می‌شه.\n"
            "/skill add <name> | <instructions...>  - ذخیره اسکیل\n"
            "/skill list  - لیست اسکیل‌ها (✅ = فعال, 📁 = فایل)\n"
            "/skill use <name>  - فعال‌سازی\n"
            "/skill del <name>  - حذف\n"
            "/skill import <file.md> - وارد کردن از skills/"
        )
        return
    sub = context.args[0].lower()
    db = SessionLocal()
    try:
        user = memory.get_or_create_user(db, update.effective_user.id)
        if sub == "list":
            skills = memory.list_skills(db, user)
            try:
                from services.tools import load_skill_files as _lsf
                files = _lsf()
            except Exception:
                files = []
            if not skills and not files:
                await update.message.reply_text(
                    "هیچ skillی نداری. بساز:\n/skill add <name> | <دستورالعمل>"
                )
                return
            active = memory.get_preference(db, user, "active_skill")
            lines = [f"🧠 Skillها ({len(skills)} db + {len(files)} file):"]
            for s in skills:
                mark = " ✅" if s.name == active else ""
                lines.append(f"• {s.name}{mark}")
            for f in files:
                mark = " ✅" if f["name"] == active else ""
                lines.append(f"• 📁 {f['name']}{mark} ({f['file']})")
            await update.message.reply_text("\n".join(lines))
            return
        if sub == "add":
            rest = " ".join(context.args[1:])
            if "|" not in rest:
                await update.message.reply_text("فرمت: /skill add <name> | <دستورالعمل>")
                return
            name, _, instructions = rest.partition("|")
            name = name.strip()
            instructions = instructions.strip()
            if not name or not instructions:
                await update.message.reply_text("نام و دستورالعمل هر دو لازمه.")
                return
            memory.add_skill(db, user, name, instructions)
            await update.message.reply_text(
                f"✅ Skill «{name}» ذخیره شد. با /skill use {name} فعالش کن."
            )
            return
        if sub == "use":
            if len(context.args) < 2:
                await update.message.reply_text("Usage: /skill use <name>")
                return
            name = context.args[1].strip()
            if not memory.get_skill(db, user, name):
                # allow file-based skills too
                try:
                    from services.tools import load_skill_files as _lsf2
                    fnames = [f["name"] for f in _lsf2()]
                except Exception:
                    fnames = []
                if name not in fnames:
                    await update.message.reply_text(f"⛔ Skill «{name}» وجود ندارد.")
                    return
            memory.set_preference(db, user, "active_skill", name)
            await update.message.reply_text(
                f"✅ Skill «{name}» فعال شد و به سیستم‌پرامپت هر پیام اضافه می‌شه."
            )
            return
        if sub == "del":
            if len(context.args) < 2:
                await update.message.reply_text("Usage: /skill del <name>")
                return
            name = context.args[1].strip()
            if memory.delete_skill(db, user, name):
                if memory.get_preference(db, user, "active_skill") == name:
                    memory.delete_preference(db, user, "active_skill")
                await update.message.reply_text(f"✅ Skill «{name}» حذف شد.")
            else:
                await update.message.reply_text(f"⛔ Skill «{name}» پیدا نشد.")
            return
        if sub == "import":
            if len(context.args) < 2:
                await update.message.reply_text("Usage: /skill import <file.md>")
                return
            import os as _os
            fn = context.args[1].strip()
            base = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "skills", fn)
            if not _os.path.isfile(base):
                await update.message.reply_text(f"⛔ فایل skills/{fn} پیدا نشد.")
                return
            try:
                with open(base, "r", encoding="utf-8") as f:
                    txt = f.read()
                name = txt.strip().splitlines()[0].lstrip("# ").strip() or fn[:-3]
                memory.add_skill(db, user, name, txt)
                await update.message.reply_text(f"✅ Skill «{name}» از فایل import شد.")
            except Exception as e:
                await update.message.reply_text(f"⚠️ import failed: {e}")
            return
        await update.message.reply_text("زیر‌دستور ناشناخته. بدون آرگومان بزن: /skill")
    finally:
        db.close()
