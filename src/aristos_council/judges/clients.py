"""JUDGE-PANEL-1 - one model call per family, over plain HTTPS.

No provider SDK is imported: the panel needs a single request/response per call, and three SDKs
(one of them not otherwise a dependency of Aristos) would be three things to install and keep in
step for that. Each function takes the judge's spec, the system prompt and the user message, and
returns the model's TEXT. Temperature is NOT set - the provider default stands, because the panel
measures how stable each model is as it ships.

Keys are read from the environment at call time, travel only in a request header, and are
scrubbed from any error text before it is raised - an error message is the one place a key could
otherwise leak into a log or onto the page.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Callable, Optional

from .config import JudgeSpec

TIMEOUT_SECONDS = 240
MAX_ANSWER_TOKENS = 1500

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
OPENAI_URL = "https://api.openai.com/v1/chat/completions"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


class CallError(RuntimeError):
    """A model call failed (network, HTTP status, an unreadable response). The text is the RAW
    detail, safe to log: the key has been scrubbed from it. ``status`` is the HTTP status when
    there was one; ``timeout`` marks a call that ran out of time. The page never shows this text -
    it shows ``error_label`` of it."""

    def __init__(self, message="", *, status=None, timeout=False):
        super().__init__(message)
        self.status = status
        self.timeout = timeout


def error_label(exc) -> str:
    """The short label a failed call shows in the Verdict column: "no credits", "model not
    available", "timeout", "<HTTP status>" otherwise. The raw body goes to the log only."""
    text = str(exc).lower()
    status = getattr(exc, "status", None)
    if status == 429 and any(w in text for w in ("insufficient_quota", "quota", "credit", "billing")):
        return "no credits"
    if "insufficient_quota" in text or "no credits" in text:
        return "no credits"
    if status == 404:
        return "model not available"
    if getattr(exc, "timeout", False) or "timed out" in text or "timeout" in text:
        return "timeout"
    if status:
        return str(status)
    return "failed"


def _scrub(text: str, key: str) -> str:
    text = str(text)
    return text.replace(key, "[key]") if key else text


def post_json(url: str, headers: dict, body: dict, *, timeout: int = TIMEOUT_SECONDS) -> dict:
    """POST ``body`` as JSON and return the decoded JSON reply. Raises ``CallError``."""
    data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST",
                                     headers={"content-type": "application/json", **headers})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:     # noqa: S310 - fixed https URLs
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", "replace")[:300]
        except Exception:                                   # noqa: BLE001
            detail = ""
        raise CallError(f"HTTP {exc.code}: {detail}".strip(), status=exc.code) from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        timed_out = isinstance(exc, TimeoutError) or "timed out" in str(exc).lower()
        raise CallError(f"{type(exc).__name__}: {exc}", timeout=timed_out) from None


def _anthropic(spec: JudgeSpec, system: str, user: str, *, post=post_json) -> str:
    reply = post(ANTHROPIC_URL,
                 {"x-api-key": spec.key(), "anthropic-version": "2023-06-01"},
                 {"model": spec.model, "max_tokens": MAX_ANSWER_TOKENS, "system": system,
                  "messages": [{"role": "user", "content": user}]})
    parts = [b.get("text", "") for b in (reply.get("content") or []) if b.get("type") == "text"]
    return "".join(parts)


def _openai(spec: JudgeSpec, system: str, user: str, *, post=post_json) -> str:
    reply = post(OPENAI_URL, {"authorization": f"Bearer {spec.key()}"},
                 {"model": spec.model,
                  "messages": [{"role": "system", "content": system},
                               {"role": "user", "content": user}],
                  "response_format": {"type": "json_object"}})
    choices = reply.get("choices") or []
    return str(((choices[0] if choices else {}).get("message") or {}).get("content") or "")


def _gemini(spec: JudgeSpec, system: str, user: str, *, post=post_json) -> str:
    reply = post(GEMINI_URL.format(model=spec.model), {"x-goog-api-key": spec.key()},
                 {"systemInstruction": {"parts": [{"text": system}]},
                  "contents": [{"role": "user", "parts": [{"text": user}]}],
                  "generationConfig": {"responseMimeType": "application/json"}})
    candidates = reply.get("candidates") or []
    parts = ((candidates[0] if candidates else {}).get("content") or {}).get("parts") or []
    return "".join(p.get("text", "") for p in parts)


_BY_FAMILY = {"anthropic": _anthropic, "openai": _openai, "google": _gemini}

# A caller takes (spec, system, user) and returns the model's text.
Caller = Callable[[JudgeSpec, str, str], str]


def http_caller(spec: JudgeSpec, system: str, user: str, *, post: Optional[Callable] = None) -> str:
    """The production caller: dispatch on the judge's family. ``post`` is the test seam."""
    fn = _BY_FAMILY.get(spec.family)
    if fn is None:
        raise CallError(f"no client for family {spec.family!r}")
    try:
        return fn(spec, system, user, **({"post": post} if post is not None else {}))
    except CallError as exc:
        raise CallError(_scrub(exc, spec.key()), status=exc.status, timeout=exc.timeout) from None
    except Exception as exc:                                # noqa: BLE001 - never leak the key
        raise CallError(_scrub(f"{type(exc).__name__}: {exc}", spec.key())) from None
