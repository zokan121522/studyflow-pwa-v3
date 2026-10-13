# StudyFlow — requisitos del sandbox de servidor (Linux / contenedor)

Nota breve para instalar StudyFlow como servicio en un servidor Linux o
contenedor-sandbox. Todo lo del "camino de instalación estándar" viene ya
incluido: no hay pasos manuales extra.

## 1. Sidecar OpenZen (IA local)

El backend de IA de la PWA es un `opencode serve` en `127.0.0.1:54321`.
En Linux no se arranca a mano: lo levanta systemd.

```bash
# instala opencode (si falta) + unidad systemd + credenciales, y lo arranca
scripts/install-opencode-sidecar-linux.sh

# opciones
scripts/install-opencode-sidecar-linux.sh --dry-run     # imprime, no cambia nada
scripts/install-opencode-sidecar-linux.sh --uninstall   # para y quita el servicio
```

Genera `~/.studyflow/opencode_sidecar.env` (chmod 600) con
`OPENCODE_SERVER_URL`, `OPENCODE_SERVER_USERNAME=studyflow-ce01` y una
password aleatoria, y crea `opencode-sidecar.service` (Restart=always).
Es el hermano Linux de `launcher/install-opencode-sidecar.command` (macOS).

- Comprobar: `systemctl status opencode-sidecar` y `curl -sS http://127.0.0.1:54321`.
- Logs: `journalctl -u opencode-sidecar -n 50`.

## 2. Chrome / Xvfb / x11vnc (login NotebookLM vía VNC)

El login embebido de NotebookLM (`backend/routes/notebooklm_login.py`, modo
VNC) necesita un display virtual, un servidor VNC y un Chrome de escritorio.
La imagen del servidor (`backend/Dockerfile`) ya los instala en su capa de
sistema: `xvfb`, `x11vnc`, `fluxbox`, `xauth`, las librerías de Chromium y el
Chrome de escritorio de Playwright. No hay que añadir nada.

- Solo afecta al login. PDF→MD, audio, infografías y flashcards usan el
  Chromium que Playwright trae aparte.
- En Linux x86_64 el Chrome de Playwright se instala en el build; en arm64
  ese canal no existe, así que se instalan las librerías de Chrome explícitas
  para que el display VNC arranque igualmente.

## 3. Docker Compose

`docker-compose.yml` ya define el servicio `opencode` y `app` lo alcanza por
nombre (`OPENCODE_SERVER_URL=http://opencode:54321`). En el servidor-sandbox
la IA se resuelve con la unidad systemd de arriba; no se reescribe el compose.
