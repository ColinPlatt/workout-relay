FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# eMudhra/emSign CA compromise (Oct 2026): take the patched ca-certificates
# and a pip whose vendored certifi drops the compromised intermediates.
RUN apt-get update \
    && apt-get install -y --no-install-recommends --only-upgrade ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade "pip>=26.2.2" \
    && pip install --no-cache-dir -r requirements.txt

COPY workout_relay ./workout_relay
RUN adduser --disabled-password --gecos "" appuser \
    && mkdir -p /data \
    && chown -R appuser:appuser /app /data
USER appuser

EXPOSE 8000
CMD ["sh", "-c", "exec uvicorn workout_relay.app:app --host 0.0.0.0 --port \"${PORT:-8000}\" --workers 1 --proxy-headers"]
