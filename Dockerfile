# Multi-stage build keeps the runtime image small and free of build deps.
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

COPY pyproject.toml ./
COPY handler_bot ./handler_bot
COPY handler_discord ./handler_discord
COPY scripts ./scripts

RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install .


FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

# Non-root user — never run app code as root in containers.
RUN groupadd --system --gid 1001 handler \
    && useradd --system --uid 1001 --gid handler --home-dir /app --shell /sbin/nologin handler

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY --chown=handler:handler handler_bot ./handler_bot
COPY --chown=handler:handler handler_discord ./handler_discord
COPY --chown=handler:handler scripts ./scripts

USER handler

CMD ["python", "-m", "scripts.run"]
