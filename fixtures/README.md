# Fixtures de audio

El proceso **api nunca llama a YouTube**. El clip se baja a mano (ops o deployer) y la Sesión usa `source.kind = fixture` apuntando a un `.wav` local.

## Stand-in local (ya en el repo)

`silence-demo.wav` — ~20 s de silencio PCM 16-bit LE, 16 kHz, mono. Sirve para que FakeSpeechBackend tenga un archivo antes de bajar el clip real. **Solo es un stand-in de pacing**; el demo del jurado necesita `nerdearla-demo.wav`.

## Clip de demo del jurado: `nerdearla-demo.wav`

Elegí cualquier charla de ediciones anteriores de Nerdearla en YouTube. Extraé 3–8 minutos a PCM wav 16-bit little-endian, 16 kHz, mono.

Requisitos: [`yt-dlp`](https://github.com/yt-dlp/yt-dlp) y [`ffmpeg`](https://ffmpeg.org/).

Desde la raíz del repo (PowerShell / bash):

```bash
# 1) Bajar audio de una charla de Nerdearla (reemplazá VIDEO_URL)
yt-dlp -x --audio-format wav -o fixtures/_raw.%(ext)s "VIDEO_URL"

# 2) Normalizar a PCM s16le / 16 kHz / mono y recortar a 3–8 min (ejemplo: 5 min desde el minuto 1)
ffmpeg -y -i fixtures/_raw.wav \
  -ss 00:01:00 -t 00:05:00 \
  -ac 1 -ar 16000 -sample_fmt s16 \
  fixtures/nerdearla-demo.wav

# 3) Borrar el intermediario (no commitear descargas de YouTube)
rm fixtures/_raw.wav
```

En PowerShell el paso 3 es `Remove-Item fixtures\_raw.wav`.

**No commitees** `nerdearla-demo.wav` ni ningún archivo bajado de YouTube. Solo documentá el comando; el wav queda local para el demo.

## Uso en Sesiones

Al crear una Sesión, apuntá la FuenteDeAudio al path del fixture, por ejemplo:

- Fake / smoke: `fixtures/silence-demo.wav`
- Demo jurado: `fixtures/nerdearla-demo.wav`

Para dos Sesiones en paralelo, podés reutilizar el mismo wav o crear un segundo extracto (`fixtures/nerdearla-demo-b.wav`) con los mismos comandos.
