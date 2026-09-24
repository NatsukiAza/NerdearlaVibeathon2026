"""In-memory store of Sesiones, Tracks, and fan-out subscribers."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.config import settings
from app.domain.models import (
    AudioSource,
    CreateSession,
    Cue,
    Glossary,
    GlossaryTerm,
    LATENCY_DEGRADED_P95_MS,
    LastError,
    LatencyStats,
    Sesion,
    SessionStatus,
    SourceLang,
    TrackLang,
)


DEST_TRACKS: tuple[TrackLang, ...] = ("es", "en", "pt")
ALL_TRACKS: tuple[TrackLang, ...] = ("original", "es", "en", "pt")


@dataclass
class SessionRecord:
    sesion: Sesion
    glossary: Glossary = field(default_factory=Glossary)
    # finals only, per track (for export + SSE rehydrate)
    finals: dict[TrackLang, list[Cue]] = field(
        default_factory=lambda: {t: [] for t in ALL_TRACKS}
    )
    # recent cues (interim+final) for live clients — not required for export
    task: asyncio.Task[None] | None = None
    mic_queue: asyncio.Queue[bytes | None] | None = None
    # SSE subscribers: queues of outbound events
    track_subs: dict[TrackLang, list[asyncio.Queue[dict[str, Any]]]] = field(
        default_factory=lambda: {t: [] for t in ALL_TRACKS}
    )
    interim_latencies: list[int] = field(default_factory=list)
    final_latencies: list[int] = field(default_factory=list)
    # mutable vocabulary list shared with SpeechBackend across rotates
    vocabulary: list[str] = field(default_factory=list)


class Store:
    def __init__(self) -> None:
        self._sessions: dict[str, SessionRecord] = {}
        self._production_subs: list[asyncio.Queue[dict[str, Any]]] = []
        self._lock = asyncio.Lock()

    def list_sessions(self) -> list[Sesion]:
        return [r.sesion.model_copy() for r in self._sessions.values()]

    def get(self, session_id: str) -> SessionRecord | None:
        return self._sessions.get(session_id)

    def create(self, body: CreateSession) -> Sesion:
        sid = str(uuid4())
        sesion = Sesion(
            id=sid,
            name=body.name,
            status="idle",
            source=body.source,
            sourceLang=None,
            reconnects=0,
            lastError=None,
            latency=None,
        )
        if settings.missing_gemini_api_key:
            sesion.status = "idle"
            # mark degraded only once started; keep idle until start
        glossary = body.glossary or Glossary(terms=[])
        # concatenate conference glossary
        conference = _load_conference_glossary()
        merged = Glossary(terms=[*conference.terms, *glossary.terms])
        rec = SessionRecord(
            sesion=sesion,
            glossary=merged,
            vocabulary=[t.term for t in merged.terms],
        )
        self._sessions[sid] = rec
        self._broadcast_production(sesion)
        return sesion.model_copy()

    def set_glossary(self, session_id: str, glossary: Glossary) -> Glossary:
        rec = self._sessions[session_id]
        conference = _load_conference_glossary()
        merged = Glossary(terms=[*conference.terms, *glossary.terms])
        rec.glossary = merged
        # Translator uses new terms immediately; mutate vocabulary in place for next Gemini rotate.
        rec.vocabulary.clear()
        rec.vocabulary.extend(t.term for t in merged.terms)
        return glossary

    def update_status(
        self,
        session_id: str,
        *,
        status: SessionStatus | None = None,
        source_lang: SourceLang | None = None,
        last_error: LastError | None = False,  # type: ignore[assignment]
        bump_reconnects: bool = False,
    ) -> Sesion:
        rec = self._sessions[session_id]
        s = rec.sesion
        data = s.model_dump()
        if status is not None:
            data["status"] = status
        if source_lang is not None:
            data["sourceLang"] = source_lang
        if last_error is not False:
            data["lastError"] = last_error.model_dump() if last_error else None
        if bump_reconnects:
            data["reconnects"] = s.reconnects + 1
        rec.sesion = Sesion(**data)
        self._maybe_degrade(rec)
        self._broadcast_production(rec.sesion)
        self._broadcast_status(rec)
        return rec.sesion.model_copy()

    def record_latency(self, session_id: str, cue: Cue) -> None:
        rec = self._sessions[session_id]
        lat = max(cue.emittedAtMs - cue.startedAtMs, 0)
        if cue.kind == "interim":
            rec.interim_latencies.append(lat)
            if len(rec.interim_latencies) > 200:
                rec.interim_latencies = rec.interim_latencies[-200:]
        else:
            rec.final_latencies.append(lat)
            if len(rec.final_latencies) > 200:
                rec.final_latencies = rec.final_latencies[-200:]
        rec.sesion = rec.sesion.model_copy(
            update={"latency": _latency_stats(rec.interim_latencies, rec.final_latencies)}
        )
        self._maybe_degrade(rec)

    def publish_cue(self, session_id: str, cue: Cue) -> None:
        rec = self._sessions[session_id]
        if cue.kind == "final":
            rec.finals[cue.track].append(cue)
        self.record_latency(session_id, cue)
        event = {"event": "cue", "id": cue.id, "data": cue.model_dump()}
        for q in list(rec.track_subs.get(cue.track, [])):
            self._put_nowait(q, event)

    def subscribe_track(
        self, session_id: str, track: TrackLang
    ) -> asyncio.Queue[dict[str, Any]]:
        rec = self._sessions[session_id]
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=256)
        rec.track_subs[track].append(q)
        return q

    def unsubscribe_track(
        self, session_id: str, track: TrackLang, q: asyncio.Queue[dict[str, Any]]
    ) -> None:
        rec = self._sessions.get(session_id)
        if not rec:
            return
        subs = rec.track_subs.get(track, [])
        if q in subs:
            subs.remove(q)

    def finals_after(
        self, session_id: str, track: TrackLang, last_event_id: str | None
    ) -> list[Cue]:
        rec = self._sessions[session_id]
        finals = rec.finals[track]
        if not last_event_id:
            return []
        # rehydrate finals only after that id
        idx = next((i for i, c in enumerate(finals) if c.id == last_event_id), None)
        if idx is None:
            return list(finals)
        return finals[idx + 1 :]

    def subscribe_production(self) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=256)
        self._production_subs.append(q)
        # initial snapshot
        for r in self._sessions.values():
            self._put_nowait(
                q, {"event": "session", "data": r.sesion.model_dump()}
            )
        return q

    def unsubscribe_production(self, q: asyncio.Queue[dict[str, Any]]) -> None:
        if q in self._production_subs:
            self._production_subs.remove(q)

    def _broadcast_production(self, sesion: Sesion) -> None:
        event = {"event": "session", "data": sesion.model_dump()}
        for q in list(self._production_subs):
            self._put_nowait(q, event)

    def _broadcast_status(self, rec: SessionRecord) -> None:
        event = {"event": "status", "data": rec.sesion.model_dump()}
        for track, subs in rec.track_subs.items():
            for q in list(subs):
                self._put_nowait(q, event)

    def publish_error(self, session_id: str, message: str, at_ms: int) -> None:
        event = {"event": "error", "data": {"message": message, "atMs": at_ms}}
        rec = self._sessions.get(session_id)
        if not rec:
            return
        for track, subs in rec.track_subs.items():
            for q in list(subs):
                self._put_nowait(q, event)

    @staticmethod
    def _put_nowait(q: asyncio.Queue[dict[str, Any]], item: dict[str, Any]) -> None:
        try:
            q.put_nowait(item)
        except asyncio.QueueFull:
            try:
                q.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                q.put_nowait(item)
            except asyncio.QueueFull:
                pass

    def _maybe_degrade(self, rec: SessionRecord) -> None:
        s = rec.sesion
        if s.status in ("idle", "stopped", "down"):
            return
        reasons: list[str] = []
        if settings.missing_gemini_api_key:
            reasons.append("missing_gemini_api_key")
        lat = s.latency
        if lat:
            if (lat.interimP95Ms or 0) > LATENCY_DEGRADED_P95_MS:
                reasons.append("interim_p95")
            if (lat.finalP95Ms or 0) > LATENCY_DEGRADED_P95_MS:
                reasons.append("final_p95")
        if s.reconnects > 0:
            reasons.append("reconnect")
        if reasons:
            err = s.lastError
            if settings.missing_gemini_api_key:
                err = LastError(
                    message="GEMINI_API_KEY missing; running FakeSpeechBackend",
                    atMs=0,
                    code="missing_gemini_api_key",
                )
            rec.sesion = s.model_copy(update={"status": "degraded", "lastError": err})


def _percentile(values: list[int], p: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    k = (len(ordered) - 1) * p
    f = int(k)
    c = min(f + 1, len(ordered) - 1)
    if f == c:
        return ordered[f]
    return int(ordered[f] + (ordered[c] - ordered[f]) * (k - f))


def _latency_stats(
    interim: list[int], final: list[int]
) -> LatencyStats | None:
    if not interim and not final:
        return None
    return LatencyStats(
        interimP50Ms=_percentile(interim, 0.50),
        interimP95Ms=_percentile(interim, 0.95),
        finalP50Ms=_percentile(final, 0.50),
        finalP95Ms=_percentile(final, 0.95),
    )


def _load_conference_glossary() -> Glossary:
    path: Path = settings.conference_glossary_path
    if not path.is_file():
        return Glossary(terms=[])
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return Glossary.model_validate(data)
    except Exception:
        return Glossary(terms=[])


store = Store()
