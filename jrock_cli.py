"""J-Rock terminal REPL — same `/` commands as Telegram + free chat."""
import os
import sys
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from db.session import init_db, SessionLocal
from services.memory_service import MemoryService
from services.llm_client import chat_completion, rank_models, probe_model, fetch_models
from services.soul_service import build_system_prompt, load_default_soul
from services.provider_router import auto_select, user_providers
from services.mcp_client import list_servers, fs_list, fs_read, sqlite_query
from services.tools import load_skill_files

try:
    from rich.console import Console
    from rich.panel import Panel
    console = Console()
    def say(t, style=""):
        console.print(t, style=style)
except Exception:
    console = None
    def say(t, style=""):
        print(t)

LOCAL_ID = int(os.getenv("LOCAL_USER_ID", "0"))
memory = MemoryService()
SOUL = load_default_soul()
HELP = (
    "/soul show|set|reset /skill list|add|use|del|import /provider list|auto|use|add|del "
    "/mcp list|fs|read|sql /models /setmodel /settings /status /gen /newchat /sessions /resume /forget /think /autocompact /quit"
)

def get_user(db):
    return memory.get_or_create_user(db, LOCAL_ID, "local", "Terminal")

def ensure_model(db, user):
    if user.active_model:
        return user.active_model
    try:
        pname, base_url, api_key, model = auto_select(user, db)
        user.base_url = base_url
        try:
            from services.llm_client import encrypt_key
            user.api_key_encrypted = encrypt_key(api_key)
        except Exception:
            pass
        user.active_provider = pname
        user.active_model = model
        db.commit()
        say(f"✅ auto: {pname} / {model}", "green")
        return model
    except Exception as e:
        say(f"⚠️ auto failed: {e}", "red")
        return None

def do_cmd(db, user, line: str) -> bool:
    parts = line.strip().split()
    if not parts:
        return True
    cmd = parts[0].lower()
    args = parts[1:]
    if cmd in ("/quit", "/exit", "/q"):
        return False
    if cmd == "/help" or cmd == "/menu":
        say(HELP, "cyan")
    elif cmd == "/settings" or cmd == "/status":
        prefs = memory.get_all_preferences(db, user)
        say(f"J-Rock provider={user.active_provider or 'auto'} model={user.active_model or 'auto'} "
            f"theme={prefs.get('theme','default')} skills={len(memory.list_skills(db, user))}", "cyan")
    elif cmd == "/models":
        try:
            for p in user_providers(db, user):
                try:
                    from services.provider_router import fetch_models_for
                    ms = fetch_models_for(p["base_url"], p["api_key"])
                    say(f"== {p['name']} ({len(ms)}):", "cyan")
                    for m in ms[:30]:
                        say(f"  {m}")
                except Exception as e:
                    say(f"== {p['name']} failed: {e}", "red")
        except Exception as e:
            say(f"⚠️ {e}", "red")
    elif cmd == "/provider":
        if not args or args[0] == "list":
            for p in user_providers(db, user):
                say(f"• {p['name']} — {p['base_url']}")
        elif args[0] == "auto":
            user.active_provider = None
            user.active_model = None
            db.commit()
            ensure_model(db, user)
        elif args[0] == "use" and len(args) > 1:
            user.active_provider = args[1]
            user.active_model = None
            db.commit()
            say(f"✅ locked to {args[1]}", "green")
        else:
            say("Usage: /provider list|auto|use <name>", "yellow")
    elif cmd == "/soul":
        if not args or args[0] == "show":
            say((user.system_prompt or SOUL)[:3000], "cyan")
            say(f"\nPERSONA: {user.soul_persona or '(none)'}", "cyan")
            say(f"STYLE: {user.soul_style or '(none)'}", "cyan")
        elif len(args) >= 3 and args[0] == "set":
            if args[1] == "prompt":
                user.system_prompt = " ".join(args[2:])
            elif args[1] == "persona":
                user.soul_persona = " ".join(args[2:])
            elif args[1] == "style":
                user.soul_style = " ".join(args[2:])
            db.commit()
            say("✅ soul updated", "green")
        elif args[0] == "reset":
            user.system_prompt = None
            user.soul_persona = None
            user.soul_style = None
            db.commit()
            say("✅ soul reset", "green")
    elif cmd == "/skill":
        if not args or args[0] == "list":
            for s in memory.list_skills(db, user):
                mark = " ✅" if s.name == memory.get_preference(db, user, "active_skill") else ""
                say(f"• {s.name}{mark}")
            for f in load_skill_files():
                mark = " ✅" if f["name"] == memory.get_preference(db, user, "active_skill") else ""
                say(f"• 📁 {f['name']}{mark} — {(f.get('description') or '')[:80]}")
        elif len(args) >= 2 and args[0] == "use":
            memory.set_preference(db, user, "active_skill", args[1])
            say(f"✅ active_skill={args[1]}", "green")
        elif len(args) >= 2 and args[0] == "import":
            import os as _os
            given = args[1]
            sdir = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "skills")
            base = None
            for c in [_os.path.join(sdir, given),
                      _os.path.join(sdir, given, "SKILL.md"),
                      _os.path.join(sdir, given, "skill.md")]:
                if _os.path.isfile(c):
                    base = c
                    break
            if base is None:
                say(f"⛔ skills/{given} not found", "red")
            else:
                from services.tools import _parse_skill_file as _psf
                s = _psf(base, given)
                memory.add_skill(db, user, s["name"], s["instructions"])
                say(f"✅ imported {s['name']}", "green")
        else:
            say("Usage: /skill list|use <name>|import <folder|file>", "yellow")
    elif cmd == "/think":
        from services.llm_client import THINK_LEVELS, think_config
        if not args:
            say(f"think={memory.get_preference(db, user, 'think', 'low')} (off|low|medium|high)", "cyan")
        elif args[0] in THINK_LEVELS:
            memory.set_preference(db, user, "think", args[0])
            say(f"✅ think={args[0]}", "green")
        else:
            say("Usage: /think off|low|medium|high", "yellow")
    elif cmd == "/autocompact":
        if not args:
            say(f"autocompact={memory.get_preference(db, user, 'autocompact', 'on')}", "cyan")
        elif args[0] in ("on", "off"):
            memory.set_preference(db, user, "autocompact", args[0])
            say(f"✅ autocompact={args[0]}", "green")
        else:
            say("Usage: /autocompact on|off", "yellow")
    elif cmd == "/mcp":
        if not args or args[0] == "list":
            for s in list_servers():
                say(f"• {s['name']} — {s.get('description','')}")
        elif len(args) >= 2 and args[0] in ("fs", "read"):
            say(fs_list(args[1]) if args[0] == "fs" else fs_read(args[1])[:4000])
        else:
            say("Usage: /mcp list|fs <path>|read <file>", "yellow")
    elif cmd == "/newchat":
        s = memory.new_session(db, user)
        say(f"✅ new session #{s.id}", "green")
    elif cmd == "/sessions":
        ss = memory.list_sessions(db, user)
        if not ss:
            say("No sessions yet.", "yellow")
        for s in ss[:15]:
            mark = " ✅" if s.id == user.current_session_id else ""
            say(f"#{s.id}{mark} — {s.message_count} msgs")
    elif cmd == "/resume":
        if not args or not args[0].isdigit():
            say("Usage: /resume <id>  (see /sessions)", "yellow")
        else:
            s = memory.resume_session(db, user, int(args[0]))
            say(f"✅ resumed #{s.id}" if s else "⛔ no such session", "green" if s else "red")
    elif cmd == "/forget":
        memory.clear_session(db, user)
        say("✅ session cleared", "green")
    else:
        say(f"unknown {cmd} — {HELP}", "yellow")
    return True

def chat(db, user, text: str):
    ensure_model(db, user)
    from services.llm_client import think_config
    cfg = think_config(memory.get_preference(db, user, "think", "low"))
    system = build_system_prompt(user, SOUL)
    if cfg.get("hint"):
        system += f"\n\n{cfg['hint']}"
    skill = memory.get_preference(db, user, "active_skill")
    if skill:
        s = memory.get_skill(db, user, skill)
        if s:
            system += f"\n\n=== ACTIVE SKILL: {s.name} ===\n{s.instructions}"
    sess = memory.get_current_session(db, user)
    hist = memory.get_history(db, user, session=sess)
    ctx = list(hist)
    summary = memory.get_compact_summary(db, sess.id)
    if summary:
        ctx = [{"role": "system", "content": f"Earlier in this session (compacted):\n{summary}"}] + ctx
    msgs = [{"role": "system", "content": system}] + ctx + [{"role": "user", "content": text}]
    try:
        from handlers.chat_handlers import _agentic_reply
        reply, steps = _agentic_reply(user, msgs, cfg)
    except Exception:
        reply, steps = chat_completion(user, msgs, cfg["temperature"], cfg["max_tokens"]), []
    memory.add_message(db, user, "user", text, session=sess)
    memory.add_message(db, user, "assistant", reply, model_used=user.active_model, session=sess)
    if memory.maybe_compact(db, user, sess):
        say("🗜 autocompact: old messages summarized.", "cyan")
    if steps and cfg.get("show_steps", True):
        say("🛠 " + ", ".join(dict.fromkeys(steps)), "yellow")
    say(reply or "(empty)")

def main():
    init_db()
    db = SessionLocal()
    user = get_user(db)
    say("🤖 J-Rock terminal — type /help, /quit to exit", "bold cyan")
    while True:
        try:
            line = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not line:
            continue
        if line.startswith("/"):
            if not do_cmd(db, user, line):
                break
        else:
            chat(db, user, line)

if __name__ == "__main__":
    main()
