"""FakeSpeechBackend — deterministic Cues from PCM via RelojDeSesion."""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator, Awaitable, Callable
from uuid import uuid4

from app.domain.models import Cue, SAMPLE_RATE, SourceLang, samples_to_ms

# Fixed scripts cycled by session name hash so two sessions differ.
ES_SCRIPT = [
    "Bienvenidos a Nerdearla",
    "Hoy hablamos de transcripcion en vivo",
    "El sistema produce cues en tiempo real",
    "Gracias por acompanarnos en la charla",
    "Preguntas al final de la sesion",
]

EN_SCRIPT = [
    "Welcome to Nerdearla",
    "Today we talk about live transcription",
    "The system emits cues in real time",
    "Thanks for joining this talk",
    "Questions at the end of the session",
]


def detect_source_lang(session_name: str) -> SourceLang:
    return "en" if "en" in session_name.lower() else "es"


def _script_for(session_name: str) -> list[str]:
    lang = detect_source_lang(session_name)
    base = EN_SCRIPT if lang == "en" else ES_SCRIPT
    h = int(hashlib.sha256(session_name.encode()).hexdigest(), 16)
    # rotate start index so two sessions differ
    start = h % len(base)
    return base[start:] + base[:start]


class FakeSpeechBackend:
    """
    Walk PCM. Every 1000 ms of audio (16000 samples):
      - interim at +300 ms of that window
      - final at +1000 ms
    Clock from sample count only. Does NOT rotate.
    If file shorter than 1s but >0 samples: scale window, emit at least one final.
    """

    async def run(
        self,
        session_id: str,
        pcm: AsyncIterator[tuple[bytes, int]],
        *,
        session_name: str,
        vocabulary: list[str],
        on_source_lang: Callable[[SourceLang], Awaitable[None] | None] | None = None,
        on_reconnect: Callable[[], Awaitable[None] | None] | None = None,
    ) -> AsyncIterator[Cue]:
        source_lang = detect_source_lang(session_name)
        if on_source_lang:
            maybe = on_source_lang(source_lang)
            if maybe is not None and hasattr(maybe, "__await__"):
                await maybe

        script = _script_for(session_name)
        # vocabulary is accepted for API parity with Gemini custom_vocabulary;
        # FakeSpeechBackend does not need it to produce deterministic text.

        samples = 0
        window_idx = 0
        interim_emitted_for: set[int] = set()
        final_emitted_for: set[int] = set()
        utterance_ids: dict[int, str] = {}

        # Collect all PCM first for short-file scaling, OR stream for long files.
        # Streaming: we don't know total length ahead of time. For short files
        # we detect at end. Spec: "Emit at least one final if the file is shorter
        # than 1s but has >0 samples (scale the window)."
        #
        # Strategy: stream normally with 1s windows; if we finish with samples>0
        # and never emitted a final, emit a scaled one.

        async for _chunk, samples_after in pcm:
            samples = samples_after
            clock_ms = samples_to_ms(samples)

            # Standard 1-second windows while we have enough audio.
            while True:
                window_start_ms = window_idx * 1000
                interim_at = window_start_ms + 300
                final_at = window_start_ms + 1000

                if clock_ms < interim_at:
                    break

                if window_idx not in utterance_ids:
                    utterance_ids[window_idx] = str(uuid4())

                text = script[window_idx % len(script)]
                uid = utterance_ids[window_idx]

                if window_idx not in interim_emitted_for and clock_ms >= interim_at:
                    interim_emitted_for.add(window_idx)
                    yield Cue(
                        id=str(uuid4()),
                        utteranceId=uid,
                        sessionId=session_id,
                        track="original",
                        sourceLang=source_lang,
                        text=text + "…",
                        kind="interim",
                        startedAtMs=window_start_ms,
                        emittedAtMs=interim_at,
                        endedAtMs=None,
                    )

                if window_idx not in final_emitted_for and clock_ms >= final_at:
                    final_emitted_for.add(window_idx)
                    yield Cue(
                        id=str(uuid4()),
                        utteranceId=uid,
                        sessionId=session_id,
                        track="original",
                        sourceLang=source_lang,
                        text=text,
                        kind="final",
                        startedAtMs=window_start_ms,
                        emittedAtMs=final_at,
                        endedAtMs=final_at,
                    )
                    window_idx += 1
                    continue
                break

        # Short file: scale window so we still emit at least one final.
        if samples > 0 and not final_emitted_for:
            total_ms = max(samples_to_ms(samples), 1)
            # scaled: interim at 30% of duration, final at end
            interim_at = max(total_ms * 30 // 100, 0)
            final_at = total_ms
            uid = str(uuid4())
            text = script[0]
            if interim_at < final_at:
                yield Cue(
                    id=str(uuid4()),
                    utteranceId=uid,
                    sessionId=session_id,
                    track="original",
                    sourceLang=source_lang,
                    text=text + "…",
                    kind="interim",
                    startedAtMs=0,
                    emittedAtMs=interim_at,
                    endedAtMs=None,
                )
            yield Cue(
                id=str(uuid4()),
                utteranceId=uid,
                sessionId=session_id,
                track="original",
                sourceLang=source_lang,
                text=text,
                kind="final",
                startedAtMs=0,
                emittedAtMs=final_at,
                endedAtMs=final_at,
            )
