# Reproducible image (EP-6, frontier-audit F12): dependencies come from uv.lock
# (--frozen), never re-resolved at build time — the deployed env is the tested env.
FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

WORKDIR /app

# uv is the only pip-installed tool (pinned); everything else is lock-frozen.
RUN pip install --no-cache-dir uv==0.11.2

# Dependency layer first: lockfile only — cache-friendly and reproducible.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

# Application layer.
COPY . .
RUN uv sync --frozen --no-dev

# Run from the locked venv.
ENV PATH="/app/.venv/bin:$PATH"

# Provenance: the run manifest (run_meta.provenance) falls back to this env var
# when the image has no git metadata. Stamp it at build time:
#   docker build --build-arg ACTIONPULSE_CODE_SHA=$(git rev-parse HEAD) ...
ARG ACTIONPULSE_CODE_SHA=unknown
ENV ACTIONPULSE_CODE_SHA=${ACTIONPULSE_CODE_SHA}

# Create non-root user
RUN useradd -m -u 1001 digest && \
    mkdir -p /data/out /data/.state && \
    chown -R digest:digest /data

USER digest

# Expose Prometheus metrics and health check ports
EXPOSE 9108 9109

ENTRYPOINT ["python", "-m", "digest_core.cli", "run"]
CMD ["--from-date", "today", "--sources", "ews", "--out", "/data/out"]
