# ── Stage 1: build dependencies ───────────────────────────────────────────────
FROM python:3.11-slim@sha256:b27df5841f3355e9473f9a516d38a6783b6c8dfeacaf2d14a240f443b368ddb6 AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt pyproject.toml ./
RUN pip install --prefix=/install -r requirements.txt

# ── Stage 2: dev (test / lint / mypy) ─────────────────────────────────────────
FROM python:3.11-slim@sha256:b27df5841f3355e9473f9a516d38a6783b6c8dfeacaf2d14a240f443b368ddb6 AS dev

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY --from=builder /install /usr/local

COPY requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt

COPY . .

CMD ["python", "run.py"]

# ── Stage 3: production runtime ───────────────────────────────────────────────
FROM python:3.11-slim@sha256:b27df5841f3355e9473f9a516d38a6783b6c8dfeacaf2d14a240f443b368ddb6 AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Copy only compiled wheels from builder — no build tools in prod image
COPY --from=builder /install /usr/local

# Copy application source only (no dev deps, no test files)
COPY reklama/ ./reklama/
COPY run.py search.py import_message.py pyproject.toml ./

CMD ["python", "run.py"]
