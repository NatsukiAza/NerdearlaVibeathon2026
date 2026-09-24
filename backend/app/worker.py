"""Worker — one asyncio.Task per Sesion: FuenteDeAudio → SpeechBackend → Translator → Tracks."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

from app.adapters.audio import fixture_pcm, is_youtube_url, mic_pcm, url_pcm
from app.adapters.fake import FakeSpeechBackend, FakeTranslator
from app.adapters.gemini import GeminiLiveSpeechBackend, GeminiTranslator
from app.config import settings
from app.domain.models import Cue, LastError, SourceLang, TRANSLATE_DEBOUNCE_MS, TrackLang
from app.export import write_exports
from app.store import ALL_TRACKS, DEST_TRACKS, SessionRecord, store

logger = logging.getLogger(__name__)


def _build_backends() -> tuple[Any, Any]:
    if settings.speech_backend == "gemini":
        return (
            GeminiLiveSpeechBackend(settings.gemini_api_key),
            GeminiTranslator(settings.gemini_api_key),
        )
    return FakeSpeechBackend(), FakeTranslator()


async def _pcm_source(rec: SessionRecord) -> AsyncIterator[tuple[bytes, int]]:
    src = rec.sesion.source
    if src.kind == "fixture":
        if not src.path:
            raise FileNotFoundError("fixture source requires path")
        async for item in fixture_pcm(src.path):
            yield item
    elif src.kind == "url":
        if not src.path:
            raise ValueError("url source requires path")
        if is_youtube_url(src.path):
            raise ValueError("YouTube hosts are not allowed")
        async for item in url_pcm(src.path):
            yield item
    elif src.kind == "mic":
        if rec.mic_queue is None:
            rec.mic_queue = asyncio.Queue()
        async for item in mic_pcm(rec.mic_queue):
            yield item
    else:
        raise ValueError(f"unknown source kind: {src.kind}")


async def run_session(session_id: str) -> None:
    rec = store.get(session_id)
    if rec is None:
        return

    speech, translator = _build_backends()

    async def on_source_lang(lang: SourceLang) -> None:
        store.update_status(session_id, source_lang=lang)

    async def on_reconnect() -> None:
        store.update_status(session_id, bump_reconnects=True)

    pending: dict[str, asyncio.Task[None]] = {}

    async def emit_translated(original: Cue, dest: TrackLang) -> None:
        terms = list(rec.glossary.terms)
        translated = await translator.translate(
            original, dest, glossary_terms=terms
        )
        store.publish_cue(session_id, translated)

    async def schedule_interim(original: Cue) -> None:
        uid = original.utteranceId

        async def _debounced() -> None:
            await asyncio.sleep(TRANSLATE_DEBOUNCE_MS / 1000)
            for dest in DEST_TRACKS:
                if dest == original.sourceLang:
                    continue
                await emit_translated(original, dest)

        old = pending.pop(uid, None)
        if old and not old.done():
            old.cancel()
        pending[uid] = asyncio.create_task(_debounced())

    try:
        if settings.missing_gemini_api_key:
            store.update_status(
                session_id,
                status="degraded",
                last_error=LastError(
                    message="GEMINI_API_KEY missing; running FakeSpeechBackend",
                    atMs=0,
                    code="missing_gemini_api_key",
                ),
            )
        else:
            store.update_status(session_id, status="live", last_error=None)

        pcm = _pcm_source(rec)
        async for cue in speech.run(
            session_id,
            pcm,
            session_name=rec.sesion.name,
            vocabulary=rec.vocabulary,
            on_source_lang=on_source_lang,
            on_reconnect=on_reconnect,
        ):
            store.publish_cue(session_id, cue)

            for dest in DEST_TRACKS:
                if dest == cue.sourceLang:
                    await emit_translated(cue, dest)

            if cue.kind == "interim":
                await schedule_interim(cue)
            else:
                old = pending.pop(cue.utteranceId, None)
                if old and not old.done():
                    old.cancel()
                for dest in DEST_TRACKS:
                    if dest == cue.sourceLang:
                        continue
                    await emit_translated(cue, dest)

    except FileNotFoundError as exc:
        logger.warning("fixture missing for session %s: %s", session_id, exc)
        store.update_status(
            session_id,
            status="down",
            last_error=LastError(
                message=str(exc),
                atMs=0,
                code="fixture_not_found",
            ),
        )
        store.publish_error(session_id, str(exc), 0)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.exception("session worker failed: %s", session_id)
        store.update_status(
            session_id,
            status="down",
            last_error=LastError(
                message=str(exc),
                atMs=0,
                code="speech_backend_error",
            ),
        )
        store.publish_error(session_id, str(exc), 0)
    finally:
        for t in pending.values():
            if not t.done():
                t.cancel()


async def start_session(session_id: str) -> SessionRecord:
    rec = store.get(session_id)
    if rec is None:
        raise KeyError(session_id)
    # 409 if already live (or degraded while running / after start)
    if rec.sesion.status in ("live", "degraded"):
        raise RuntimeError("already_live")
    if rec.task is not None and not rec.task.done():
        raise RuntimeError("already_live")
    if rec.sesion.source.kind == "mic":
        rec.mic_queue = asyncio.Queue()
    rec.task = asyncio.create_task(run_session(session_id), name=f"session-{session_id}")
    return rec


async def stop_session(session_id: str) -> None:
    rec = store.get(session_id)
    if rec is None:
        raise KeyError(session_id)
    if rec.task and not rec.task.done():
        rec.task.cancel()
        try:
            await rec.task
        except asyncio.CancelledError:
            pass
    if rec.mic_queue is not None:
        try:
            rec.mic_queue.put_nowait(None)
        except asyncio.QueueFull:
            pass
    out_dir = settings.exports_dir / session_id
    write_exports(session_id, {t: list(rec.finals[t]) for t in ALL_TRACKS}, out_dir)
    store.update_status(session_id, status="stopped")
