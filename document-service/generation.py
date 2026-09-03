"""Answer generation over retrieved passages — the "Ask" mode.

Retrieval (``embeddings`` + ``reranker`` + ``vector_store``) finds the source
passages; this module asks an LLM to turn them into a grounded, cited answer.
The reader picks the model per session; a model is identified as
``provider:model`` (split on the first colon):

  ``ollama:<tag>``       a model served by a local Ollama — free, offline
  ``anthropic:<model>``  the Claude API      (needs ``ANTHROPIC_API_KEY``)
  ``openai:<model>``     the OpenAI API      (needs ``OPENAI_API_KEY``)
  ``google:<model>``     the Gemini API      (needs ``GEMINI_API_KEY``)

Cloud providers appear only when their key is set; Ollama models are discovered
live from ``/api/tags``. ``anthropic`` is reached through its own SDK (imported
lazily); ``ollama`` / ``openai`` / ``google`` share one OpenAI-compatible
streaming path over ``httpx``.
"""

import json
import logging
import os
import time
from typing import AsyncIterator, List, Optional, Tuple

logger = logging.getLogger("ai_librarian")

# Decoding knobs, shared across providers.
GENERATION_MAX_TOKENS = int(os.environ.get("GENERATION_MAX_TOKENS", "2000"))
GENERATION_TEMPERATURE = float(os.environ.get("GENERATION_TEMPERATURE", "0.2"))
GENERATION_TIMEOUT = float(os.environ.get("GENERATION_TIMEOUT", "120"))

# How many reranked passages to put in front of the model, and a character
# ceiling on the whole context block (a rough proxy for tokens — the passages
# are already token-budgeted chunks). Cloud models get a larger slice because
# their context windows dwarf a typical local model's.
ASK_CONTEXT_PASSAGES = int(os.environ.get("ASK_CONTEXT_PASSAGES", "10"))
ASK_CONTEXT_PASSAGES_CLOUD = int(os.environ.get("ASK_CONTEXT_PASSAGES_CLOUD", "30"))
ASK_MAX_CONTEXT_CHARS = int(os.environ.get("ASK_MAX_CONTEXT_CHARS", "10000"))

# Local models default to a 2K–4K context window and would silently truncate a
# 10-passage prompt. Ask mode talks to Ollama over its native /api/chat so it can
# raise num_ctx per request.
OLLAMA_NUM_CTX = int(os.environ.get("OLLAMA_NUM_CTX", "8192"))

# provider -> (default model list, API-key env var). Ollama is not here — it is
# discovered, not configured.
_CLOUD = {
    "anthropic": ("claude-opus-5,claude-sonnet-5,claude-haiku-4-5", "ANTHROPIC_API_KEY"),
    "openai": ("gpt-5.1,gpt-5.1-mini", "OPENAI_API_KEY"),
    "google": ("gemini-2.5-pro,gemini-2.5-flash", "GEMINI_API_KEY"),
}
_CLOUD_ORDER = ("anthropic", "openai", "google")
_OPENAI_COMPAT_BASE = {
    "openai": "https://api.openai.com/v1",
    "google": "https://generativelanguage.googleapis.com/v1beta/openai",
}

_OLLAMA_TTL = 5.0
_ollama_cache: Tuple[float, Optional[List[dict]]] = (0.0, None)

_SYSTEM_PROMPT = (
    "You are the assistant for a personal reading library. Answer the user's "
    "question using only the numbered sources below. Cite every claim with the "
    "number of the source it came from in square brackets, like [1] or [2][3]. "
    "If the sources do not contain the answer, say so plainly and do not guess. "
    "The sources are the top matches from a search, not the whole library — if "
    "the question calls for broader coverage, answer from what is here and note "
    "what may be missing. Be concise and preserve any mathematical notation "
    "exactly as written."
)


class GenerationError(RuntimeError):
    """A provider failed to produce an answer (network, auth, bad response)."""


def _ollama_url() -> str:
    return os.environ.get("OLLAMA_URL", "http://host.docker.internal:11434").strip().rstrip("/")


def _default_model_env() -> str:
    return os.environ.get("GENERATION_MODEL", "").strip()


def _cloud_key(provider: str) -> str:
    var = _CLOUD.get(provider, (None, None))[1]
    return (os.environ.get(var) or "").strip() if var else ""


def _cloud_models(provider: str) -> List[str]:
    override = os.environ.get(f"{provider.upper()}_MODELS", "").strip()
    raw = override or _CLOUD[provider][0]
    return [name.strip() for name in raw.split(",") if name.strip()]


def _parse_ollama_tags(payload: Optional[dict]) -> List[dict]:
    out: List[dict] = []
    for entry in (payload or {}).get("models") or []:
        name = entry.get("name") or entry.get("model")
        if name:
            out.append({"id": f"ollama:{name}", "label": name, "provider": "ollama"})
    return out


async def _fetch_ollama_models() -> List[dict]:
    """Local Ollama models, ``[]`` if it is not reachable. Cached ~5s."""
    global _ollama_cache
    url = _ollama_url()
    if not url:
        return []
    now = time.monotonic()
    fetched_at, cached = _ollama_cache
    if cached is not None and now - fetched_at < _OLLAMA_TTL:
        return cached

    import httpx

    models: List[dict] = []
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(f"{url}/api/tags")
            response.raise_for_status()
            models = _parse_ollama_tags(response.json())
    except Exception as exc:  # unreachable, refused, bad JSON — all non-fatal
        logger.debug("Ollama model list unavailable at %s: %s", url, exc)
        models = []
    _ollama_cache = (now, models)
    return models


async def list_models() -> List[dict]:
    """Every model the reader may pick, ``{id, label, provider}`` each.

    Ollama first (discovered), then each cloud provider whose key is set, in a
    stable order. De-duplicated by id.
    """
    out: List[dict] = list(await _fetch_ollama_models())
    for provider in _CLOUD_ORDER:
        if _cloud_key(provider):
            out.extend(
                {"id": f"{provider}:{name}", "label": name, "provider": provider}
                for name in _cloud_models(provider)
            )
    seen = set()
    deduped = []
    for model in out:
        if model["id"] not in seen:
            seen.add(model["id"])
            deduped.append(model)
    return deduped


async def default_model() -> Optional[str]:
    """The model used when the request names none: the env default if available,
    otherwise the first listed (Ollama wins, then anthropic/openai/google)."""
    models = await list_models()
    if not models:
        return None
    ids = [model["id"] for model in models]
    env_default = _default_model_env()
    return env_default if env_default in ids else ids[0]


async def enabled() -> bool:
    return bool(await list_models())


async def backend_info() -> dict:
    """What ``/config`` reports so the UI can show or hide the Ask tab."""
    models = await list_models()
    return {
        "enabled": bool(models),
        "default_model": await default_model(),
        "providers": sorted({model["provider"] for model in models}),
    }


def context_passages_for(model_id: str) -> int:
    """How many retrieved passages to show `model_id` (and cite)."""
    provider = (model_id or "").partition(":")[0]
    return ASK_CONTEXT_PASSAGES if provider == "ollama" else ASK_CONTEXT_PASSAGES_CLOUD


def disabled_reason() -> str:
    return (
        "Ask mode has no models available — start Ollama on the server, or set "
        "an API key (ANTHROPIC_API_KEY / OPENAI_API_KEY / GEMINI_API_KEY)."
    )


def _passage_location(source: dict) -> str:
    page = source.get("page")
    page_end = source.get("page_end")
    if page is None:
        return "location unknown"
    if page_end and page_end != page:
        return f"pp. {page}-{page_end}"
    return f"p. {page}"


def build_ask_prompt(
    question: str,
    sources: List[dict],
    history: Optional[List[dict]] = None,
    max_passages: Optional[int] = None,
) -> Tuple[str, List[dict], List[dict]]:
    """Build the ``(system, messages, used_sources)`` triple for a question.

    ``sources`` are ``format_hits`` dicts. ``used_sources`` is the prefix that
    actually fit the passage count and character budget — the caller sends
    exactly that list to the UI so the ``[n]`` citations line up with the cards.
    """
    limit = max_passages or ASK_CONTEXT_PASSAGES
    blocks: List[str] = []
    used: List[dict] = []
    chars = 0
    for source in sources[:limit]:
        text = (source.get("text") or "").strip()
        if not text:
            continue
        if blocks and chars + len(text) > ASK_MAX_CONTEXT_CHARS:
            break
        index = len(used) + 1
        label = source.get("document") or source.get("document_id") or "source"
        blocks.append(f"[{index}] {label} ({_passage_location(source)})\n{text}")
        used.append(source)
        chars += len(text)

    context = "\n\n".join(blocks) if blocks else "(no sources retrieved)"
    messages: List[dict] = list(history or [])
    messages.append(
        {"role": "user", "content": f"Sources:\n\n{context}\n\n---\n\nQuestion: {question}"}
    )
    return _SYSTEM_PROMPT, messages, used


def _split_model_id(model_id: str) -> Tuple[str, str]:
    provider, sep, model = (model_id or "").partition(":")
    if not sep or not provider or not model:
        raise GenerationError(f"invalid model id: {model_id!r} (expected 'provider:model')")
    return provider, model


async def generate_stream(model_id: str, system: str, messages: List[dict]) -> AsyncIterator[str]:
    """Yield answer-text chunks from the provider named in ``model_id``.

    ``messages`` is a list of ``{"role": "user"|"assistant", "content": str}``.
    Raises :class:`GenerationError` on any provider failure.
    """
    provider, model = _split_model_id(model_id)
    if provider == "anthropic":
        async for chunk in _anthropic_stream(model, system, messages):
            yield chunk
        return
    if provider == "ollama":
        async for chunk in _ollama_native_stream(model, system, messages):
            yield chunk
        return
    if provider in _CLOUD:
        key = _cloud_key(provider)
        if not key:
            raise GenerationError(f"{provider} is not configured (no API key)")
        async for chunk in _openai_compatible_stream(
            _OPENAI_COMPAT_BASE[provider], key, model, system, messages
        ):
            yield chunk
        return
    raise GenerationError(f"unknown provider: {provider!r}")


def _delta_from_ollama_line(line: str) -> Optional[str]:
    """The text delta from one line of Ollama's native /api/chat NDJSON stream."""
    line = line.strip()
    if not line:
        return None
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return None
    return (obj.get("message") or {}).get("content")


async def _ollama_native_stream(
    model: str, system: str, messages: List[dict]
) -> AsyncIterator[str]:
    """Stream from Ollama's native endpoint so num_ctx can be set per request.

    The OpenAI-compatible route ignores ``options``, leaving the model at its
    2K–4K default window — which silently drops most of a 10-passage prompt.
    """
    import httpx

    url = _ollama_url()
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system}, *messages],
        "stream": True,
        "options": {
            "num_ctx": OLLAMA_NUM_CTX,
            "temperature": GENERATION_TEMPERATURE,
            "num_predict": GENERATION_MAX_TOKENS,
        },
    }
    try:
        async with httpx.AsyncClient(timeout=GENERATION_TIMEOUT) as client:
            async with client.stream("POST", f"{url}/api/chat", json=payload) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", "replace")[:300]
                    raise GenerationError(f"{model}: Ollama {response.status_code} — {body}")
                async for line in response.aiter_lines():
                    text = _delta_from_ollama_line(line)
                    if text:
                        yield text
    except httpx.HTTPError as exc:
        raise GenerationError(f"{model}: Ollama request failed — {exc}") from exc


def _delta_from_sse_line(line: str) -> Optional[str]:
    """The text delta carried by one OpenAI-style ``data:`` line, if any."""
    line = line.strip()
    if not line.startswith("data:"):
        return None
    data = line[len("data:") :].strip()
    if not data or data == "[DONE]":
        return None
    try:
        event = json.loads(data)
    except json.JSONDecodeError:
        return None
    choices = event.get("choices") or [{}]
    return (choices[0].get("delta") or {}).get("content")


async def _openai_compatible_stream(
    base_url: str, api_key: str, model: str, system: str, messages: List[dict]
) -> AsyncIterator[str]:
    import httpx

    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system}, *messages],
        "stream": True,
        "temperature": GENERATION_TEMPERATURE,
        "max_tokens": GENERATION_MAX_TOKENS,
    }
    try:
        async with httpx.AsyncClient(timeout=GENERATION_TIMEOUT) as client:
            async with client.stream(
                "POST", f"{base_url}/chat/completions", json=payload, headers=headers
            ) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", "replace")[:300]
                    raise GenerationError(f"{model}: upstream {response.status_code} — {body}")
                async for line in response.aiter_lines():
                    text = _delta_from_sse_line(line)
                    if text:
                        yield text
    except httpx.HTTPError as exc:
        raise GenerationError(f"{model}: request failed — {exc}") from exc


_anthropic_client = None
_anthropic_client_key = None


async def _anthropic_stream(
    model: str, system: str, messages: List[dict]
) -> AsyncIterator[str]:
    global _anthropic_client, _anthropic_client_key
    key = _cloud_key("anthropic")
    if not key:
        raise GenerationError("ANTHROPIC_API_KEY is not set")
    try:
        import anthropic
    except ModuleNotFoundError as exc:  # pragma: no cover - deployment choice
        raise GenerationError("the 'anthropic' package is not installed") from exc

    if _anthropic_client is None or _anthropic_client_key != key:
        _anthropic_client = anthropic.AsyncAnthropic(api_key=key)
        _anthropic_client_key = key
    try:
        async with _anthropic_client.messages.stream(
            model=model,
            max_tokens=GENERATION_MAX_TOKENS,
            system=system,
            messages=messages,
        ) as stream:
            async for text in stream.text_stream:
                yield text
    except anthropic.APIError as exc:
        raise GenerationError(f"Claude API error: {exc}") from exc
