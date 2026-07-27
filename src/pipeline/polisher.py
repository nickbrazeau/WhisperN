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

VALID_MODES = {"general", "medical", "clinical_strict", "research", "email"}


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
    ) -> None:
        _assert_local_endpoint(endpoint)
        if retries < 0:
            raise ValueError("retries must be >= 0")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0")

        self.endpoint = endpoint.rstrip("/")
        self.model = model
        self.timeout = timeout_seconds
        self.retries = retries
        self.temperature = temperature
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

    def polish(self, raw_text: str, glossary: list[str], mode: str) -> PolishResult:
        """Polish `raw_text`. Never raises; failures return a failed result."""
        if mode not in VALID_MODES:
            return PolishResult.failed(raw_text, f"unknown mode: {mode}")
        if not raw_text or not raw_text.strip():
            return PolishResult.failed(raw_text, "empty transcript")

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {
                    "role": "user",
                    "content": self._build_user_message(raw_text, glossary, mode),
                },
            ],
            "stream": False,
            "format": "json",
            "options": {
                "temperature": self.temperature,
                "top_p": 1.0,
                "seed": 0,
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
                # No proxy handler: a system proxy could route loopback-looking
                # traffic off-machine.
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                with opener.open(req, timeout=self.timeout) as resp:
                    data = json.loads(resp.read().decode("utf-8"))

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
