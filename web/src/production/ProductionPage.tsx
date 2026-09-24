import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import {
  exportUrl,
  productionEventsUrl,
  putGlossary,
  startSession,
  stopSession,
} from "../lib/api";
import { PRODUCTION_TOKEN_KEY, TRACK_OPTIONS } from "../lib/labels";
import { subscribeProductionEvents } from "../lib/sse";
import type { ExportFormat, Session, TrackLang } from "../lib/types";

function loadToken(): string {
  try {
    return sessionStorage.getItem(PRODUCTION_TOKEN_KEY) ?? "";
  } catch {
    return "";
  }
}

function saveToken(token: string) {
  try {
    sessionStorage.setItem(PRODUCTION_TOKEN_KEY, token);
  } catch {
    /* ignore */
  }
}

function clearToken() {
  try {
    sessionStorage.removeItem(PRODUCTION_TOKEN_KEY);
  } catch {
    /* ignore */
  }
}

function formatMs(n: number | undefined): string {
  if (n == null || Number.isNaN(n)) return "—";
  return `${Math.round(n)} ms`;
}

function SessionRow({
  session,
  token,
  onUpdated,
}: {
  session: Session;
  token: string;
  onUpdated: (s: Session) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [glossaryText, setGlossaryText] = useState(
    "Nerdearla\nKonex",
  );
  const [glossaryMsg, setGlossaryMsg] = useState<string | null>(null);
  const [actionErr, setActionErr] = useState<string | null>(null);

  const canStart =
    session.status === "idle" ||
    session.status === "stopped" ||
    session.status === "down";
  const canStop =
    session.status === "live" || session.status === "degraded";

  const onStart = async () => {
    setBusy(true);
    setActionErr(null);
    try {
      const next = await startSession(session.id);
      onUpdated(next);
    } catch (err) {
      setActionErr(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const onStop = async () => {
    setBusy(true);
    setActionErr(null);
    try {
      const next = await stopSession(session.id);
      onUpdated(next);
    } catch (err) {
      setActionErr(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const onSaveGlossary = async () => {
    setBusy(true);
    setGlossaryMsg(null);
    try {
      const terms = glossaryText
        .split(/\r?\n/)
        .map((line) => line.trim())
        .filter(Boolean)
        .map((term) => ({ term, doNotTranslate: true }));
      await putGlossary(session.id, { terms });
      setGlossaryMsg(`Guardado · ${terms.length} términos`);
    } catch (err) {
      setGlossaryMsg(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  void token;

  const formats: ExportFormat[] = ["srt", "vtt", "txt"];
  const langs: TrackLang[] = ["original", "es", "en", "pt"];

  return (
    <article className="prod-card">
      <div className="prod-card-head">
        <h2 className="prod-name">{session.name}</h2>
        <span className="lamp" data-status={session.status}>
          {session.status}
        </span>
      </div>

      <div className="prod-metrics">
        <div>
          sourceLang <strong>{session.sourceLang ?? "null"}</strong>
        </div>
        <div>
          reconnects <strong>{session.reconnects}</strong>
        </div>
        <div>
          interim p50 <strong>{formatMs(session.latency?.interimP50Ms)}</strong>
        </div>
        <div>
          interim p95 <strong>{formatMs(session.latency?.interimP95Ms)}</strong>
        </div>
        <div>
          final p50 <strong>{formatMs(session.latency?.finalP50Ms)}</strong>
        </div>
        <div>
          final p95 <strong>{formatMs(session.latency?.finalP95Ms)}</strong>
        </div>
      </div>

      {session.lastError && (
        <p className="prod-error" role="status">
          [{session.lastError.code ?? "error"}] {session.lastError.message}
          {session.lastError.atMs != null
            ? ` · atMs ${session.lastError.atMs}`
            : ""}
        </p>
      )}

      {actionErr && (
        <p className="prod-error" role="alert">
          {actionErr}
        </p>
      )}

      <div className="prod-actions">
        <button
          type="button"
          className="prod-btn"
          disabled={busy || !canStart}
          onClick={() => void onStart()}
        >
          Arrancar
        </button>
        <button
          type="button"
          className="prod-btn"
          data-danger="true"
          disabled={busy || !canStop}
          onClick={() => void onStop()}
        >
          Detener
        </button>
      </div>

      <div className="prod-links">
        <span>Overlay:</span>
        {TRACK_OPTIONS.map((opt) => (
          <Link
            key={opt.lang}
            to={`/sessions/${session.id}/overlay?lang=${opt.lang}`}
            target="_blank"
            rel="noreferrer"
          >
            {opt.label}
          </Link>
        ))}
      </div>

      <div className="prod-links" style={{ marginTop: "0.35rem" }}>
        <span>Export:</span>
        {langs.flatMap((lang) =>
          formats.map((fmt) => (
            <a
              key={`${lang}-${fmt}`}
              href={exportUrl(session.id, lang, fmt)}
              download
            >
              {lang}.{fmt}
            </a>
          )),
        )}
      </div>

      <div className="glossary-box">
        <label htmlFor={`glossary-${session.id}`}>
          GlosarioTécnico (un término por línea)
        </label>
        <textarea
          id={`glossary-${session.id}`}
          value={glossaryText}
          onChange={(e) => setGlossaryText(e.target.value)}
          spellCheck={false}
        />
        <button
          type="button"
          className="prod-btn"
          disabled={busy}
          onClick={() => void onSaveGlossary()}
        >
          Guardar glosario
        </button>
        {glossaryMsg && <p className="prod-hint">{glossaryMsg}</p>}
      </div>
    </article>
  );
}

export function ProductionPage() {
  const [tokenInput, setTokenInput] = useState("");
  const [token, setToken] = useState(() => loadToken());
  const [sessions, setSessions] = useState<Map<string, Session>>(new Map());
  const [authError, setAuthError] = useState<string | null>(null);
  const [connError, setConnError] = useState<string | null>(null);

  const list = useMemo(
    () => [...sessions.values()].sort((a, b) => a.name.localeCompare(b.name)),
    [sessions],
  );

  const upsert = useCallback((s: Session) => {
    setSessions((prev) => {
      const next = new Map(prev);
      next.set(s.id, s);
      return next;
    });
  }, []);

  useEffect(() => {
    if (!token) return;
    setAuthError(null);
    setConnError(null);
    const unsub = subscribeProductionEvents(productionEventsUrl(), token, {
      onSession: upsert,
      onUnauthorized: () => {
        setAuthError("Token rechazado (401). Pegá PRODUCTION_TOKEN de nuevo.");
        clearToken();
        setToken("");
      },
      onConnectionError: () => {
        setConnError("SSE de producción desconectado — reintentando…");
      },
    });
    return unsub;
  }, [token, upsert]);

  const onSubmitToken = (e: FormEvent) => {
    e.preventDefault();
    const t = tokenInput.trim();
    if (!t) return;
    saveToken(t);
    setToken(t);
    setTokenInput("");
    setSessions(new Map());
  };

  const onLogout = () => {
    clearToken();
    setToken("");
    setSessions(new Map());
  };

  if (!token) {
    return (
      <div className="prod-shell">
        <form className="prod-gate" onSubmit={onSubmitToken}>
          <h1>MonitorDeProduccion</h1>
          <p>
            Pegá el <code>PRODUCTION_TOKEN</code> del entorno. Se guarda en{" "}
            <code>sessionStorage</code> y solo se envía como{" "}
            <code>Authorization: Bearer</code> a{" "}
            <code>/api/production/events</code>.
          </p>
          {authError && (
            <p className="prod-error" role="alert">
              {authError}
            </p>
          )}
          <label htmlFor="prod-token">Token</label>
          <input
            id="prod-token"
            type="password"
            autoComplete="off"
            value={tokenInput}
            onChange={(e) => setTokenInput(e.target.value)}
            placeholder="dev-token"
          />
          <button type="submit">Entrar</button>
          <p className="prod-hint">
            Con MSW cualquier token no vacío sirve (salvo <code>wrong</code>).
          </p>
        </form>
      </div>
    );
  }

  return (
    <div className="prod-shell">
      <header className="prod-header">
        <h1 className="prod-title">
          Monitor · <em>producción</em>
        </h1>
        <div style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
          <Link to="/" className="prod-btn" style={{ textDecoration: "none" }}>
            Audiencia
          </Link>
          <button type="button" className="prod-btn" onClick={onLogout}>
            Salir
          </button>
        </div>
      </header>

      {connError && (
        <p className="prod-hint" style={{ padding: "0.5rem 1rem" }} role="status">
          {connError}
        </p>
      )}

      <div className="prod-grid">
        {list.length === 0 ? (
          <p className="prod-hint">Esperando eventos SSE de Sesiones…</p>
        ) : (
          list.map((s) => (
            <SessionRow
              key={s.id}
              session={s}
              token={token}
              onUpdated={upsert}
            />
          ))
        )}
      </div>
    </div>
  );
}
