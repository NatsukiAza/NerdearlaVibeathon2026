# Web — VistaAudiencia, OverlayDeStream, MonitorDeProduccion

SPA Vite + React + TypeScript. El backend vive en `backend/` (puerto 8000).

## Arranque

```bash
cd web
npm install
npm run dev
```

Abre `http://localhost:5173`.

## Variables

| Variable | Default | Qué hace |
| --- | --- | --- |
| `VITE_API_BASE` | `http://localhost:8000` | Base del API (OpenAPI) |
| `VITE_USE_MSW` | (off) | Si es `true`, MSW sirve las mismas formas del contrato (2 Sesiones fake, SSE de Track con interim→final, SSE de producción) |

Ejemplo sin API:

```bash
# PowerShell
$env:VITE_USE_MSW="true"; npm run dev

# bash
VITE_USE_MSW=true npm run dev
```

Token de producción en MSW: cualquier string no vacío (salvo `wrong`). En API real: `PRODUCTION_TOKEN` del `.env` (local default `dev-token`).

## Rutas

| Ruta | Superficie |
| --- | --- |
| `/` | Programa (lista de Sesiones) |
| `/sessions/:id` | Track en vivo + picker de idioma + export |
| `/sessions/:id/overlay?lang=` | Browser Source 1920×1080, fondo transparente |
| `/production` | Monitor (token en sessionStorage) |

Overlay y audiencia **no** comparten chrome de layout.
