FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# System deps kept minimal; add libs (libpq, etc.) as workers require them.
# `gosu` is used by docker-entrypoint.sh to drop from root → app after
# fixing the volume mount permissions.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       build-essential \
       libpq-dev \
       curl \
       gosu \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY app/ ./app/
COPY scripts/ ./scripts/
COPY tests/ ./tests/
COPY alembic.ini ./
COPY migrations/ ./migrations/
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# Non-root user for the app process (spec §4.4: app code runs non-root).
# Container starts as root so the entrypoint can chown the mounted state
# volume, then gosu drops to this user before exec'ing the CMD.
RUN useradd --system --create-home --shell /bin/bash app \
    && chown -R app:app /app

EXPOSE 8000
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
