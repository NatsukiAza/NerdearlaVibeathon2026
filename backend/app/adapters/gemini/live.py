"""Gemini Live transcription adapter with 8-minute rotate."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
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


class GeminiLiveSpeechBackend:
    """
    Live transcription via google-genai.
    Rotate at 8 minutes: open new live session, keep feeding PCM, drain ~2s the old one,
    close. RelojDeSesion and sessionId unchanged. Utterance straddling rotate is
    finalized on the old socket; new socket uses a new utteranceId. Increment reconnects.
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
        on_source_lang: Callable[[SourceLang], Awaitable[None] | None] | None = None,
        on_reconnect: Callable[[], Awaitable[None] | None] | None = None,
    ) -> AsyncIterator[Cue]:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=self._api_key)
        source_lang_box: dict[str, SourceLang] = {"lang": "es"}
        cue_queue: asyncio.Queue[Cue | None | BaseException] = asyncio.Queue()
        samples_box = {"n": 0}
        stop = asyncio.Event()

        def _clock_ms() -> int:
            return samples_to_ms(samples_box["n"])

        def _build_config(vocab: list[str]):
            # Adapt to installed SDK surface; avoid leaking SDK types into domain.
            kwargs: dict = {
                "response_modalities": ["TEXT"],
            }
            # language hints es/en/pt
            try:
                kwargs["speech_config"] = types.SpeechConfig(
                    language_code="es",
                )
            except Exception:
                pass
            # custom_vocabulary from glossary terms
            if vocab:
                try:
                    kwargs["transcription_config"] = types.TranscriptionConfig(
                        custom_vocabulary=vocab,
                    )
                except Exception:
                    try:
                        kwargs["input_audio_transcription"] = types.AudioTranscriptionConfig()
                    except Exception:
                        pass
            try:
                return types.LiveConnectConfig(**kwargs)
            except Exception:
                return types.LiveConnectConfig(response_modalities=["TEXT"])

        async def _consume_session(
            live_session,
            utterance_id: str,
            started_samples: int,
            drain_until: asyncio.Event | None,
        ) -> str:
            """Read provider messages → Cue. Returns last utterance_id used."""
            uid = utterance_id
            started_at = samples_to_ms(started_samples)
            current_text = ""
            try:
                async for msg in live_session.receive():
                    if stop.is_set():
                        break
                    text, is_final = _extract_text(msg)
                    if text is None:
                        continue
                    # Detect lang heuristically once
                    if on_source_lang and source_lang_box.get("_set") is not True:
                        detected = _guess_lang(text)
                        source_lang_box["lang"] = detected
                        source_lang_box["_set"] = True
                        maybe = on_source_lang(detected)
                        if maybe is not None and hasattr(maybe, "__await__"):
                            await maybe

                    emitted = _clock_ms()
                    if is_final:
                        cue = Cue(
                            id=str(uuid4()),
                            utteranceId=uid,
                            sessionId=session_id,
                            track="original",
                            sourceLang=source_lang_box["lang"],
                            text=text,
                            kind="final",
                            startedAtMs=started_at,
                            emittedAtMs=emitted,
                            endedAtMs=emitted,
                        )
                        await cue_queue.put(cue)
                        uid = str(uuid4())
                        started_at = emitted
                        current_text = ""
                    else:
                        current_text = text
                        cue = Cue(
                            id=str(uuid4()),
                            utteranceId=uid,
                            sessionId=session_id,
                            track="original",
                            sourceLang=source_lang_box["lang"],
                            text=text,
                            kind="interim",
                            startedAtMs=started_at,
                            emittedAtMs=emitted,
                            endedAtMs=None,
                        )
                        await cue_queue.put(cue)

                    if drain_until and drain_until.is_set():
                        # finalize open utterance on old socket
                        if current_text:
                            emitted = _clock_ms()
                            await cue_queue.put(
                                Cue(
                                    id=str(uuid4()),
                                    utteranceId=uid,
                                    sessionId=session_id,
                                    track="original",
                                    sourceLang=source_lang_box["lang"],
                                    text=current_text,
                                    kind="final",
                                    startedAtMs=started_at,
                                    emittedAtMs=emitted,
                                    endedAtMs=emitted,
                                )
                            )
                            uid = str(uuid4())
                            current_text = ""
                        break
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("gemini live receive error")
                await cue_queue.put(exc)
            return uid

        async def _pipeline() -> None:
            config = _build_config(vocabulary)
            rotate_ms = GEMINI_ROTATE_MS
            session_start_clock = 0
            try:
                while not stop.is_set():
                    utterance_id = str(uuid4())
                    started_samples = samples_box["n"]
                    drain_flag = asyncio.Event()

                    async with client.aio.live.connect(
                        model=SPEECH_MODEL, config=config
                    ) as live:
                        recv_task = asyncio.create_task(
                            _consume_session(
                                live, utterance_id, started_samples, drain_flag
                            )
                        )
                        rotate_at = _clock_ms() + rotate_ms
                        try:
                            async for chunk, samples_after in pcm:
                                samples_box["n"] = samples_after
                                try:
                                    await live.send(
                                        input={
                                            "data": chunk,
                                            "mime_type": "audio/pcm;rate=16000",
                                        }
                                    )
                                except TypeError:
                                    # alternate SDK surface
                                    await live.send_realtime_input(
                                        audio=types.Blob(
                                            data=chunk,
                                            mime_type="audio/pcm;rate=16000",
                                        )
                                    )
                                except Exception:
                                    try:
                                        await live.send_realtime_input(media=chunk)
                                    except Exception as send_exc:
                                        await cue_queue.put(send_exc)
                                        return

                                if _clock_ms() >= rotate_at:
                                    # open is already this session; signal drain
                                    drain_flag.set()
                                    await asyncio.sleep(GEMINI_DRAIN_MS / 1000)
                                    recv_task.cancel()
                                    try:
                                        await recv_task
                                    except asyncio.CancelledError:
                                        pass
                                    if on_reconnect:
                                        maybe = on_reconnect()
                                        if maybe is not None and hasattr(maybe, "__await__"):
                                            await maybe
                                    # rebuild config so glossary PUT applies on next rotate
                                    config = _build_config(vocabulary)
                                    break
                            else:
                                # PCM exhausted
                                drain_flag.set()
                                await asyncio.sleep(0.2)
                                if not recv_task.done():
                                    recv_task.cancel()
                                    try:
                                        await recv_task
                                    except asyncio.CancelledError:
                                        pass
                                stop.set()
                                return
                        except Exception as exc:
                            await cue_queue.put(exc)
                            return
            finally:
                await cue_queue.put(None)

        task = asyncio.create_task(_pipeline())
        try:
            while True:
                item = await cue_queue.get()
                if item is None:
                    break
                if isinstance(item, BaseException):
                    raise item
                yield item
        finally:
            stop.set()
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass


def _extract_text(msg) -> tuple[str | None, bool]:
    """Best-effort extract (text, is_final) from google-genai live messages."""
    # server_content.output_transcription / input_transcription
    sc = getattr(msg, "server_content", None) or getattr(msg, "serverContent", None)
    if sc is not None:
        for attr in ("input_transcription", "output_transcription", "inputTranscription", "outputTranscription"):
            tr = getattr(sc, attr, None)
            if tr is not None:
                text = getattr(tr, "text", None) or ""
                finished = bool(
                    getattr(tr, "finished", False)
                    or getattr(tr, "is_final", False)
                    or getattr(sc, "turn_complete", False)
                )
                if text:
                    return text, finished
        # model turn text
        mt = getattr(sc, "model_turn", None) or getattr(sc, "modelTurn", None)
        if mt is not None:
            parts = getattr(mt, "parts", None) or []
            texts = []
            for p in parts:
                t = getattr(p, "text", None)
                if t:
                    texts.append(t)
            if texts:
                finished = bool(getattr(sc, "turn_complete", False) or getattr(sc, "generation_complete", False))
                return "".join(texts), finished
    # dict-like
    if isinstance(msg, dict):
        text = msg.get("text")
        if text:
            return text, bool(msg.get("is_final") or msg.get("finished"))
    return None, False


def _guess_lang(text: str) -> SourceLang:
    lower = text.lower()
    # crude heuristic; SpeechBackend may refine later
    en_markers = (" the ", " and ", " welcome ", " today ", " session ")
    if any(m in f" {lower} " for m in en_markers):
        return "en"
    pt_markers = ("ção", "ões", " não ", " vocês ", " obrigado ")
    if any(m in lower for m in pt_markers):
        return "pt"
    return "es"
