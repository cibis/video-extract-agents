FROM python:3.11-slim AS builder
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
RUN pip install poetry==1.8.3
COPY pyproject.toml poetry.lock* ./
RUN poetry config virtualenvs.in-project true \
    && poetry lock --no-update \
    && poetry install --only=main --no-root

FROM python:3.11-slim AS production
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
RUN groupadd -r appgroup && useradd -r -g appgroup appuser
COPY --from=builder /app/.venv ./.venv
COPY app/ ./app/
ENV PATH="/app/.venv/bin:$PATH"
USER appuser
EXPOSE 8200
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8200/health')"
CMD uvicorn app.main:app --host 0.0.0.0 --port 8200 --log-level ${LOG_LEVEL:-info}
