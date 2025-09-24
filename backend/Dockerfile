FROM python:3.12-slim
WORKDIR /code
ENV PORT 8000
EXPOSE 8000
# Install dependencies and clean up in one layer
RUN apt-get update && \
   apt-get install -y --no-install-recommends \
       build-essential \
       gcc \
       g++ \
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
       git && \
   apt-get clean && \
   rm -rf /var/lib/apt/lists/*
# Set LD_LIBRARY_PATH for both x86_64 and aarch64
ENV LD_LIBRARY_PATH=/usr/lib/x86_64-linux-gnu:/usr/lib/aarch64-linux-gnu:$LD_LIBRARY_PATH

# Set encoding for Turkish character support
ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8
ENV PYTHONIOENCODING=utf-8

# Copy application code
COPY . /code
# Copy requirements file and install Python dependencies
COPY requirements.txt /code/
# --no-cache-dir --upgrade 
RUN pip install --upgrade pip
RUN pip install -r requirements.txt

# Set command with UTF-8 encoding support
CMD ["gunicorn", "score:app", "--workers", "8", "--threads", "8", "--worker-class", "uvicorn.workers.UvicornWorker", "--bind", "0.0.0.0:8000", "--timeout", "300", "--preload"]
