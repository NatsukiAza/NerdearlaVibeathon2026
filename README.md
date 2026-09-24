# Transcripción simultánea para conferencias

Sistema **open source** (MIT) que toma el audio en vivo de una charla y produce subtítulos en tiempo real. La audiencia elige **Sesión** e idioma en la **VistaAudiencia**; producción quema el **OverlayDeStream** en OBS/vMix, vigila el **MonitorDeProduccion** y descarga el **ExportDeTranscript**. Cada Sesión produce un **Track** original y Tracks `es` / `en` / `pt` de **Cues**. Pensado para Nerdearla y para que otra conferencia lo despliegue con Docker Compose + una API key de Gemini (o FakeSpeechBackend sin key).

## Requisitos

- Python 3.12+ (local) o Docker
- Node 22+ (local) o Docker
- ffmpeg (API / fixtures)
- Opcional: `GEMINI_API_KEY` para Gemini Live; sin ella corre FakeSpeechBackend

Copiá las variables de ejemplo:

```bash
cp .env.example .env
# Editá .env: PRODUCTION_TOKEN (obligatorio en compose) y, si tenés, GEMINI_API_KEY
```

## Levantar en local

### Backend (API)

```bash
cd backend
pip install -e .
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### Web (SPA)

```bash
cd web
npm ci   # o npm install
# El browser llama al API publicado en el host:
export VITE_API_BASE=http://localhost:8000   # PowerShell: $env:VITE_API_BASE="http://localhost:8000"
npm run dev -- --host 0.0.0.0 --port 5173
```

- VistaAudiencia: `http://localhost:5173`
- MonitorDeProduccion: `http://localhost:5173/production` (token = `PRODUCTION_TOKEN`)
- OverlayDeStream: `http://localhost:5173/sessions/{id}/overlay?lang=es`

## Docker Compose

Desde la **raíz del repo** (hace falta un `.env` con al menos `PRODUCTION_TOKEN`; no hay default silencioso en compose):

```bash
docker compose -f deploy/docker-compose.yml up
```

- API: `http://localhost:8000`
- Web: `http://localhost:5173`

**Importante — `VITE_API_BASE`:** el valor embebido en el build debe ser `http://localhost:8000` porque la SPA corre en el **browser del host**. El nombre DNS Docker `api` solo resuelve *dentro* de la red de Compose; el browser no lo ve. No uses `http://api:8000` salvo que un proxy en el contenedor `web` reenvíe al API (este stack no lo hace).

Imágenes: `deploy/Dockerfile.api` (Python 3.12 + ffmpeg + uvicorn) y `deploy/Dockerfile.web` (Node 22, build Vite, `serve` en 5173).

Guía corta OBS: [`deploy/obs-browser-source.md`](deploy/obs-browser-source.md).

## GEMINI_API_KEY y SpeechBackend

| Situación | Resultado |
| --- | --- |
| Sin `GEMINI_API_KEY` y sin `SPEECH_BACKEND` | FakeSpeechBackend + FakeTranslator |
| Con key y sin `SPEECH_BACKEND` | Gemini |
| `SPEECH_BACKEND=fake` | Fake aunque haya key |
| `SPEECH_BACKEND=gemini` con key | Gemini |
| `SPEECH_BACKEND=gemini` sin key | Arranca en fake; Monitor marca Sesiones `degraded` (`missing_gemini_api_key`) |

En `.env.example`, `SPEECH_BACKEND` está comentado para que aplique la resolución por defecto. Dejá `GEMINI_API_KEY` vacío hasta tener key. `PRODUCTION_TOKEN=dev-token` es **solo para local**.

## Dos Sesiones en simultáneo

1. Creá dos Sesiones con `POST` (API) apuntando a fixtures, p. ej. `fixtures/silence-demo.wav` o `fixtures/nerdearla-demo.wav` (ver [`fixtures/README.md`](fixtures/README.md)).
2. Arrancá ambas (`start`).
3. Abrí VistaAudiencia / Overlay con cada `{id}`; en el Monitor deberían aparecer las dos.

El proceso es **un solo FastAPI**: cada Sesión es una `asyncio.Task` (Worker), no un contenedor por escenario.

## Cómo escalar

- ~10 escenarios: más Tasks en el **mismo** proceso.
- 30+: más **réplicas** del proceso después; **no** hace falta Redis, Postgres ni un bus de mensajes para el MVP.

## Idiomas

Tracks por Sesión: `original`, `es`, `en`, `pt`. El selector muestra Original / Español / English / Português. Si el destino coincide con el idioma original, ese Track es un alias (no se traduce).

Para sumar otro idioma el día del evento: agregalo a la config de destinos + un label en la UI. Hoy el enum de path del contrato está congelado a `original|es|en|pt`; ampliarlo implica un cambio de contrato, no solo un deploy.

## GlosarioTécnico

Fixture de conferencia: [`glossary/nerdearla.json`](glossary/nerdearla.json). Se inyecta al SpeechBackend (`custom_vocabulary`) y al Translator (no traducir nombres canónicos). Producción puede editar el glosario por Sesión desde el Monitor.

## ExportDeTranscript

Al cerrar (o desde VistaAudiencia / Monitor): descargá **SRT**, **VTT** y **TXT** a partir de los Cues **finales** de un Track.

## OBS y vMix (OverlayDeStream)

1. OBS → **Browser Source** (vMix → **Web Browser** input).
2. URL: `http://localhost:5173/sessions/{id}/overlay?lang=es` (cambiá `{id}` y `lang`).
3. Tamaño: **1920 × 1080**.
4. Fondo transparente: en OBS, Custom CSS:

   ```css
   body { background-color: rgba(0,0,0,0); }
   ```

5. Desactivá **Shutdown source when not visible**.

No hay JSON de escena OBS en el repo (el schema de scene collections varía entre versiones y no está verificado). Configurá el Browser Source con estos pasos. Detalle corto: [`deploy/obs-browser-source.md`](deploy/obs-browser-source.md).

## MonitorDeProduccion

Abrí `http://localhost:5173/production` y autenticá con el `PRODUCTION_TOKEN` del `.env`. Ahí ves estado (`live` / `degraded` / `down`), latencia, errores, start/stop, glosario, links a overlay y export.

## Licencia

[MIT](LICENSE) — Copyright 2026 Nerdearla Vibeathon contributors.

## Checklist demo (jurado)

Sin afirmar que el video de YouTube ya está grabado; checklist operativo:

- [ ] `.env` con `PRODUCTION_TOKEN`; opcional `GEMINI_API_KEY` para Gemini real
- [ ] Fixture: `silence-demo.wav` para smoke; para el demo del jurado, bajar `nerdearla-demo.wav` ([`fixtures/README.md`](fixtures/README.md)) — el API **no** habla con YouTube
- [ ] Dos Sesiones en paralelo (dos POST + start)
- [ ] Cambio de idioma en VistaAudiencia / Overlay
- [ ] OverlayDeStream en OBS o vMix (1920×1080, transparente)
- [ ] MonitorDeProduccion con token
- [ ] Export SRT / VTT / TXT de un Track
