import type { Cue, Glossary, Session, TrackLang } from "../lib/types";

export const SESSION_A: Session = {
  id: "11111111-1111-4111-8111-111111111111",
  name: "Abrir Nerdearla sin miedo",
  status: "live",
  source: { kind: "fixture", path: "fixtures/nerdearla-demo.wav" },
  sourceLang: "es",
  reconnects: 0,
  lastError: null,
  latency: {
    interimP50Ms: 420,
    interimP95Ms: 890,
    finalP50Ms: 1100,
    finalP95Ms: 2100,
  },
};

export const SESSION_B: Session = {
  id: "22222222-2222-4222-8222-222222222222",
  name: "Scaling open source conferences",
  status: "idle",
  source: { kind: "fixture", path: "fixtures/nerdearla-en.wav" },
  sourceLang: "en",
  reconnects: 1,
  lastError: {
    message: "SpeechBackend reconnect after rotate",
    atMs: 480_000,
    code: "backend_reconnect",
  },
  latency: {
    interimP50Ms: 510,
    interimP95Ms: 1200,
    finalP50Ms: 1400,
    finalP95Ms: 2800,
  },
};

/** Mutable store for MSW start/stop/glossary. */
export const mockStore: {
  sessions: Map<string, Session>;
  glossaries: Map<string, Glossary>;
} = {
  sessions: new Map([
    [SESSION_A.id, { ...SESSION_A }],
    [SESSION_B.id, { ...SESSION_B }],
  ]),
  glossaries: new Map([
    [
      SESSION_A.id,
      {
        terms: [
          { term: "Nerdearla", doNotTranslate: true },
          { term: "Konex", doNotTranslate: true },
        ],
      },
    ],
    [
      SESSION_B.id,
      {
        terms: [
          { term: "Nerdearla", doNotTranslate: true },
          { term: "vMix", doNotTranslate: true },
        ],
      },
    ],
  ]),
};

const SCRIPT: Record<TrackLang, string[]> = {
  original: [
    "Bienvenidos a Nerdearla",
    "Hoy hablamos de subtítulos en vivo",
    "El Track original alimenta el Translator",
  ],
  es: [
    "Bienvenidos a Nerdearla",
    "Hoy hablamos de subtítulos en vivo",
    "El Track original alimenta el Translator",
  ],
  en: [
    "Welcome to Nerdearla",
    "Today we talk about live captions",
    "The original Track feeds the Translator",
  ],
  pt: [
    "Bem-vindos à Nerdearla",
    "Hoje falamos de legendas ao vivo",
    "O Track original alimenta o Translator",
  ],
};

function uuid(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `00000000-0000-4000-8000-${Math.random().toString(16).slice(2, 14).padEnd(12, "0")}`;
}

function sseChunk(
  event: string,
  data: unknown,
  id?: string,
): string {
  const lines = [
    `event: ${event}`,
    ...(id ? [`id: ${id}`] : []),
    `data: ${JSON.stringify(data)}`,
    "",
    "",
  ];
  return lines.join("\n");
}

/** Ticking interim → final loop for a track. */
export function createTrackSseStream(
  sessionId: string,
  lang: TrackLang,
): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  const lines = SCRIPT[lang] ?? SCRIPT.original;
  let lineIdx = 0;
  let clockMs = 1_000;
  let cancelled = false;

  return new ReadableStream({
    start(controller) {
      const session = mockStore.sessions.get(sessionId);
      if (session) {
        controller.enqueue(
          encoder.encode(sseChunk("status", session)),
        );
      }

      const tick = () => {
        if (cancelled) return;
        const textBase = lines[lineIdx % lines.length] ?? "…";
        lineIdx += 1;
        const utteranceId = uuid();
        const sourceLang =
          mockStore.sessions.get(sessionId)?.sourceLang ?? "es";

        const emitInterim = (partial: string, started: number) => {
          if (cancelled) return;
          const cue: Cue = {
            id: uuid(),
            utteranceId,
            sessionId,
            track: lang,
            sourceLang,
            text: partial,
            kind: "interim",
            startedAtMs: started,
            endedAtMs: null,
            emittedAtMs: started + 300,
          };
          controller.enqueue(
            encoder.encode(sseChunk("cue", cue, cue.id)),
          );
        };

        const started = clockMs;
        clockMs += 3_500;

        // progressive interim
        const words = textBase.split(" ");
        let built = "";
        let wi = 0;

        const stepInterim = () => {
          if (cancelled) return;
          if (wi < words.length) {
            built = built ? `${built} ${words[wi]}` : words[wi]!;
            wi += 1;
            emitInterim(built, started);
            setTimeout(stepInterim, 280);
            return;
          }
          const finalCue: Cue = {
            id: uuid(),
            utteranceId,
            sessionId,
            track: lang,
            sourceLang,
            text: textBase,
            kind: "final",
            startedAtMs: started,
            endedAtMs: started + 2_800,
            emittedAtMs: started + 3_000,
          };
          controller.enqueue(
            encoder.encode(sseChunk("cue", finalCue, finalCue.id)),
          );
          setTimeout(tick, 1_200);
        };

        stepInterim();
      };

      setTimeout(tick, 400);
    },
    cancel() {
      cancelled = true;
    },
  });
}

export function createProductionSseStream(): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  let cancelled = false;
  let timer: ReturnType<typeof setInterval> | null = null;

  return new ReadableStream({
    start(controller) {
      const pushAll = () => {
        for (const s of mockStore.sessions.values()) {
          controller.enqueue(
            encoder.encode(sseChunk("session", s)),
          );
        }
      };
      pushAll();
      timer = setInterval(() => {
        if (cancelled) return;
        // nudge latency numbers so the monitor feels alive
        for (const [id, s] of mockStore.sessions) {
          if (s.status === "live" || s.status === "degraded") {
            const jitter = Math.floor(Math.random() * 40);
            const next: Session = {
              ...s,
              latency: s.latency
                ? {
                    ...s.latency,
                    interimP50Ms: (s.latency.interimP50Ms ?? 400) + jitter - 20,
                  }
                : s.latency,
            };
            mockStore.sessions.set(id, next);
            controller.enqueue(
              encoder.encode(sseChunk("session", next)),
            );
          }
        }
      }, 2_500);
    },
    cancel() {
      cancelled = true;
      if (timer) clearInterval(timer);
    },
  });
}

export function fakeExport(
  lang: TrackLang,
  format: "srt" | "vtt" | "txt",
): string {
  const lines = SCRIPT[lang] ?? SCRIPT.original;
  if (format === "txt") {
    return lines
      .map((t, i) => `[00:0${i}:00] ${t}`)
      .join("\n");
  }
  if (format === "vtt") {
    const body = lines
      .map(
        (t, i) =>
          `${i + 1}\n00:0${i}:00.000 --> 00:0${i}:03.000\n${t}\n`,
      )
      .join("\n");
    return `WEBVTT\n\n${body}`;
  }
  return lines
    .map(
      (t, i) =>
        `${i + 1}\n00:0${i}:00,000 --> 00:0${i}:03,000\n${t}\n`,
    )
    .join("\n");
}
