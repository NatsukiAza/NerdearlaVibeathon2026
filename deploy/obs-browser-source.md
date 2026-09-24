# Guía OBS / vMix (detalle completo en el README raíz)

Pasos rápidos — OverlayDeStream:

1. OBS → Sources → Browser Source (o vMix → Web Browser input).
2. URL: `http://localhost:5173/sessions/{id}/overlay?lang=es`
3. Width × Height: **1920 × 1080**
4. OBS: Custom CSS → `body { background-color: rgba(0,0,0,0); }` y activar Custom CSS / transparent.
5. Desactivar **Shutdown source when not visible**.
6. MonitorDeProduccion: `http://localhost:5173/production` (header/token = `PRODUCTION_TOKEN`).

No incluimos un JSON de escena OBS: el schema de scene collections cambia entre versiones y no está verificado aquí. Configurá el Browser Source a mano con los pasos de arriba (o del README raíz).
