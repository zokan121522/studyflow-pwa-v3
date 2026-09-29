# Análisis: estructura del contenido de asignaturas en v2 → restore a v3

> Investigación para issue #8. Fuente: backup `~/Downloads/studyflow_backup_2026-09-29.zip`
> (786 MB) + código v2 en `~/orca/workspaces/studyflow-hub/tierra`.

## 1. Veredicto: la estructura de contenido v2 → v3 es 1:1, sin traducción

| Aspecto | v2 | v3 | Acción en restore |
|---|---|---|---|
| Tipo de bloque | `blocks.type='content'` | `blocks.type='content'` | **idéntico** — copiar `content` tal cual |
| Motor de render | `ContentBlocks.renderContentPreview` | el mismo nombre | **idéntico** (verificado por diff) |
| Scripts en `content` | eliminados por DOMPurify | eliminados por `_stripScripts` | mismo comportamiento |
| Bloques interactivos | `App.InteractiveBlocks.renderInteractive` | `App.ContentBlocks.renderInteractive` | mismo nombre, mismo `sandbox="allow-scripts"` |
| Edición del HTML | textarea → `PUT /blocks/:id` | igual (implementado en #11) | ya resuelto |

**Conclusión**: no hace falta "hacerlo igual" porque ya *es* igual. El motor de render
v3 nació como copia del de v2 (ver `content-blocks.js` diff). La única diferencia es
que v3 eliminó las CDN (`marked`, `DOMPurify`) por ser PWA offline y lleva su propio
parser de markdown — irrelevante para bloques `content`, que no pasan por markdown.

## 2. Los 156 bloques `content` tienen tres formas

| Forma | Nº | Contenido | En v3 |
|---|---|---|---|
| Infografía | 96 | `<img src="/api/ai/notebooklm/infographic/NAME.png">` | imagen servida desde `~/.studyflow-app/infographics/` — **requiere copiar los 140 PNG del ZIP** |
| Fragmento | 50 | `<p>`, `<audio>`, etc. (100-150 chars) | shadow DOM, sin cambios |
| Documento completo | 5 | `<!DOCTYPE html>…` (18 KB – 488 KB) | iframe blob, sin cambios |

Scripts inline: solo **2 de 156** los tienen (el de English IFP y otro). En v2 tampoco
funcionaban (DOMPurify los eliminaba). → **no hay pérdida de funcionalidad**.

## 3. El pipeline de generación (v2)

`ai_tasks` (955 filas) es el historial:
- `task_type='generate_content'` (75) — genera el HTML de la asignatura
- `task_type='notebooklm_md_to_html'` (7) — markdown → HTML
- `task_type='generate_htmlia'` (5) — HTML interactivo
- `source_id` apunta al **block** destino (329 tasks), o al topic, o a un slug de curso
- `result_content` guarda el HTML generado (10.8 MB en total)
- 674 `done` / 265 `error` / 16 `cancelled`

En v3 la IA escribe en `ai_tasks.result_content` (igual), y el frontend inyecta el
resultado en el bloque. → **mismo diseño, misma tabla**.

## 4. Los assets: qué hay que migrar

| Asset | En el ZIP | Referenciado por | Destino v3 |
|---|---|---|---|
| 140 infografías PNG | `files/infographics/` | 96 bloques content (los 96 existen) | `~/.studyflow-app/infographics/` |
| 90 audios MP3 | `files/audio/` | 19 bloques content (+ ai_tasks) | `~/.studyflow-app/audio/` |
| 15 PDFs | `files/pdfs/` | 21 bloques pdf (por uuid) | `uploads/pdfs/` + tabla `pdfs` |
| `english-ifp.css` | **NO está en el ZIP** | 1 content (English IFP) | ⚠️ vive en el repo v2 |

**Todos los assets referenciados por los content blocks están presentes en el ZIP**
(96/96 infografías, 19/19 audios, 0 ausentes). El único hueco es `english-ifp.css`
(737 líneas) — está en `studyflow-hub/tierra/frontend/english-ifp.css`.

## 5. Lo que NO está en el backup (pérdidas reales)

- **Notas de las asignaturas**: el HTML generado tiene `<textarea>` que guardaba en
  `localStorage` del navegador (`floatingNotes2`, `notes-nav-english`). Eso vivía en el
  navegador, no en PostgreSQL → **no se puede migrar**. No hay tabla de notas de
  asignatura en v2.
- **Checkboxes de progreso** del contenido: también en `localStorage` (`data-key`).
- 116 topics huérfanos (curso borrado) + 267 blocks huérfanos → se omiten con aviso.

## 6. Requisito para el restore

El orden importa porque los content blocks apuntan a assets por URL absoluta
(`/api/ai/notebooklm/infographic/X.png`):

1. Extraer `files/` a los directorios v3 **antes** de importar filas.
2. Importar courses/topics/blocks.
3. Importar questions → `quiz_questions` (block_id nativo desde #10).
4. Los content blocks quedan apuntando a URLs que ya funcionan.

Los PDFs necesitan paso extra: crear fila en tabla `pdfs` + copiar a `uploads/pdfs/`,
y el bloque `pdf-ref` lleva `url = /api/pdf/<id>`.
