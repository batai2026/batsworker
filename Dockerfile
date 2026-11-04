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

WORKDIR /workspace
# Copy the installed package + console script from the build stage.
COPY --from=build /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=build /usr/local/bin/openworker-server /usr/local/bin/openworker-server

ENV PYTHONUNBUFFERED=1 \
    COWORKER_STATE_DIR=/workspace/.coworker-state

ENTRYPOINT ["openworker-server"]
CMD ["--cwd", "/workspace"]