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
    # Latest interim per utterance. A newer partial updates this instead of
    # cancelling an HTTP call that already left (the cancel still counts as RPM).
    latest_interim: dict[str, Cue] = {}
    # utteranceIds already finalized: late interim translations are dropped so they
    # cannot re-open the live line after the final was published.
    finalized: set[str] = set()
    # Finals are translated in order (export order) by one Task that drains the
    # backlog into batches, so the SpeechBackend loop never waits on the model.
    final_queue: asyncio.Queue[Cue | None] = asyncio.Queue()
    FINAL_BATCH_MAX = 8
    # Translators may offer translate_many(cues, dests, glossary_terms=, must=) to
    # serve several Cues × languages in one model call (GeminiTranslator does).
    translate_many = getattr(translator, "translate_many", None)

    async def translate_to(original: Cue, dest: TrackLang) -> Cue:
        terms = list(rec.glossary.terms)
        return await translator.translate(original, dest, glossary_terms=terms)

    async def emit_translated(original: Cue, dest: TrackLang) -> None:
        store.publish_cue(session_id, await translate_to(original, dest))

    async def translate_batch(
        cues: list[Cue], *, must: bool
    ) -> dict[tuple[str, TrackLang], Cue]:
        """{(cue.id, dest): Cue} for every non-alias destination; may omit interims."""
        terms = list(rec.glossary.terms)
        if translate_many is not None:
            dests: list[TrackLang] = [d for d in DEST_TRACKS if d != cues[0].sourceLang]
            return await translate_many(cues, dests, glossary_terms=terms, must=must)
        out: dict[tuple[str, TrackLang], Cue] = {}
        for c in cues:
            for d in DEST_TRACKS:
                if d != c.sourceLang:
                    out[(c.id, d)] = await translator.translate(c, d, glossary_terms=terms)
        return out

    async def final_worker() -> None:
        while True:
            first = await final_queue.get()
            if first is None:
                return
            batch = [first]
            while len(batch) < FINAL_BATCH_MAX:
                try:
                    nxt = final_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if nxt is None:
                    final_queue.put_nowait(None)  # keep the sentinel for the next loop
                    break
                batch.append(nxt)
            try:
                results = await translate_batch(batch, must=True)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("final translation failed (%s)", session_id)
                continue
            for c in batch:
                for d in DEST_TRACKS:
                    if d == c.sourceLang:
                        continue
                    translated = results.get((c.id, d))
                    if translated is not None:
                        store.publish_cue(session_id, translated)

    final_workers = [asyncio.create_task(final_worker(), name=f"translate-{session_id}")]

    async def schedule_interim(original: Cue) -> None:
        uid = original.utteranceId
        latest_interim[uid] = original
        inflight = pending.get(uid)
        if inflight is not None and not inflight.done():
            return

        async def _debounced() -> None:
            while True:
                await asyncio.sleep(TRANSLATE_DEBOUNCE_MS / 1000)
                cue = latest_interim.get(uid)
                if cue is None or uid in finalized:
                    return
                try:
                    results = await translate_batch([cue], must=False)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning("interim translation failed: %s", exc)
                    return
                if uid in finalized:
                    return
                for d in DEST_TRACKS:
                    if d == cue.sourceLang:
                        continue
                    translated = results.get((cue.id, d))
                    if translated is not None:
                        store.publish_cue(session_id, translated)
                # One follow-up if the line grew while this call was in flight.
                newer = latest_interim.get(uid)
                if newer is None or newer.id == cue.id or uid in finalized:
                    return

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

            # Alias Track (dest == sourceLang): no model call, publish right away.
            for dest in DEST_TRACKS:
                if dest == cue.sourceLang:
                    await emit_translated(cue, dest)

            if cue.kind == "interim":
                await schedule_interim(cue)
            else:
                finalized.add(cue.utteranceId)
                if len(finalized) > 5000:
                    finalized.clear()
                old = pending.pop(cue.utteranceId, None)
                if old and not old.done():
                    old.cancel()
                if any(dest != cue.sourceLang for dest in DEST_TRACKS):
                    final_queue.put_nowait(cue)

        # Audio ended: let queued final translations finish before the Task exits.
        final_queue.put_nowait(None)
        await asyncio.gather(*final_workers, return_exceptions=True)

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
        for w in final_workers:
            if not w.done():
                w.cancel()


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
