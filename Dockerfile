# LiveKit Cloud agent worker image.
# Builds the same agent_dispatcher.py used in Flow 1 simulate_job, but here
# it runs as a long-lived registered worker accepting dispatched jobs.

FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        ffmpeg \
        libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY src/ ./src/

RUN pip install -e .

# Pre-download VAD + turn detector model weights into the image so cold-start
# in cloud is fast.
RUN python src/agent_dispatcher.py download-files

# LK Cloud injects LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET at runtime.
CMD ["python", "src/agent_dispatcher.py", "start"]
