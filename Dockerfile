# Pin the exact multi-architecture image manifest used by the checked-in
# Remotion lockfile. Updating this requires an intentional digest refresh.
FROM node:22.13.1-bookworm@sha256:5145c882f9e32f07dd7593962045d97f221d57a1b609f5bf7a807eb89deff9d6

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    BACKLOT_HOST=0.0.0.0 \
    BACKLOT_PORT=4750 \
    HOME=/home/node

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-pip ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy source before installing the local distribution: requirements.txt is a
# wrapper around ``-e .`` and cannot be resolved before the project exists.
COPY pyproject.toml requirements.txt requirements-dev.txt requirements-gpu.txt setup.py ./
COPY . .

# Bake the pinned Remotion browser into the image. Remotion stores this browser
# under the project-local ``node_modules/.remotion`` directory (not HOME/.cache),
# so the immutable image retains it even when runtime caches are mounted as
# tmpfs. Runtime rendering must not silently depend on a network download,
# especially with a read-only filesystem and a restricted production egress
# policy.
RUN python3 -m pip install --break-system-packages --no-cache-dir . \
    && npm ci --prefix remotion-composer \
    && cd remotion-composer \
    && npx --no-install remotion browser ensure \
    && cd /app \
    && mkdir -p /app/projects /app/output /app/.backlot \
    && chown -R node:node /app

RUN python3 -m compileall -q backlot lib schemas tools

EXPOSE 4750
VOLUME ["/app/projects", "/app/output"]

USER node

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD node -e "const t=process.env.BACKLOT_AUTH_TOKEN; const headers=t?{authorization:'Bearer '+t}:{}; fetch('http://127.0.0.1:4750/api/health',{headers}).then(r => process.exit(r.ok ? 0 : 1)).catch(() => process.exit(1))"

CMD ["python3", "-m", "backlot", "serve"]
