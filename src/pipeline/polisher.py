"""Local-only medical text polisher.

CLAUDE.md §3.1 requirements, and how each is met here:

  bind only to loopback      -- the endpoint is validated against a loopback
                                allowlist at construction; anything else raises.
  no external API key        -- none is read, sent, or accepted.
  fail closed                -- every failure path returns PolishResult.failed()
                                carrying the raw text. There is no fallback
                                provider to fall back to.
  no cloud fallback          -- there is no code path to a non-local endpoint.
  deterministic              -- temperature 0.
  structured output          -- responses failing schema validation are rejected.
  enforce a timeout          -- required, bounded.
  limit retries              -- default 0.
  do not log prompt contents -- log records carry sizes and outcomes, never text.

The polisher is an *editor*. It is never the source of clinical facts, and a
failure here must degrade to the raw transcript rather than to a guess.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

log = logging.getLogger(__name__)

# Hosts considered local. Anything outside this set is refused outright rather
# than merely discouraged: a misconfigured endpoint is the single most likely
# way for transcript text to leave the machine.
_LOOPBACK_HOSTNAMES = {"localhost", "127.0.0.1", "::1", "[::1]"}

_VALID_FLAG_REASONS = {
    "ambiguous_term",
    "protected_change",
    "uncertain_correction",
    "unknown_term",
}
_VALID_BASES = {"glossary", "exact_replacement", "punctuation_only"}

# "rewrite" is the email/research mode of ADR 0002: concise, eloquent prose
# rather than a constrained edit. It uses prompts/eloquent_rewriter.md.
VALID_MODES = {"general", "medical", "clinical_strict", "research", "email", "rewrite"}


class LocalOnlyError(RuntimeError):
    """Raised when configuration would permit non-local network access."""


@dataclass
class Flag:
    raw_span: str
    reason: str
    suggestion: str | None = None


@dataclass
class ChangedTerm:
    raw: str
    polished: str
    basis: str


@dataclass
class PolishResult:
    """Outcome of a polishing attempt.

    `ok` False means polishing did not happen and `polished_text` is the raw
    text unchanged. Callers must not treat a failed result as an edit.
    """

    ok: bool
    raw_text: str
    polished_text: str
    flags: list[Flag] = field(default_factory=list)
    changed_terms: list[ChangedTerm] = field(default_factory=list)
    error: str | None = None
    latency_ms: int = 0

    @classmethod
    def failed(cls, raw_text: str, error: str, latency_ms: int = 0) -> "PolishResult":
        # Fail closed: hand back exactly what came in.
        return cls(
            ok=False,
            raw_text=raw_text,
            polished_text=raw_text,
            error=error,
            latency_ms=latency_ms,
        )


def _assert_local_endpoint(endpoint: str) -> None:
    """Refuse any endpoint that is not loopback."""
    parsed = urlparse(endpoint)
    if parsed.scheme not in ("http", "https"):
        raise LocalOnlyError(f"endpoint scheme must be http(s): {endpoint!r}")

    host = parsed.hostname
    if host is None:
        raise LocalOnlyError(f"endpoint has no host: {endpoint!r}")

    if host in _LOOPBACK_HOSTNAMES:
        return

    # Accept any address inside a loopback network (127.0.0.0/8, ::1), and
    # nothing else. Names that are not literal addresses are refused: a
    # hostname could resolve anywhere, and could resolve differently later.
    try:
        addr = ipaddress.ip_address(host)
    except ValueError as exc:
        raise LocalOnlyError(
            f"endpoint host {host!r} is not a loopback address. "
            "Only 127.0.0.0/8 and ::1 are permitted; hostnames are refused "
            "because resolution can change."
        ) from exc

    if not addr.is_loopback:
        raise LocalOnlyError(
            f"endpoint {host!r} is not loopback. Cloud and LAN endpoints are "
            "not permitted (CLAUDE.md 3.1)."
        )


def _assert_local_model(model: str) -> None:
    """Refuse Ollama model names that are served from Ollama's cloud.

    A loopback endpoint is not enough on its own. Recent Ollama releases can
    run "cloud" models: the request goes to the local server, which forwards
    the prompt to ollama.com. Those models are named with a "-cloud" or
    ":cloud" suffix, so a config edit alone could send transcript text off
    the machine through an endpoint that passes the loopback check.
    """
    name = model.strip().lower()
    if not name:
        raise LocalOnlyError("model name is empty")
    tag = name.rsplit(":", 1)[-1] if ":" in name else ""
    if name.endswith("-cloud") or tag == "cloud" or tag.endswith("-cloud"):
        raise LocalOnlyError(
            f"model {model!r} is an Ollama cloud model, which forwards prompts "
            "to a remote service. Only locally stored models are permitted "
            "(CLAUDE.md 3.1)."
        )


def _extract_json(text: str) -> dict[str, Any] | None:
    """Pull a JSON object out of a model response.

    Models wrap JSON in prose or code fences even when told not to. This
    tolerates that framing but does NOT repair malformed JSON: a response that
    is not valid JSON is rejected, per CLAUDE.md §3.2.
    """
    text = text.strip()
    if not text:
        return None

    fence = re.search(r"```(?:json)?\s*(.+?)\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()

    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass

    # Fall back to the outermost brace pair.
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        obj = json.loads(text[start : end + 1])
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


def _validate_payload(obj: dict[str, Any]) -> tuple[str, list[Flag], list[ChangedTerm]]:
    """Validate the model response against the schema.

    Raises ValueError on any deviation. The application must reject output that
    does not match the schema (CLAUDE.md §3.2), so this is strict on purpose.
    """
    if "polished_text" not in obj:
        raise ValueError("missing polished_text")
    polished = obj["polished_text"]
    if not isinstance(polished, str):
        raise ValueError("polished_text is not a string")
    if not polished.strip():
        raise ValueError("polished_text is empty")

    flags: list[Flag] = []
    for item in obj.get("flags") or []:
        if not isinstance(item, dict):
            raise ValueError("flag entry is not an object")
        reason = item.get("reason")
        if reason not in _VALID_FLAG_REASONS:
            raise ValueError(f"invalid flag reason: {reason!r}")
        suggestion = item.get("suggestion")
        if suggestion is not None and not isinstance(suggestion, str):
            raise ValueError("flag suggestion must be string or null")
        flags.append(Flag(str(item.get("raw_span", "")), reason, suggestion))

    changed: list[ChangedTerm] = []
    for item in obj.get("changed_medical_terms") or []:
        if not isinstance(item, dict):
            raise ValueError("changed_medical_terms entry is not an object")
        basis = item.get("basis")
        if basis not in _VALID_BASES:
            raise ValueError(f"invalid basis: {basis!r}")
        changed.append(
            ChangedTerm(str(item.get("raw", "")), str(item.get("polished", "")), basis)
        )

    return polished, flags, changed


class LocalPolisher:
    """Polish transcripts using a local instruction-following model.

    Speaks the Ollama chat API over loopback. The transport is deliberately
    plain urllib: no client library, no telemetry, no retry-with-backoff
    machinery that could mask a failure as success.
    """

    def __init__(
        self,
        endpoint: str = "http://127.0.0.1:11434",
        model: str = "llama3.1:8b",
        prompt_path: str = "prompts/medical_polisher.md",
        timeout_seconds: int = 20,
        retries: int = 0,
        temperature: float = 0.0,
        keep_alive: str | None = None,
        num_ctx: int | None = None,
    ) -> None:
        _assert_local_endpoint(endpoint)
        _assert_local_model(model)
        if retries < 0:
            raise ValueError("retries must be >= 0")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0")

        self.endpoint = endpoint.rstrip("/")
        self.model = model
        self.timeout = timeout_seconds
        self.retries = retries
        self.temperature = temperature
        # How long Ollama keeps the model resident after a request. None leaves
        # Ollama's default (5 min). Rewrite mode sets it longer, because a cold
        # load of a 14B model costs seconds on every dictation after a pause.
        self.keep_alive = keep_alive
        # Context window to load the model with. None leaves Ollama's default,
        # which for qwen2.5:14b is the model's full 32768 tokens: 15.2 GB
        # resident on a 16 GB machine, almost all of it KV cache that a
        # dictation never uses. Ollama truncates an over-long prompt from the
        # START -- dropping the system prompt -- so polish() refuses input that
        # would not fit rather than letting that happen.
        if num_ctx is not None and num_ctx < 1024:
            raise ValueError("num_ctx must be >= 1024")
        self.num_ctx = num_ctx
        self.system_prompt = self._load_prompt(prompt_path)

    @staticmethod
    def _load_prompt(path: str) -> str:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        # Everything after the implementer-notes marker is documentation and
        # must not be sent to the model.
        marker = "---\n\nNOTES FOR IMPLEMENTERS"
        if marker in text:
            text = text.split(marker, 1)[0]
        return text.strip()

    def _build_user_message(self, raw_text: str, glossary: list[str], mode: str) -> str:
        parts = [f"MODE: {mode}"]
        if glossary:
            # Cap the glossary. An unbounded list would dominate the context
            # and slow inference without improving edits.
            shown = glossary[:200]
            parts.append("GLOSSARY (approved spellings; absence is not an error):")
            parts.append(", ".join(shown))
        parts.append("TRANSCRIPT:")
        parts.append(raw_text)
        return "\n\n".join(parts)

    def _opener(self) -> urllib.request.OpenerDirector:
        # No proxy handler: a system proxy could route loopback-looking
        # traffic off-machine.
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def warm(self) -> bool:
        """Load the model into memory without generating anything.

        Sends no text: an Ollama generate request with no prompt only loads
        the model. Returns False on any failure, which callers may ignore --
        the first real request then pays the load time instead.
        """
        payload = {"model": self.model}
        if self.keep_alive:
            payload["keep_alive"] = self.keep_alive
        # Must match polish(): a different num_ctx makes Ollama reload.
        if self.num_ctx:
            payload["options"] = {"num_ctx": self.num_ctx}
        req = urllib.request.Request(
            f"{self.endpoint}/api/generate",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self._opener().open(req, timeout=max(self.timeout, 120)) as resp:
                resp.read()
            return True
        except Exception as exc:  # noqa: BLE001 - warming is best effort
            log.warning("model warm-up failed: %s", type(exc).__name__)
            return False

    # Conservative: English averages ~4 characters per token; 3 overestimates.
    _CHARS_PER_TOKEN = 3
    # Chat template, role markers, and the JSON envelope around the reply.
    _OVERHEAD_TOKENS = 256

    def fits(self, user_message: str, raw_text: str) -> bool:
        """Whether prompt plus a reply as long as the input fits in num_ctx.

        A rewrite or polish is at most about as long as its input, so the
        reply is budgeted at the transcript's size again.
        """
        if not self.num_ctx:
            return True
        prompt = (len(self.system_prompt) + len(user_message)) // self._CHARS_PER_TOKEN
        reply = len(raw_text) // self._CHARS_PER_TOKEN
        return prompt + reply + self._OVERHEAD_TOKENS <= self.num_ctx

    def polish(self, raw_text: str, glossary: list[str], mode: str) -> PolishResult:
        """Polish `raw_text`. Never raises; failures return a failed result."""
        if mode not in VALID_MODES:
            return PolishResult.failed(raw_text, f"unknown mode: {mode}")
        if not raw_text or not raw_text.strip():
            return PolishResult.failed(raw_text, "empty transcript")

        user_message = self._build_user_message(raw_text, glossary, mode)
        if self.num_ctx and not self.fits(user_message, raw_text):
            return PolishResult.failed(
                raw_text, f"dictation too long for the model's {self.num_ctx}-token "
                          "context; split it or raise num_ctx")

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": user_message},
            ],
            "stream": False,
            "format": "json",
            **({"keep_alive": self.keep_alive} if self.keep_alive else {}),
            "options": {
                "temperature": self.temperature,
                "top_p": 1.0,
                "seed": 0,
                **({"num_ctx": self.num_ctx} if self.num_ctx else {}),
            },
        }

        started = time.monotonic()
        last_error = "unknown error"

        for attempt in range(self.retries + 1):
            try:
                body = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(
                    f"{self.endpoint}/api/chat",
                    data=body,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self._opener().open(req, timeout=self.timeout) as resp:
                    data = json.loads(resp.read().decode("utf-8"))

                # Backstop for the estimate in fits(): output cut off by the
                # context limit is incomplete text, never a usable result.
                if self.num_ctx and (
                    data.get("done_reason") == "length"
                    or data.get("prompt_eval_count", 0) + data.get("eval_count", 0)
                    >= self.num_ctx
                ):
                    raise ValueError("output reached the context limit (truncated)")

                content = (data.get("message") or {}).get("content", "")
                obj = _extract_json(content)
                if obj is None:
                    raise ValueError("response was not valid JSON")

                polished, flags, changed = _validate_payload(obj)
                elapsed = int((time.monotonic() - started) * 1000)

                # Sizes and counts only -- never the text itself.
                log.info(
                    "polish ok: mode=%s raw_chars=%d polished_chars=%d "
                    "flags=%d changed=%d ms=%d",
                    mode, len(raw_text), len(polished), len(flags), len(changed), elapsed,
                )
                return PolishResult(
                    ok=True,
                    raw_text=raw_text,
                    polished_text=polished,
                    flags=flags,
                    changed_terms=changed,
                    latency_ms=elapsed,
                )

            except urllib.error.URLError as exc:
                last_error = f"local model unreachable: {exc.reason}"
            except TimeoutError:
                last_error = f"timed out after {self.timeout}s"
            except ValueError as exc:
                last_error = f"invalid model output: {exc}"
            except Exception as exc:  # noqa: BLE001 - must never propagate
                last_error = f"{type(exc).__name__}: {exc}"

            if attempt < self.retries:
                log.warning("polish attempt %d failed: %s", attempt + 1, last_error)

        elapsed = int((time.monotonic() - started) * 1000)
        log.error("polish failed: %s (ms=%d)", last_error, elapsed)
        return PolishResult.failed(raw_text, last_error, elapsed)
