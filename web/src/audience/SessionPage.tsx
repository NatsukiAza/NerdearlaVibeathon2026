import { useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { exportUrl, getSession } from "../lib/api";
import { TRACK_OPTIONS } from "../lib/labels";
import { useTrackLive } from "../lib/useTrackLive";
import type { ExportFormat, Session, TrackLang } from "../lib/types";

function formatClock(ms: number): string {
  const totalSec = Math.floor(ms / 1000);
  const m = Math.floor(totalSec / 60);
  const s = totalSec % 60;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

function isTrackLang(v: string | null): v is TrackLang {
  return v === "original" || v === "es" || v === "en" || v === "pt";
}

export function SessionPage() {
  const { id } = useParams<{ id: string }>();
  const [search, setSearch] = useSearchParams();
  const langParam = search.get("lang");
  const lang: TrackLang = isTrackLang(langParam) ? langParam : "original";

  const [meta, setMeta] = useState<Session | null>(null);
  const [metaError, setMetaError] = useState<string | null>(null);

  const { live, finals, trackError } = useTrackLive(id, lang);

  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    getSession(id)
      .then((s) => {
        if (!cancelled) {
          setMeta(s);
          setMetaError(null);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setMetaError(err instanceof Error ? err.message : String(err));
        }
      });
    return () => {
      cancelled = true;
    };
  }, [id]);

  const setLang = (next: TrackLang) => {
    setSearch({ lang: next }, { replace: true });
  };

  if (!id) {
    return (
      <div className="audience-shell">
        <main className="audience-main">
          <p className="audience-error">Sesión no encontrada.</p>
        </main>
      </div>
    );
  }

  const formats: ExportFormat[] = ["srt", "vtt", "txt"];

  return (
    <div className="audience-shell">
      <header className="audience-header">
        <div>
          <h1 className="audience-brand">
            Nerdearla <span>2026</span>
          </h1>
          <p className="audience-tag">Vista audiencia · Track en vivo</p>
        </div>
      </header>
      <main className="audience-main">
        <Link className="session-back" to="/">
          ← Programa
        </Link>

        {metaError && (
          <p className="audience-error" role="alert">
            {metaError}. Si la API está caída, corré con{" "}
            <code>VITE_USE_MSW=true</code>.
          </p>
        )}

        <h2 className="session-title">{meta?.name ?? "Sesión"}</h2>
        <p className="session-sub">
          {meta
            ? `${meta.status} · sourceLang ${meta.sourceLang ?? "—"}`
            : "Cargando…"}
        </p>

        <div className="lang-picker" role="group" aria-label="Idioma del Track">
          {TRACK_OPTIONS.map((opt) => (
            <button
              key={opt.lang}
              type="button"
              className="lang-btn"
              aria-pressed={lang === opt.lang}
              onClick={() => setLang(opt.lang)}
            >
              {opt.label}
            </button>
          ))}
        </div>

        <section className="live-stage" aria-live="polite" aria-atomic="true">
          <p className="live-label">En vivo</p>
          <p
            className="live-line"
            data-empty={live ? "false" : "true"}
          >
            {live?.text || "Esperando Cues…"}
          </p>
        </section>

        {trackError && (
          <p className="audience-error" role="alert">
            Error del Track: {trackError.message}
          </p>
        )}

        <section aria-label="Historial de subtítulos finales">
          <h3 className="program-title">Finales</h3>
          {finals.length === 0 ? (
            <p className="audience-empty">Todavía no hay Cues finales.</p>
          ) : (
            <ul className="finals-list">
              {finals.map((cue) => (
                <li key={cue.id}>
                  <p className="final-item">
                    <span className="final-time">
                      {formatClock(cue.startedAtMs)}
                    </span>
                    {cue.text}
                  </p>
                </li>
              ))}
            </ul>
          )}
        </section>

        <div className="export-bar">
          <span>ExportDeTranscript</span>
          {formats.map((fmt) => (
            <a
              key={fmt}
              className="export-link"
              href={exportUrl(id, lang, fmt)}
              download
            >
              .{fmt}
            </a>
          ))}
        </div>
      </main>
    </div>
  );
}
