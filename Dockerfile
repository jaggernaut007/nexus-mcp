FROM python:3.12-slim

WORKDIR /app

# Install build dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc g++ ripgrep && \
    rm -rf /var/lib/apt/lists/*

# Copy and install package
COPY pyproject.toml README.md LICENSE ./
COPY src/ src/
RUN pip install --no-cache-dir .

# Clean up build dependencies
RUN apt-get purge -y --auto-remove gcc g++

# Default environment
ENV NEXUS_STORAGE_DIR=/data/.nexus \
    NEXUS_EMBEDDING_MODEL=bge-small-en \
    NEXUS_LOG_LEVEL=INFO

VOLUME /data

ENTRYPOINT ["nexus-mcp-ci"]
