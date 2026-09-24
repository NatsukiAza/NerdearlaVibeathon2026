import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { listSessions } from "../lib/api";
import type { Session } from "../lib/types";
import { SessionList } from "./SessionList";

export function ProgramPage() {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    listSessions()
      .then((data) => {
        if (!cancelled) {
          setSessions(data);
          setError(null);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : String(err));
          setSessions([]);
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="audience-shell">
      <header className="audience-header">
        <div>
          <h1 className="audience-brand">
            Nerdearla <span>2026</span>
          </h1>
          <p className="audience-tag">Subtítulos en vivo · elegí Sesión e idioma</p>
        </div>
        <Link to="/production" className="session-back" style={{ margin: 0 }}>
          Producción
        </Link>
      </header>
      <main className="audience-main">
        <h2 className="program-title">Programa</h2>
        <SessionList sessions={sessions} loading={loading} error={error} />
      </main>
    </div>
  );
}
