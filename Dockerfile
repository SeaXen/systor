# syntax=docker/dockerfile:1
FROM python:3.11-slim

LABEL maintainer="Dr. Sagar (SeaXen) <https://github.com/SeaXen/systor>"
LABEL org.opencontainers.image.title="Systor"
LABEL org.opencontainers.image.description="Lightweight Linux system monitor with web dashboard, alerts, SMART health, and Prometheus metrics"
LABEL org.opencontainers.image.url="https://github.com/SeaXen/systor"
LABEL org.opencontainers.image.source="https://github.com/SeaXen/systor"
LABEL org.opencontainers.image.licenses="MIT"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    SYSTOR_WEB_HOST=0.0.0.0 \
    SYSTOR_WEB_PORT=6677

# Install essential system utilities:
# - smartmontools: provides smartctl for Drive Health & SMART Insights
# - curl: provides container healthcheck and network probes
# - ca-certificates: enables TLS/SSL certificate trust
# - tini: lightweight init process to forward signals and reap zombie child processes
RUN apt-get update && apt-get install -y --no-install-recommends \
    smartmontools \
    curl \
    ca-certificates \
    tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/systor

# Create standard directories with full path parity to bare-metal Linux
RUN mkdir -p /etc/systor /var/lib/systor /var/log/systor

# Install Python requirements first for optimal layer caching
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy source tree and install package
COPY setup.py README.md ./
COPY systor/ ./systor/
RUN pip install --no-cache-dir .

EXPOSE 6677

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:6677/health || exit 1

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["systor", "serve", "all"]
