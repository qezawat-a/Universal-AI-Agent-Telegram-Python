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
    CSS = "Screen { layout: vertical; } #body { height: 1fr; } #chat { width: 2fr; } #side { width: 1fr; }"
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
            yield RichLog(id="chat", wrap=True, highlight=True)
            with Vertical(id="side"):
                yield Static(f"🤖 J-Rock\nprovider={self.user.active_provider or 'auto'}\nmodel={self.user.active_model or 'auto'}", id="info")
                yield Button("Soul", id="b_soul")
                yield Button("Providers", id="b_prov")
                yield Button("Models", id="b_models")
                yield Button("Auto-pick", id="b_auto")
                yield Button("New chat", id="b_new")
        yield Input(placeholder="Type message or /help …", id="inp")
        yield Footer()

    def chat_log(self, msg: str):
        self.query_one("#chat", RichLog).write(msg)

    async def on_mount(self):
        self.chat_log("🤖 J-Rock TUI — /soul /provider /models /sessions /resume /newchat or free chat")

    async def on_button_pressed(self, e: Button.Pressed):
        bid = e.button.id
        if bid == "b_soul":
            self.chat_log("🧬 soul preview:\n" + (self.user.system_prompt or self.soul)[:2000])
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
        hist = self.memory.get_history(self.db, self.user)
        msgs = [{"role": "system", "content": system}] + hist + [{"role": "user", "content": text}]
        self.chat_log("…thinking")
        try:
            from handlers.chat_handlers import _agentic_reply
            reply, steps = await self.run_in_thread(_agentic_reply, self.user, msgs)
        except Exception:
            reply, steps = chat_completion(self.user, msgs), []
        self.memory.add_message(self.db, self.user, "user", text)
        self.memory.add_message(self.db, self.user, "assistant", reply, model_used=self.user.active_model)
        self.chat_log(f"J-Rock> {reply or '(empty)'}")

    async def on_input_submitted_cmd(self, text: str):
        if text in ("/help", "/menu"):
            self.chat_log("/soul /provider /models /sessions /resume <id> /newchat + free chat")
        elif text.startswith("/soul"):
            self.chat_log("🧬 " + (self.user.system_prompt or self.soul)[:2000])
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
