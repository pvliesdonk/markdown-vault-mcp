FROM python:3.14-slim

# DOCKERFILE-APT-DEPS-START — add domain apt packages below; kept across copier update
RUN apt-get update && apt-get install -y --no-install-recommends git git-lfs gosu \
    && rm -rf /var/lib/apt/lists/* \
    && git lfs install --system
# DOCKERFILE-APT-DEPS-END

COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Optional remote-debugger listener.  Default off so production images stay
# lean; pass ``--build-arg DEBUG=true`` to install the ``[debug]`` extra
# (debugpy) and bake the listener into the image.  Accepts the same boolean
# vocabulary as runtime ``parse_bool`` (``true``/``1``/``yes``/``on``,
# case-insensitive); anything else is treated as off.  See
# ``docs/deployment/docker.md`` for the full attach workflow.
ARG DEBUG=false

# DOCKERFILE-UV-EXTRAS-START — append `--extra <name>` flags below to pull domain-specific extras; kept across copier update
# Install dependencies first (cache layer).  The ``$( ... )`` shell
# expansion appends ``--extra debug`` only when ``--build-arg DEBUG=true``;
# default builds get the lean install.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    uv sync --frozen --no-install-project --no-dev --extra all \
        $( case "$DEBUG" in [Tt][Rr][Uu][Ee]|1|[Yy][Ee][Ss]|[Oo][Nn]) echo "--extra debug" ;; esac )

# Copy source and install project.
COPY pyproject.toml uv.lock README.md /app/
COPY src/ /app/src/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --extra all \
        $( case "$DEBUG" in [Tt][Rr][Uu][Ee]|1|[Yy][Ee][Ss]|[Oo][Nn]) echo "--extra debug" ;; esac )
# DOCKERFILE-UV-EXTRAS-END

# Create non-root user with configurable UID/GID for bind-mount compatibility.
ARG APP_UID=1000
ARG APP_GID=1000
RUN if [ "$APP_UID" -eq 0 ] || [ "$APP_GID" -eq 0 ]; then \
        echo "ERROR: APP_UID and APP_GID must be non-zero" >&2; exit 1; \
    fi \
    && groupadd -r --gid $APP_GID --non-unique appuser \
    && useradd -r --uid $APP_UID --gid $APP_GID --no-log-init -d /app appuser \
    # DOCKERFILE-STATE-DIRS-START — domain state subdirs; kept across copier update
    && mkdir -p /data/vault /data/state/embeddings /data/state/fastembed /data/state/fastmcp \
    # DOCKERFILE-STATE-DIRS-END
    && chown -R appuser:appuser /app /data

COPY --chmod=0755 docker-entrypoint.sh /usr/local/bin/
# `FASTMCP_ENABLE_RICH_LOGGING=false` because a container has no terminal.
# Rich then assumes 80 columns, and a structured request-log record is longer
# than the room left beside its time, level and source columns, so every
# record wraps across three space-padded lines that neither `docker logs` nor
# a collector can read back.  Off, each record is one line: JSON from the
# request-logging middleware, `LEVEL: message` from the rest of FastMCP's own
# loggers.  Rich's time column goes with it, and Docker's log driver timestamps
# every line it captures anyway (`docker logs -t`).  An image default rather
# than a compose `environment:` entry, so a `.env` can still turn it back on.
ENV PATH="/app/.venv/bin:$PATH" \
    FASTMCP_HOME=/data/state/fastmcp \
    FASTMCP_ENABLE_RICH_LOGGING=false

EXPOSE 8000
# Remote debugger: ``EXPOSE`` is metadata — nothing actually listens unless
# the image was built with ``--build-arg DEBUG=true`` (which installs
# debugpy) AND ``MARKDOWN_VAULT_MCP_DEBUG_PORT`` is set at runtime.  Always
# declared so the toggled ``[debug]`` extra has a stable port surface
# to reach with ``-p 127.0.0.1:5678:5678``.
EXPOSE 5678

# DOCKERFILE-VOLUMES-START — mounted volume list; kept across copier update
VOLUME ["/data/vault", "/data/state"]
# DOCKERFILE-VOLUMES-END

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
# Liveness for a bare `docker run`: the same `GET /health` probe compose.yml
# declares, so a container reports health with or without Compose (a compose
# `healthcheck:` overrides this one where both exist).  Assumes the
# conventional `/mcp` mount — see the compose.yml comment for the
# `MARKDOWN_VAULT_MCP_HTTP_PATH` caveat.  Shell form on purpose: `python` is the
# venv interpreter on PATH, and the one-liner needs no argument splitting.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).close()"
# Both the bind address and the port are fixed by the image, not read from the
# environment: `EXPOSE`, `HEALTHCHECK`, the compose port mapping and the compose
# healthcheck all name 8000, and `MARKDOWN_VAULT_MCP_PORT` in a `.env` would
# otherwise move the listener out from under all four.  Publish a different
# host port instead (`-p 9000:8000`, or compose's `ports:`).
CMD ["markdown-vault-mcp", "serve", "--transport", "http", "--host", "0.0.0.0", "--port", "8000"]
