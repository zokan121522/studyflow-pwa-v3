# Guía de build y despliegue · StudyFlow PWA

> Orden de operaciones para publicar una versión de la PWA sin romper las
> actualizaciones de las apps ya instaladas.
>
> **Estado del documento:** refleja el repo a 2026-10-05.
> La versión anterior daba por hecho que existía `scripts/build-frontend.sh`.
> No existía. Ahora sí.

---

## 0 · La idea en una frase

Dos carpetas, un interruptor:

```
frontend/          fuente. Sin build. Es lo que se edita y se prueba en local.
frontend-dist/     producción. Mismos nombres de fichero, mismo contenido
                   functionally idéntico, pero minificado y sin comentarios.
```

Flask sirve una u otra según `FRONTEND_DIR`. Cambiar de una a otra es cambiar
**una variable de entorno**. No hay dos bases de código que se desincronicen.

---

## 1 · Por qué NO hay hashes en los nombres de fichero

La guía anterior proponía `agenda-glue.a3f9b2c1.js`. Se descartó a propósito.

El Service Worker precachea por **ruta**, no por hash:

```js
// frontend/sw.js
const PRECACHE_ASSETS = [
  '/features/agenda/agenda-glue.js',   // ← ruta literal
];
```

Y `tests/test_sw_asset_version.py::test_every_precached_asset_exists_on_disk`
comprueba que cada ruta de esa lista exista **en disco bajo `frontend/`**.

Si los nombres llevan hash, hay que reescribir a la vez:

1. los `<script src>` de `index.html`
2. la lista `PRECACHE_ASSETS` de `sw.js`
3. la lógica del test que resuelve rutas contra `frontend/`

Tres sitios que deben coincidir al mismo tiempo, o el precache falla **en
silencio** — el precache usa `Promise.allSettled` sobre `cache.add()`, así que
un 404 no tumba el install, solo deja un hueco que nadie nota hasta que alguien
intenta usar esa feature sin conexión.

Ese es exactamente el modo de fallo que ha mordido seis veces: v20, v24, v27,
v29, v30 y v86. Todas las veces, código en el servidor que ninguna PWA
instalada podía cargar.

**El cache-busting lo da `ASSET_CACHE`, que ya funciona.** `sw.js` cambia en
cada release → el navegador byte-compara → dispara `install` → precachea en la
cache nueva → `activate` borra las antiguas → el banner de app.js ofrece
recargar. Probado durante 90 versiones, con un test que falla si alguien edita
un asset precacheado sin subir el número.

Hashes: no. `ASSET_CACHE`: sí.

---

## 2 · Requisitos

```bash
node -v    # v18+ (probado con v26)
npm -v
```

Solo para el build. La app en producción **no** depende de Node: el contenedor
es Python + Flask y sirve ficheros estáticos ya construidos.

---

## 3 · Desarrollo: sin build, a propósito

```bash
# 1) tests primero
python3 -m pytest --ignore=tests/test_md_to_pdf.py -q

# 2) backend en local
./scripts/dev-server.sh          # http://localhost:8081
```

Editas en `frontend/`, recargas, ya está. No hay paso de build en el bucle
diario porque un paso de build slow se acaba esquivando, y un paso de build
esquivado se acabaGB forgetting de ejecutar antes de publicar.

---

## 4 · Publicar

### Paso 1 · Tests en verde

```bash
python3 -m pytest --ignore=tests/test_md_to_pdf.py -q
```

Debe salir `N passed`. **Un rojo aquí para el deploy.** Si falla
`test_asset_cache_version_tracks_a_content_digest`, mira el §6.

### Paso 2 · Build

```bash
./scripts/build-frontend.sh
```

Qué hace:

1. borra `frontend-dist/` si existe
2. copia `frontend/` → `frontend-dist/` (ficheros y estructura idénticos)
3. minifica cada `.js` y `.css` **en su sitio** (Terser / esbuild + cssnano)
4. minifica `index.html` (colapsa atributos, quita comentarios)
5. copia `sw.js`, `manifest.json`, `icons/` y `vendor/` sin tocar — tienen su
   propia semántica y no ganan nada minificados
6. **sube `ASSET_CACHE`** en el `sw.js` copiado y añade el pin del digest
7. imprime un resumen: versión nueva, tamaño antes/después, nº de ficheros

No modifica `frontend/`. El árbol de trabajo queda como estaba, salvo por el
`sw.js` de origen si se le pide sincronizar.

### Paso 3 · Comprobar el dist

```bash
./scripts/verify-frontend.sh      # rutas, precache, integridad, secretos
```

Comprueba que:

- existe un `.js` para cada `<script src>` de `index.html`
- existe un `.css` para cada `<link rel=stylesheet>`
- **todas** las rutas de `PRECACHE_ASSETS` existen en `frontend-dist/`
- ningún `dist/` se ha colado en `frontend-dist/`
- no hay secretos (`sk-`, `Bearer`, `api_key`) en el JS del cliente

### Paso 4 · Desplegar

`docker-compose.yml` monta el repo entero en `/srv`, así que `frontend-dist/`
ya está visible dentro del contenedor sin tocar el Dockerfile. Solo hay que
apuntar `FRONTEND_DIR`:

```yaml
services:
  app:
    environment:
      - FRONTEND_DIR=/srv/frontend-dist   # producción
      # - FRONTEND_DIR=/srv/frontend       # desarrollo
```

```bash
docker compose up -d --build app
```

Detrás del túnel de Cloudflare, todos los clientes alcanzan la URL nueva.

### Paso 5 · Verificar que la actualización llega

Esto es el paso que de verdad importa, y el que siempre se salta:

1. Abre la PWA **ya instalada** (no una ventana normal de Chrome)
2. Debe aparecer el banner **"🔄 Nueva versión disponible"**
3. Al aceptar: recarga y sirve el código nuevo

Si no aparece el banner, el service worker no se ha re-instalado. Suele ser
`ASSET_CACHE` sin subir. Comprobación en DevTools → Application → Service
Workers, o en consola:

```js
fetch('/sw.js').then(r => r.text())
  .then(t => console.log(t.match(/ASSET_CACHE\s*=\s*['"]([^'"]+)/)?.[1]))
```

Ese número debe coincidir con el que imprimió el paso 2.

---

## 5 · Volver a desarrollo

```yaml
- FRONTEND_DIR=/srv/frontend
```

```bash
docker compose up -d app
```

---

## 6 · El paso que nadie recuerda: `sw_asset_pins.json`

`ASSET_CACHE` sube solo. **El pin del digest hay que añadirlo a mano.**

```bash
# El test falla con este mensaje si te lo saltas:
#   ASSET_CACHE was bumped to 'studyflow-assets-v91' but no digest is pinned
#   for it. Re-run with the pin file deleted to record the new baseline.
```

`scripts/build-frontend.sh` lo hace por ti en el `sw.js` del dist. Si has
editado `frontend/sw.js` a mano (y es normal: se bumpea para propagar un fix
que no toca assets), tienes que añadir el pin de esa versión a
`tests/sw_asset_pins.json` o el deploy no pasa los tests.

**No borres el fichero para regenerarlo**, aunque el mensaje del test lo sugiera:
eso tiraría 24 versiones de historial. Añade solo la clave nueva con el digest
de los assets precacheados actuales.

---

## 7 · Sobre la ofuscación

`build-frontend.sh` minifica y quita comentarios. **No ofusca de verdad**, y
es deliberado.

La ofuscación de JavaScript en el cliente no es protección: es una barrera que
se baja con `prettier` y un beautifier en minutos. `javascript-obfuscator` con
arrays de control y string encoding es reversible por quien se tome un minuto
más. Pagar horas de debugging en producción a cambio de esa protección es mal
negocio.

Lo que protege de verdad ya está montado y no depende del build:

- 185 endpoints y 31.049 líneas de Python en el servidor
- los prompts de IA, la lógica de backup/restore, el importador de ICS y la
  automatización de NotebookLM **no salen del servidor**
- cero secretos en el cliente (lo verifica `verify-frontend.sh`)

El material que alguien querría copiar no está en `frontend/`. Está en
`backend/`.

Minificar, limpiar comentarios y no publicar source maps es útil igual: encarece
el trabajo y hace el código más ligero. Eso es todo.

---

## 8 · Checklist mínima antes de publicar

- [ ] `python3 -m pytest --ignore=tests/test_md_to_pdf.py -q` → todo passed
- [ ] Probado en dev con `FRONTEND_DIR=/srv/frontend`
- [ ] `./scripts/build-frontend.sh` sin errores
- [ ] `./scripts/verify-frontend.sh` sin errores
- [ ] `docker compose` con `FRONTEND_DIR=/srv/frontend-dist`
- [ ] **Banner "Nueva versión disponible" visto en una PWA ya instalada**
- [ ] ASSET_CACHE del servidor == el que imprimió el build
- [ ] Modo offline: la app arranca sin red y la agenda abre

---

## 9 · Referencia rápida

| Qué | Comando |
|---|---|
| Tests | `python3 -m pytest --ignore=tests/test_md_to_pdf.py -q` |
| Dev | `./scripts/dev-server.sh` |
| Build | `./scripts/build-frontend.sh` |
| Verificar dist | `./scripts/verify-frontend.sh` |
| Estado del SW en el navegador | ver §4 paso 5 |
| Servir el dist a pelo | `python3 -m http.server 5173 -d frontend-dist` (sin API, solo para ver estáticos) |
