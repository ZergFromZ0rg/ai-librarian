# Build context is the repo root (see docker-compose.yml) so this stage can
# also pull in ui/ -- the built static bundle is copied into the final image
# below, and document-service serves it directly. No separate nginx/UI
# container: nginx here was only ever serving a prebuilt SPA bundle and
# forwarding one header, both of which FastAPI does itself.
FROM node:22-alpine AS ui-build
WORKDIR /ui
COPY ui/package.json ui/package-lock.json ./
RUN npm ci
COPY ui/ ./
# document-service serves this build itself, from the same origin as the API
# -- no /api prefix needed (contrast `npm run dev`'s vite.config.js proxy,
# which does still route through /api to a separate standalone backend).
ENV VITE_API_BASE=""
RUN npm run build

FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/models/huggingface \
    HOME=/home/app

# gosu lets the entrypoint drop from root to the app user after fixing the
# ownership of bind-mounted volumes (see docker-entrypoint.sh).
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends gosu; \
    rm -rf /var/lib/apt/lists/*; \
    groupadd --gid 10001 app; \
    useradd --uid 10001 --gid 10001 --create-home --home-dir /home/app app

WORKDIR /app

COPY document-service/requirements.txt ./
RUN python -m pip install --index-url https://download.pytorch.org/whl/cpu torch==2.13.0 \
    && python -m pip install -r requirements.txt

COPY document-service/*.py ./
COPY document-service/fonts ./fonts
COPY document-service/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
COPY --from=ui-build /ui/dist ./static

# PREBAKE_MODELS=1 bakes the embedding and reranker models into the image at
# /opt/models; the entrypoint seeds an empty models volume from there on first
# start, so a fully air-gapped host can complete its first search.
ARG PREBAKE_MODELS=0
RUN if [ "$PREBAKE_MODELS" = "1" ]; then \
        HF_HOME=/opt/models/huggingface python warm_models.py; \
    fi

RUN mkdir -p /app/data /models/huggingface \
    && chmod +x /usr/local/bin/docker-entrypoint.sh \
    && chown -R app:app /app /models /home/app \
    && if [ -d /opt/models ]; then chown -R app:app /opt/models; fi

ENV DATA_DIR=/app/data

EXPOSE 8000

ENTRYPOINT ["docker-entrypoint.sh"]
# One process is intentional: the in-process queues have a single owner.
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
