FROM python:3.11-slim AS builder
WORKDIR /app
RUN pip install poetry==1.8.3
COPY pyproject.toml poetry.lock* ./
RUN poetry config virtualenvs.in-project true \
    && poetry install --only=main --no-root

FROM python:3.11-slim AS production
WORKDIR /app
RUN groupadd -r appgroup && useradd -r -g appgroup appuser
COPY --from=builder /app/.venv ./.venv
COPY app/ ./app/
ENV PATH="/app/.venv/bin:$PATH"
USER appuser
EXPOSE 8003
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8003/health')"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8003"]
