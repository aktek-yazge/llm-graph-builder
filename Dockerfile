FROM python:3.12-slim
WORKDIR /workspace
EXPOSE 8005
EXPOSE 5175

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
       git \
       curl \
       ca-certificates \
       gnupg \
       lsb-release \
       net-tools \
       iputils-ping \
       zsh && \
   # Docker CE repository ekle
   curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg && \
   echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/debian $(lsb_release -cs) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null && \
   apt-get update && \
   apt-get install -y docker-ce-cli && \
   apt-get clean && \
   rm -rf /var/lib/apt/lists/*

# Install Node.js 22 (after curl is available)
RUN curl -fsSL https://deb.nodesource.com/setup_24.x | bash - && \
    apt-get install -y nodejs

# Install yarn globally
RUN npm install -g yarn

# Install uv package manager
RUN curl -LsSf https://astral.sh/uv/install.sh | sh && \
    echo 'export PATH="$HOME/.local/bin:$PATH"' >> /root/.bashrc

# Install Oh My Zsh and configure zsh
RUN sh -c "$(curl -fsSL https://raw.github.com/ohmyzsh/ohmyzsh/master/tools/install.sh)" "" --unattended && \
    echo 'export PATH="$HOME/.local/bin:$PATH"' >> /root/.zshrc && \
    chsh -s $(which zsh)

# Set LD_LIBRARY_PATH for both x86_64 and aarch64
ENV LD_LIBRARY_PATH=/usr/lib/x86_64-linux-gnu:/usr/lib/aarch64-linux-gnu
ENV PATH="/root/.local/bin:$PATH"

# Python environment
ENV PYTHONPATH=/workspace/backend
ENV PYTHONUNBUFFERED=1

# Keep container running for manual start
CMD ["tail", "-f", "/dev/null"]