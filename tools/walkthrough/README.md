# Walkthrough narrado (voz + app)

Genera vídeos-tutorial donde una voz TTS explica y **la app se navega sola**, con
la acción anclada a la palabra exacta de la narración.

## Qué hace

1. **TTS primero** (`edge-tts`, voz `es-ES-AlvaroNeural`) por cada cue del guion.
   Con `boundary="WordBoundary"` obtiene el *timestamp de cada palabra*.
2. Mide cada audio (`ffprobe`) y localiza el segundo exacto de la palabra-ancla.
3. Conduce la app en tiempo real con **Playwright + Chrome** y dispara cada acción
   (clic / escribir / seleccionar) justo en su palabra.
4. Monta con **ffmpeg**: coloca la narración y sincroniza con el vídeo.
5. **Se autolimpia**: borra la sesión de prueba que creó (ver «Reglas»).

## Uso

```bash
# la app debe estar levantada en http://127.0.0.1:8081
python3 tools/walkthrough/render.py tools/walkthrough/storyboards/agenda.json
# salida -> tools/walkthrough/pilot/<id>.mp4
```

## Guion (`storyboards/*.json`)

```jsonc
{
  "id": "agenda-pilot-desktop",          // nombre del mp4 de salida
  "voice": "es-ES-AlvaroNeural",
  "rate": "-4%",
  "viewport": [1280, 720],               // desktop (no móvil)
  "leadin": 1.2, "gap": 0.40, "tail": 2.2, "click_lead": 0.50,
  "cleanup": {                            // opcional: autolimpieza
    "date": "2026-10-03",
    "match": { "title": "Repaso de bases de datos", "notes": "Repasar el tema 5 y hacer los ejercicios." }
  },
  "cues": [
    {
      "say": "…lo que dice la voz…",
      "anchor": "palabra",               // dónde disparar la acción dentro del `say`
      "action": { "kind": "click", "selector": ".ht-btn[data-action=\"add-session\"]" },
      "focus": ".ht-btn[data-action=\"add-session\"]"
    }
  ]
}
```

`kind`: `click` · `fill` (`value`) · `fill_pair` (`selector2`/`value2`) · `select` (`index`).
`pre` (opcional): selector que se pulsa antes de la acción (p. ej. abrir la pestaña «Editar»).

## Reglas

- **Tono**: coloquial, tuteo. **No** decir «Señor» en los vídeos.
- **Formato**: pantalla grande (1280×720), no versión móvil.
- **Autolimpieza**: si el guion crea datos de prueba (una sesión de agenda), el
  pipeline los borra al terminar. **Solo borra lo que ha creado él** (identificado
  por `title` + `notes` + `created_at` posterior al inicio del run); jamás toca
  datos preexistentes.

## Notas técnicas

- Captura nativa de Playwright: requiere su propio ffmpeg. Se resuelve con
  `ln -s /opt/homebrew/bin/ffmpeg ~/Library/Caches/ms-playwright/ffmpeg-1011/ffmpeg-mac`.
- `pilot/` (mp3 de caché, webm y mp4) está en `.gitignore`.
