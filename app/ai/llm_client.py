"""
LLM access for the FORSETI AI layer.

DESIGN RULE THAT MATTERS MOST
----------------------------
No LLM is ever in the enforcement path. The DTL invariant, the cost governor and
the trained detector decide outcomes on their own, deterministically. The LLM
only *explains*, *translates* and *proposes* — and every proposal is validated
against a schema and re-checked by the deterministic engine before it can affect
anything. If every provider is exhausted the system keeps working; the AI
surfaces report LLM_UNAVAILABLE and say so in the UI.

Provider strategy mirrors the free-tier fallback pattern: walk providers in tier
order, try each key, treat "out of quota" as an ordinary event rather than an
error, and only give up when everything is exhausted. Stdlib only — no new
runtime dependency.
"""

from __future__ import annotations

import json
import os
import re
import ssl
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
DEFAULT_TIMEOUT = 45
KEYS_PER_PROVIDER = 8


@dataclass(frozen=True)
class Provider:
    slug: str
    name: str
    tier: int
    base_url: str
    model: str
    api_style: str = "openai"
    alt_models: Tuple[str, ...] = ()

    @property
    def env_prefix(self) -> str:
        return self.slug.upper()

    def key_vars(self) -> List[str]:
        """Env vars holding this provider's keys, in try order."""
        base = [f"{self.env_prefix}_API_KEY"]
        base += [f"{self.env_prefix}_API_KEY_{i}" for i in range(1, KEYS_PER_PROVIDER + 1)]
        return base


# Tier 1 = perpetual free tier, spend first. Higher tiers are held back.
PROVIDERS: Tuple[Provider, ...] = (
    Provider("groq", "Groq Cloud", 1, "https://api.groq.com/openai/v1",
             "llama-3.3-70b-versatile", alt_models=("llama-3.1-8b-instant", "openai/gpt-oss-120b")),
    Provider("cerebras", "Cerebras", 1, "https://api.cerebras.ai/v1",
             "llama-3.3-70b", alt_models=("llama3.1-8b", "qwen-3-32b")),
    Provider("gemini", "Google Gemini", 1,
             "https://generativelanguage.googleapis.com/v1beta/openai",
             "gemini-2.0-flash", alt_models=("gemini-1.5-flash", "gemini-2.5-flash")),
    Provider("mistral", "Mistral", 1, "https://api.mistral.ai/v1",
             "mistral-small-latest", alt_models=("open-mistral-nemo", "mistral-large-latest")),
    Provider("openrouter", "OpenRouter", 1, "https://openrouter.ai/api/v1",
             "meta-llama/llama-3.3-70b-instruct:free",
             alt_models=("google/gemma-2-9b-it:free", "mistralai/mistral-7b-instruct:free")),
    Provider("nvidia", "NVIDIA NIM", 1, "https://integrate.api.nvidia.com/v1",
             "meta/llama-3.3-70b-instruct", alt_models=("meta/llama-3.1-8b-instruct",)),
    Provider("cohere", "Cohere", 2, "https://api.cohere.com/v2",
             "command-r-08-2024", api_style="cohere", alt_models=("command-r7b-12-2024",)),
    Provider("huggingface", "HuggingFace Router", 2, "https://router.huggingface.co/v1",
             "meta-llama/Llama-3.1-8B-Instruct", alt_models=("Qwen/Qwen2.5-7B-Instruct",)),
    Provider("sambanova", "SambaNova", 2, "https://api.sambanova.ai/v1",
             "Meta-Llama-3.3-70B-Instruct", alt_models=("Meta-Llama-3.1-8B-Instruct",)),
    Provider("openai", "OpenAI", 3, "https://api.openai.com/v1",
             "gpt-4o-mini", alt_models=("gpt-4.1-mini",)),
)

_EXHAUSTED = ("quota", "exhausted", "insufficient", "rate limit", "rate_limit", "ratelimit",
              "too many requests", "credit", "balance", "billing", "payment required",
              "free tier", "usage limit", "limit exceeded", "out of capacity",
              "trial has expired", "expired", "no capacity", "overloaded")
_BAD_MODEL = ("model not found", "does not exist", "unknown model", "invalid model",
              "no such model", "decommissioned", "not supported", "model_not_found",
              "invalid_model", "unavailable")


class LLMUnavailable(RuntimeError):
    """Every provider and key was tried without success."""


@dataclass
class Attempt:
    provider: str
    key_var: str
    status: str  # ok | exhausted | auth | bad_model | error
    detail: str = ""
    latency_ms: int = 0


@dataclass
class Reply:
    text: str
    provider: str
    model: str
    latency_ms: int
    cached: bool = False
    trace: List[Attempt] = field(default_factory=list)


def load_env(path: Path = ENV_PATH) -> int:
    """Parse KEY=value into os.environ. Existing environment wins."""
    if not path.exists():
        return 0
    n = 0
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if not value or value.startswith("<") or value.lower().startswith("your_"):
            continue
        os.environ.setdefault(key, value)
        n += 1
    return n


load_env()
_SSL_CTX = ssl.create_default_context()


def _post(url: str, payload: dict, headers: Dict[str, str], timeout: int) -> Tuple[int, str]:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    # Some providers sit behind bot management that rejects the default UA.
    req.add_header("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                 "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as e:
        return 0, f"network error: {e.reason}"
    except Exception as e:  # noqa: BLE001 - one dead provider must not kill the chain
        return 0, f"network error: {type(e).__name__}: {e}"


def _build(p: Provider, model: str, messages: List[dict], key: str,
           max_tokens: int, temperature: float, json_mode: bool):
    if p.api_style == "cohere":
        url = f"{p.base_url.rstrip('/')}/chat"
        payload = {"model": model, "messages": messages, "max_tokens": max_tokens,
                   "temperature": temperature}
        return url, payload, {"Authorization": f"Bearer {key}"}

    url = f"{p.base_url.rstrip('/')}/chat/completions"
    payload: Dict[str, Any] = {"model": model, "messages": messages,
                               "max_tokens": max_tokens, "temperature": temperature,
                               "stream": False}
    if json_mode and p.slug in ("groq", "openai", "cerebras", "mistral", "openrouter"):
        payload["response_format"] = {"type": "json_object"}
    headers = {"Authorization": f"Bearer {key}"}
    if p.slug == "openrouter":
        headers["HTTP-Referer"] = "https://localhost/forseti"
        headers["X-Title"] = "FORSETI"
    return url, payload, headers


def _extract(style: str, body: str) -> Optional[str]:
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return None
    if style == "cohere":
        content = (data.get("message") or {}).get("content") or []
        text = "".join(c.get("text", "") for c in content if isinstance(c, dict))
        return text.strip() or None
    choices = data.get("choices") or []
    if not choices:
        return None
    msg = choices[0].get("message") or {}
    text = msg.get("content")
    if isinstance(text, list):
        text = "".join(part.get("text", "") for part in text if isinstance(part, dict))
    if not text:
        text = msg.get("reasoning_content") or choices[0].get("text")
    return text.strip() if isinstance(text, str) and text.strip() else None


def _classify(status: int, body: str) -> Tuple[str, str]:
    low = body.lower()
    snippet = " ".join(body.split())[:180]
    if status == 0:
        return "error", snippet
    if status in (402, 429):
        return "exhausted", snippet
    if status in (401, 403):
        return "auth", snippet
    if status == 404 or any(m in low for m in _BAD_MODEL):
        return "bad_model", snippet
    if status >= 400:
        if any(m in low for m in _EXHAUSTED):
            return "exhausted", snippet
        return "error", snippet
    return "ok", ""


class LLMClient:
    """Thread-safe fallback chat client with an in-process response cache."""

    def __init__(self, enabled: Optional[bool] = None):
        if enabled is None:
            enabled = os.environ.get("ENABLE_AI", "1").lower() not in ("0", "false", "no")
        self.enabled = enabled
        self._cache: Dict[str, Reply] = {}
        self._dead: set[str] = set()   # "slug:KEY_VAR" that reported exhaustion
        self._lock = threading.Lock()
        self._last_provider: Optional[str] = None

    # ------------------------------------------------------------ status

    def available_providers(self) -> List[Dict[str, Any]]:
        rows = []
        for p in sorted(PROVIDERS, key=lambda x: x.tier):
            keys = [v for v in p.key_vars() if os.environ.get(v, "").strip()]
            live = [k for k in keys if f"{p.slug}:{k}" not in self._dead]
            rows.append({"provider": p.slug, "name": p.name, "tier": p.tier,
                         "model": p.model, "keys_configured": len(keys),
                         "keys_live": len(live)})
        return rows

    def status(self) -> Dict[str, Any]:
        rows = self.available_providers()
        total = sum(r["keys_configured"] for r in rows)
        live = sum(r["keys_live"] for r in rows)
        return {
            "enabled": self.enabled,
            "available": bool(self.enabled and live),
            "providers_configured": sum(1 for r in rows if r["keys_configured"]),
            "keys_configured": total,
            "keys_live": live,
            "last_provider_used": self._last_provider,
            "cache_entries": len(self._cache),
            "providers": rows,
            "enforcement_note": "The LLM never decides an authorization outcome. "
                                "It explains, translates and proposes; the deterministic "
                                "engine validates and decides.",
        }

    # ------------------------------------------------------------ calling

    def chat(self, system: str, user: str, *, max_tokens: int = 900,
             temperature: float = 0.2, json_mode: bool = False,
             cache_key: Optional[str] = None) -> Reply:
        """Returns a Reply, or raises LLMUnavailable when nothing answers."""
        if not self.enabled:
            raise LLMUnavailable("AI layer disabled (ENABLE_AI=0)")

        ck = cache_key or f"{hash((system, user, max_tokens, json_mode))}"
        with self._lock:
            hit = self._cache.get(ck)
        if hit:
            return Reply(hit.text, hit.provider, hit.model, hit.latency_ms, cached=True, trace=hit.trace)

        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        trace: List[Attempt] = []

        for p in sorted(PROVIDERS, key=lambda x: x.tier):
            for key_var in p.key_vars():
                key = os.environ.get(key_var, "").strip()
                if not key:
                    continue
                dead_id = f"{p.slug}:{key_var}"
                if dead_id in self._dead:
                    continue

                for model in (p.model, *p.alt_models):
                    started = time.perf_counter()
                    url, payload, headers = _build(p, model, messages, key,
                                                   max_tokens, temperature, json_mode)
                    status, body = _post(url, payload, headers, DEFAULT_TIMEOUT)
                    elapsed = int((time.perf_counter() - started) * 1000)
                    label, detail = _classify(status, body)

                    if label == "ok":
                        text = _extract(p.api_style, body)
                        if not text:
                            trace.append(Attempt(p.slug, key_var, "error", "empty response", elapsed))
                            continue
                        trace.append(Attempt(p.slug, key_var, "ok", "", elapsed))
                        reply = Reply(text, p.slug, model, elapsed, trace=trace)
                        with self._lock:
                            self._cache[ck] = reply
                            self._last_provider = f"{p.slug}/{model}"
                        return reply

                    trace.append(Attempt(p.slug, key_var, label, detail, elapsed))
                    if label == "bad_model":
                        continue          # try the next model on this same key
                    if label in ("exhausted", "auth"):
                        with self._lock:
                            self._dead.add(dead_id)
                    break                 # move to the next key

        raise LLMUnavailable(
            f"all providers exhausted after {len(trace)} attempts; "
            f"last: {trace[-1].detail if trace else 'no keys configured'}"
        )

    def chat_json(self, system: str, user: str, *, schema_hint: str = "",
                  max_tokens: int = 900, cache_key: Optional[str] = None) -> Tuple[Dict[str, Any], Reply]:
        """
        Chat that must return a JSON object. Models wrap JSON in prose or fences
        often enough that parsing has to be defensive rather than trusting.
        """
        sys_prompt = system
        if schema_hint:
            sys_prompt += (f"\n\nReply with ONE JSON object and nothing else. "
                           f"Required shape:\n{schema_hint}")
        reply = self.chat(sys_prompt, user, max_tokens=max_tokens, temperature=0.1,
                          json_mode=True, cache_key=cache_key)
        return parse_json_object(reply.text), reply


def parse_json_object(text: str) -> Dict[str, Any]:
    """Extracts the first JSON object from a model response."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
        if isinstance(parsed, list):
            return {"items": parsed}
    except json.JSONDecodeError:
        pass
    # Fall back to the outermost balanced {...} span.
    start = text.find("{")
    if start >= 0:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        parsed = json.loads(text[start:i + 1])
                        if isinstance(parsed, dict):
                            return parsed
                    except json.JSONDecodeError:
                        break
    raise ValueError(f"model did not return a JSON object: {text[:200]}")


_client: Optional[LLMClient] = None


def get_client() -> LLMClient:
    global _client
    if _client is None:
        _client = LLMClient()
    return _client
