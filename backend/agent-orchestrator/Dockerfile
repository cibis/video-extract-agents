# Build stage
# Build context must be the repository root:
#   docker build -f backend/agent-orchestrator/Dockerfile -t ... .
FROM python:3.11-slim AS builder
WORKDIR /app
COPY backend/agent-orchestrator/requirements.txt ./
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
COPY backend/agent-orchestrator/app/ ./app/
COPY scripts/init_db.py ./init_db.py
ENV PATH="/app/.venv/bin:$PATH"
ENV HOME="/home/appuser"
USER appuser
EXPOSE 8001
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8001/health')"
CMD uvicorn app.main:app --host 0.0.0.0 --port 8001 --log-level ${LOG_LEVEL:-info}
