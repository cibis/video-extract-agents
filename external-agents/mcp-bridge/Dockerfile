FROM python:3.11-slim AS builder
WORKDIR /app
RUN pip install poetry==1.8.3
COPY pyproject.toml poetry.lock* ./
RUN poetry config virtualenvs.in-project true \
    && poetry lock --no-update \
    && poetry install --only=main --no-root

FROM python:3.11-slim AS production
WORKDIR /app
RUN groupadd -r appgroup && useradd -r -g appgroup appuser
COPY --from=builder /app/.venv ./.venv
COPY app/ ./app/
ENV PATH="/app/.venv/bin:$PATH"
USER appuser
EXPOSE 8300
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8300/health')"
CMD sh -c 'uvicorn app.main:fastapi_app --host 0.0.0.0 --port 8300 --log-level "$(echo ${LOG_LEVEL:-info} | tr "[:upper:]" "[:lower:]")"'
