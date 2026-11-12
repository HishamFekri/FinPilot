# syntax=docker/dockerfile:1.10
#
# FinPilot API image. Build it off the server, from a recorded source revision:
#
#     docker build -t finpilot-api:<revision> backend
#
# At runtime it is an unprivileged user (10201, unused on the home server) on a read-only root
# filesystem, with Chromium's own sandbox on for PDF exports (deploy/seccomp/README.md). It never
# runs migrations or changes the database on start: those are separate, explicit steps
# (deploy/README.md).
#
# Base images are pinned to exact versions here. Releases additionally pin them by digest.

ARG PYTHON_IMAGE=python:3.14.6-slim-trixie
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.11.21

FROM ${UV_IMAGE} AS uv

# --- Dependencies, exactly as locked. No compilers or uv in the final image.
FROM ${PYTHON_IMAGE} AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /src
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev

# Test-only dependencies, for the stack-test stage below. Never part of the runtime image.
FROM build AS build-test
ENV UV_PROJECT_ENVIRONMENT=/opt/venv-test
RUN uv sync --locked

# --- The runtime image
FROM ${PYTHON_IMAGE} AS runtime
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright \
    # The bundled Chromium, not a system Chrome (blank means unset).
    PDF_BROWSER_CHANNEL= \
    HOME=/home/finpilot
COPY --from=build /opt/venv /opt/venv
# Chromium's headless shell with its libraries, and fonts for Arabic and Turkish PDFs.
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-noto-core fonts-noto-ui-core fonts-dejavu-core \
    && playwright install --with-deps --only-shell chromium \
    && rm -rf /var/lib/apt/lists/* /root/.cache \
    && groupadd --system --gid 10201 finpilot \
    && useradd --system --uid 10201 --gid finpilot --home-dir /home/finpilot --no-create-home \
       --shell /usr/sbin/nologin finpilot
WORKDIR /app
# Owned by root and not writable by the app user, even if the filesystem weren't read-only.
COPY alembic.ini ./
COPY migrations ./migrations
COPY scripts ./scripts
COPY app ./app
USER 10201:10201
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/healthz', timeout=4).status == 200 else 1)"]
# One process: the rate limiter counts per process (docs/PRODUCTION_READINESS.md, LB-5).
# --no-proxy-headers: only the app's own TRUSTED_PROXIES setting decides whose X-Forwarded-For
# is believed. --no-access-log: the app logs route templates, never raw URLs.
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "1", "--no-proxy-headers", "--no-server-header", "--no-access-log", \
     "--timeout-graceful-shutdown", "20"]

# --- Local verification only: the same image plus the stack tests (deploy/scripts/stack-test.sh).
FROM runtime AS stack-test
COPY --from=build-test /opt/venv-test /opt/venv-test
ENV PATH=/opt/venv-test/bin:$PATH
COPY tests_stack ./tests_stack
# python -m puts /app on the path, so the tests import the app as it is in the image.
CMD ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider", "-rs", "tests_stack"]

# The default target is the runtime image.
FROM runtime
