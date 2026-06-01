FROM python:3.11-slim

WORKDIR /app

# Install system dependencies and Azure CLI
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Azure CLI via pip and jq via apt
RUN apt-get update && apt-get install -y --no-install-recommends jq && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir azure-cli

# Copy requirements first for better layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
# ARG CACHEBUST added to invalidate Docker layer cache on each build
# Use: docker build --build-arg CACHEBUST=$(git rev-parse HEAD) ...
ARG CACHEBUST=1
COPY . .

# Run as non-root user
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

RUN chmod +x scripts/startup.sh
CMD ["./scripts/startup.sh"]
