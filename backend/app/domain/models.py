"""Domain models for Sesion, Cue, Track, GlosarioTecnico."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from uuid import uuid4


TrackLang = Literal["original", "es", "en", "pt"]
SourceLang = Literal["es", "en", "pt"]
CueKind = Literal["interim", "final"]
SessionStatus = Literal["idle", "live", "degraded", "down", "stopped"]
AudioKind = Literal["fixture", "mic", "url"]
ExportFormat = Literal["srt", "vtt", "txt"]


class GlossaryTerm(BaseModel):
    model_config = ConfigDict(extra="forbid")

    term: str
    doNotTranslate: bool = True


class Glossary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    terms: list[GlossaryTerm] = Field(default_factory=list)


class AudioSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: AudioKind
    path: str | None = None


class LastError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str
    atMs: int
    code: str | None = None


class LatencyStats(BaseModel):
    model_config = ConfigDict(extra="forbid")

    interimP50Ms: int | None = None
    interimP95Ms: int | None = None
    finalP50Ms: int | None = None
    finalP95Ms: int | None = None


class Cue(BaseModel):
    """Matches docs/contracts/cue.schema.json — additionalProperties forbidden."""

    model_config = ConfigDict(extra="forbid")

    id: str
    utteranceId: str
    sessionId: str
    track: TrackLang
    sourceLang: SourceLang
    text: str
    kind: CueKind
    startedAtMs: int
    emittedAtMs: int
    endedAtMs: int | None = None

    @staticmethod
    def new_id() -> str:
        return str(uuid4())


class Sesion(BaseModel):
    """Session JSON as returned by openapi Session schema."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    status: SessionStatus
    source: AudioSource
    sourceLang: SourceLang | None = None
    reconnects: int = 0
    lastError: LastError | None = None
    latency: LatencyStats | None = None


class CreateSession(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    source: AudioSource
    glossary: Glossary | None = None


SAMPLE_RATE = 16000
BYTES_PER_SAMPLE = 2  # s16le
CHUNK_MS = 100
CHUNK_BYTES = SAMPLE_RATE * BYTES_PER_SAMPLE * CHUNK_MS // 1000  # 3200
LATENCY_DEGRADED_P95_MS = 4000
GEMINI_ROTATE_MS = 8 * 60 * 1000  # 8 minutes
GEMINI_DRAIN_MS = 2000
# Collapse ASR partials. 300 ms fired on almost every interim and, once Flash Lite
# was blocked, each one landed on Gemini 3.5 Flash (peak 815 RPM).
TRANSLATE_DEBOUNCE_MS = 1000


def samples_to_ms(samples: int) -> int:
    return samples * 1000 // SAMPLE_RATE


def ms_to_samples(ms: int) -> int:
    return ms * SAMPLE_RATE // 1000
