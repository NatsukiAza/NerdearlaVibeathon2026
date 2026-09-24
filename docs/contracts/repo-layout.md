# Layout del repo

Nadie crea `src/`, `server/` ni `frontend/` en la raíz. Si un path no está acá, no se inventa: se pregunta (el jueves hay humano).

```
backend/                      agente api, exclusivo
  pyproject.toml
  app/
    domain/                 Cue, Sesion, Track, puertos SpeechBackend y Translator
    adapters/fake/          FakeSpeechBackend, FakeTranslator
    adapters/gemini/        Live transcribe + Translator texto + rotate 8 min
    adapters/audio/         fixture (ffmpeg), mic (WS PCM), url http(s)/HLS — NO YouTube
    api/                    FastAPI: rutas de openapi.yaml
    store.py                dict in-memory de Sesiones
web/                          agente web, exclusivo
  package.json
  src/audience/             VistaAudiencia
  src/overlay/              OverlayDeStream
  src/production/           MonitorDeProduccion
  src/lib/                  cliente HTTP/SSE/WS tipado al contrato
deploy/                       agente ops, exclusivo
  docker-compose.yml        servicios `api` (8000) y `web` (5173)
  Dockerfile.api
  Dockerfile.web
fixtures/                     agente ops
  README.md                 cómo bajar el clip de Nerdearla
  (wav/mp3 el 24, no ahora)
docs/
  VIBEATHON.md
  adr/
  contracts/                congelado; no se pisa el 24
CONTEXT.md
```

## Dueños que no se pisan

| Path | Dueño |
| --- | --- |
| `backend/**` | api |
| `web/**` | web |
| `deploy/**`, `fixtures/**`, `README.md`, `LICENSE` | ops |
| `docker-compose.yml`, `.env.example` | ops |
| `docs/**`, `CONTEXT.md` | nadie el 24 |

Contrato de compose: servicio `api` publica `8000`, servicio `web` publica `5173`, `web` recibe `VITE_API_BASE=http://api:8000` solo en build; en dev del jueves, `VITE_API_BASE=http://localhost:8000`.
