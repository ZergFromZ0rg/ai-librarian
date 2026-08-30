#!/bin/sh
set -e

# Run the service as the unprivileged "app" user. The container starts as root
# so it can give the bind-mounted data and model directories the right owner;
# it then steps down with gosu. If compose already pins a non-root user, just
# exec the command as-is.
if [ "$(id -u)" = "0" ]; then
    chown -R app:app /app/data /models 2>/dev/null || true
    exec gosu app "$@"
fi

exec "$@"
