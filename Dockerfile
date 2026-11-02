# syntax=docker/dockerfile:1

# ---------------------------------------------------------------------------
# Stage 1: build the wheel. Keeps pip/setuptools/build tooling out of the final
# image so the runtime layer carries no compiler and no pip cache.
# ---------------------------------------------------------------------------
FROM python:3.11-slim-bookworm AS build

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

RUN pip install --no-cache-dir --upgrade pip build

COPY pyproject.toml README.md ./
COPY systor/ ./systor/
RUN python -m build --wheel --outdir /wheels

# ---------------------------------------------------------------------------
# Stage 2: runtime. Runs as a dedicated non-root system user.
# ---------------------------------------------------------------------------
FROM python:3.11-slim-bookworm

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
# - tini: lightweight init process to forward signals and reap zombie children
RUN apt-get update && apt-get install -y --no-install-recommends \
      smartmontools \
      curl \
      ca-certificates \
      tini \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 systor \
    && useradd --system --uid 10001 --gid 10001 \
               --no-create-home --shell /usr/sbin/nologin systor

WORKDIR /opt/systor

# Standard directories with full path parity to bare-metal Linux.
# Owned by the runtime user so fresh named volumes inherit the right ownership.
RUN mkdir -p /etc/systor /var/lib/systor /var/log/systor /var/run/systor \
    && chown -R systor:systor /etc/systor /var/lib/systor /var/log/systor /var/run/systor

# Install the prebuilt wheel; no source tree, no compiler, no pip cache.
COPY --from=build /wheels /wheels
RUN pip install --no-cache-dir --no-index --find-links=/wheels systor \
    && rm -rf /wheels

# Drop privileges. Everything below runs as uid/gid 10001.
USER systor

EXPOSE 6677

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS http://127.0.0.1:6677/health || exit 1

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["systor", "serve", "all"]
