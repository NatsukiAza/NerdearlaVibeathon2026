import { Link } from "react-router-dom";
import type { Session } from "../lib/types";

const STATUS_LABEL: Record<Session["status"], string> = {
  idle: "en espera",
  live: "en vivo",
  degraded: "degradada",
  down: "caída",
  stopped: "detenida",
};

function slotLabel(index: number): string {
  const hour = 10 + index;
  return `${String(hour).padStart(2, "0")}:00`;
}

export function SessionList({
  sessions,
  loading,
  error,
}: {
  sessions: Session[];
  loading: boolean;
  error: string | null;
}) {
  if (loading) {
    return <p className="audience-empty">Cargando el programa…</p>;
  }

  if (error) {
    return (
      <div className="audience-error" role="alert">
        <p>No se pudo hablar con la API ({error}).</p>
        <p>
          Si el backend todavía no está arriba, el operador puede arrancar la
          web con <code>VITE_USE_MSW=true</code> para demoear con mocks.
        </p>
      </div>
    );
  }

  if (sessions.length === 0) {
    return (
      <div className="audience-empty">
        <p>No hay Sesiones en el programa todavía.</p>
        <p>
          Si la API está caída, corré con <code>VITE_USE_MSW=true</code>.
        </p>
      </div>
    );
  }

  return (
    <ul className="program-grid">
      {sessions.map((s, i) => (
        <li key={s.id}>
          <Link className="program-row" to={`/sessions/${s.id}`}>
            <span className="program-slot">{slotLabel(i)}</span>
            <div>
              <h2 className="program-name">{s.name}</h2>
              <p className="program-meta">
                {s.sourceLang ? `Audio · ${s.sourceLang}` : "Audio · pendiente"}
                {s.source.kind === "fixture" ? " · fixture" : ` · ${s.source.kind}`}
              </p>
            </div>
            <span className="program-status" data-status={s.status}>
              {STATUS_LABEL[s.status]}
            </span>
          </Link>
        </li>
      ))}
    </ul>
  );
}
