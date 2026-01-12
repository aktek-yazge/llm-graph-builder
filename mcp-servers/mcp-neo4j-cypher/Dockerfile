FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies (curl for healthcheck)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install build dependencies
RUN pip install --no-cache-dir hatchling

# Copy project files
COPY pyproject.toml /app/
COPY README.md /app/

# Install Python dependencies
RUN pip install --no-cache-dir \
    neo4j>=5.26.0 \
    fastmcp>=2.10.5 \
    pydantic>=2.10.1 \
    tiktoken>=0.11.0 \
    langchain-openai>=0.3.0 \
    openai>=1.0.0 \
    python-dotenv>=1.0.0

# Copy the source code
COPY src/ /app/src/

# Install the package
RUN pip install --no-cache-dir -e .

# Environment variables for Neo4j connection (defaults - override via docker-compose)
ENV NEO4J_URI="bolt://host.docker.internal:7687"
ENV NEO4J_USERNAME="neo4j"
ENV NEO4J_PASSWORD="password"
ENV NEO4J_DATABASE="neo4j"
ENV NEO4J_NAMESPACE=""
ENV NEO4J_TRANSPORT="http"
ENV NEO4J_MCP_SERVER_HOST="0.0.0.0"
ENV NEO4J_MCP_SERVER_PORT="${MCP_PORT:-8002}"
ENV NEO4J_MCP_SERVER_PATH="/mcp/"
ENV NEO4J_MCP_SERVER_ALLOW_ORIGINS="*"
ENV NEO4J_MCP_SERVER_ALLOWED_HOSTS="localhost,127.0.0.1,host.docker.internal"
ENV NEO4J_READ_TIMEOUT="30"

# Expose both ports (sigorta: 8002, bakim: 8003)
EXPOSE 8002 8003

# Command to run the server directly via Python module
CMD ["python", "-m", "mcp_neo4j_cypher.server"]
