"""J-Rock graphical TUI (Textual): chat + soul/skills/providers/models/logs panels."""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
load_dotenv()

try:
    from textual.app import App, ComposeResult
    from textual.containers import Horizontal, Vertical
    from textual.widgets import Header, Footer, Input, RichLog, Button, Static
    from rich.text import Text
except Exception:
    print("Textual not installed. Run: pip install textual rich")
    raise SystemExit(1)

from db.session import init_db, SessionLocal
from services.memory_service import MemoryService
from services.soul_service import build_system_prompt, load_default_soul
from services.provider_router import auto_select, user_providers
from services.llm_client import chat_completion

LOCAL_ID = int(os.getenv("LOCAL_USER_ID", "0"))

class JRockApp(App):
    CSS = (
        "Screen { layout: vertical; } "
        "#body { height: 1fr; min-height: 8; } "
        "#chat { width: 3fr; min-width: 30; scrollbar-gutter: stable; } "
        "#side { width: 1fr; min-width: 22; } "
        "#inp { height: 3; border: solid green; }"
    )
    def __init__(self):
        super().__init__()
        init_db()
        self.db = SessionLocal()
        self.memory = MemoryService()
        self.user = self.memory.get_or_create_user(self.db, LOCAL_ID, "local", "TUI")
        self.soul = load_default_soul()

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="body"):
            yield RichLog(id="chat", wrap=True, highlight=True, markup=False)
            with Vertical(id="side"):
                yield Static(f"🤖 J-Rock\nprovider={self.user.active_provider or 'auto'}\nmodel={self.user.active_model or 'auto'}", id="info")
                yield Button("Soul", id="b_soul")
                yield Button("Skills", id="b_skills")
                yield Button("Think", id="b_think")
                yield Button("Providers", id="b_prov")
                yield Button("Models", id="b_models")
                yield Button("Auto-pick", id="b_auto")
                yield Button("New chat", id="b_new")
        yield Input(placeholder="Type message or /help …", id="inp")
        yield Footer()

    def chat_log(self, msg: str):
        # Text() = no Rich-markup parsing, so [brackets] never eat words.
        self.query_one("#chat", RichLog).write(Text(str(msg)))

    async def on_mount(self):
        self.chat_log("🤖 J-Rock TUI — /soul /skill /think /provider /models /sessions /resume /newchat or free chat")
        self.query_one("#inp", Input).focus()

    def _skill_list_lines(self) -> list[str]:
        from services.tools import load_skill_files
        active = self.memory.get_preference(self.db, self.user, "active_skill")
        lines = []
        for s in self.memory.list_skills(self.db, self.user):
            lines.append(f"• {s.name}{' ✅' if s.name == active else ''}")
        for f in load_skill_files():
            lines.append(f"• 📁 {f['name']}{' ✅' if f['name'] == active else ''}")
        return lines or ["(no skills — add with /skill import <folder>)"]

    async def on_button_pressed(self, e: Button.Pressed):
        bid = e.button.id
        if bid == "b_soul":
            self.chat_log("🧬 soul preview:\n" + (self.user.system_prompt or self.soul)[:2000])
        elif bid == "b_skills":
            self.chat_log("🧠 Skills (/skill use <name>):\n" + "\n".join(self._skill_list_lines()))
        elif bid == "b_think":
            from services.llm_client import THINK_LEVELS
            order = list(THINK_LEVELS.keys())
            cur = self.memory.get_preference(self.db, self.user, "think", "low")
            nxt = order[(order.index(cur) + 1) % len(order)] if cur in order else "low"
            self.memory.set_preference(self.db, self.user, "think", nxt)
            self.chat_log(f"🧠 think: {cur} → {nxt}")
        elif bid == "b_prov":
            for p in user_providers(self.db, self.user):
                self.chat_log(f"🔌 {p['name']} — {p['base_url']}")
        elif bid == "b_models":
            try:
                from services.provider_router import fetch_models_for
                for p in user_providers(self.db, self.user)[:2]:
                    try:
                        ms = fetch_models_for(p["base_url"], p["api_key"])
                        self.chat_log(f"== {p['name']}: " + ", ".join(ms[:10]))
                    except Exception as ex:
                        self.chat_log(f"== {p['name']} failed: {ex}")
            except Exception as ex:
                self.chat_log(f"⚠️ {ex}")
        elif bid == "b_auto":
            try:
                pname, base_url, api_key, model = auto_select(self.user, self.db)
                self.user.base_url = base_url
                try:
                    from services.llm_client import encrypt_key
                    self.user.api_key_encrypted = encrypt_key(api_key)
                except Exception:
                    pass
                self.user.active_provider = pname
                self.user.active_model = model
                self.db.commit()
                self.chat_log(f"✅ auto: {pname} / {model}")
            except Exception as ex:
                self.chat_log(f"⚠️ auto failed: {ex}")
        elif bid == "b_new":
            s = self.memory.new_session(self.db, self.user)
            self.chat_log(f"✅ new session #{s.id}")

    async def on_input_submitted(self, e: Input.Submitted):
        text = e.value.strip()
        e.input.value = ""
        if not text:
            return
        self.chat_log(f"you> {text}")
        if text.startswith("/"):
            await self.on_input_submitted_cmd(text)
            return
        if not self.user.active_model:
            try:
                pname, base_url, api_key, model = auto_select(self.user, self.db)
                self.user.base_url = base_url
                self.user.active_provider = pname
                self.user.active_model = model
                self.db.commit()
                self.chat_log(f"✅ auto: {pname} / {model}")
            except Exception as ex:
                self.chat_log(f"⚠️ auto failed: {ex}")
                return
        system = build_system_prompt(self.user, self.soul)
        from services.llm_client import think_config
        cfg = think_config(self.memory.get_preference(self.db, self.user, "think", "low"))
        if cfg.get("hint"):
            system += f"\n\n{cfg['hint']}"
        skill = self.memory.get_preference(self.db, self.user, "active_skill")
        if skill:
            s = self.memory.get_skill(self.db, self.user, skill)
            if s:
                system += f"\n\n=== ACTIVE SKILL: {s.name} ===\n{s.instructions}"
        sess = self.memory.get_current_session(self.db, self.user)
        hist = self.memory.get_history(self.db, self.user, session=sess)
        ctx = list(hist)
        summary = self.memory.get_compact_summary(self.db, sess.id)
        if summary:
            ctx = [{"role": "system", "content": f"Earlier in this session (compacted):\n{summary}"}] + ctx
        msgs = [{"role": "system", "content": system}] + ctx + [{"role": "user", "content": text}]
        self.chat_log("…thinking")
        try:
            from handlers.chat_handlers import _agentic_reply
            reply, steps = await self.run_in_thread(_agentic_reply, self.user, msgs, cfg)
        except Exception:
            reply, steps = chat_completion(self.user, msgs, cfg["temperature"], cfg["max_tokens"]), []
        self.memory.add_message(self.db, self.user, "user", text, session=sess)
        self.memory.add_message(self.db, self.user, "assistant", reply, model_used=self.user.active_model, session=sess)
        if self.memory.maybe_compact(self.db, self.user, sess):
            self.chat_log("🗜 autocompact: old messages summarized.")
        if steps and cfg.get("show_steps", True):
            seen = []
            for s in steps:
                if s not in seen:
                    seen.append(s)
            self.chat_log("🛠 " + ", ".join(seen))
        self.chat_log(f"J-Rock> {reply or '(empty)'}")

    async def on_input_submitted_cmd(self, text: str):
        if text in ("/help", "/menu"):
            self.chat_log("/soul /skill list|use|import /think off|low|medium|high /provider /models /sessions /resume <id> /newchat + free chat")
        elif text.startswith("/soul"):
            self.chat_log("🧬 " + (self.user.system_prompt or self.soul)[:2000])
        elif text.startswith("/skill"):
            parts = text.split()
            if len(parts) == 1 or parts[1] == "list":
                self.chat_log("🧠 Skills:\n" + "\n".join(self._skill_list_lines()))
            elif len(parts) >= 3 and parts[1] == "use":
                name = parts[2]
                ok = self.memory.get_skill(self.db, self.user, name)
                if not ok:
                    from services.tools import load_skill_files
                    ok = name in [f["name"] for f in load_skill_files()]
                if not ok:
                    self.chat_log(f"⛔ no skill «{name}»")
                else:
                    self.memory.set_preference(self.db, self.user, "active_skill", name)
                    self.chat_log(f"✅ active_skill={name}")
            elif len(parts) >= 3 and parts[1] == "import":
                import os as _os
                given = parts[2]
                sdir = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "skills")
                base = None
                for c in [_os.path.join(sdir, given),
                          _os.path.join(sdir, given, "SKILL.md"),
                          _os.path.join(sdir, given, "skill.md")]:
                    if _os.path.isfile(c):
                        base = c
                        break
                if base is None:
                    self.chat_log(f"⛔ skills/{given} not found")
                else:
                    from services.tools import _parse_skill_file as _psf
                    s = _psf(base, given)
                    self.memory.add_skill(self.db, self.user, s["name"], s["instructions"])
                    self.chat_log(f"✅ imported {s['name']}")
            else:
                self.chat_log("Usage: /skill list|use <name>|import <folder|file>")
        elif text.startswith("/think"):
            from services.llm_client import THINK_LEVELS
            parts = text.split()
            if len(parts) == 1:
                self.chat_log(f"think={self.memory.get_preference(self.db, self.user, 'think', 'low')} (off|low|medium|high)")
            elif parts[1] in THINK_LEVELS:
                self.memory.set_preference(self.db, self.user, "think", parts[1])
                self.chat_log(f"✅ think={parts[1]}")
            else:
                self.chat_log("Usage: /think off|low|medium|high")
        elif text.strip() == "/sessions":
            for s in self.memory.list_sessions(self.db, self.user)[:15]:
                mark = " ✅" if s.id == self.user.current_session_id else ""
                self.chat_log(f"#{s.id}{mark} — {s.message_count} msgs")
        elif text.startswith("/resume"):
            parts = text.split()
            if len(parts) < 2 or not parts[1].isdigit():
                self.chat_log("Usage: /resume <id> (see /sessions)")
            else:
                s = self.memory.resume_session(self.db, self.user, int(parts[1]))
                self.chat_log(f"✅ resumed #{s.id}" if s else "⛔ no such session")
        else:
            self.chat_log(f"Use CLI for full `/` set: python jrock_cli.py ({text})")

    async def run_in_thread(self, fn, *a):
        import asyncio
        return await asyncio.to_thread(fn, *a)

if __name__ == "__main__":
    JRockApp().run()
