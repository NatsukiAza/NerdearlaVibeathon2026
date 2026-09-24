"""Gemini Live transcription adapter with 8-minute rotate.

Verified against google-genai 2.x and ``gemini-3.5-transcribe-live`` (24 sep 2026):

- Interims arrive as ``server_content.interim_input_transcription.text`` and are
  cumulative for the whole voice-activity segment (the text grows).
- Finals arrive as ``server_content.input_transcription.text`` only when the VAD
  detects end of speech, or after ``audio_stream_end``. A speaker who does not
  pause can go 40+ s without a final, so this adapter splits caption-sized
  utterances itself (sentence / clause boundaries, max ~72 chars) and reconciles
  them with the provider final when it arrives.
- ``custom_vocabulary`` lives in ``LiveConnectConfig.input_audio_transcription``.
- ``language_code`` is never populated; the source language is guessed from text.

RelojDeSesion: every timestamp comes from PCM samples sent so far (samples/16000),
never from wall clock or provider offsets.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any
from uuid import uuid4

from app.adapters.gemini.models import SPEECH_MODEL
from app.domain.models import (
    Cue,
    GEMINI_DRAIN_MS,
    GEMINI_ROTATE_MS,
    SourceLang,
    samples_to_ms,
)

logger = logging.getLogger(__name__)

# Caption-sized utterances: cut a final out of the growing interim when a sentence
# ends, or when the uncommitted text is longer than this (cut at clause / word).
MAX_UTTERANCE_CHARS = 56
MIN_CLAUSE_CHARS = 28
# Wait this long for the provider final after audio_stream_end when PCM ends.
END_DRAIN_MS = 3000
# Decide sourceLang once the stopword vote reaches this many hits, and allow
# one correction later if a stronger vote disagrees.
LANG_MIN_EVIDENCE = 2
LANG_CORRECTION_EVIDENCE = 4
# VAD: commit end-of-speech quickly so natural pauses become finals.
VAD_SILENCE_MS = 400
# Unplanned reconnects tolerated inside the window before the Sesión goes down.
RECONNECT_MAX = 3
RECONNECT_WINDOW_MS = 60_000

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?…])\s+")
_CLAUSE_SPLIT = re.compile(r"(?<=[,;:])\s+")

_MaybeAwait = Awaitable[None] | None


class GeminiLiveSpeechBackend:
    """
    Live transcription via google-genai.

    Rotate at 8 minutes: open a new live connection, keep feeding PCM to it, ask the
    old one to finish (audio_stream_end) and drain it for ~2 s, then close it.
    RelojDeSesion and sessionId are unchanged. An utterance straddling the rotate is
    finalized on the old socket; the new socket uses a new utteranceId.
    ``on_reconnect`` is called on each rotate (Monitor increments reconnects).
    """

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    async def run(
        self,
        session_id: str,
        pcm: AsyncIterator[tuple[bytes, int]],
        *,
        session_name: str,
        vocabulary: list[str],
        on_source_lang: Callable[[SourceLang], _MaybeAwait] | None = None,
        on_reconnect: Callable[[], _MaybeAwait] | None = None,
    ) -> AsyncIterator[Cue]:
        from google import genai

        client = genai.Client(api_key=self._api_key)
        out: asyncio.Queue[Cue | None | BaseException] = asyncio.Queue()
        clock = _SessionClock()
        lang = _LangState(on_source_lang, prior=prior_lang_from_name(session_name))
        segments: list[_Segment] = []

        async def _pipeline() -> None:
            drains: list[asyncio.Task[None]] = []
            failures: list[int] = []  # clock ms of recent unplanned reconnects
            try:
                seg = await _Segment.open(
                    client, session_id, clock, lang, out, list(vocabulary)
                )
                segments.append(seg)
                rotate_at = clock.ms() + GEMINI_ROTATE_MS

                async for chunk, samples_after in pcm:
                    clock.set_samples(samples_after)
                    try:
                        await seg.send(chunk)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        # Unplanned drop (throttle, 10-min cap, network): reopen and
                        # keep the Sesión alive instead of marking it down. Give up
                        # after RECONNECT_MAX failures inside RECONNECT_WINDOW_MS.
                        now = clock.ms()
                        failures[:] = [t for t in failures if now - t < RECONNECT_WINDOW_MS]
                        failures.append(now)
                        if len(failures) > RECONNECT_MAX:
                            raise
                        logger.warning(
                            "gemini live connection lost for %s (%s); reconnecting",
                            session_id, exc,
                        )
                        await seg.flush_and_close()
                        seg = await _Segment.open(
                            client, session_id, clock, lang, out, list(vocabulary)
                        )
                        segments.append(seg)
                        rotate_at = clock.ms() + GEMINI_ROTATE_MS
                        await _call(on_reconnect)
                        await seg.send(chunk)

                    if clock.ms() >= rotate_at:
                        # Open the replacement first so no PCM is lost, then drain
                        # the old connection concurrently. Vocabulary is re-read so
                        # a glossary PUT applies on the next rotate.
                        new_seg = await _Segment.open(
                            client, session_id, clock, lang, out, list(vocabulary)
                        )
                        segments.append(new_seg)
                        old, seg = seg, new_seg
                        drains.append(
                            asyncio.create_task(old.finish(GEMINI_DRAIN_MS))
                        )
                        rotate_at = clock.ms() + GEMINI_ROTATE_MS
                        await _call(on_reconnect)

                # PCM exhausted: let the provider emit its last final.
                await seg.finish(END_DRAIN_MS)
                if drains:
                    await asyncio.gather(*drains, return_exceptions=True)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # SDK / network failure → session down, not crash
                logger.exception("gemini live pipeline failed for %s", session_id)
                await out.put(exc)
            finally:
                for d in drains:
                    if not d.done():
                        d.cancel()
                for s in segments:
                    await s.close()
                await out.put(None)

        task = asyncio.create_task(_pipeline(), name=f"gemini-live-{session_id}")
        try:
            while True:
                item = await out.get()
                if item is None:
                    break
                if isinstance(item, BaseException):
                    raise item
                yield item
        finally:
            if not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass


class _SessionClock:
    """RelojDeSesion: ms derived from PCM samples sent so far."""

    def __init__(self) -> None:
        self._samples = 0

    def set_samples(self, samples: int) -> None:
        self._samples = samples

    def ms(self) -> int:
        return samples_to_ms(self._samples)


class _LangState:
    """Guess sourceLang from the text and notify the worker.

    Holds Cues until the first guess that has real evidence (stopword hits),
    and allows exactly one correction afterwards: a talk that opens with a
    short or ambiguous phrase must not lock the wrong language forever, but
    flipping more than once would rewrite Tracks the audience already saw.
    """

    def __init__(
        self,
        on_source_lang: Callable[[SourceLang], _MaybeAwait] | None,
        prior: SourceLang | None = None,
    ) -> None:
        self.lang: SourceLang = prior or "es"
        self.decided = False
        self.corrected = False
        self._cb = on_source_lang

    async def observe(self, text: str, *, final: bool) -> None:
        if self.corrected:
            return
        guess, evidence = guess_lang(text, prior=self.lang if self.decided else None)
        if not self.decided:
            if evidence < LANG_MIN_EVIDENCE and not final:
                return
            self.lang = guess
            self.decided = True
            await _call(self._cb, self.lang)
            return
        if evidence >= LANG_CORRECTION_EVIDENCE and guess != self.lang:
            self.lang = guess
            self.corrected = True
            await _call(self._cb, self.lang)


def prior_lang_from_name(session_name: str) -> SourceLang | None:
    """Language hinted by the Sesión name, e.g. 'Nerdearla EN' → en.

    Used only as a tie-break for the very first Cues; the text overrides it.
    """
    import re

    for token in re.findall(r"[a-z]+", session_name.lower()):
        if token in ("en", "eng", "english", "ingles"):
            return "en"
        if token in ("pt", "por", "portugues", "portuguese"):
            return "pt"
        if token in ("es", "esp", "spanish", "espanol", "castellano"):
            return "es"
    return None


class _Segment:
    """One Gemini Live connection: sends PCM, turns provider messages into Cues."""

    def __init__(
        self,
        client: Any,
        session_id: str,
        clock: _SessionClock,
        lang: _LangState,
        out: asyncio.Queue[Cue | None | BaseException],
    ) -> None:
        self._client = client
        self._session_id = session_id
        self._clock = clock
        self._lang = lang
        self._out = out
        self._cm: Any = None
        self._live: Any = None
        self._recv: asyncio.Task[None] | None = None
        self._ending = asyncio.Event()
        self._got_final_after_end = asyncio.Event()
        self._closed = False
        self._failure: BaseException | None = None  # receive loop died unexpectedly
        # Segmentation state for the current provider activity
        self._committed = ""  # text already emitted as finals in this activity
        self._utt_id = str(uuid4())
        self._utt_start_ms = clock.ms()
        self._last_interim_text = ""
        # Cues held until sourceLang is decided (so early Cues carry the right lang)
        self._held: list[Cue] = []

    @classmethod
    async def open(
        cls,
        client: Any,
        session_id: str,
        clock: _SessionClock,
        lang: _LangState,
        out: asyncio.Queue[Cue | None | BaseException],
        vocabulary: list[str],
    ) -> "_Segment":
        seg = cls(client, session_id, clock, lang, out)
        config = _build_config(vocabulary)
        seg._cm = client.aio.live.connect(model=SPEECH_MODEL, config=config)
        seg._live = await seg._cm.__aenter__()
        seg._recv = asyncio.create_task(seg._receive_loop())
        return seg

    async def send(self, chunk: bytes) -> None:
        from google.genai import types

        if self._failure is not None:
            # receive loop died → surface its error instead of sending into a void
            raise self._failure
        await self._live.send_realtime_input(
            audio=types.Blob(data=chunk, mime_type="audio/pcm;rate=16000")
        )

    async def flush_and_close(self) -> None:
        """Connection is gone: finalize whatever interim text is open, then close."""
        if self._closed:
            return
        self._ending.set()
        await self._flush_open_utterance()
        await self.close()

    async def finish(self, drain_ms: int) -> None:
        """Ask for the last final, wait up to drain_ms, flush leftovers, close."""
        if self._closed:
            return
        self._ending.set()
        try:
            await self._live.send_realtime_input(audio_stream_end=True)
        except Exception as exc:  # already closed by the provider, etc.
            logger.debug("audio_stream_end failed: %s", exc)
        try:
            await asyncio.wait_for(self._got_final_after_end.wait(), drain_ms / 1000)
        except asyncio.TimeoutError:
            pass
        await self._flush_open_utterance()
        await self.close()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._recv is not None and not self._recv.done():
            self._recv.cancel()
            try:
                await self._recv
            except (asyncio.CancelledError, Exception):
                pass
        if self._cm is not None:
            try:
                await self._cm.__aexit__(None, None, None)
            except Exception as exc:
                logger.debug("live connection close failed: %s", exc)

    # -- receive side -----------------------------------------------------

    async def _receive_loop(self) -> None:
        try:
            async for msg in self._live.receive():
                await self._handle_message(msg)
            if not (self._ending.is_set() or self._closed):
                # provider closed the socket on us; the pipeline reconnects on next send
                self._failure = ConnectionError("gemini live socket closed by provider")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if self._ending.is_set() or self._closed:
                logger.debug("live receive ended: %s", exc)
                return
            logger.warning("gemini live receive error: %s", exc)
            self._failure = exc

    async def _handle_message(self, msg: Any) -> None:
        sc = getattr(msg, "server_content", None)
        va = getattr(msg, "voice_activity", None)
        if va is not None:
            kind = str(getattr(va, "voice_activity_type", "") or "")
            if kind.endswith("ACTIVITY_START") and not self._committed and not self._last_interim_text:
                # speech starts after a gap: the utterance starts now
                self._utt_start_ms = self._clock.ms()
        if sc is None:
            return
        interim = getattr(sc, "interim_input_transcription", None)
        final = getattr(sc, "input_transcription", None)
        if final is not None and (getattr(final, "text", None) or "").strip():
            await self._on_text(final.text, final=True)
            if self._ending.is_set():
                self._got_final_after_end.set()
        elif interim is not None and (getattr(interim, "text", None) or "").strip():
            await self._on_text(interim.text, final=False)
        elif self._ending.is_set() and (
            getattr(sc, "generation_complete", False) or getattr(sc, "turn_complete", False)
        ):
            self._got_final_after_end.set()

    async def _on_text(self, full_text: str, *, final: bool) -> None:
        """Reconcile the cumulative provider text with what we already finalized."""
        rest = strip_committed(full_text, self._committed).strip()
        await self._lang.observe(full_text, final=final)

        if final:
            if rest:
                await self._emit_final(rest)
            elif self._last_interim_text:
                # provider final did not cover the open interim; keep the text
                await self._emit_final(self._last_interim_text)
            # provider activity closed → new activity starts clean
            self._committed = ""
            self._last_interim_text = ""
            return

        head, tail = split_caption(rest)
        while head:
            await self._emit_final(head)
            self._committed = (self._committed + " " + head).strip() if self._committed else head
            head, tail = split_caption(tail)

        if tail and tail != self._last_interim_text:
            self._last_interim_text = tail
            await self._emit(
                Cue(
                    id=str(uuid4()),
                    utteranceId=self._utt_id,
                    sessionId=self._session_id,
                    track="original",
                    sourceLang=self._lang.lang,
                    text=tail,
                    kind="interim",
                    startedAtMs=self._utt_start_ms,
                    emittedAtMs=self._clock.ms(),
                    endedAtMs=None,
                )
            )

    async def _emit_final(self, text: str) -> None:
        now = self._clock.ms()
        started = min(self._utt_start_ms, now)
        await self._emit(
            Cue(
                id=str(uuid4()),
                utteranceId=self._utt_id,
                sessionId=self._session_id,
                track="original",
                sourceLang=self._lang.lang,
                text=text,
                kind="final",
                startedAtMs=started,
                emittedAtMs=now,
                endedAtMs=now,
            )
        )
        self._utt_id = str(uuid4())
        self._utt_start_ms = now
        self._last_interim_text = ""

    async def _flush_open_utterance(self) -> None:
        """No provider final arrived in time: close the open utterance ourselves."""
        if self._last_interim_text:
            await self._emit_final(self._last_interim_text)
        self._committed = ""

    async def _emit(self, cue: Cue) -> None:
        if not self._lang.decided:
            self._held.append(cue)
            if cue.kind == "interim":
                return
        if self._held:
            held, self._held = self._held, []
            for h in held:
                await self._out.put(h.model_copy(update={"sourceLang": self._lang.lang}))
            if cue in held:
                return
        await self._out.put(cue)


# -- helpers ---------------------------------------------------------------


async def _call(cb: Callable[..., _MaybeAwait] | None, *args: Any) -> None:
    if cb is None:
        return
    maybe = cb(*args)
    if maybe is not None and hasattr(maybe, "__await__"):
        await maybe


def _build_config(vocab: list[str]) -> Any:
    """LiveConnectConfig for transcription; degrade gracefully across SDK versions."""
    from google.genai import types

    kwargs: dict[str, Any] = {"response_modalities": ["TEXT"]}
    try:
        atc_kwargs: dict[str, Any] = {}
        if vocab:
            atc_kwargs["custom_vocabulary"] = list(dict.fromkeys(vocab))
        atc_kwargs["language_codes"] = ["es", "en", "pt"]
        kwargs["input_audio_transcription"] = types.AudioTranscriptionConfig(**atc_kwargs)
    except Exception:
        try:
            kwargs["input_audio_transcription"] = types.AudioTranscriptionConfig()
        except Exception:
            pass
    try:
        kwargs["realtime_input_config"] = types.RealtimeInputConfig(
            automatic_activity_detection=types.AutomaticActivityDetection(
                end_of_speech_sensitivity="END_SENSITIVITY_HIGH",
                silence_duration_ms=VAD_SILENCE_MS,
            )
        )
    except Exception:
        pass
    try:
        return types.LiveConnectConfig(**kwargs)
    except Exception:
        kwargs.pop("realtime_input_config", None)
        try:
            return types.LiveConnectConfig(**kwargs)
        except Exception:
            return types.LiveConnectConfig(response_modalities=["TEXT"])


def strip_committed(full_text: str, committed: str) -> str:
    """Return the part of the cumulative provider text not yet emitted as finals.

    The provider may revise earlier words between interims and in the final, so
    fall back to locating the tail of the committed text before using lengths.
    """
    if not committed:
        return full_text
    if full_text.startswith(committed):
        return full_text[len(committed):]
    for n in (24, 14, 8, 4):
        tail = committed[-n:].strip()
        if not tail:
            continue
        # anchor the search near where the tail is expected (revisions shift a bit)
        idx = full_text.find(tail, max(0, len(committed) - n - 40))
        if idx >= 0:
            return full_text[idx + len(tail):]
    if len(full_text) > len(committed):
        return full_text[len(committed):]
    return ""


def split_caption(text: str) -> tuple[str, str]:
    """Split ``text`` into (finalizable head, still-open tail).

    Head is non-empty only when a sentence ends and more text follows, or when
    the text is longer than MAX_UTTERANCE_CHARS (cut at the last clause or word
    boundary inside the limit). Returns ("", text) when nothing can be cut yet.
    """
    text = text.strip()
    if not text:
        return "", ""
    parts = _SENTENCE_SPLIT.split(text, maxsplit=1)
    if len(parts) == 2 and parts[1].strip():
        return parts[0].strip(), parts[1].strip()
    if len(text) <= MAX_UTTERANCE_CHARS:
        return "", text
    window = text[: MAX_UTTERANCE_CHARS + 1]
    cut = -1
    for m in _CLAUSE_SPLIT.finditer(window):
        if m.start() >= MIN_CLAUSE_CHARS:
            cut = m.start()
    if cut < 0:
        cut = window.rfind(" ")
    if cut <= 0:
        return "", text
    return text[:cut].strip(), text[cut:].strip()


_EN_WORDS = frozenset(
    "the and is are of to in that we you it this with for on have be as so what".split()
)
_ES_WORDS = frozenset(
    "el la los las de que y en un una es por con para no se lo como pero más".split()
)
_PT_WORDS = frozenset(
    "o a os as de que e em um uma é não com para do da você isso mas mais".split()
)


def guess_lang(text: str, prior: SourceLang | None = None) -> tuple[SourceLang, int]:
    """Stopword vote → (language, evidence hits). Ties fall back to ``prior`` or es."""
    words = re.findall(r"[a-záéíóúñãõçü]+", text.lower())
    if not words:
        return prior or "es", 0
    en = sum(w in _EN_WORDS for w in words)
    es = sum(w in _ES_WORDS for w in words)
    pt = sum(w in _PT_WORDS for w in words)
    if any(ch in text for ch in "ãõç") or any(w in ("não", "você", "é") for w in words):
        pt += 2
    if any(ch in text for ch in "ñ¿¡"):
        es += 2
    scores = {"es": es, "en": en, "pt": pt}
    best_score = max(scores.values())
    if best_score == 0:
        return prior or "es", 0
    winners = [k for k, v in scores.items() if v == best_score]
    if prior in winners:
        return prior, best_score
    return winners[0], best_score
