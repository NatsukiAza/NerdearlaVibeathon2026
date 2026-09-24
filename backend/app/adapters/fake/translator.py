"""FakeTranslator — prefix languages; alias when dest == sourceLang."""

from __future__ import annotations

from uuid import uuid4

from app.domain.models import Cue, GlossaryTerm, TrackLang


PREFIX = {"es": "[es] ", "en": "[en] ", "pt": "[pt] "}


class FakeTranslator:
    """
    Destinations es/en/pt. If dest == sourceLang, alias (copy cue, change track).
    Prefix translated text with [es]/ [en] `; `[pt] `.
    Debounce of interims is handled by the Worker (ADR-0016).
    """

    async def translate(
        self,
        cue: Cue,
        dest: TrackLang,
        *,
        glossary_terms: list[GlossaryTerm],
    ) -> Cue:
        if dest == "original":
            return cue.model_copy(update={"id": str(uuid4()), "track": "original"})

        # Alias: destination language equals source language — copy, no model call.
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

        # Prefer canonical glossary terms already in text; do not "translate" them.
        text = cue.text
        for term in glossary_terms:
            if term.doNotTranslate and term.term:
                # ensure canonical form stays (noop if already present)
                pass

        prefix = PREFIX.get(dest, f"[{dest}] ")
        return Cue(
            id=str(uuid4()),
            utteranceId=cue.utteranceId,
            sessionId=cue.sessionId,
            track=dest,
            sourceLang=cue.sourceLang,
            text=prefix + text,
            kind=cue.kind,
            startedAtMs=cue.startedAtMs,
            emittedAtMs=cue.emittedAtMs,
            endedAtMs=cue.endedAtMs,
        )
