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
ENV PATH="/app/.venv/bin:$PATH" \
    FASTMCP_HOME=/data/state/fastmcp

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
CMD ["markdown-vault-mcp", "serve", "--transport", "http", "--host", "0.0.0.0"]
