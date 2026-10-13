# syntax=docker/dockerfile:1
# Built from the repository root (the render target also needs skills/pptx-builder):
#   target `runtime` — API, migrations and the AI worker
#   target `render`  — the render worker: runtime + Node + Chromium + fonts + the pptx skill

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
    && mkdir -p /data \
    && chown app:app /data
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

# ---- the skill's Node dependency (playwright-core, used only to drive Chromium) ----
FROM node:24-bookworm-slim AS skill-node
WORKDIR /skill
COPY skills/pptx-builder/package.json skills/pptx-builder/package-lock.json ./
# --ignore-scripts: no install hooks; Chromium comes from Debian below, not from a download.
RUN --mount=type=cache,target=/root/.npm npm ci --omit=dev --ignore-scripts

# ---- render worker ----
FROM runtime AS render
USER root
# Chromium lays the slides out. The skill lists metric-compatible fonts after each brand font
# (skills/pptx-builder/fonts/substitutes.json): Liberation for Arial/Times/Courier, Gelasio
# (Latin) and DejaVu Serif (Cyrillic, never narrower) for Georgia.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        chromium fontconfig fonts-liberation2 fonts-dejavu-core \
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
ENV PPTX_SKILL_DIR=${SKILL_DIR} \
    CHROMIUM_PATH=/usr/lib/chromium/chromium
USER app
