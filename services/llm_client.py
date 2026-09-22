import logging
import os
import json

logger = logging.getLogger(__name__)


def _openai_cls():
    try:
        from openai import OpenAI
        return OpenAI
    except Exception:
        pass
    # Fallback: minimal OpenAI-compatible client over httpx (no openai pkg needed).
    import httpx as _httpx

    class _ModelItem:
        def __init__(self, _id):
            self.id = _id

    class _Models:
        def __init__(self, client):
            self._c = client

        def list(self):
            r = self._c._http.get(f"{self._c._base}/models")
            r.raise_for_status()
            data = [ _ModelItem(m.get("id", "")) for m in r.json().get("data", []) ]
            return type("ModelsResp", (), {"data": data})()

    class _Msg:
        def __init__(self, content, tool_calls=None):
            self.content = content
            self.tool_calls = tool_calls

    class _Choice:
        def __init__(self, msg):
            self.message = msg

    class _Completions:
        def __init__(self, client):
            self._c = client

        def create(self, model, messages, **kw):
            payload = {"model": model, "messages": messages}
            for k in ("max_tokens", "temperature", "tools", "tool_choice"):
                if kw.get(k) is not None:
                    payload[k] = kw[k]
            r = self._c._http.post(f"{self._c._base}/chat/completions", json=payload)
            r.raise_for_status()
            j = r.json()
            ch = (j.get("choices") or [{}])[0]
            m = ch.get("message") or {}
            content = m.get("content") or ""
            tcs = None
            if m.get("tool_calls"):
                tcs = []
                for tc in m["tool_calls"]:
                    fn = tc.get("function", {})
                    tcs.append(type("TC", (), {
                        "id": tc.get("id", ""),
                        "function": type("FN", (), {
                            "name": fn.get("name", ""),
                            "arguments": fn.get("arguments", "{}")})()})())
            return type("ChatResp", (), {"choices": [_Choice(_Msg(content, tcs))]})()

    class _Chat:
        def __init__(self, client):
            self.completions = _Completions(client)

    class _Images:
        def __init__(self, client):
            self._c = client

        def generate(self, **kw):
            r = self._c._http.post(f"{self._c._base}/images/generations", json=kw)
            r.raise_for_status()
            items = []
            for d in r.json().get("data", []):
                items.append(type("IMG", (), {"url": d.get("url"), "b64_json": d.get("b64_json")})())
            return type("ImgResp", (), {"data": items})()

    class _Speech:
        def __init__(self, client):
            self._c = client

        def create(self, **kw):
            raise RuntimeError("TTS needs the openai package")

    class _Transcriptions:
        def __init__(self, client):
            self._c = client

        def create(self, **kw):
            raise RuntimeError("STT needs the openai package")

    class _Audio:
        def __init__(self, client):
            self.speech = _Speech(client)
            self.transcriptions = _Transcriptions(client)

    class _Shim:
        def __init__(self, base_url, api_key, timeout=90, max_retries=2):
            self._base = (base_url or "").rstrip("/")
            self._http = _httpx.Client(
                headers={"Authorization": f"Bearer {api_key or 'no-key'}"},
                timeout=timeout)

        @property
        def models(self):
            return _Models(self)

        @property
        def chat(self):
            return _Chat(self)

        @property
        def images(self):
            return _Images(self)

        @property
        def audio(self):
            return _Audio(self)

    return _Shim

ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY", "").encode()

# Default persona for J-Rock. Override per-user with /soul or /setsystem,
# or globally with DEFAULT_SYSTEM_PROMPT env var / soul/jrock_default.md.
def _load_soul_file() -> str:
    import os as _os
    for cand in (_os.getenv("SOUL_FILE", ""),
                 _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
                               "soul", "jrock_default.md")):
        if cand and _os.path.isfile(cand):
            try:
                with open(cand, "r", encoding="utf-8") as f:
                    return f.read()
            except Exception:
                pass
    return "You are J-Rock, a helpful agentic AI assistant."

DEFAULT_SYSTEM_PROMPT = os.getenv("DEFAULT_SYSTEM_PROMPT", _load_soul_file())


def _fernet():
    """Lazily build the Fernet cipher; never crash at import if cryptography
    is missing (e.g. Termux without Rust) or the key is invalid."""
    if not ENCRYPTION_KEY:
        return None
    try:
        from cryptography.fernet import Fernet
        return Fernet(ENCRYPTION_KEY)
    except Exception as e:
        logger.warning(
            "cryptography unavailable or ENCRYPTION_KEY invalid (%s). "
            "API keys will not be encrypted. Generate a key with "
            "cryptography.fernet.Fernet.generate_key().", e
        )
        return None


def encrypt_key(api_key: str) -> str:
    f = _fernet()
    if not f:
        raise ValueError("ENCRYPTION_KEY env var not set or invalid")
    return f.encrypt(api_key.encode()).decode()


def decrypt_key(encrypted: str) -> str:
    f = _fernet()
    if not f:
        raise ValueError("ENCRYPTION_KEY env var not set or invalid")
    return f.decrypt(encrypted.encode()).decode()


def _default_base_url() -> str:
    return (os.getenv("AI_BASE_URL")
            or os.getenv("DEFAULT_BASE_URL", "https://cline2api-workers.azarnezam0.workers.dev/v1"))


def _default_api_key() -> str:
    return os.getenv("AI_API_KEY", os.getenv("DEFAULT_API_KEY", "no-key"))


def get_client(user):
    base_url = user.base_url or _default_base_url()
    if user.api_key_encrypted:
        api_key = decrypt_key(user.api_key_encrypted)
    else:
        api_key = _default_api_key()
    return _openai_cls()(
        base_url=base_url,
        api_key=api_key,
        timeout=90,       # don't hang forever if the endpoint is dead
        max_retries=2,
    )


def fetch_models(user) -> list[str]:
    client = get_client(user)
    models = client.models.list()
    return sorted([m.id for m in models.data])


# capability-specific model ids we should NOT pick as a default chat model
_SKIP_HINTS = ("/image", "/tts", "/stt", "/embedding", "/search", "/fetch", "combo")
_PREF_HINTS = ("gemini", "gpt", "claude", "llama", "flash", "opus", "sonnet", "deepseek", "qwen", "mistral")


def rank_models(models: list[str]) -> list[str]:
    """Order models best-first for auto-selection: skip capability-specific ids,
    then prefer common chat families, then the rest. Returns a ranked list."""
    if not models:
        return []
    chat_models = [m for m in models if not any(s in m.lower() for s in _SKIP_HINTS)]
    pool = chat_models or models
    lowered = [m.lower() for m in pool]
    ranked: list[str] = []
    used: set = set()
    for hint in _PREF_HINTS:
        for m, low in zip(pool, lowered):
            if hint in low and m not in used:
                ranked.append(m)
                used.add(m)
    for m in pool:
        if m not in used:
            ranked.append(m)
            used.add(m)
    return ranked


def pick_default_model(models: list[str]) -> str | None:
    ranked = rank_models(models)
    return ranked[0] if ranked else None


def probe_model(user, model: str) -> None:
    """Cheap validation call to confirm a model is usable (has credentials).
    Raises if the provider/model is unavailable."""
    client = get_client(user)
    client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=1,
    )


def chat_completion(user, messages: list[dict]) -> str:
    client = get_client(user)
    model = user.active_model or os.getenv("DEFAULT_MODEL", "gpt-4o")
    response = client.chat.completions.create(
        model=model,
        messages=messages,
        max_tokens=2048,
        temperature=0.7,
    )
    return response.choices[0].message.content


def analyze_image(user, image_base64: str, caption: str) -> str:
    client = get_client(user)
    model = user.active_model or os.getenv("DEFAULT_MODEL", "gpt-4o")
    response = client.chat.completions.create(
        model=model,
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": caption or "Describe this image in detail."},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}}
            ]
        }],
        max_tokens=1024,
    )
    return response.choices[0].message.content


def transcribe_audio(user, audio_path: str) -> str:
    client = get_client(user)
    with open(audio_path, "rb") as f:
        resp = client.audio.transcriptions.create(model="whisper-1", file=f)
    return resp.text


def get_base_and_key(user) -> tuple:
    """Return (base_url, api_key) for raw HTTP calls to extra 9Router endpoints
    (search, web/fetch) that aren't covered by the OpenAI SDK."""
    base_url = user.base_url or _default_base_url()
    if user.api_key_encrypted:
        api_key = decrypt_key(user.api_key_encrypted)
    else:
        api_key = _default_api_key()
    return base_url.rstrip("/"), api_key


def generate_image(user, prompt: str, model: str | None = None, size: str | None = None) -> dict:
    """Generate an image via the OpenAI-compatible /v1/images/generations endpoint.
    Returns {"url": ..., "b64_json": ...} (one will be populated)."""
    client = get_client(user)
    model = model or user.active_model or os.getenv("DEFAULT_IMAGE_MODEL", "openai/dall-e-3")
    kwargs: dict = {"model": model, "prompt": prompt, "n": 1}
    if size:
        kwargs["size"] = size
    resp = client.images.generate(**kwargs)
    item = resp.data[0]
    return {
        "url": getattr(item, "url", None),
        "b64_json": getattr(item, "b64_json", None),
    }


def run_agentic(user, messages: list[dict], tool_defs: list[dict], tool_registry: dict, max_iter: int = 8) -> str:
    """Agentic chat loop with tool/function calling.

    Sends `messages` (with `tool_defs`) to the model. If the model emits
    tool_calls, executes each via `tool_registry` (name -> callable(user, **args))
    and feeds the results back, looping until the model returns a final answer
    or `max_iter` is hit. Returns the final assistant text.
    """
    client = get_client(user)
    model = user.active_model or os.getenv("DEFAULT_MODEL", "gpt-4o")
    convo = list(messages)
    steps: list = []
    for _ in range(max_iter):
        resp = client.chat.completions.create(
            model=model,
            messages=convo,
            tools=tool_defs,
            tool_choice="auto",
            max_tokens=2048,
            temperature=0.7,
        )
        msg = resp.choices[0].message
        if not msg.tool_calls:
            return msg.content or "", steps
        # record the assistant turn that requested the tools
        convo.append({
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in msg.tool_calls
            ],
        })
        for tc in msg.tool_calls:
            name = tc.function.name
            steps.append(name)
            try:
                args = json.loads(tc.function.arguments or "{}")
            except Exception:
                args = {}
            func = tool_registry.get(name)
            if func is None:
                result = f"Unknown tool: {name}"
            else:
                try:
                    result = func(user, **args)
                except Exception as e:  # tool failure shouldn't kill the loop
                    result = f"Error in {name}: {e}"
            convo.append({"role": "tool", "tool_call_id": tc.id, "content": str(result)})
    # ran out of iterations — return whatever the model last produced
    return convo[-1].get("content") or "", steps
