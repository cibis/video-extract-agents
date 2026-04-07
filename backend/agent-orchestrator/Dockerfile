# Build stage
FROM python:3.11-slim AS builder
WORKDIR /app
COPY requirements.txt ./
RUN python -m venv .venv \
    && .venv/bin/pip install --no-cache-dir setuptools \
    && .venv/bin/pip install --no-cache-dir -r requirements.txt

# Production stage
FROM python:3.11-slim AS production
WORKDIR /app
RUN groupadd -r appgroup && useradd -r -g appgroup -m -d /home/appuser appuser \
    && mkdir -p /home/appuser/.local/share/app \
    && chown -R appuser:appgroup /home/appuser
COPY --from=builder /app/.venv ./.venv
COPY app/ ./app/
ENV PATH="/app/.venv/bin:$PATH"
ENV HOME="/home/appuser"
USER appuser
EXPOSE 8001
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8001/health')"
CMD uvicorn app.main:app --host 0.0.0.0 --port 8001 --log-level ${LOG_LEVEL:-info}
