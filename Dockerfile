FROM ghcr.io/astral-sh/uv:debian-slim

ARG GIT_COMMIT=unknown
LABEL git-commit=$GIT_COMMIT

COPY ./service.py /opt/service/
COPY ./mqtt.py /opt/service/
COPY ./pyproject.toml /opt/service/
WORKDIR /opt/service

RUN mkdir /opt/service/monitoring

# Keeps Python from buffering stdout and stderr to avoid situations where
# the application crashes without emitting any logs due to buffering.
ENV PYTHONUNBUFFERED=1

# Enable bytecode compilation
ENV UV_COMPILE_BYTECODE=1

# Copy from the cache instead of linking since it's a mounted volume
ENV UV_LINK_MODE=copy

# Omit development dependencies
ENV UV_NO_DEV=1

# Ensure installed tools can be executed out of the box
ENV UV_TOOL_BIN_DIR=/usr/local/bin

# Install the project's dependencies using the lockfile and settings
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project

# Then, add the rest of the project source code and install it
# Installing separately from its dependencies allows optimal layer caching
COPY uv.lock  /opt/service
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked

# Place executables in the environment at the front of the path
ENV PATH="/opt/service/.venv/bin:$PATH"

EXPOSE 5000

ENTRYPOINT ["python"]
CMD ["service.py"]