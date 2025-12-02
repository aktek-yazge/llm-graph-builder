FROM ubuntu:24.04
WORKDIR /code
ENV PORT 8000
EXPOSE 8000

# Install Python 3.13 and pip properly
RUN apt-get update && \
    apt-get install -y software-properties-common && \
    add-apt-repository ppa:deadsnakes/ppa && \
    apt-get update && \
    apt-get install -y python3.13 python3.13-venv python3.13-dev python3-pip && \
    update-alternatives --install /usr/bin/python python /usr/bin/python3.13 1 && \
    update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.13 1 && \
    python3.13 -m ensurepip || true

# Install dependencies and clean up in one layer
RUN apt-get update && \
   apt-get install -y --no-install-recommends \
       build-essential \
       gcc \
       g++ \
       libdbus-1-dev libglib2.0-dev \
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
       python3-wheel \
       docker.io && \
   apt-get clean && \
   rm -rf /var/lib/apt/lists/*

# Set LD_LIBRARY_PATH for both x86_64 and aarch64
ENV LD_LIBRARY_PATH=/usr/lib/x86_64-linux-gnu:/usr/lib/aarch64-linux-gnu:$LD_LIBRARY_PATH

# Set encoding for Turkish character support
ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8
ENV PYTHONIOENCODING=utf-8

# Python environment
ENV PYTHONPATH=/code
ENV PYTHONUNBUFFERED=1

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

# Copy dependency files
COPY pyproject.toml uv.lock /code/

# Install dependencies using uv
RUN cd /code && uv sync --frozen --no-dev

# Copy application code
COPY . /code

# Set command with UTF-8 encoding support using uv
# Use uvicorn worker for ASGI (FastAPI) compatibility
CMD ["uv", "run", "gunicorn", "score:app", "--bind", "0.0.0.0:8000", "--timeout", "600", "--preload", "-k", "uvicorn.workers.UvicornWorker"]
