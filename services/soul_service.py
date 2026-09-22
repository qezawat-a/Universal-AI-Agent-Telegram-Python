"""J-Rock soul builder: prompt + persona + style -> system prompt."""
import os


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_default_soul() -> str:
    for cand in (
        os.getenv("SOUL_FILE", ""),
        os.path.join(_repo_root(), "soul", "jrock_default.md"),
    ):
        if cand and os.path.isfile(cand):
            try:
                with open(cand, "r", encoding="utf-8") as f:
                    return f.read()
            except Exception:
                pass
    return "You are J-Rock, a helpful agentic AI assistant."


def build_system_prompt(user, default_soul: str | None = None) -> str:
    """Combine soul prompt + persona + style. user.system_prompt overrides base."""
    base = (getattr(user, "system_prompt", None) or default_soul
            or os.getenv("DEFAULT_SYSTEM_PROMPT") or load_default_soul())
    persona = getattr(user, "soul_persona", None)
    style = getattr(user, "soul_style", None)
    parts = [base]
    if persona:
        parts.append(f"\n\n=== PERSONA ===\n{persona}")
    if style:
        parts.append(f"\n\n=== STYLE ===\n{style}")
    return "\n".join(parts)
