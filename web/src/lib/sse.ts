import type { Cue, Session, TrackErrorEvent } from "./types";

export type TrackSseHandlers = {
  onCue?: (cue: Cue) => void;
  onStatus?: (session: Session) => void;
  onError?: (err: TrackErrorEvent) => void;
  onConnectionError?: (err: Event) => void;
};

export type ProductionSseHandlers = {
  onSession?: (session: Session) => void;
  onConnectionError?: (err: Event) => void;
  onUnauthorized?: () => void;
};

/**
 * SSE for /api/sessions/{id}/tracks/{lang}/live
 * Sends Last-Event-ID = last Cue.id on reconnect.
 */
export function subscribeTrackLive(
  url: string,
  handlers: TrackSseHandlers,
  options?: { lastEventId?: string | null },
): () => void {
  let closed = false;
  let es: EventSource | null = null;
  let lastId = options?.lastEventId ?? null;
  let retryTimer: ReturnType<typeof setTimeout> | null = null;
  let attempt = 0;

  const connect = () => {
    if (closed) return;
    const withId =
      lastId != null && lastId.length > 0
        ? `${url}${url.includes("?") ? "&" : "?"}lastEventId=${encodeURIComponent(lastId)}`
        : url;

    // EventSource cannot set custom headers; Last-Event-ID is sent automatically
    // by the browser when EventSource reconnects if we set es.lastEventId via
    // the id: field on messages. For first connect after a manual reconnect we
    // also pass lastEventId as query for MSW / backends that honor it.
    es = new EventSource(withId);

    const wire = (
      name: string,
      fn: (data: string, eventId: string | null) => void,
    ) => {
      es!.addEventListener(name, (ev) => {
        const me = ev as MessageEvent<string>;
        if (me.lastEventId) lastId = me.lastEventId;
        fn(me.data, me.lastEventId || null);
      });
    };

    wire("cue", (data) => {
      try {
        const cue = JSON.parse(data) as Cue;
        lastId = cue.id;
        handlers.onCue?.(cue);
      } catch {
        /* ignore malformed */
      }
    });

    wire("status", (data) => {
      try {
        handlers.onStatus?.(JSON.parse(data) as Session);
      } catch {
        /* ignore */
      }
    });

    wire("error", (data) => {
      try {
        handlers.onError?.(JSON.parse(data) as TrackErrorEvent);
      } catch {
        /* ignore */
      }
    });

    es.onerror = (ev) => {
      handlers.onConnectionError?.(ev);
      es?.close();
      es = null;
      if (closed) return;
      attempt += 1;
      const delay = Math.min(8000, 500 + attempt * 400);
      retryTimer = setTimeout(connect, delay);
    };
  };

  connect();

  return () => {
    closed = true;
    if (retryTimer) clearTimeout(retryTimer);
    es?.close();
  };
}

/**
 * SSE for /api/production/events with Authorization: Bearer.
 * Native EventSource cannot set headers, so we use fetch + ReadableStream.
 */
export function subscribeProductionEvents(
  url: string,
  token: string,
  handlers: ProductionSseHandlers,
): () => void {
  let closed = false;
  let abort: AbortController | null = null;
  let retryTimer: ReturnType<typeof setTimeout> | null = null;
  let attempt = 0;

  const connect = async () => {
    if (closed) return;
    abort = new AbortController();
    try {
      const res = await fetch(url, {
        headers: {
          Accept: "text/event-stream",
          Authorization: `Bearer ${token}`,
        },
        signal: abort.signal,
      });

      if (res.status === 401) {
        handlers.onUnauthorized?.();
        return;
      }
      if (!res.ok || !res.body) {
        throw new Error(`SSE ${res.status}`);
      }

      attempt = 0;
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let eventName = "message";
      let dataLines: string[] = [];

      const dispatch = () => {
        if (dataLines.length === 0) {
          eventName = "message";
          return;
        }
        const data = dataLines.join("\n");
        dataLines = [];
        const name = eventName;
        eventName = "message";
        if (name === "session") {
          try {
            handlers.onSession?.(JSON.parse(data) as Session);
          } catch {
            /* ignore */
          }
        }
      };

      while (!closed) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const parts = buffer.split(/\r?\n/);
        buffer = parts.pop() ?? "";
        for (const line of parts) {
          if (line === "") {
            dispatch();
            continue;
          }
          if (line.startsWith(":")) continue;
          if (line.startsWith("event:")) {
            eventName = line.slice(6).trim();
            continue;
          }
          if (line.startsWith("data:")) {
            dataLines.push(line.slice(5).trimStart());
          }
        }
      }
    } catch (err) {
      if (closed) return;
      handlers.onConnectionError?.(err as Event);
    }

    if (closed) return;
    attempt += 1;
    const delay = Math.min(8000, 500 + attempt * 400);
    retryTimer = setTimeout(() => {
      void connect();
    }, delay);
  };

  void connect();

  return () => {
    closed = true;
    if (retryTimer) clearTimeout(retryTimer);
    abort?.abort();
  };
}
