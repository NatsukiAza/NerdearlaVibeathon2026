/** Types frozen to docs/contracts/openapi.yaml + cue.schema.json. Do not invent fields. */

export type TrackLang = "original" | "es" | "en" | "pt";

export type SourceLang = "es" | "en" | "pt";

export type SessionStatus = "idle" | "live" | "degraded" | "down" | "stopped";

export type AudioSourceKind = "fixture" | "mic" | "url";

export type CueKind = "interim" | "final";

export interface AudioSource {
  kind: AudioSourceKind;
  path?: string;
}

export interface GlossaryTerm {
  term: string;
  doNotTranslate?: boolean;
}

export interface Glossary {
  terms: GlossaryTerm[];
}

export interface SessionLastError {
  message?: string;
  atMs?: number;
  code?: string;
}

export interface SessionLatency {
  interimP50Ms?: number;
  interimP95Ms?: number;
  finalP50Ms?: number;
  finalP95Ms?: number;
}

export interface Session {
  id: string;
  name: string;
  status: SessionStatus;
  source: AudioSource;
  sourceLang: SourceLang | null;
  reconnects: number;
  lastError: SessionLastError | null;
  latency: SessionLatency | null;
}

export interface Cue {
  id: string;
  utteranceId: string;
  sessionId: string;
  track: TrackLang;
  sourceLang: SourceLang;
  text: string;
  kind: CueKind;
  startedAtMs: number;
  endedAtMs: number | null;
  emittedAtMs: number;
}

export interface TrackErrorEvent {
  message: string;
  atMs: number;
}

export type ExportFormat = "srt" | "vtt" | "txt";
