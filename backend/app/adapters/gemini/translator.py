"""Gemini text Translator — gemini-3.5-flash + GlosarioTecnico.

Model ids live in ``models.py``. Verified 24 sep 2026 against a free-tier key:
``gemini-3.5-flash`` answered 503 for minutes and then hit a 20 requests/day
quota; ``gemini-3.5-flash-lite`` allows 15 requests/min. So this adapter:

- translates one or more Cues to *all* destination languages in a single request
  (``translate_many``), JSON in/out;
- honours the ``retry in Xs`` of a 429 per model (shared across Sessions) and
  falls through the chain in ``models.py``;
- skips interims while every model is blocked (a late interim is worthless) but
  waits for finals, echoing ``[lang] text`` only as a last resort.

``translate`` (the port) is kept for single Cues; it delegates to ``translate_many``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, ClassVar
from uuid import uuid4

from app.adapters.gemini.models import TRANSLATOR_FALLBACK_MODELS, TRANSLATOR_MODEL
from app.domain.models import Cue, GlossaryTerm, TrackLang

logger = logging.getLogger(__name__)

LANG_NAMES = {"es": "Spanish", "en": "English", "pt": "Portuguese"}

# Block a model for this long after 429/5xx when the API gives no retry hint.
DEFAULT_BLOCK_S = 30.0
# Per-day quota exhausted: do not bother that model again for a while.
DAILY_QUOTA_BLOCK_S = 600.0
# Finals wait at most this long for a model to unblock before echoing.
FINAL_MAX_WAIT_S = 20.0
INTERIM_TIMEOUT_S = 6.0
FINAL_TIMEOUT_S = 15.0

_RETRYABLE_CODES = {408, 429, 500, 502, 503, 504}
_RETRY_IN = re.compile(r"retry in ([\d.]+)\s*s", re.IGNORECASE)


class GeminiTranslator:
    # Shared across Sessions: quota is per project, not per Sesión.
    _blocked_until: ClassVar[dict[str, float]] = {}

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            from google import genai

            self._client = genai.Client(api_key=self._api_key)
        return self._client

    # -- port -------------------------------------------------------------

    async def translate(
        self,
        cue: Cue,
        dest: TrackLang,
        *,
        glossary_terms: list[GlossaryTerm],
    ) -> Cue:
        if dest == "original":
            return cue.model_copy(update={"id": str(uuid4()), "track": "original"})
        result = await self.translate_many(
            [cue], [dest], glossary_terms=glossary_terms, must=cue.kind == "final"
        )
        got = result.get((cue.id, dest))
        if got is None:
            # interim skipped under rate limit: caller decides; keep port contract
            return _retrack(cue, dest, cue.text) if dest == cue.sourceLang else _retrack(
                cue, dest, f"[{dest}] {cue.text}"
            )
        return got

    # -- batch ------------------------------------------------------------

    async def translate_many(
        self,
        cues: list[Cue],
        dests: list[TrackLang],
        *,
        glossary_terms: list[GlossaryTerm],
        must: bool,
    ) -> dict[tuple[str, TrackLang], Cue]:
        """Translate ``cues`` to every ``dests`` in one model call.

        Returns {(cue.id, dest): Cue}. Alias destinations (dest == sourceLang) are
        copied without a model call. When ``must`` is False (interims) and every
        model is blocked, the non-alias entries are simply absent.
        """
        out: dict[tuple[str, TrackLang], Cue] = {}
        if not cues:
            return out
        source = cues[0].sourceLang
        for cue in cues:
            for dest in dests:
                if dest == cue.sourceLang:
                    out[(cue.id, dest)] = _retrack(cue, dest, cue.text)
        targets = [d for d in dests if d != source and d != "original"]
        if not targets:
            return out

        # Cues with a different sourceLang than the batch go one by one.
        same = [c for c in cues if c.sourceLang == source]
        others = [c for c in cues if c.sourceLang != source]
        for c in others:
            out.update(
                await self.translate_many([c], dests, glossary_terms=glossary_terms, must=must)
            )

        texts = [c.text for c in same]
        translations = await self._translate_texts(
            texts, source, targets, glossary_terms, must=must
        )
        if translations is None:
            if must:
                for c in same:
                    for d in targets:
                        out[(c.id, d)] = _retrack(c, d, f"[{d}] {c.text}")
            return out
        for d in targets:
            lines = translations.get(d) or []
            for i, c in enumerate(same):
                text = lines[i].strip() if i < len(lines) and lines[i] else ""
                out[(c.id, d)] = _retrack(c, d, text or f"[{d}] {c.text}")
        return out

    # -- model call -------------------------------------------------------

    async def _translate_texts(
        self,
        texts: list[str],
        source: str,
        targets: list[TrackLang],
        glossary_terms: list[GlossaryTerm],
        *,
        must: bool,
    ) -> dict[str, list[str]] | None:
        do_not = [t.term for t in glossary_terms if t.doNotTranslate]
        prefer = [t.term for t in glossary_terms]
        target_names = ", ".join(f"{LANG_NAMES.get(d, d)} ({d})" for d in targets)
        system = (
            f"You translate live conference captions from {LANG_NAMES.get(source, source)} "
            f"to {target_names}. The input is a JSON array of caption lines; some may be "
            "partial sentences — translate each line as-is, do not merge, complete or "
            "reorder lines. Output ONLY a JSON object whose keys are the target language "
            "codes and whose values are arrays with exactly one translated string per "
            "input line, same order. No commentary."
        )
        if do_not:
            system += (
                " Do NOT translate these proper nouns / terms; keep them exactly: "
                + ", ".join(do_not)
                + "."
            )
        if prefer:
            system += " Prefer these canonical forms when relevant: " + ", ".join(prefer) + "."
        contents = json.dumps(texts, ensure_ascii=False)
        schema = {
            "type": "object",
            "properties": {d: {"type": "array", "items": {"type": "string"}} for d in targets},
            "required": list(targets),
        }
        config: dict[str, Any] = {
            "system_instruction": system,
            "temperature": 0.2,
            "response_mime_type": "application/json",
            "response_schema": schema,
        }
        timeout = FINAL_TIMEOUT_S if must else INTERIM_TIMEOUT_S
        deadline = time.monotonic() + (FINAL_MAX_WAIT_S if must else 0.0)

        while True:
            model = self._pick_model()
            if model is None:
                wait = self._next_unblock() - time.monotonic()
                if not must or time.monotonic() + max(wait, 0.0) > deadline:
                    return None
                await asyncio.sleep(max(wait, 0.2))
                continue
            try:
                resp = await asyncio.wait_for(
                    self._get_client().aio.models.generate_content(
                        model=model, contents=contents, config=config
                    ),
                    timeout,
                )
            except asyncio.TimeoutError:
                logger.warning("translator %s timed out after %.0fs", model, timeout)
                self._block(model, DEFAULT_BLOCK_S)
                continue
            except Exception as exc:
                if _is_retryable(exc):
                    secs = _retry_seconds(exc)
                    logger.warning("translator %s rate-limited/unavailable; blocked %.0fs", model, secs)
                    self._block(model, secs)
                    continue
                logger.exception("translator %s failed", model)
                return None
            parsed = _parse_json(getattr(resp, "text", None) or _resp_text(resp) or "")
            if isinstance(parsed, dict) and all(isinstance(parsed.get(d), list) for d in targets):
                return {d: [str(x) for x in parsed[d]] for d in targets}
            logger.warning("translator %s returned unparseable JSON; retrying once", model)
            self._block(model, 1.0)

    def _pick_model(self) -> str | None:
        now = time.monotonic()
        for m in (TRANSLATOR_MODEL, *TRANSLATOR_FALLBACK_MODELS):
            if self._blocked_until.get(m, 0.0) <= now:
                return m
        return None

    def _next_unblock(self) -> float:
        chain = (TRANSLATOR_MODEL, *TRANSLATOR_FALLBACK_MODELS)
        return min(self._blocked_until.get(m, 0.0) for m in chain)

    def _block(self, model: str, seconds: float) -> None:
        until = time.monotonic() + seconds
        if until > self._blocked_until.get(model, 0.0):
            self._blocked_until[model] = until


# -- helpers ---------------------------------------------------------------


def _is_retryable(exc: BaseException) -> bool:
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if code in _RETRYABLE_CODES:
        return True
    msg = str(exc)
    return any(tok in msg for tok in ("503", "429", "UNAVAILABLE", "RESOURCE_EXHAUSTED", "overloaded"))


def _retry_seconds(exc: BaseException) -> float:
    msg = str(exc)
    if "PerDay" in msg:
        return DAILY_QUOTA_BLOCK_S
    m = _RETRY_IN.search(msg)
    if m:
        try:
            return min(max(float(m.group(1)) + 0.5, 1.0), 120.0)
        except ValueError:
            pass
    return DEFAULT_BLOCK_S


def _parse_json(text: str) -> Any:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _retrack(cue: Cue, dest: TrackLang, text: str) -> Cue:
    return Cue(
        id=str(uuid4()),
        utteranceId=cue.utteranceId,
        sessionId=cue.sessionId,
        track=dest,
        sourceLang=cue.sourceLang,
        text=text,
        kind=cue.kind,
        startedAtMs=cue.startedAtMs,
        emittedAtMs=cue.emittedAtMs,
        endedAtMs=cue.endedAtMs,
    )


def _resp_text(resp: Any) -> str | None:
    try:
        cands = getattr(resp, "candidates", None) or []
        if not cands:
            return None
        content = getattr(cands[0], "content", None)
        parts = getattr(content, "parts", None) or []
        bits = [getattr(p, "text", "") for p in parts if getattr(p, "text", None)]
        return "".join(bits).strip() or None
    except Exception:
        return None
