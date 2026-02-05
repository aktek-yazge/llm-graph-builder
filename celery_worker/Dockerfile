# syntax=docker/dockerfile:1
# Celery Worker Dockerfile
# Build: DOCKER_BUILDKIT=1 docker build -t celery-worker .
# Run: docker run celery-worker
#
# BuildKit cache mount kullanılıyor - dependency'ler cache'te tutulur

FROM ubuntu:24.04

WORKDIR /app

# ============================================
# STAGE 1: System Dependencies
# ============================================

# Install Python 3.13 and system dependencies in one layer
RUN apt-get update && \
    apt-get install -y software-properties-common && \
    add-apt-repository ppa:deadsnakes/ppa && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
        python3.13 \
        python3.13-dev \
        python3-pip \
        build-essential \
        gcc \
        g++ \
        libdbus-1-dev \
        libglib2.0-dev \
        pkg-config \
        libgmp-dev \
        libmpfr-dev \
        libmpc-dev \
        libxml2-dev \
        libxslt1-dev \
        zlib1g-dev \
        libjpeg-dev \
        libpng-dev \
        libfreetype6-dev \
        libffi-dev \
        libssl-dev \
        libmagic1 \
        libgl1 \
        libreoffice \
        cmake \
        poppler-utils \
        tesseract-ocr \
        git \
        curl \
        ca-certificates \
        libcairo2-dev \
        libgirepository1.0-dev \
        gobject-introspection \
        gir1.2-gtk-3.0 \
        python3-dbus \
        python3-gi \
        python3-setuptools \
        python3-wheel && \
    # Set Python 3.13 as default
    update-alternatives --install /usr/bin/python python /usr/bin/python3.13 1 && \
    update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.13 1 && \
    # Cleanup
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# ============================================
# STAGE 2: Python Dependencies (via uv)
# ============================================

# Get uv for fast dependency installation
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/

# Copy only dependency files (for better caching)
COPY pyproject.toml uv.lock /app/

# Install dependencies to GLOBAL Python (no venv)
# --system: Install to system Python, not virtualenv
# --mount=type=cache: BuildKit cache mount for faster rebuilds
#   - uv cache: downloaded packages
#   - pip cache: wheel cache
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=cache,target=/root/.cache/pip \
    cd /app && \
    uv pip install --system -r pyproject.toml && \
    # Remove uv after install (not needed at runtime)
    rm /usr/local/bin/uv

# ============================================
# STAGE 3: Application Code
# ============================================

# Copy src folder (application code)
COPY src/ /app/src/

# Copy prompts folder (domain-specific prompts for entity extraction)
COPY prompts/ /app/prompts/

# ============================================
# ENVIRONMENT
# ============================================

# Library paths for x86_64 and aarch64
ENV LD_LIBRARY_PATH=/usr/lib/x86_64-linux-gnu:/usr/lib/aarch64-linux-gnu:$LD_LIBRARY_PATH

# Encoding for Turkish character support
ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8
ENV PYTHONIOENCODING=utf-8

# Python environment
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# ============================================
# RUNTIME
# ============================================

# Create output directory
RUN mkdir -p /app/output

# Default command - can be overridden in docker-compose
# Each worker type (main, db_writer, neo4j_writer) uses different queues
CMD ["python", "-m", "celery", "-A", "src.celery_app", "worker", "--loglevel=info", "--pool=threads", "--concurrency=4", "-Q", "celery"]
