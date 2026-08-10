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
# [ops] installs sentry-sdk. It stays inert without SENTRY_DSN (see
# swinglab.web.app.init_sentry), but without it installed the DSN alone does
# nothing — error monitoring cannot be turned on from the dashboard.
#
# [backup] installs boto3 for the caddieinsight-backup console script the
# image already ships (pyproject [project.scripts]). Without it the tool
# raises on first use, and the documented workaround was a runtime
# `pip install` into an ephemeral container on every cron invocation —
# which meant backing up the one SQLite file holding every entitlement was
# structurally blocked by the image itself. Like [ops], it stays inert
# until its variables (CADDIE_BACKUP_*) are set.
RUN pip install --no-cache-dir ".[web,ops,backup]"

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

# exec form + `exec` so the app is PID 1 and receives SIGTERM directly. Shell
# form leaves /bin/sh as PID 1, which does not forward signals: every redeploy
# then SIGKILLs the container after the grace period, killing any analysis in
# flight and leaving the SQLite WAL uncheckpointed. `sh -c` is still needed to
# expand ${PORT}, but `exec` replaces the shell rather than parenting the app.
CMD ["sh", "-c", "exec swinglab serve --host 0.0.0.0 --port ${PORT:-8000} --sessions-dir /data/sessions"]
