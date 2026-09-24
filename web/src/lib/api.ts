import type {
  ExportFormat,
  Glossary,
  Session,
  TrackLang,
} from "./types";

const DEFAULT_API_BASE = "http://localhost:8000";

export function getApiBase(): string {
  const raw = import.meta.env.VITE_API_BASE as string | undefined;
  if (raw && raw.trim().length > 0) return raw.replace(/\/$/, "");
  return DEFAULT_API_BASE;
}

export function useMsw(): boolean {
  return import.meta.env.VITE_USE_MSW === "true";
}

async function parseJson<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(text || `${res.status} ${res.statusText}`);
  }
  return res.json() as Promise<T>;
}

export async function listSessions(): Promise<Session[]> {
  const res = await fetch(`${getApiBase()}/api/sessions`);
  return parseJson<Session[]>(res);
}

export async function getSession(sessionId: string): Promise<Session> {
  const res = await fetch(`${getApiBase()}/api/sessions/${sessionId}`);
  return parseJson<Session>(res);
}

export async function startSession(sessionId: string): Promise<Session> {
  const res = await fetch(`${getApiBase()}/api/sessions/${sessionId}/start`, {
    method: "POST",
  });
  return parseJson<Session>(res);
}

export async function stopSession(sessionId: string): Promise<Session> {
  const res = await fetch(`${getApiBase()}/api/sessions/${sessionId}/stop`, {
    method: "POST",
  });
  return parseJson<Session>(res);
}

export async function putGlossary(
  sessionId: string,
  glossary: Glossary,
): Promise<Glossary> {
  const res = await fetch(`${getApiBase()}/api/sessions/${sessionId}/glossary`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(glossary),
  });
  return parseJson<Glossary>(res);
}

export function exportUrl(
  sessionId: string,
  lang: TrackLang,
  format: ExportFormat,
): string {
  return `${getApiBase()}/api/sessions/${sessionId}/export/${lang}?format=${format}`;
}

export function trackLiveUrl(sessionId: string, lang: TrackLang): string {
  return `${getApiBase()}/api/sessions/${sessionId}/tracks/${lang}/live`;
}

export function productionEventsUrl(): string {
  return `${getApiBase()}/api/production/events`;
}
