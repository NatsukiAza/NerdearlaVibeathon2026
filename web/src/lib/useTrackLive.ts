import { useEffect, useRef, useState } from "react";
import { trackLiveUrl } from "./api";
import { subscribeTrackLive } from "./sse";
import type { Cue, Session, TrackErrorEvent, TrackLang } from "./types";

export interface TrackLiveState {
  live: Cue | null;
  finals: Cue[];
  session: Session | null;
  trackError: TrackErrorEvent | null;
  connected: boolean;
}

/**
 * Subscribe to a Track SSE. Interim cues replace the live line by utteranceId;
 * finals append to history and clear the live line when utteranceId matches.
 */
export function useTrackLive(
  sessionId: string | undefined,
  lang: TrackLang,
): TrackLiveState {
  const [live, setLive] = useState<Cue | null>(null);
  const [finals, setFinals] = useState<Cue[]>([]);
  const [session, setSession] = useState<Session | null>(null);
  const [trackError, setTrackError] = useState<TrackErrorEvent | null>(null);
  const [connected, setConnected] = useState(false);
  const lastIdRef = useRef<string | null>(null);

  useEffect(() => {
    if (!sessionId) return;
    setLive(null);
    setFinals([]);
    setTrackError(null);
    setConnected(false);
    lastIdRef.current = null;

    const url = trackLiveUrl(sessionId, lang);
    const unsub = subscribeTrackLive(
      url,
      {
        onCue: (cue) => {
          setConnected(true);
          lastIdRef.current = cue.id;
          if (cue.kind === "interim") {
            setLive((prev) => {
              if (prev && prev.utteranceId !== cue.utteranceId) {
                // new utterance — drop previous interim
              }
              return cue;
            });
          } else {
            setFinals((prev) => {
              if (prev.some((f) => f.id === cue.id)) return prev;
              return [...prev, cue];
            });
            setLive((prev) =>
              prev && prev.utteranceId === cue.utteranceId ? null : prev,
            );
          }
        },
        onStatus: (s) => {
          setConnected(true);
          setSession(s);
        },
        onError: (e) => setTrackError(e),
        onConnectionError: () => setConnected(false),
      },
      { lastEventId: lastIdRef.current },
    );

    return unsub;
  }, [sessionId, lang]);

  return { live, finals, session, trackError, connected };
}
