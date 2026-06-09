FROM python:3.11-slim-bookworm

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    apt-transport-https \
    ca-certificates \
    curl \
    gcc \
    gnupg \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Azure CLI via pip and jq via apt
RUN apt-get update && apt-get install -y --no-install-recommends jq && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir azure-cli

# Install Microsoft Admin shell tooling. GitHub tooling stays in the GitHub connector.
RUN curl -sSL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor > /usr/share/keyrings/microsoft.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/microsoft.gpg] https://packages.microsoft.com/debian/12/prod bookworm main" > /etc/apt/sources.list.d/microsoft-prod.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends powershell \
    && rm -rf /var/lib/apt/lists/*

RUN set -eux; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in \
      amd64) bicep_arch="x64" ;; \
      arm64) bicep_arch="arm64" ;; \
      *) echo "Unsupported architecture for Bicep: $arch" >&2; exit 1 ;; \
    esac; \
    curl -fsSL -o /usr/local/bin/bicep "https://github.com/Azure/bicep/releases/latest/download/bicep-linux-${bicep_arch}"; \
    chmod +x /usr/local/bin/bicep; \
    bicep --version

RUN pwsh -NoLogo -NoProfile -NonInteractive -Command "\
    Set-PSRepository -Name PSGallery -InstallationPolicy Trusted; \
    Install-Module Microsoft.Graph -Scope AllUsers -Force -AllowClobber; \
    Install-Module ExchangeOnlineManagement -Scope AllUsers -Force -AllowClobber; \
    Install-Module MicrosoftTeams -Scope AllUsers -Force -AllowClobber; \
    Install-Module Az -Scope AllUsers -Force -AllowClobber"

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
