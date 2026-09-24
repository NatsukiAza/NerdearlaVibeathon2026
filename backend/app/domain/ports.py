"""Ports: SpeechBackend and Translator seams."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Awaitable
from typing import Protocol

from app.domain.models import Cue, GlossaryTerm, SourceLang, TrackLang


class SpeechBackend(Protocol):
    """Converts PCM into original-track Cues. Clock from RelojDeSesion only."""

    def run(
        self,
        session_id: str,
        pcm: AsyncIterator[tuple[bytes, int]],
        *,
        session_name: str,
        vocabulary: list[str],
        on_source_lang: Callable[[SourceLang], Awaitable[None] | None] | None = None,
        on_reconnect: Callable[[], Awaitable[None] | None] | None = None,
    ) -> AsyncIterator[Cue]:
        """Yield original-track Cues. pcm yields (chunk_bytes, samples_after_chunk)."""
        ...


class Translator(Protocol):
    """Text translation of Cues. Alias when dest == sourceLang (no model call)."""

    async def translate(
        self,
        cue: Cue,
        dest: TrackLang,
        *,
        glossary_terms: list[GlossaryTerm],
    ) -> Cue:
        ...
