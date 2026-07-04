# Container image for the Auto_BI web app (`auto_bi serve`).
#
# Built on every push/PR by the `docker` job in `.github/workflows/ci.yml` (build-only,
# catches Dockerfile drift) and published to GHCR on tagged releases by
# `.github/workflows/release.yml` (`ghcr.io/<repo>:<version>` + `:latest`). Local dev on
# Windows has no Docker (container builds/runs happen on CI or the Mac stand):
#
#   docker build -t auto_bi .
#   docker run --rm -p 8200:8200 --env-file .env auto_bi
#
# The DWH connection it talks to (ClickHouse/Greenplum) and the BI it builds into
# (Superset/DataLens) are external — configure them via AUTO_BI_* env vars / --env-file.
FROM python:3.12-slim

# uv for fast, lockfile-pinned installs (pinned tag for reproducible builds)
COPY --from=ghcr.io/astral-sh/uv:0.8.23 /uv /usr/local/bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# Install dependencies first (this layer is cached while only app code changes),
# then the project itself. --frozen pins to uv.lock; --no-dev skips test/lint tools.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-install-project --no-dev
COPY auto_bi ./auto_bi
COPY semantic ./semantic
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"

# Secrets (DWH/BI credentials, AUTO_BI_* config) come from the environment / --env-file
# at run time — never baked into the image (security §4).
EXPOSE 8200
CMD ["auto_bi", "serve", "--host", "0.0.0.0", "--port", "8200"]
