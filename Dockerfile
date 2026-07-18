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
# C-4: base images pinned by digest (multi-arch manifest list) — a re-tagged base
# cannot silently change the build. Bump = update tag AND digest together.
FROM python:3.12-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de

# uv for fast, lockfile-pinned installs (pinned tag+digest for reproducible builds)
COPY --from=ghcr.io/astral-sh/uv:0.8.23@sha256:94390f20a83e2de83f63b2dadcca2efab2e6798f772edab52bf545696c86bdb4 /uv /usr/local/bin/uv

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

# C-4: run as non-root. The store (data/) and llm-call log (logs/) are created at
# runtime, so they must be writable by the app user; everything else stays root-owned
# read-only. uid/gid 1000 matches the HF-demo convention.
RUN groupadd --gid 1000 app && useradd --uid 1000 --gid app --no-create-home app \
    && mkdir -p /app/data /app/logs \
    && chown app:app /app/data /app/logs
USER app

# Secrets (DWH/BI credentials, AUTO_BI_* config) come from the environment / --env-file
# at run time — never baked into the image (security §4).
EXPOSE 8200
# C-4: container-level liveness — /api/v1/health answers without auth or a DWH.
# python:slim ships no curl; stdlib urllib keeps the image lean.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8200/api/v1/health', timeout=4).status == 200 else 1)"]
CMD ["auto_bi", "serve", "--host", "0.0.0.0", "--port", "8200"]
