#!/usr/bin/env bash
# Instala/actualiza la protección Basic Auth de /stats en el contenedor.
# Uso:  bash install-nginx-stats.sh            (aplica y recarga nginx)
#       DRY_RUN=1 bash install-nginx-stats.sh  (solo nginx -t)
# Funciona también si el contenedor se recreó (docker cp + reload).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONTAINER="${STATS_CONTAINER:-studyflow-downloads}"

docker cp "$HERE/nginx-default.conf" "$CONTAINER":/etc/nginx/conf.d/default.conf
docker cp "$HERE/.htpasswd"          "$CONTAINER":/etc/nginx/.htpasswd

docker exec "$CONTAINER" nginx -t
if [ -z "${DRY_RUN:-}" ]; then
  docker exec "$CONTAINER" nginx -s reload
  echo "[ok] nginx recargado — /stats protegido con Basic Auth."
fi

# Regenera el .htpasswd local (si se cambia la contraseña):
#   printf 'señor:%s\n' "$(openssl passwd -apr1 'NUEVA_CLAVE')" > .htpasswd
