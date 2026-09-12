FROM python:3.11-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:0.9.21 /uv /usr/local/bin/uv
RUN apt-get update && apt-get install -y --no-install-recommends \
    git ca-certificates build-essential linux-libc-dev libglib2.0-0 \
    libegl1 libgl1 libglfw3 libosmesa6 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
ENV MUJOCO_GL=osmesa UV_LINK_MODE=copy
COPY simulation/prepare.py simulation/prepare.py
RUN uv run --no-project simulation/prepare.py
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --locked --no-dev
COPY simulation/__init__.py simulation/sim.py simulation/evaluate.py simulation/
CMD ["uv", "run", "--no-sync", "-m", "simulation.sim", "smoke"]
