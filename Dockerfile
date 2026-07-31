# CaddieInsight web app.
#
#   docker build -t swinglab .
#   docker run -p 8000:8000 -v swinglab-sessions:/data swinglab
#
# Or just: docker compose up  (see docker-compose.yml)

FROM python:3.11-slim

# ffmpeg/ffprobe are called as external binaries; mediapipe's native library
# needs the GL ES stack even in CPU mode; DejaVu supplies the image-label font.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg fonts-dejavu-core libgles2 libegl1 libgl1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md config.yaml ./
COPY swinglab ./swinglab
RUN pip install --no-cache-dir ".[web]"

# Bake the pose model in at build time so containers start warm instead of
# downloading ~6 MB on the first analysis.
RUN python -c "from swinglab.pose import ensure_model; ensure_model()"

# Session data (uploads, deliverables, job database) lives under /data —
# mount a volume there so it outlives the container. docker-compose.yml does;
# on Railway/Render/Fly attach the platform's volume at /data. (No VOLUME
# instruction here: Railway rejects Dockerfiles that declare one.)
EXPOSE 8000

# Cloud hosts inject PORT for their routing; default to 8000 elsewhere.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT', '8000') + '/healthz', timeout=4)"

CMD swinglab serve --host 0.0.0.0 --port ${PORT:-8000} --sessions-dir /data/sessions
