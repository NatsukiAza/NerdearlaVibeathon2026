import { http, HttpResponse } from "msw";
import { getApiBase } from "../lib/api";
import type { Glossary, Session, TrackLang } from "../lib/types";
import {
  createProductionSseStream,
  createTrackSseStream,
  fakeExport,
  mockStore,
} from "./data";

const base = () => getApiBase();

const trackLangs = new Set(["original", "es", "en", "pt"]);

export const handlers = [
  http.get(`${base()}/api/sessions`, () => {
    return HttpResponse.json([...mockStore.sessions.values()]);
  }),

  http.get(`${base()}/api/sessions/:sessionId`, ({ params }) => {
    const s = mockStore.sessions.get(String(params.sessionId));
    if (!s) return new HttpResponse(null, { status: 404 });
    return HttpResponse.json(s);
  }),

  http.post(`${base()}/api/sessions/:sessionId/start`, ({ params }) => {
    const id = String(params.sessionId);
    const s = mockStore.sessions.get(id);
    if (!s) return new HttpResponse(null, { status: 404 });
    if (s.status === "live") {
      return new HttpResponse(null, { status: 409 });
    }
    const next: Session = {
      ...s,
      status: "live",
      lastError: null,
    };
    mockStore.sessions.set(id, next);
    return HttpResponse.json(next);
  }),

  http.post(`${base()}/api/sessions/:sessionId/stop`, ({ params }) => {
    const id = String(params.sessionId);
    const s = mockStore.sessions.get(id);
    if (!s) return new HttpResponse(null, { status: 404 });
    const next: Session = { ...s, status: "stopped" };
    mockStore.sessions.set(id, next);
    return HttpResponse.json(next);
  }),

  http.put(`${base()}/api/sessions/:sessionId/glossary`, async ({ params, request }) => {
    const id = String(params.sessionId);
    if (!mockStore.sessions.has(id)) {
      return new HttpResponse(null, { status: 404 });
    }
    const body = (await request.json()) as Glossary;
    mockStore.glossaries.set(id, body);
    return HttpResponse.json(body);
  }),

  http.get(`${base()}/api/sessions/:sessionId/export/:lang`, ({ params, request }) => {
    const id = String(params.sessionId);
    const lang = String(params.lang) as TrackLang;
    if (!mockStore.sessions.has(id) || !trackLangs.has(lang)) {
      return new HttpResponse(null, { status: 404 });
    }
    const url = new URL(request.url);
    const format = url.searchParams.get("format");
    if (format !== "srt" && format !== "vtt" && format !== "txt") {
      return new HttpResponse("format required", { status: 400 });
    }
    const body = fakeExport(lang, format);
    const type =
      format === "vtt"
        ? "text/vtt"
        : format === "srt"
          ? "application/x-subrip"
          : "text/plain";
    return new HttpResponse(body, {
      status: 200,
      headers: {
        "Content-Type": type,
        "Content-Disposition": `attachment; filename="session-${lang}.${format}"`,
      },
    });
  }),

  http.get(`${base()}/api/sessions/:sessionId/tracks/:lang/live`, ({ params }) => {
    const id = String(params.sessionId);
    const lang = String(params.lang) as TrackLang;
    if (!mockStore.sessions.has(id) || !trackLangs.has(lang)) {
      return new HttpResponse(null, { status: 404 });
    }
    const stream = createTrackSseStream(id, lang);
    return new HttpResponse(stream, {
      status: 200,
      headers: {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        Connection: "keep-alive",
      },
    });
  }),

  http.get(`${base()}/api/production/events`, ({ request }) => {
    const auth = request.headers.get("Authorization") ?? "";
    // MSW demo: any non-empty Bearer works except the literal "wrong"
    if (!auth.startsWith("Bearer ")) {
      return new HttpResponse(null, { status: 401 });
    }
    const token = auth.slice("Bearer ".length).trim();
    if (!token || token === "wrong") {
      return new HttpResponse(null, { status: 401 });
    }
    const stream = createProductionSseStream();
    return new HttpResponse(stream, {
      status: 200,
      headers: {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        Connection: "keep-alive",
      },
    });
  }),
];
