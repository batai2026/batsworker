# OpenWorker CLI/server container image (P0 — v0.3.1 gap).
#
# Runs the OpenWorker sidecar server (`openworker-server`) inside a container, so a
# coworker can run fully inside an NVIDIA OpenShell sandbox (or any OCI runtime)
# with the workspace mounted at /workspace. Network egress from the sandbox is
# governed by the OpenShell network policy; the app's `request_egress` tool asks
# the user to allow blocked domains.
#
# Build (local smoke):
#   docker build -t openworker:local .
#   docker run --rm -v "$PWD:/workspace" openworker:local --cwd /workspace

FROM python:3.12-slim AS build

# pip install . needs git to fetch the pinned `aisuite` dependency.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
COPY pyproject.toml README.md ./
COPY coworker ./coworker
COPY scripts ./scripts
RUN pip install --no-cache-dir .

FROM python:3.12-slim AS runtime

# This image is published to GHCR and runs agent-driven shell commands, so it must
# not run as root: uid 0 with the default capability set turns any tool-execution
# escape into host-level privilege, and files written into a bind-mounted workspace
# land on the host owned by root.
#
# Bind mount a workspace on Linux with a UID that already owns it:
#   docker build --build-arg OPENWORKER_UID=$(id -u) \
#                --build-arg OPENWORKER_GID=$(id -g) -t openworker:local .
ARG OPENWORKER_UID=1000
ARG OPENWORKER_GID=1000

WORKDIR /workspace
# Copy the installed package + console script from the build stage.
COPY --from=build /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=build /usr/local/bin/openworker-server /usr/local/bin/openworker-server

ENV PYTHONUNBUFFERED=1 \
    COWORKER_STATE_DIR=/home/openworker/.coworker-state

# Create the unprivileged user and give it the two paths the server writes to
# (the state dir holds the generated sidecar token, written mode 0600).
#
# The state dir MUST stay outside /workspace: that directory is the agent's session
# root, and `read_file` only checks that a path is inside the session directories --
# so a state dir under /workspace would let the model read secrets.json (connector
# credentials) and sidecar-<port>.token (the full local API token) with no approval.
RUN groupadd --gid "${OPENWORKER_GID}" openworker \
    && useradd --uid "${OPENWORKER_UID}" --gid "${OPENWORKER_GID}" --create-home \
        --home-dir /home/openworker --shell /usr/sbin/nologin openworker \
    && mkdir -p /home/openworker/.coworker-state \
    && chown -R openworker:openworker /workspace /home/openworker/.coworker-state

USER openworker

ENTRYPOINT ["openworker-server"]
CMD ["--cwd", "/workspace"]