"""Gemini text Translator — gemini-3.5-flash + GlosarioTecnico."""

from __future__ import annotations

import logging
from uuid import uuid4

from app.adapters.gemini.models import TRANSLATOR_MODEL
from app.domain.models import Cue, GlossaryTerm, TrackLang

logger = logging.getLogger(__name__)

LANG_NAMES = {"es": "Spanish", "en": "English", "pt": "Portuguese"}


class GeminiTranslator:
    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    async def translate(
        self,
        cue: Cue,
        dest: TrackLang,
        *,
        glossary_terms: list[GlossaryTerm],
    ) -> Cue:
        if dest == "original":
            return cue.model_copy(update={"id": str(uuid4()), "track": "original"})

        # Alias — do not call the model.
        if dest == cue.sourceLang:
            return Cue(
                id=str(uuid4()),
                utteranceId=cue.utteranceId,
                sessionId=cue.sessionId,
                track=dest,
                sourceLang=cue.sourceLang,
                text=cue.text,
                kind=cue.kind,
                startedAtMs=cue.startedAtMs,
                emittedAtMs=cue.emittedAtMs,
                endedAtMs=cue.endedAtMs,
            )

        from google import genai

        client = genai.Client(api_key=self._api_key)
        do_not = [t.term for t in glossary_terms if t.doNotTranslate]
        prefer = [t.term for t in glossary_terms]
        system = (
            f"You translate conference captions from {LANG_NAMES.get(cue.sourceLang, cue.sourceLang)} "
            f"to {LANG_NAMES.get(dest, dest)}. "
            "Return ONLY the translated caption text, no quotes or commentary. "
        )
        if do_not:
            system += (
                "Do NOT translate these proper nouns / terms; keep them exactly: "
                + ", ".join(do_not)
                + ". "
            )
        if prefer:
            system += "Prefer these canonical forms when relevant: " + ", ".join(prefer) + "."

        try:
            resp = await client.aio.models.generate_content(
                model=TRANSLATOR_MODEL,
                contents=cue.text,
                config={
                    "system_instruction": system,
                    "temperature": 0.2,
                },
            )
            text = (getattr(resp, "text", None) or "").strip()
            if not text:
                # fallback extract
                text = _resp_text(resp) or cue.text
        except Exception:
            logger.exception("gemini translate failed; echoing source with lang tag")
            text = f"[{dest}] {cue.text}"

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


def _resp_text(resp) -> str | None:
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
