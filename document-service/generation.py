"""Answer generation over retrieved passages — the "Ask" mode.

Retrieval (``embeddings`` + ``reranker`` + ``vector_store``) finds the source
passages; this module asks an LLM to turn them into a grounded, cited answer.
The backend is pluggable so the service stays self-hostable and air-gappable:

  ``GENERATION_BACKEND=llamacpp``  a llama.cpp server (default; fully local/offline)
  ``GENERATION_BACKEND=anthropic`` the Claude API (needs ``ANTHROPIC_API_KEY`` + network)
  ``GENERATION_BACKEND=off``       Ask mode disabled; ``/ask`` returns 503

Each provider is reached through one coroutine, ``generate_stream(system,
messages)``, that yields answer-text chunks as they arrive. ``anthropic`` is
imported lazily so a llamacpp-only or offline deployment never needs the package.
"""

import json
import logging
import os
from typing import AsyncIterator, List, Optional, Tuple

logger = logging.getLogger("ai_librarian")

BACKEND = os.environ.get("GENERATION_BACKEND", "llamacpp").strip().lower()

# llama.cpp server (OpenAI-compatible /v1/chat/completions).
LLAMA_URL = os.environ.get("LLAMA_URL", "http://llm:8080").rstrip("/")
LLAMA_MODEL = os.environ.get("LLAMA_MODEL", "local-gguf")

# Claude API.
GENERATION_MODEL = os.environ.get("GENERATION_MODEL", "claude-opus-5")

# Shared decoding knobs.
GENERATION_MAX_TOKENS = int(os.environ.get("GENERATION_MAX_TOKENS", "2000"))
GENERATION_TEMPERATURE = float(os.environ.get("GENERATION_TEMPERATURE", "0.2"))
GENERATION_TIMEOUT = float(os.environ.get("GENERATION_TIMEOUT", "120"))

# How many reranked passages to put in front of the model, and a character
# ceiling on the whole context block (a rough proxy for tokens — the passages
# are already token-budgeted chunks).
ASK_CONTEXT_PASSAGES = int(os.environ.get("ASK_CONTEXT_PASSAGES", "6"))
ASK_MAX_CONTEXT_CHARS = int(os.environ.get("ASK_MAX_CONTEXT_CHARS", "6000"))

_SYSTEM_PROMPT = (
    "You are the assistant for a personal reading library. Answer the user's "
    "question using only the numbered sources below. Cite every claim with the "
    "number of the source it came from in square brackets, like [1] or [2][3]. "
    "If the sources do not contain the answer, say so plainly and do not guess. "
    "Be concise and preserve any mathematical notation exactly as written."
)


class GenerationError(RuntimeError):
    """A backend failed to produce an answer (network, auth, bad response)."""


def _anthropic_key() -> str:
    return (os.environ.get("ANTHROPIC_API_KEY") or "").strip()


def backend_info() -> dict:
    """What ``/config`` reports so the UI can show or hide the Ask tab."""
    if BACKEND == "anthropic":
        return {"backend": "anthropic", "model": GENERATION_MODEL, "enabled": bool(_anthropic_key())}
    if BACKEND == "llamacpp":
        return {"backend": "llamacpp", "model": LLAMA_MODEL, "enabled": True}
    return {"backend": "off", "model": None, "enabled": False}


def enabled() -> bool:
    return backend_info()["enabled"]


def disabled_reason() -> str:
    if BACKEND == "off":
        return "Ask mode is disabled on this server (GENERATION_BACKEND=off)."
    if BACKEND == "anthropic":
        return "Ask mode is configured for the Claude API but ANTHROPIC_API_KEY is not set."
    return f"Ask mode backend {BACKEND!r} is not available."


def _passage_location(source: dict) -> str:
    page = source.get("page")
    page_end = source.get("page_end")
    if page is None:
        return "location unknown"
    if page_end and page_end != page:
        return f"pp. {page}-{page_end}"
    return f"p. {page}"


def build_ask_prompt(
    question: str, sources: List[dict], history: Optional[List[dict]] = None
) -> Tuple[str, List[dict], List[dict]]:
    """Build the ``(system, messages, used_sources)`` triple for a question.

    ``sources`` are ``format_hits`` dicts. ``used_sources`` is the prefix that
    actually fit the context budget — the caller sends exactly that list to the
    UI so the ``[n]`` citations line up with the passage cards.
    """
    blocks: List[str] = []
    used: List[dict] = []
    chars = 0
    for source in sources[:ASK_CONTEXT_PASSAGES]:
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


async def generate_stream(system: str, messages: List[dict]) -> AsyncIterator[str]:
    """Yield answer-text chunks from the configured backend.

    ``messages`` is a list of ``{"role": "user"|"assistant", "content": str}``.
    Raises :class:`GenerationError` on any backend failure.
    """
    if BACKEND == "anthropic":
        async for chunk in _anthropic_stream(system, messages):
            yield chunk
    elif BACKEND == "llamacpp":
        async for chunk in _llamacpp_stream(system, messages):
            yield chunk
    elif BACKEND == "off":
        raise GenerationError(disabled_reason())
    else:
        raise GenerationError(f"unknown GENERATION_BACKEND: {BACKEND!r}")


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


async def _llamacpp_stream(system: str, messages: List[dict]) -> AsyncIterator[str]:
    import httpx

    payload = {
        "model": LLAMA_MODEL,
        "messages": [{"role": "system", "content": system}, *messages],
        "stream": True,
        "temperature": GENERATION_TEMPERATURE,
        "max_tokens": GENERATION_MAX_TOKENS,
    }
    try:
        async with httpx.AsyncClient(timeout=GENERATION_TIMEOUT) as client:
            async with client.stream(
                "POST", f"{LLAMA_URL}/v1/chat/completions", json=payload
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    text = _delta_from_sse_line(line)
                    if text:
                        yield text
    except httpx.HTTPError as exc:
        raise GenerationError(f"llama.cpp backend at {LLAMA_URL} failed: {exc}") from exc


_anthropic_client = None


async def _anthropic_stream(system: str, messages: List[dict]) -> AsyncIterator[str]:
    global _anthropic_client
    if not _anthropic_key():
        raise GenerationError("ANTHROPIC_API_KEY is not set")
    try:
        import anthropic
    except ModuleNotFoundError as exc:  # pragma: no cover - deployment choice
        raise GenerationError("the 'anthropic' package is not installed") from exc

    if _anthropic_client is None:
        _anthropic_client = anthropic.AsyncAnthropic(api_key=_anthropic_key())
    try:
        async with _anthropic_client.messages.stream(
            model=GENERATION_MODEL,
            max_tokens=GENERATION_MAX_TOKENS,
            system=system,
            messages=messages,
        ) as stream:
            async for text in stream.text_stream:
                yield text
    except anthropic.APIError as exc:
        raise GenerationError(f"Claude API error: {exc}") from exc
