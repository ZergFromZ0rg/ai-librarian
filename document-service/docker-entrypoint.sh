#!/bin/sh
set -e

# Run the service as the unprivileged "app" user. The container starts as root
# so it can seed and give the right owner to the bind-mounted volumes, then
# steps down with gosu. If compose already pins a non-root user, just exec.
if [ "$(id -u)" = "0" ]; then
    # Seed an empty models volume from the image (PREBAKE_MODELS=1 builds).
    if [ -d /opt/models ] && [ -z "$(ls -A /models 2>/dev/null)" ]; then
        cp -a /opt/models/. /models/
    fi
    chown -R app:app /app/data /models 2>/dev/null || true
    exec gosu app "$@"
fi

exec "$@"
