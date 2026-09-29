FROM node:20-slim

# Install opencode-ai globally (stable version)
RUN npm install -g opencode-ai@1.18.27

# Copy custom entrypoint that patches SQLite FK constraint
COPY docker/opencode/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Default free-model config (Phase 57). docker-compose bind-mounts
# ./docker/opencode/opencode.jsonc over this path at runtime (read-only);
# COPY is the image fallback so a bare `docker run` still works.
COPY docker/opencode/opencode.jsonc /root/.config/opencode/opencode.jsonc

EXPOSE 54321

ENTRYPOINT ["/entrypoint.sh"]
