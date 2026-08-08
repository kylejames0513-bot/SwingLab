# AGENTS.md

## Cursor Cloud specific instructions

CaddieInsight (`swinglab`) is a single-product Python 3.11+ codebase: a golf-swing
analysis engine + CLI (`swinglab/`) plus a FastAPI/Uvicorn web app (`swinglab/web/`).
There is no separate database, queue, or cache — SQLite is embedded and all background
work runs as in-process threads inside the web app. See `README.md` for the full product
tour and `pyproject.toml` for entry points and extras.

### Environment layout
- Python deps are installed into a virtualenv at `.venv` (Ubuntu 24.04 is PEP-668
  externally-managed, so a venv is required — do not `pip install` into system Python).
  The startup update script recreates/refreshes it. Run tools via `.venv/bin/<tool>`
  (e.g. `.venv/bin/swinglab`, `.venv/bin/pytest`) or activate with
  `source .venv/bin/activate`.
- System packages `ffmpeg ffprobe libgles2 libegl1 libgl1 fonts-dejavu-core` are required
  (ffmpeg for video I/O; the GL stack for MediaPipe's native lib in CPU mode; DejaVu for
  image labels). These are already present on the base VM; they are NOT installed by the
  update script.
- The MediaPipe pose model (`swinglab/models/pose_landmarker_lite.task`, ~6 MB, gitignored)
  is downloaded automatically on first use. Pre-warm it with
  `.venv/bin/python -c "from swinglab.pose import ensure_model; ensure_model()"`.

### Lint / test / build / run
- Test suite: `.venv/bin/python -m pytest -q` (mirrors CI; ~4 min, ~1940 tests). There is
  no separate linter configured — CI (`.github/workflows/ci.yml`) runs an import/boundary
  check plus pytest. Browser tests use Playwright Chromium (installed via
  `.venv/bin/python -m playwright install chromium`).
- Import/boundary smoke (mirrors CI):
  `.venv/bin/python -c "import fastapi; from swinglab.analysis import analyze_video; from swinglab.api import create_app"`
  and `.venv/bin/python -m swinglab.cli --help`.
- Run the web app (dev):
  `.venv/bin/swinglab serve --host 0.0.0.0 --port 8000 --sessions-dir ./sessions`
  then open `http://127.0.0.1:8000`. Health check: `GET /healthz`.
- Run the analysis engine directly:
  `.venv/bin/swinglab analyze <video> --out <dir> --hand right --club iron`.

### Non-obvious gotchas
- `/healthz` reports `"status":"degraded"` on a bare setup. This is EXPECTED, not a
  failure: the shipped `config.yaml` sets `shopify_customer_sync.enabled: true`, but with
  no Shopify credentials the sync worker cannot bind. The core web service is fully
  functional. All Shopify/Stripe/email/Sentry/S3 integrations are optional and stay inert
  unless their environment variables are set (see `.env.example`).
- Account signup uses the local no-mail password path when commerce is disabled (the
  default here), so you can create an account without an email provider. If Shopify
  commerce or Admin sync is ever enabled, signup switches to requiring email verification
  and will return 503 without a configured mailer (`RESEND_API_KEY` or `SWINGLAB_SMTP_URL`).
- Ball-strike detection is audio-based (it looks for the impact "click"). A video with no
  impact sound reports "No ball strikes detected"; pass `--strikes "<seconds>"` to force a
  window. Producing real metrics requires a real golfer visible in frame — MediaPipe finds
  0 usable pose frames on synthetic/test-pattern clips. `GET /sample-report` (no login)
  renders a complete report from synthetic data through the real coaching/report machinery
  and is the best way to see finished analysis output without a real clip.
