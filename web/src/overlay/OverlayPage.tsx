import { useEffect } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { useTrackLive } from "../lib/useTrackLive";
import type { TrackLang } from "../lib/types";

function isTrackLang(v: string | null): v is TrackLang {
  return v === "original" || v === "es" || v === "en" || v === "pt";
}

/**
 * OverlayDeStream — transparent Browser Source, 1920×1080 safe area.
 * No app chrome, no picker, no long history.
 */
export function OverlayPage() {
  const { id } = useParams<{ id: string }>();
  const [search] = useSearchParams();
  const langParam = search.get("lang");
  const lang: TrackLang = isTrackLang(langParam) ? langParam : "es";

  const { live, finals } = useTrackLive(id, lang);
  const recentFinals = finals.slice(-2);

  useEffect(() => {
    document.documentElement.classList.add("overlay-mode");
    return () => {
      document.documentElement.classList.remove("overlay-mode");
    };
  }, []);

  return (
    <div className="overlay-root" aria-live="polite">
      <div className="overlay-finals">
        {recentFinals.map((cue) => (
          <p key={cue.id} className="overlay-final">
            {cue.text}
          </p>
        ))}
      </div>
      {live?.text ? <p className="overlay-live">{live.text}</p> : null}
    </div>
  );
}
