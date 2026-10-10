# Stats internas de studyflowhub.dev (`/stats`)

Página interna con el **nº de descargas de los zips** (por plataforma) y el
**nº de visitas a la página de descargas**. No es pública: va detrás de
**HTTP Basic Auth**.

## Acceso

| Dato | Valor |
|------|-------|
| URL | `https://studyflowhub.dev/stats` (también `/stats.html`) |
| Usuario | `señor` |
| Contraseña | `studyflow-stats-2026` |
| Cadencia | regenerado cada **5 min** por cron (`*/5`) |

```bash
curl -s -u 'señor:studyflow-stats-2026' https://studyflowhub.dev/stats
# sin credenciales → 401
```

## Cómo funciona

1. **Fuente del tráfico**: nginx dentro del contenedor `studyflow-downloads`
   loguea en `/var/log/nginx/access.log`, que en la imagen oficial está
   enlazado a `/dev/stdout` → sale por `docker logs studyflow-downloads`.
   El volumen `./public` está montado en **ro**, así que NO se puede escribir
   un access_log ahí; se usa `docker logs` como fuente.
2. **`stats.sh`** (en el server: `~/studyflow-downloads/stats.sh`) hace dos cosas:
   - Ing incremental: `docker logs -t` → añade solo las líneas nuevas a
     `~/studyflow-downloads/logs/access.log` (estado en `logs/.last_docker_ts`;
     sobrevive a recrear el contenedor).
   - Genera `~/studyflow-downloads/public/stats.html` (tema oscuro, en español).
3. **Filtros**: visitas = `GET /`, `index.html`, `*.html` con 200, excluyendo
   el healthcheck interno (`127.0.0.1`, UA `Wget`), `/version.json`
   (auto-updater cada 5 s) y la propia `/stats`.
   Descargas = `GET /StudyFlow-{windows,macos,linux}-*.zip` con 200/206.
   La IP que se muestra es la de `X-Forwarded-For` (túnel de Cloudflare).

## Protección (Basic Auth) — instalación / reparación

Archivos en el server: `~/studyflow-downloads/nginx/default.conf` y
`~/studyflow-downloads/nginx/.htpasswd` (mismo contenido que este directorio).

Forma **duradera** (ya aplicada): `docker-compose.yml` monta ambos ficheros:

```yaml
    volumes:
      - ./public:/usr/share/nginx/html:ro
      - ./nginx/default.conf:/etc/nginx/conf.d/default.conf:ro
      - ./nginx/.htpasswd:/etc/nginx/.htpasswd:ro
```

Si el contenedor se recrea sin esos mounts, reaplica a mano:

```bash
bash scripts/stats/install-nginx-stats.sh     # docker cp + nginx -t + reload
# o directamente:
docker cp scripts/stats/nginx-default.conf studyflow-downloads:/etc/nginx/conf.d/default.conf
docker cp scripts/stats/.htpasswd          studyflow-downloads:/etc/nginx/.htpasswd
docker exec studyflow-downloads nginx -t && docker exec studyflow-downloads nginx -s reload
```

⚠️ `.htpasswd` debe quedar con permisos **644** dentro del contenedor
(si queda en 600, el worker de nginx no puede leerlo y `/stats` devuelve 404/500).

Regenerar el `.htpasswd` tras cambiar la contraseña:

```bash
printf 'señor:%s\n' "$(openssl passwd -apr1 'NUEVA_CLAVE')" > .htpasswd
```

## Instalar en otro server ( réplica / supervivencia )

```bash
# 1. código
scp scripts/stats/stats.sh <host>:~/studyflow-downloads/stats.sh
scp scripts/stats/nginx-default.conf <host>:~/studyflow-downloads/nginx/default.conf
scp scripts/stats/.htpasswd <host>:~/studyflow-downloads/nginx/.htpasswd
ssh <host> 'chmod 755 ~/studyflow-downloads/stats.sh; chmod 644 ~/studyflow-downloads/nginx/.htpasswd'
# 2. protección en nginx (ver arriba: mounts en compose o install-nginx-stats.sh)
# 3. cron
ssh <host> "(crontab -l 2>/dev/null | grep -v stats.sh; echo '*/5 * * * * bash \$HOME/studyflow-downloads/stats.sh >> \$HOME/studyflow-downloads/logs/cron.log 2>&1') | crontab -"
# 4. primera generación
ssh <host> 'bash ~/studyflow-downloads/stats.sh'
```

Requisitos en el server: `docker`, `python3` (3.9+; usa `zoneinfo`), `crontab`.

## Nota sobre `publish.sh`

`deploy/downloads/publish.sh` solo lleva un **comentario** recordando que,
tras un deploy/recreate del contenedor, hay que reasegurar los mounts de
nginx y dejar correr el cron. **No cambia su comportamiento.**
