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

COPY requirements.txt ./
RUN python -m pip install --index-url https://download.pytorch.org/whl/cpu torch==2.13.0 \
    && python -m pip install -r requirements.txt

COPY *.py ./
COPY fonts ./fonts
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

RUN mkdir -p /app/data /models/huggingface \
    && chmod +x /usr/local/bin/docker-entrypoint.sh \
    && chown -R app:app /app /models /home/app

ENV DATA_DIR=/app/data

EXPOSE 8000

ENTRYPOINT ["docker-entrypoint.sh"]
# One process is intentional: the in-process queues have a single owner.
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
