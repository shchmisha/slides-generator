# syntax=docker/dockerfile:1
# Built from the repository root (the render target also needs skills/):
#   target `runtime` — API, migrations, the AI worker and the render worker
#   target `render`  — the renderer: Node + headless Chromium + FFmpeg + fonts + the pptx and
#                      video skills. Runs without secrets on a network with no route out
#                      (docker-compose.yml)

# ---- build: resolve Python dependencies into a venv using the lockfile ----
FROM python:3.12-slim-bookworm AS builder
COPY --from=ghcr.io/astral-sh/uv:0.12.13 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# ---- runtime: API and AI worker, no build tools, non-root ----
FROM python:3.12-slim-bookworm AS runtime
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --create-home --home-dir /home/app app \
    && mkdir -p /data /spool \
    && chown app:app /data /spool
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY backend/alembic.ini ./
COPY backend/alembic ./alembic
COPY backend/app ./app
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
USER app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]

# ---- the skills' Node dependencies, from their lockfiles ----
# --ignore-scripts: no install hooks; Chromium comes from Debian below, not from a download
# (puppeteer, under HyperFrames, would otherwise fetch its own Chrome).
FROM node:24-bookworm-slim AS skill-node
WORKDIR /skill
COPY skills/pptx-builder/package.json skills/pptx-builder/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --omit=dev --ignore-scripts
WORKDIR /video
COPY skills/video-builder/package.json skills/video-builder/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --omit=dev --ignore-scripts

# ---- renderer ----
FROM runtime AS render
USER root
# Headless Chromium (the headless shell: what BeginFrame capture needs) lays the slides out
# and draws the video frames; FFmpeg encodes them.
# The skill lists metric-compatible fonts after each brand font
# (skills/pptx-builder/fonts/substitutes.json): Liberation for Arial/Times/Courier, Gelasio
# (Latin) and DejaVu Serif (Cyrillic, never narrower) for Georgia.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        chromium-headless-shell ffmpeg fontconfig fonts-liberation2 fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
COPY --from=skill-node /usr/local/bin/node /usr/local/bin/node
COPY skills/pptx-builder/fonts/*.ttf /usr/local/share/fonts/gelasio/
RUN fc-cache -f
ENV SKILL_DIR=/opt/skills/pptx-builder
COPY --from=skill-node /skill/node_modules ${SKILL_DIR}/node_modules
COPY skills/pptx-builder/package.json skills/pptx-builder/SKILL.md ${SKILL_DIR}/
COPY skills/pptx-builder/src ${SKILL_DIR}/src
COPY skills/pptx-builder/pptx_builder ${SKILL_DIR}/pptx_builder
COPY skills/pptx-builder/themes ${SKILL_DIR}/themes
COPY skills/pptx-builder/fonts/substitutes.json ${SKILL_DIR}/fonts/
# The video skill reads the same font table, at ../pptx-builder/fonts/substitutes.json.
ENV VIDEO_DIR=/opt/skills/video-builder
COPY --from=skill-node /video/node_modules ${VIDEO_DIR}/node_modules
COPY skills/video-builder/package.json skills/video-builder/SKILL.md ${VIDEO_DIR}/
COPY skills/video-builder/src ${VIDEO_DIR}/src
ENV PPTX_SKILL_DIR=${SKILL_DIR} \
    VIDEO_SKILL_DIR=${VIDEO_DIR} \
    CHROMIUM_PATH=/usr/lib/chromium/chromium-headless-shell \
    PRODUCER_HEADLESS_SHELL_PATH=/usr/lib/chromium/chromium-headless-shell
USER app
CMD ["python", "-m", "app.sandbox.server"]
