FROM python:3.11-slim AS builder
WORKDIR /app
# git is needed to install the ultralytics CLIP fork (required by YOLO-World set_classes)
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libgl1 git && rm -rf /var/lib/apt/lists/*
RUN pip install poetry==1.8.3
COPY pyproject.toml poetry.lock* ./
RUN poetry config virtualenvs.in-project true \
    && poetry lock --no-update \
    && poetry install --only=main --no-root \
    && .venv/bin/pip install --no-cache-dir "jsonpath-ng>=1.6" "openai-whisper>=20231117" \
    && .venv/bin/pip install --no-cache-dir "git+https://github.com/ultralytics/CLIP.git"

FROM python:3.11-slim AS production
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libgl1 && rm -rf /var/lib/apt/lists/*
RUN groupadd -r appgroup && useradd -r -g appgroup -d /app/home appuser
COPY --from=builder /app/.venv ./.venv
COPY app/ ./app/
ENV PATH="/app/.venv/bin:$PATH"
# Pre-download model weights and CLIP cache to /app/home so appuser can access them at runtime.
# YOLO_CONFIG_DIR and HOME are set so all caches land in /app (chowned to appuser below).
ENV YOLO_CONFIG_DIR=/tmp/ultralytics_config
ENV HF_HOME=/app/hf_cache
ENV HOME=/app/home
RUN mkdir -p /app/models /app/hf_cache /app/home && \
    cd /app/models && \
    python -c "from ultralytics import YOLO; YOLO('yolov8n.pt')" && \
    python -c "from ultralytics import YOLOWorld; m=YOLOWorld('yolov8s-worldv2.pt'); m.set_classes(['person']); print('YOLO-World + CLIP: ok')" && \
    python -c "from transformers import AutoImageProcessor, AutoModelForDepthEstimation; AutoImageProcessor.from_pretrained('depth-anything/Depth-Anything-V2-Metric-Outdoor-Small-hf'); AutoModelForDepthEstimation.from_pretrained('depth-anything/Depth-Anything-V2-Metric-Outdoor-Small-hf')" && \
    chown -R appuser:appgroup /app/models /app/hf_cache /app/home
USER appuser
EXPOSE 8100
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8100/health')"
CMD uvicorn app.main:app --host 0.0.0.0 --port 8100 --log-level ${LOG_LEVEL:-info}
