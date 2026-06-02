FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/storage /app/logs \
    && chown -R appuser:appuser /app

COPY --chown=appuser:appuser . .

USER appuser

HEALTHCHECK --interval=60s --timeout=10s --start-period=90s --retries=3 \
  CMD python -c "from pathlib import Path; import time; p=Path('/app/storage/heartbeat'); raise SystemExit(0 if p.exists() and time.time()-p.stat().st_mtime < 180 else 1)"

CMD ["python", "-m", "app.main"]
