# syntax=docker/dockerfile:1
# Multi-stage build: install deps in a build stage with compilers available,
# copy only the resulting venv + source into a slim runtime image.

FROM python:3.12-slim AS builder

# libgomp1 is required by LightGBM's OpenMP-parallelised inference/training at
# import time -- without it `import lightgbm` raises immediately.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY requirements.txt .
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

FROM python:3.12-slim AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 appuser

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
COPY app ./app
COPY migrations ./migrations
COPY alembic.ini .

# Model artifacts are NOT baked into the image -- they are build outputs of
# training, mounted or fetched at deploy time (see docs/DEPLOYMENT.md).
# The runtime looks for them under /app/models by default; override
# MODEL_DIR to point elsewhere.
RUN mkdir -p /app/models && chown -R appuser:appuser /app

USER appuser
EXPOSE 8100

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8100/healthz', timeout=3)" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8100"]
