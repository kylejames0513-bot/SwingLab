# SwingLab

Golf swing analysis from a single phone video. Film yourself hitting balls,
point SwingLab at the clip, and get back per-swing metrics plus visual
deliverables:

- a labeled **key-position strip** (address / top / impact / finish),
- a smooth **quarter-speed slow-motion** clip per swing,
- a **centerline overlay** comparing the captured body (orange) against a
  corrected one (green) via an ankle-pinned shear,
- **report.html** with a metrics table, plain-English coaching notes, and every
  deliverable embedded, plus machine-readable **metrics.json**.

The whole product is white-label: brand name, logo, colors, footer, watermark,
disclaimer, and every detection/coaching threshold live in `config.yaml` — no
code edits needed to rebrand or retune.

## Requirements

- Python 3.11+
- `ffmpeg` and `ffprobe` on the PATH (called as external binaries — this also
  keeps licensing simple; revisit codec licensing only if you ever bundle
  ffmpeg into an installer)
- On headless Linux, mediapipe's native library needs OpenGL ES even in CPU
  mode: `apt install libgles2 libegl1 libgl1`
- `DejaVuSans-Bold` for image labels (ships with most Linux distributions;
  `apt install fonts-dejavu-core` if missing — Pillow falls back to a default
  font otherwise)

## Install

```bash
pip install -e .          # plus:  pip install -e ".[dev]"  for tests
```

The pose model (`pose_landmarker_lite.task`, ~5.8 MB) is downloaded once on
first run and cached inside the package under `swinglab/models/`.

## Usage

```bash
swinglab analyze path/to/video.mov --out results/ --hand right
swinglab analyze path/to/folder --batch
```

Useful flags:

- `--strikes "12.5,31.0"` — manual strike times (seconds), skips audio
  detection when it misses (or when the clip has no audio track)
- `--hand right|left` — golfer handedness (default right); also overrides the
  target-direction inference
- `--config path/to/config.yaml` — alternate branding/threshold config
- `--keep-work` — keep intermediate frames and audio for debugging

A short summary table plus the path to `report.html` is printed when done.
Each analyzed video gets its own session folder:

```
results/<video-name>/
├── report.html
├── metrics.json
└── media/
    ├── strip_s1.png      # key positions
    ├── overlay_s1.png    # centerline overlay
    ├── slowmo_s1.mp4     # quarter-speed clip
    └── ... one set per swing
```

## Web app

```bash
pip install -e ".[web]"
swinglab serve --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000 for a branded upload page: pick a clip, choose
handedness, optionally enter manual strike times, and watch a live status page
while the analysis runs in the background (the exact same `pipeline` module the
CLI uses — nothing is duplicated in the web layer). Finished sessions land in
`sessions/<id>/` and survive server restarts.

The JSON API under `/api` is the surface a future mobile app talks to:

- `POST /upload` — multipart upload (`video`, `hand`, optional `strikes`);
  redirects to the session page, whose id is the job id
- `GET /api/session/{id}` — job status, progress log, and (when done)
  `report_url` + `metrics_url`
- `GET /session/{id}/files/...` — report, media, and metrics.json

Single machine, no auth yet: `ensure_user_can_analyze()` in
`swinglab/web/app.py` is the clearly marked stub where payment or account
gating plugs in later.

## How it works

1. **Probe** — `ffprobe` reads duration, resolution, fps, and rotation.
   Phone `.mov` files store rotation as metadata which ffmpeg applies
   automatically during extraction; SwingLab never rotates manually (that
   would double-rotate).
2. **Strike detection** — ball strikes are sharp audio transients. The mono
   16 kHz track is enveloped in 10 ms hops and peaks are found with
   configurable height / prominence / minimum-gap thresholds.
3. **Frame extraction** — for each strike `t`, the window `t−1.8s … t+0.8s`
   at 30 fps, 480 px wide. Input-side trimming (`-ss`/`-t` before `-i`) is
   load-bearing: output-side `-t` silently truncates stretched clips.
4. **Pose tracking** — mediapipe pose landmarker (tasks API; pip wheels
   0.10.30+ no longer ship `mp.solutions`). Frames failing an upright sanity
   check (nose above shoulders above hips above ankles) are dropped.
5. **Swing events** — address baseline, takeaway, top of backswing, impact
   (audio time mapped to the nearest frame), finish. All lateral measurements
   are normalized by shoulder width at address so numbers are comparable
   across camera distances.
6. **Metrics** — backswing/downswing durations, tempo ratio (benchmark 3.0),
   signed head sway and hip slide in shoulder widths (positive = away from
   the target), plus per-session mean and standard deviation.
7. **Deliverables and report** — Pillow-rendered strip and overlay, ffmpeg
   `minterpolate` slow motion (interpolate to a high frame rate first, THEN
   stretch), Jinja2 report.

## Configuration

See `config.yaml` — everything is documented inline. Highlights:

| Section | What it controls |
| --- | --- |
| `brand` | name, logo, colors, footer, watermark on/off, disclaimer |
| `detection` | audio peak height / prominence / minimum gap between swings |
| `coaching` | sway warning, tempo target/warning, consistency praise thresholds |
| `analysis` | window size, working/full resolutions, takeaway threshold |
| `slowmo` | slow-motion factor, clip bounds, output height, crf |
| `overlay` | captured/corrected skeleton colors, arrow threshold |

## Tests

```bash
python -m pytest
```

The suite covers the acceptance checks: strike detection within 50 ms on a
synthetic wav, graceful zero-strike behavior, portrait-rotation handling on a
display-matrix `.mov`, white-label config changes reaching the report and
overlays, and an end-to-end three-swing run (three metric rows, three strips,
three slow-motion clips, three overlays, one report) with a replayed pose
sequence so no human footage is required. Tests needing ffmpeg auto-skip when
it is not installed.

## Roadmap

- **Milestone 1 (done)** — CLI: video in → results folder out.
- **Milestone 2 (done)** — FastAPI web app wrapping the same pipeline module
  (upload, status, results page, JSON API).
- **Milestone 3** — white-label polish: PDF export, richer batch mode.
- A native mobile app can sit on top of the existing JSON API (`/upload`,
  `/api/session/{id}`) without server changes.

## License notes

mediapipe is Apache 2.0 (commercial use fine). ffmpeg is LGPL/GPL and is
invoked as a system binary, which is standard practice for products.
