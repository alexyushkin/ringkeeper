# syntax=docker/dockerfile:1
# The worker image: Python, the Claude Agent SDK (which bundles the Claude Code
# CLI), git, pytest and uv for project checks, and the small runner. It never contains the master key, the data
# dir or a Docker socket, and none of them are ever mounted into it.
FROM python:3.12-slim-trixie

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

ARG CLAUDE_AGENT_SDK_VERSION=0.2.174
ARG PYTEST_VERSION=9.1.1
# pytest (and uv, for projects that use it) so a project's checks can run here.
RUN pip install --no-cache-dir         "claude-agent-sdk==${CLAUDE_AGENT_SDK_VERSION}" "pytest==${PYTEST_VERSION}"
COPY --from=ghcr.io/astral-sh/uv:0.9.22 /uv /usr/local/bin/uv

# Workspaces are bind mounts owned by someone else; let git work in them.
# /state is where the per-task volume mounts (the SDK's own state, for resume);
# owned by the worker here, so a fresh named volume starts out writable.
RUN useradd --uid 1000 --create-home --shell /usr/sbin/nologin worker \
    && mkdir -p /state && chown worker:worker /state \
    && printf '[safe]\n\tdirectory = /workspace\n[init]\n\tdefaultBranch = main\n' > /etc/gitconfig

COPY ringkeeper_worker /opt/ringkeeper_worker

ENV PYTHONPATH=/opt \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/home/worker \
    GIT_AUTHOR_NAME="Ringkeeper worker" \
    GIT_AUTHOR_EMAIL=worker@ringkeeper.invalid \
    GIT_COMMITTER_NAME="Ringkeeper worker" \
    GIT_COMMITTER_EMAIL=worker@ringkeeper.invalid

USER worker
WORKDIR /workspace
ENTRYPOINT ["python", "-m", "ringkeeper_worker"]
