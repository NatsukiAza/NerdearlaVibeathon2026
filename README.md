# Transcripción simultánea para conferencias

Open-source live captions for conferences. One audio source in, one transcription, subtitles in Spanish, English and Portuguese — for the audience, for an OBS/vMix overlay, and for production. MIT licensed. Runs with a Gemini API key, or with a fake backend if you don't have one.

Sistema open source (MIT) de subtítulos en vivo. Una charla entra por una fuente de audio, el sistema la transcribe **una vez** y traduce el texto a varios idiomas. La audiencia elige sesión e idioma en el navegador; producción pega los subtítulos en OBS o vMix, vigila el estado de cada sesión y exporta la transcripción.

```
audio → Sesión → transcripción (Track original)
                 + glosario
                      → traducción → Tracks es / en / pt
                                      → vista de audiencia
                                      → overlay para OBS / vMix
                                      → export SRT / VTT / TXT
                 estado y latencia → monitor de producción
```

Una conferencia levanta **un proceso**. Cada sesión es una tarea async dentro de ese proceso, no un contenedor por escenario. Probado con **10 sesiones en paralelo** en el mismo proceso.

## Probarlo en cinco minutos (sin API key)

Hace falta Python 3.11+, Node 20+ y ffmpeg. Sin `GEMINI_API_KEY` el sistema arranca igual: transcribe con un backend de demostración (texto fijo, no el audio) y traduce prefijando el idioma. Alcanza para ver las dos sesiones, el cambio de idioma, el overlay y el export.

```bash
cp .env.example .env
```

En `.env`, `PRODUCTION_TOKEN=dev-token` ya sirve para local. Dejá `GEMINI_API_KEY` vacío.

Terminal 1 — API:

```bash
cd backend
pip install -e .
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Terminal 2 — web:

```bash
cd web
npm ci
npm run dev -- --host 0.0.0.0 --port 5173
```

Crear y arrancar **dos sesiones** sobre el audio de prueba que viene en el repo (`fixtures/silence-demo.wav`, ~20 s de silencio; el `path` es relativo a `fixtures/`, sin el directorio):

```bash
curl -s -X POST http://localhost:8000/api/sessions \
  -H 'Content-Type: application/json' \
  -d '{"name":"Charla ES","source":{"kind":"fixture","path":"silence-demo.wav"}}'

curl -s -X POST http://localhost:8000/api/sessions \
  -H 'Content-Type: application/json' \
  -d '{"name":"Talk EN","source":{"kind":"fixture","path":"silence-demo.wav"}}'
```

Con el `id` que devuelve cada una:

```bash
curl -s -X POST http://localhost:8000/api/sessions/ID/start
```

En PowerShell, el equivalente del alta:

```powershell
Invoke-RestMethod -Method Post http://localhost:8000/api/sessions -ContentType 'application/json' `
  -Body '{"name":"Charla ES","source":{"kind":"fixture","path":"silence-demo.wav"}}'
```

Abrí:

| URL | Qué es |
| --- | --- |
| http://localhost:5173 | Vista de audiencia. Elige sesión e idioma. **No** arranca ni detiene sesiones. |
| http://localhost:5173/sessions/{id} | Subtítulos en vivo: Original, Español, English, Português. El renglón de abajo se reemplaza; los finales quedan arriba. |
| http://localhost:5173/sessions/{id}/overlay?lang=es | Overlay para OBS/vMix. Fondo transparente, sin chrome. |
| http://localhost:5173/production | Monitor. Token: `dev-token`. Ahí se arranca, se detiene, se edita el glosario y se descargan los exports. |

El backend de demostración usa el **nombre** de la sesión para el idioma: si contiene `en` (como `Talk EN`) el original es inglés y el Track English es una copia; si no, el original es español. Así se ve la diferencia entre alias y traducción. El texto no sale del audio: es un guion fijo, a propósito.

## Con audio real (Gemini)

1. Creá una API key en [Google AI Studio](https://aistudio.google.com/apikey) y pegala en `.env` como `GEMINI_API_KEY`. No hace falta tocar `SPEECH_BACKEND`: con key usa Gemini, sin key usa el backend de demostración.
2. El repo **no** incluye audio de YouTube. Bajá un extracto de una charla de Nerdearla y convertilo a PCM 16 kHz mono con los comandos de [`fixtures/README.md`](fixtures/README.md). El resultado queda en `fixtures/nerdearla-demo.wav` y no se commitea.
3. Creá la sesión con `"path": "nerdearla-demo.wav"` y arrancala igual que arriba.

La transcripción es Gemini Live (`gemini-3.5-transcribe-live`), una conexión por sesión. La traducción es texto (`gemini-3.5-flash`), no una segunda pasada de audio. La key vive solo en el servidor; el navegador no la ve.

| `GEMINI_API_KEY` | `SPEECH_BACKEND` | Qué corre |
| --- | --- | --- |
| vacía | omitido | Demostración. No falla el arranque. |
| presente | omitido | Gemini |
| cualquiera | `fake` | Demostración, aunque haya key |
| vacía | `gemini` | Arranca en demostración y el monitor marca cada sesión `degraded` (`missing_gemini_api_key`) |

## Qué se puede hacer

**Dos sesiones a la vez.** Repetí el POST y el start. Las dos quedan en vivo en el mismo proceso; el monitor las muestra juntas.

**Escala.** Unas 10 sesiones son 10 tareas en el mismo proceso: se probó con 10 en paralelo y las 10 siguieron en vivo, transcribiendo y traduciendo, sin Redis, Postgres ni un bus. Para 30 o más escenarios, se levantan más réplicas del proceso; no hace falta cambiar la arquitectura.

**Idiomas.** Cada sesión tiene Tracks `original`, `es`, `en` y `pt`. Si el idioma de destino es el del audio, ese Track es un alias y no se llama al modelo.

**Glosario.** [`glossary/nerdearla.json`](glossary/nerdearla.json) trae `Nerdearla`, `Konex`, `Vibeathon` y `open source` marcados para no traducir. Se inyecta en la transcripción y en la traducción. Desde el monitor se agregan términos por sesión.

**Export.** SRT, VTT y TXT de los subtítulos **finales** de un Track, desde la audiencia o el monitor. Al detener la sesión también se escriben en `backend/var/exports/{sessionId}/`. Los tiempos son milisegundos de audio desde el arranque, no la hora de la pared.

**OBS / vMix.** Browser Source (OBS) o Web Browser (vMix):

1. URL `http://localhost:5173/sessions/{id}/overlay?lang=es`
2. Tamaño **1920×1080**
3. En OBS, CSS custom para fondo transparente: `body { background-color: rgba(0,0,0,0); }`
4. Desactivar *Shutdown source when not visible*

Detalle en [`deploy/obs-browser-source.md`](deploy/obs-browser-source.md). No hay archivo de escena OBS: el formato cambia entre versiones.

**Otras fuentes.** Además del archivo, una sesión puede tomar micrófono (`kind: mic`, PCM por WebSocket) o una URL http(s)/HLS (`kind: url`). YouTube no está soportado dentro del proceso: el audio se baja afuera y entra como archivo.

## Docker Compose

Desde la raíz, con un `.env` que tenga `PRODUCTION_TOKEN` (no hay default en Compose):

```bash
docker compose --env-file .env -f deploy/docker-compose.yml up --build
```

API en `http://localhost:8000`, web en `http://localhost:5173`. El `--env-file .env` hace falta porque Compose interpola `${PRODUCTION_TOKEN}` desde su propio directorio (`deploy/`), no desde el `env_file` del servicio.

La web se construye con `VITE_API_BASE=http://localhost:8000`: el navegador del host habla con el puerto publicado. No uses `http://api:8000`; ese nombre solo existe dentro de la red de Compose.

## Tests

```bash
cd backend
pip install -e ".[dev]"
python -m pytest -q
```

No usan red ni la API key.

## Licencia

[MIT](LICENSE). Copyright 2026 Nerdearla Vibeathon contributors.
