"""J-Rock provider router: multi-provider auto-switch by real probe.

No blind fallback: every candidate (provider, model) is validated with a
cheap chat call (max_tokens=1) before it is selected. First usable wins.
"""
import logging
import os

logger = logging.getLogger(__name__)

PROVIDER_ORDER = ["cline-worker", "openrouter", "ollama", "lmstudio"]


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default)


def default_providers() -> list[dict]:
    """Built-in chain. Secrets come from env, never hardcoded."""
    return [
        {
            "name": "cline-worker",
            "base_url": _env("AI_BASE_URL", _env("DEFAULT_BASE_URL", "https://cline2api-workers.azarnezam0.workers.dev/v1")),
            "api_key": _env("AI_API_KEY", _env("DEFAULT_API_KEY", "no-key")),
            "enabled": True,
        },
        {
            "name": "openrouter",
            "base_url": "https://openrouter.ai/api/v1",
            "api_key": _env("OPENROUTER_API_KEY", ""),
            "enabled": bool(_env("OPENROUTER_API_KEY", "")),
        },
        {
            "name": "ollama",
            "base_url": _env("OLLAMA_BASE_URL", "http://localhost:11434/v1"),
            "api_key": _env("OLLAMA_API_KEY", "ollama"),
            "enabled": True,
        },
        {
            "name": "lmstudio",
            "base_url": _env("LMSTUDIO_BASE_URL", "http://localhost:1234/v1"),
            "api_key": _env("LMSTUDIO_API_KEY", "lm-studio"),
            "enabled": True,
        },
    ]


def user_providers(db, user) -> list[dict]:
    """Merge: user custom base_url/key first, then DB providers, then defaults."""
    from services.llm_client import decrypt_key
    out: list[dict] = []
    if getattr(user, "base_url", None):
        try:
            key = decrypt_key(user.api_key_encrypted) if user.api_key_encrypted else _env("AI_API_KEY", _env("DEFAULT_API_KEY", "no-key"))
        except Exception:
            key = _env("AI_API_KEY", _env("DEFAULT_API_KEY", "no-key"))
        out.append({"name": getattr(user, "active_provider", None) or "custom", "base_url": user.base_url, "api_key": key, "enabled": True})
    try:
        from db.models import Provider as ProviderModel
        rows = db.query(ProviderModel).filter(ProviderModel.telegram_id == user.telegram_id).all() if db is not None else []
        for r in rows:
            if not r.enabled:
                continue
            try:
                from services.llm_client import decrypt_key as _dec
                k = _dec(r.api_key_encrypted) if r.api_key_encrypted else ""
            except Exception:
                k = ""
            out.append({"name": r.name, "base_url": r.base_url, "api_key": k, "enabled": True})
    except Exception as e:
        logger.debug("db providers skipped: %s", e)
    for d in default_providers():
        if d["name"] == "cline-worker" and out:
            # avoid duplicate when user already set custom == cline-worker URL
            if any(p.get("base_url") == d["base_url"] for p in out):
                continue
        if d["enabled"] or d["name"] == "cline-worker":
            out.append(d)
    # dedupe by (base_url), keep order
    seen, uniq = set(), []
    for p in out:
        if p["base_url"] in seen:
            continue
        seen.add(p["base_url"])
        uniq.append(p)
    return uniq


def _client_for(base_url: str, api_key: str):
    from services.llm_client import _openai_cls
    return _openai_cls()(base_url=base_url, api_key=api_key or "no-key", timeout=60, max_retries=1)


def fetch_models_for(base_url: str, api_key: str) -> list[str]:
    c = _client_for(base_url, api_key)
    models = c.models.list()
    return sorted([m.id for m in models.data])


def probe_for(base_url: str, api_key: str, model: str) -> None:
    c = _client_for(base_url, api_key)
    c.chat.completions.create(model=model, messages=[{"role": "user", "content": "hi"}], max_tokens=1)


def auto_select(user, db=None, limit_per_provider: int = 6):
    """Return (provider_name, base_url, api_key, model) for first probe-passing candidate."""
    from services.llm_client import rank_models
    # If user pinned a model that exists, probe it first (fast path).
    preferred = getattr(user, "active_model", None) or _env("DEFAULT_MODEL", "")
    last_err = None
    if preferred:
        for p in user_providers(db, user):
            if not p.get("api_key"):
                continue
            try:
                probe_for(p["base_url"], p["api_key"], preferred)
                return p["name"], p["base_url"], p["api_key"], preferred
            except Exception as e:
                last_err = e
                continue
    for p in user_providers(db, user):
        if not p.get("api_key"):
            continue
        try:
            models = fetch_models_for(p["base_url"], p["api_key"])
        except Exception as e:
            last_err = e
            logger.warning("provider %s /models failed: %s", p["name"], e)
            continue
        if not models:
            continue
        for cand in rank_models(models)[:limit_per_provider]:
            try:
                probe_for(p["base_url"], p["api_key"], cand)
                return p["name"], p["base_url"], p["api_key"], cand
            except Exception as e:
                last_err = e
                logger.warning("probe %s @ %s failed: %s", cand, p["name"], e)
                continue
    raise RuntimeError(f"No usable provider+model found. Last error: {last_err}")
