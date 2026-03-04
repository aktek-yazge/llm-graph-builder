FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml .

RUN pip install --no-cache-dir \
    "fastmcp>=2.10.5" \
    "neo4j>=5.26.0" \
    "pydantic>=2.10.1" \
    "tiktoken>=0.11.0" \
    "langchain-openai>=0.3.0" \
    "openai>=1.0.0" \
    "python-dotenv>=1.0.0" \
    "minio>=7.2.0" \
    "starlette>=0.40.0"

COPY . .

EXPOSE 8010

CMD ["python", "main.py"]
