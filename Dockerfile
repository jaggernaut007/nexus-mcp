FROM python:3.12-slim

# Install system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates curl git gcc g++ ripgrep nodejs npm && \
    npm install -g mcp-proxy@6.4.3 && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy and install package
COPY . .
RUN pip install --no-cache-dir .

# Ensure nexus-mcp-ci is in PATH
RUN which nexus-mcp-ci

# Default environment
ENV NEXUS_STORAGE_DIR=/data/.nexus \
    NEXUS_EMBEDDING_MODEL=jina-code \
    NEXUS_LOG_LEVEL=INFO

VOLUME /data

# Use mcp-proxy as entrypoint to match Glama's expectation
ENTRYPOINT ["mcp-proxy", "--", "nexus-mcp-ci"]
