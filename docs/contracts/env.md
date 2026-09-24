# Variables de entorno

Archivo runtime: `.env` en la raíz (lo crea ops el 24). Nunca commitear secretos.

| Nombre | Obligatorio | Default | Quién lo lee |
| --- | --- | --- | --- |
| `GEMINI_API_KEY` | no | vacío | `backend` |
| `SPEECH_BACKEND` | no | `gemini` si hay key, si no `fake` | `backend` |
| `PRODUCTION_TOKEN` | sí en compose | `dev-token` solo en local | `backend`, header del Monitor |
| `CORS_ORIGIN` | no | `http://localhost:5173` | `backend` |
| `API_HOST` | no | `0.0.0.0` | `backend` |
| `API_PORT` | no | `8000` | `backend`, compose |
| `WEB_ORIGIN` | no | `http://localhost:5173` | `web` (documental) |
| `VITE_API_BASE` | no | `http://localhost:8000` | `web` build |

## Resolución de SpeechBackend

1. Si `SPEECH_BACKEND=fake` → FakeSpeechBackend + FakeTranslator, aunque haya key.
2. Si `SPEECH_BACKEND=gemini` y hay `GEMINI_API_KEY` → Gemini.
3. Si `SPEECH_BACKEND` omitido y hay key → Gemini.
4. Si `SPEECH_BACKEND` omitido y no hay key → fake.
5. Si `SPEECH_BACKEND=gemini` y no hay key → el proceso **arranca en fake** y el Monitor marca cada Sesión `degraded` con error `missing_gemini_api_key`. No crash.

El video demo del viernes corre en (2). Los tests y las primeras horas del jueves corren en (4).

## Modelos Gemini (nombres congelados)

- Speech: `gemini-3.5-transcribe-live`
- Translator: `gemini-3.5-flash` (texto). Si el nombre no existe el 24, un único cambio en `app/adapters/gemini/models.py` — no espalmar strings en el resto del código.
