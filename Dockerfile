FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN apt-get update \
    && apt-get install -y --no-install-recommends graphviz gosu \
    && rm -rf /var/lib/apt/lists/*
RUN python -m pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/data /app/logs /app/backups /app/models \
    && chown -R appuser:appuser /app

COPY --chown=appuser:appuser . .

COPY docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh

HEALTHCHECK --interval=60s --timeout=10s --start-period=90s --retries=3 \
  CMD python -c "from pathlib import Path; import time; p=Path('/app/data/heartbeat'); raise SystemExit(0 if p.exists() and time.time()-p.stat().st_mtime < 180 else 1)"

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["python", "-m", "app.main"]
