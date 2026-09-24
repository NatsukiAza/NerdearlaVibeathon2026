"""FastAPI routes matching docs/contracts/openapi.yaml."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Literal

from fastapi import APIRouter, Header, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import PlainTextResponse, Response, StreamingResponse

from app.adapters.audio import is_youtube_url
from app.config import settings
from app.domain.models import CreateSession, Glossary, TrackLang
from app.export import media_type, render
from app.store import store
from app.worker import start_session, stop_session

router = APIRouter(prefix="/api")


def _sse_pack(event: str, data: Any, event_id: str | None = None) -> bytes:
    payload = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
    parts: list[str] = []
    if event_id:
        parts.append(f"id: {event_id}")
    parts.append(f"event: {event}")
    for line in payload.splitlines() or [""]:
        parts.append(f"data: {line}")
    parts.append("")
    parts.append("")
    return "\n".join(parts).encode("utf-8")


@router.get("/sessions")
async def list_sessions() -> list[dict[str, Any]]:
    return [s.model_dump() for s in store.list_sessions()]


@router.post("/sessions", status_code=201)
async def create_session(body: CreateSession) -> dict[str, Any]:
    if body.source.kind == "url" and body.source.path:
        if is_youtube_url(body.source.path):
            raise HTTPException(status_code=400, detail="YouTube hosts are not allowed")
    if body.source.kind == "fixture" and not body.source.path:
        raise HTTPException(status_code=400, detail="fixture source requires path")
    if body.source.kind == "url" and not body.source.path:
        raise HTTPException(status_code=400, detail="url source requires path")
    sesion = store.create(body)
    return sesion.model_dump()


@router.get("/sessions/{session_id}")
async def get_session(session_id: str) -> dict[str, Any]:
    rec = store.get(session_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="session not found")
    return rec.sesion.model_dump()


@router.post("/sessions/{session_id}/start")
async def start(session_id: str) -> dict[str, Any]:
    rec = store.get(session_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="session not found")
    try:
        await start_session(session_id)
    except RuntimeError as exc:
        if str(exc) == "already_live":
            raise HTTPException(status_code=409, detail="already live") from exc
        raise
    # give the worker a tick to set status
    await asyncio.sleep(0)
    rec = store.get(session_id)
    assert rec is not None
    return rec.sesion.model_dump()


@router.post("/sessions/{session_id}/stop")
async def stop(session_id: str) -> dict[str, Any]:
    rec = store.get(session_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="session not found")
    await stop_session(session_id)
    rec = store.get(session_id)
    assert rec is not None
    return rec.sesion.model_dump()


@router.get("/sessions/{session_id}/tracks/{lang}/live")
async def track_live(
    session_id: str,
    lang: TrackLang,
    request: Request,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    rec = store.get(session_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="session not found")

    async def gen():
        # First connect (no Last-Event-ID): rehydrate all finals so late joiners
        # see history. Reconnect with Last-Event-ID: finals strictly after that id.
        if last_event_id:
            replay = store.finals_after(session_id, lang, last_event_id)
        else:
            rec_now = store.get(session_id)
            replay = list(rec_now.finals[lang]) if rec_now else []
        for cue in replay:
            yield _sse_pack("cue", cue.model_dump(), event_id=cue.id)
        # current status
        rec_now = store.get(session_id)
        if rec_now:
            yield _sse_pack("status", rec_now.sesion.model_dump())

        q = store.subscribe_track(session_id, lang)
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    item = await asyncio.wait_for(q.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    # keep-alive comment
                    yield b": keepalive\n\n"
                    continue
                ev = item.get("event", "cue")
                data = item.get("data")
                eid = item.get("id")
                yield _sse_pack(ev, data, event_id=eid if ev == "cue" else None)
        finally:
            store.unsubscribe_track(session_id, lang, q)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/sessions/{session_id}/export/{lang}")
async def export_track(
    session_id: str,
    lang: TrackLang,
    format: Literal["srt", "vtt", "txt"] = Query(...),
) -> Response:
    rec = store.get(session_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="session not found")
    body = render(rec.finals[lang], format)
    return Response(content=body, media_type=media_type(format))


@router.put("/sessions/{session_id}/glossary")
async def put_glossary(session_id: str, body: Glossary) -> dict[str, Any]:
    rec = store.get(session_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="session not found")
    # store concatenates conference glossary internally; return the session override
    store.set_glossary(session_id, body)
    return body.model_dump()


@router.get("/production/events")
async def production_events(
    request: Request,
    authorization: str | None = Header(default=None),
) -> StreamingResponse:
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    if token != settings.production_token:
        raise HTTPException(status_code=401, detail="unauthorized")

    async def gen():
        q = store.subscribe_production()
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    item = await asyncio.wait_for(q.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield b": keepalive\n\n"
                    continue
                yield _sse_pack("session", item.get("data"))
        finally:
            store.unsubscribe_production(q)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.websocket("/sessions/{session_id}/mic")
async def mic_ws(websocket: WebSocket, session_id: str) -> None:
    rec = store.get(session_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="session not found")
    if rec.sesion.source.kind != "mic":
        # 400 if source.kind != mic (openapi)
        raise HTTPException(status_code=400, detail="source.kind is not mic")

    await websocket.accept()
    if rec.mic_queue is None:
        rec.mic_queue = asyncio.Queue()
    try:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break
            data = message.get("bytes")
            if data is not None:
                await rec.mic_queue.put(data)
    except WebSocketDisconnect:
        pass
    finally:
        try:
            rec.mic_queue.put_nowait(None)
        except Exception:
            pass
