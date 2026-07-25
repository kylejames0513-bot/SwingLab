"""Inline-SVG drill diagrams, animations, issue-card sparklines, and the
progress dashboard's trend charts.

Every function returns a complete ``<svg …>…</svg>`` string (never a full
HTML document), hand-built with no dependencies. Brand colors come in as
the plain ``cfg.brand`` dict (keys used: ``primary_color``,
``accent_color``) — this module never imports Config or reads files, which
keeps it trivially testable.

Drawing convention: every scene shows a right-handed golfer, face-on, with
the target to the image LEFT. That is a drawing convention only — these
are instructional illustrations of drill setups, not measurements of the
player's swing.

The animations are CSS-only opacity crossfades (the universally supported
interpolation): no SMIL, no JS, no external fonts, and no ``url(...)``
references anywhere, so many animations can be inlined into one report
page. All class and keyframe names are namespaced per drill id because
inlined SVG shares the page's global CSS namespace. Under
``prefers-reduced-motion`` every animation pauses on its setup pose.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from math import atan2, cos, radians, sin
from typing import Sequence

# Neutral ink for ground/props/labels; brand primary draws the figure and
# brand accent draws training aids and motion cues.
GRAY = "#9aa0a6"

# ---------------------------------------------------------------------------
# Shared geometry vocabulary
# ---------------------------------------------------------------------------
# Canvas is viewBox="0 0 200 200"; the ground line sits at y=180 and the
# figure is centered near x=100. Image y grows downward, as in the source
# footage.

Pose = dict[str, tuple[float, float]]  # named joints, canvas coords

JOINTS = ("head", "neck", "hip", "knee_lead", "knee_trail",
          "ankle_lead", "ankle_trail", "elbow", "grip", "club")
# "club" (grip-to-clubhead line endpoint) is optional per pose; every other
# joint is required. One elbow/grip pair only - the stick figure has a single
# drawn arm, which reads clearly at this size.

P_ADDRESS: Pose = {
    "head": (91, 60), "neck": (94, 74), "hip": (100, 122),
    "knee_lead": (89, 150), "knee_trail": (111, 150),
    "ankle_lead": (88, 180), "ankle_trail": (112, 180),
    "elbow": (97, 92), "grip": (100, 112), "club": (79, 172),
}
P_HALF_BACK: Pose = {
    "head": (93, 60), "neck": (96, 74), "hip": (101, 122),
    "knee_lead": (89, 150), "knee_trail": (111, 150),
    "ankle_lead": (88, 180), "ankle_trail": (112, 180),
    "elbow": (110, 84), "grip": (122, 90), "club": (146, 72),
}
P_TOP: Pose = {
    "head": (95, 59), "neck": (97, 73), "hip": (102, 122),
    "knee_lead": (90, 150), "knee_trail": (112, 150),
    "ankle_lead": (88, 180), "ankle_trail": (112, 180),
    "elbow": (118, 66), "grip": (128, 52), "club": (150, 36),
}
P_IMPACT: Pose = {
    "head": (92, 60), "neck": (93, 73), "hip": (96, 121),
    "knee_lead": (88, 149), "knee_trail": (108, 150),
    "ankle_lead": (88, 180), "ankle_trail": (112, 180),
    "elbow": (94, 90), "grip": (92, 110), "club": (77, 172),
}
P_FINISH: Pose = {
    "head": (86, 57), "neck": (88, 71), "hip": (92, 119),
    "knee_lead": (88, 149), "knee_trail": (102, 152),
    "ankle_lead": (88, 180), "ankle_trail": (112, 180),
    "elbow": (78, 66), "grip": (66, 58), "club": (50, 42),
}


def pose_with(base: Pose, **joints: tuple[float, float]) -> Pose:
    """A copy of ``base`` with the named joints overridden."""
    out = dict(base)
    out.update(joints)
    return out


def _n(x: float) -> str:
    """Compact SVG number: trims trailing zeros ('84.0' -> '84')."""
    s = f"{float(x):.2f}".rstrip("0").rstrip(".")
    return s if s else "0"


def _esc(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _pts(pose: Pose, *names: str) -> str:
    return " ".join(f"{_n(pose[j][0])},{_n(pose[j][1])}" for j in names)


def _figure(pose: Pose, color: str, width: float = 3.0) -> str:
    """Stick figure: head circle r=8 at 'head'; polylines neck-hip,
    hip-knee_lead-ankle_lead, hip-knee_trail-ankle_trail, neck-elbow-grip;
    line neck-head; thin line (width*0.6) grip-club when 'club' present.
    round linecaps/joins; fill none; stroke = color."""
    hx, hy = pose["head"]
    nx, ny = pose["neck"]
    # The neck-head line stops at the head circle's rim (the head is
    # unfilled; a line to its centre would read as a spoke).
    dist = ((nx - hx) ** 2 + (ny - hy) ** 2) ** 0.5 or 1.0
    ex = hx + 8.0 * (nx - hx) / dist
    ey = hy + 8.0 * (ny - hy) / dist
    parts = [
        f'<g stroke="{color}" stroke-width="{_n(width)}" fill="none" '
        'stroke-linecap="round" stroke-linejoin="round">',
        f'<line x1="{_n(nx)}" y1="{_n(ny)}" x2="{_n(ex)}" y2="{_n(ey)}"/>',
        f'<circle cx="{_n(hx)}" cy="{_n(hy)}" r="8"/>',
        f'<polyline points="{_pts(pose, "neck", "hip")}"/>',
        f'<polyline points="{_pts(pose, "hip", "knee_lead", "ankle_lead")}"/>',
        f'<polyline points="{_pts(pose, "hip", "knee_trail", "ankle_trail")}"/>',
        f'<polyline points="{_pts(pose, "neck", "elbow", "grip")}"/>',
    ]
    if "club" in pose:
        gx, gy = pose["grip"]
        cx, cy = pose["club"]
        parts.append(
            f'<line x1="{_n(gx)}" y1="{_n(gy)}" x2="{_n(cx)}" y2="{_n(cy)}" '
            f'stroke-width="{_n(width * 0.6)}"/>'
        )
    parts.append("</g>")
    return "".join(parts)


# ---------------------------------------------------------------------------
# Prop vocabulary — SVG fragment *format templates* with {primary}, {accent}
# and {gray} placeholders; the renderer fills them. No <defs>, no element
# ids, arrowheads as explicit <polygon> — many of these SVGs share one HTML
# page, so nothing here may collide.
# ---------------------------------------------------------------------------

def _ground() -> str:
    return ('<line x1="8" y1="180" x2="192" y2="180" stroke="{gray}" '
            'stroke-width="2" stroke-linecap="round"/>')


def _ball(x: float = 80.0) -> str:
    return f'<circle cx="{_n(x)}" cy="176" r="3" fill="{{gray}}"/>'


def _stick(x: float, lean: float = 0.0) -> str:
    """Alignment stick: from (x, 180) up 60, leaned ``lean`` degrees about
    its base (positive leans the top away from the golfer)."""
    tx = x + 60.0 * sin(radians(lean))
    ty = 180.0 - 60.0 * cos(radians(lean))
    return (f'<line x1="{_n(x)}" y1="180" x2="{_n(tx)}" y2="{_n(ty)}" '
            'stroke="{accent}" stroke-width="2.5" stroke-linecap="round"/>')


def _chair(x: float) -> str:
    """Chair glyph: back post at ``x`` (facing the golfer), seat and two
    legs extending right of it."""
    return (
        f'<g stroke="{{gray}}" stroke-width="2" fill="none" '
        'stroke-linecap="round" stroke-linejoin="round">'
        f'<polyline points="{_n(x)},104 {_n(x)},126 {_n(x + 24)},126"/>'
        f'<line x1="{_n(x + 3)}" y1="126" x2="{_n(x + 3)}" y2="178"/>'
        f'<line x1="{_n(x + 21)}" y1="126" x2="{_n(x + 21)}" y2="178"/>'
        "</g>"
    )


def _wall(x: float) -> str:
    ticks = "".join(
        f'<line x1="{_n(x)}" y1="{_n(y)}" x2="{_n(x + 7)}" y2="{_n(y - 7)}"/>'
        for y in (75, 115, 155)
    )
    return (f'<g stroke="{{gray}}" stroke-width="2" stroke-linecap="round">'
            f'<line x1="{_n(x)}" y1="40" x2="{_n(x)}" y2="180" '
            f'stroke-width="2.5"/>{ticks}</g>')


def _towel(x: float, y: float) -> str:
    return (f'<rect x="{_n(x)}" y="{_n(y)}" width="10" height="16" rx="3" '
            'fill="{accent}" opacity="0.8"/>')


def _band(x1: float, y1: float, x2: float, y2: float) -> str:
    """Resistance band: a zigzag path from (x1, y1) to (x2, y2)."""
    segs = 6
    amp = 3.0
    dx, dy = x2 - x1, y2 - y1
    length = (dx * dx + dy * dy) ** 0.5 or 1.0
    # unit perpendicular
    px, py = -dy / length, dx / length
    pts = [f"{_n(x1)},{_n(y1)}"]
    for k in range(1, segs):
        t = k / segs
        off = amp if k % 2 else -amp
        pts.append(f"{_n(x1 + dx * t + px * off)},{_n(y1 + dy * t + py * off)}")
    pts.append(f"{_n(x2)},{_n(y2)}")
    return (f'<polyline points="{" ".join(pts)}" stroke="{{accent}}" '
            'stroke-width="2" fill="none" stroke-linejoin="round"/>')


def _mirror(x: float) -> str:
    return (f'<rect x="{_n(x - 3)}" y="42" width="6" height="136" '
            'fill="{gray}" opacity="0.5"/>')


def _metronome(x: float, y: float) -> str:
    return (
        f'<g stroke="{{gray}}" stroke-width="2" fill="none" '
        'stroke-linecap="round" stroke-linejoin="round">'
        f'<polygon points="{_n(x - 9)},{_n(y + 24)} {_n(x + 9)},{_n(y + 24)} '
        f'{_n(x)},{_n(y)}"/>'
        f'<line x1="{_n(x)}" y1="{_n(y + 22)}" x2="{_n(x + 7)}" y2="{_n(y + 4)}"/>'
        "</g>"
    )


def _arrow(x1: float, y1: float, x2: float, y2: float) -> str:
    """Accent motion arrow with an explicit polygon head (no markers)."""
    ang = atan2(y2 - y1, x2 - x1)
    head = 7.0
    spread = radians(26)
    bx1 = x2 - head * cos(ang - spread)
    by1 = y2 - head * sin(ang - spread)
    bx2 = x2 - head * cos(ang + spread)
    by2 = y2 - head * sin(ang + spread)
    lx2 = x2 - 0.6 * head * cos(ang)
    ly2 = y2 - 0.6 * head * sin(ang)
    return (
        f'<line x1="{_n(x1)}" y1="{_n(y1)}" x2="{_n(lx2)}" y2="{_n(ly2)}" '
        'stroke="{accent}" stroke-width="2" stroke-linecap="round"/>'
        f'<polygon points="{_n(x2)},{_n(y2)} {_n(bx1)},{_n(by1)} '
        f'{_n(bx2)},{_n(by2)}" fill="{{accent}}"/>'
    )


def _label(x: float, y: float, text: str) -> str:
    return (f'<text x="{_n(x)}" y="{_n(y)}" font-size="9" '
            f'fill="{{gray}}">{_esc(text)}</text>')


def _box(x: float, y: float, w: float, h: float) -> str:
    """Small dashed reference box (e.g. the mirror drill's tape marks)."""
    return (f'<rect x="{_n(x)}" y="{_n(y)}" width="{_n(w)}" height="{_n(h)}" '
            'fill="none" stroke="{gray}" stroke-width="1.5" '
            'stroke-dasharray="3 2"/>')


def _hbar(x1: float, x2: float, y: float) -> str:
    """Horizontal accent bar (e.g. the head-window 'ceiling' stick)."""
    return (f'<line x1="{_n(x1)}" y1="{_n(y)}" x2="{_n(x2)}" y2="{_n(y)}" '
            'stroke="{accent}" stroke-width="2.5" stroke-linecap="round"/>')


# ---------------------------------------------------------------------------
# Scene registry — the single data source for both the static diagram and
# the animation.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Scene:
    drill_id: str
    label: str                # aria-label, e.g. "Chair drill setup"
    props: tuple[str, ...]    # format templates from the vocabulary above
    poses: tuple[Pose, ...]   # 2..4 keyframe poses; poses[0] is the setup


def _scene(drill_id: str, label: str, props: Sequence[str],
           poses: Sequence[Pose]) -> Scene:
    return Scene(drill_id, label, tuple(props), tuple(poses))


_FEET_TOGETHER = dict(
    ankle_lead=(96.0, 180.0), ankle_trail=(104.0, 180.0),
    knee_lead=(95.0, 150.0), knee_trail=(105.0, 150.0),
)

_SCENES = [
    _scene(
        "tempo-three-beat-count",
        "Three-beat count: swing on a 1-2-3 count",
        [_ground(), _ball(), _metronome(160, 60),
         _label(110, 118, "1"), _label(129, 96, "2"), _label(138, 62, "3")],
        [P_ADDRESS, P_HALF_BACK, P_TOP, P_IMPACT],
    ),
    _scene(
        "tempo-late-whoosh",
        "Late whoosh: speed peaks past the ball",
        [_ground(), _ball(), _arrow(104, 168, 56, 163)],
        [P_TOP, P_IMPACT, P_FINISH],
    ),
    _scene(
        "tempo-pause-at-top",
        "Pause at the top, then start down",
        [_ground(), _ball(), _label(140, 44, "hold")],
        [P_ADDRESS, P_TOP, P_TOP, P_IMPACT],  # repeated TOP = the pause
    ),
    _scene(
        "sway-stick-outside-trail-foot",
        "Stick outside the trail foot setup",
        [_ground(), _ball(), _stick(122, lean=-8)],
        [P_ADDRESS, P_TOP],
    ),
    _scene(
        "sway-mirror-head-box",
        "Mirror head box setup",
        [_ground(), _mirror(30), _box(22, 50, 16, 18), _label(12, 46, "tape")],
        [P_ADDRESS, P_TOP],
    ),
    _scene(
        "hip-slide-banded-turn",
        "Banded turn setup",
        [_ground(), _ball(), _band(89, 146, 111, 146)],
        [P_ADDRESS, P_TOP],
    ),
    _scene(
        "hip-slide-trail-pocket",
        "Trail-pocket turn setup",
        [_ground(), _wall(128), _arrow(105, 120, 123, 116)],
        [P_ADDRESS, P_TOP],
    ),
    _scene(
        "consistency-one-count",
        "One count, every club",
        [_ground(), _ball(), _metronome(160, 60)],
        [P_ADDRESS, P_TOP, P_IMPACT],
    ),
    _scene(
        "consistency-rehearsal-pairs",
        "Rehearsal pairs: practice swing, then ball",
        [_ground(), _ball(76), _ball(84), _label(52, 162, "pair")],
        [P_ADDRESS, P_TOP, P_IMPACT],
    ),
    _scene(
        "clean-baseline-refilm",
        "Baseline re-film setup",
        [_ground(), _ball(), _mirror(168), _label(146, 38, "camera")],
        [P_ADDRESS, P_IMPACT],
    ),
    _scene(
        "clean-mirror-checkpoints",
        "Mirror checkpoints",
        [_ground(), _mirror(30)],
        [P_ADDRESS, P_TOP, P_IMPACT],
    ),
    _scene(
        "dip-chair-drill",
        "Chair drill setup",
        [_ground(), _ball(), _chair(114)],
        [P_ADDRESS, P_TOP, P_IMPACT],
    ),
    _scene(
        "dip-head-window",
        "Head-window drill setup",
        [_ground(), _ball(), _hbar(70, 120, 48), _label(126, 51, "window")],
        [P_ADDRESS, P_HALF_BACK, P_IMPACT],
    ),
    _scene(
        "arm-towel-under-lead",
        "Towel under the lead arm setup",
        [_ground(), _ball(), _towel(88, 84)],
        [P_ADDRESS, P_HALF_BACK, P_IMPACT],
    ),
    _scene(
        "arm-impact-freeze",
        "Impact freeze setup",
        [_ground(), _ball(), _mirror(30), _label(40, 40, "hold 3s")],
        [P_ADDRESS, P_IMPACT, P_IMPACT],  # repeated IMPACT = the freeze
    ),
    _scene(
        "balance-feet-together",
        "Feet-together swings setup",
        [_ground(), _ball()],
        [pose_with(P_ADDRESS, **_FEET_TOGETHER),
         pose_with(P_TOP, **_FEET_TOGETHER),
         pose_with(P_FINISH, ankle_lead=(96.0, 180.0),
                   ankle_trail=(104.0, 180.0), knee_lead=(95.0, 149.0),
                   knee_trail=(102.0, 152.0))],
    ),
    _scene(
        "balance-hold-the-finish",
        "Hold the finish for a three count",
        [_ground(), _ball(), _label(56, 40, "1-2-3")],
        [P_IMPACT, P_FINISH, P_FINISH],  # repeated FINISH = the hold
    ),
]

DRILL_SCENES: dict[str, Scene] = {s.drill_id: s for s in _SCENES}


def _colors(brand: dict) -> tuple[str, str]:
    return str(brand["primary_color"]), str(brand["accent_color"])


def _props_svg(scene: Scene, primary: str, accent: str) -> str:
    return "".join(
        p.format(primary=primary, accent=accent, gray=GRAY)
        for p in scene.props
    )


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def drill_diagram(drill_id: str, brand: dict) -> str:
    """Static instructional setup view for a drill (raises KeyError for an
    unknown drill_id — callers only ask for ids from drills.DRILLS)."""
    scene = DRILL_SCENES[drill_id]
    primary, accent = _colors(brand)
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 200" '
        f'role="img" aria-label="{_esc(scene.label)}" class="drill-svg">'
        f"{_props_svg(scene, primary, accent)}"
        f"{_figure(scene.poses[0], primary)}"
        "</svg>"
    )


def _anim_key(drill_id: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", drill_id.lower()).strip("-")


def drill_animation(drill_id: str, brand: dict) -> str:
    """CSS-only crossfade through the scene's keyframe poses. Loops forever;
    pauses on the setup pose under prefers-reduced-motion. Opacity-only
    keyframes, namespaced ``sl-{key}-…`` per drill so many animations can
    share one page. Duplicate animations of the same drill emit
    byte-identical CSS — redundant but harmless by design."""
    scene = DRILL_SCENES[drill_id]
    primary, accent = _colors(brand)
    key = _anim_key(drill_id)
    n = len(scene.poses)
    period = 1.6 * n              # seconds per full cycle
    w = 100.0 / (5 * n)           # fade width: a fifth of a slice, in %

    frames: list[str] = []
    bindings: list[str] = []
    for k in range(n):
        s = 100.0 * k / n
        e = 100.0 * (k + 1) / n
        if k == 0:
            body = (f"0%{{opacity:1}} {_n(e - w)}%{{opacity:1}} "
                    f"{_n(e)}%{{opacity:0}} {_n(100 - w)}%{{opacity:0}} "
                    "100%{opacity:1}")
        elif k < n - 1:
            body = (f"0%{{opacity:0}} {_n(s - w)}%{{opacity:0}} "
                    f"{_n(s)}%{{opacity:1}} {_n(e - w)}%{{opacity:1}} "
                    f"{_n(e)}%{{opacity:0}} 100%{{opacity:0}}")
        else:
            body = (f"0%{{opacity:0}} {_n(s - w)}%{{opacity:0}} "
                    f"{_n(s)}%{{opacity:1}} {_n(100 - w)}%{{opacity:1}} "
                    "100%{opacity:0}")
        frames.append(f"@keyframes sl-{key}-p{k} {{ {body} }}")
        bindings.append(
            f".sl-{key}-p{k} {{ animation: sl-{key}-p{k} {_n(period)}s "
            "linear infinite; }"
        )

    style = (
        "<style>"
        f".sl-{key}-pose {{ opacity: 0; }}"
        + "".join(frames)
        + "".join(bindings)
        + " @media (prefers-reduced-motion: reduce) {"
        f" .sl-{key}-pose {{ animation: none !important; opacity: 0; }}"
        f" .sl-{key}-p0 {{ opacity: 1; }}"
        " }"
        "</style>"
    )
    groups = "".join(
        f'<g class="sl-{key}-pose sl-{key}-p{k}">'
        f"{_figure(scene.poses[k], primary)}</g>"
        for k in range(n)
    )
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 200" '
        f'role="img" aria-label="{_esc(scene.label)} — animated" '
        'class="drill-svg">'
        f"{style}{_props_svg(scene, primary, accent)}{groups}"
        "</svg>"
    )


def sparkline(
    values: Sequence[float | None],
    benchmark: float | None,
    brand: dict,
    worse: str = "higher",      # which side of the benchmark is bad
) -> str:
    """Tiny per-swing trend SVG for the issue cards: dots (accent-filled on
    the bad side of the benchmark), a dashed benchmark line, polyline runs
    broken at missing values. Returns "" when there is nothing to plot."""
    vals = list(values)
    if not vals or all(v is None for v in vals):
        return ""
    primary, accent = _colors(brand)

    nums = [float(v) for v in vals if v is not None]
    domain = nums + ([float(benchmark)] if benchmark is not None else [])
    lo, hi = min(domain), max(domain)
    if hi - lo == 0:
        lo, hi = lo - 1.0, hi + 1.0
    pad = 0.08 * (hi - lo)
    lo -= pad
    hi += pad
    span = hi - lo
    n = len(vals)
    single = len(nums) == 1

    def x_of(i: int) -> float:
        return 60.0 if single else 3 + i * 114 / max(1, n - 1)

    def y_of(v: float) -> float:
        return 25 - (v - lo) / span * 22

    parts: list[str] = []
    if benchmark is not None:
        by = _n(y_of(float(benchmark)))
        parts.append(
            f'<line x1="3" y1="{by}" x2="117" y2="{by}" stroke="#999" '
            'stroke-width="1" stroke-dasharray="3 2"/>'
        )

    run: list[str] = []

    def flush() -> None:
        if len(run) >= 2:
            parts.append(
                f'<polyline points="{" ".join(run)}" stroke="{primary}" '
                'stroke-width="1.5" fill="none"/>'
            )
        run.clear()

    for i, v in enumerate(vals):
        if v is None:
            flush()
        else:
            run.append(f"{_n(x_of(i))},{_n(y_of(float(v)))}")
    flush()

    for i, v in enumerate(vals):
        if v is None:
            continue
        v = float(v)
        bad = benchmark is not None and (
            v > benchmark if worse == "higher" else v < benchmark
        )
        parts.append(
            f'<circle cx="{_n(x_of(i))}" cy="{_n(y_of(v))}" r="2.2" '
            f'fill="{accent if bad else primary}"/>'
        )

    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 28" '
        'width="120" height="28" role="img" aria-label="per-swing values">'
        + "".join(parts)
        + "</svg>"
    )


def trend_chart(
    points: Sequence[float | None],
    benchmark: float | None,
    brand: dict,
    worse: str = "higher",      # which side of the benchmark is bad
) -> str:
    """Session-over-session line chart for the progress dashboard.

    Same hand-built contract as everything else here: a complete
    ``<svg>…</svg>`` string, brand colors only, no <defs>, no url(...), no
    external anything. Sessions are spaced evenly (the story is session to
    session, not calendar-accurate gaps); the benchmark renders as a dashed
    line with a faint accent band over its bad side; dots sit on every
    session, accent-filled when that session is on the bad side. Returns ""
    when there is nothing to plot.
    """
    vals = [float(v) for v in points if v is not None]
    if not vals:
        return ""
    primary, accent = _colors(brand)
    x0, x1 = 12.0, 308.0
    y0, y1 = 14.0, 102.0
    domain = vals + ([float(benchmark)] if benchmark is not None else [])
    lo, hi = min(domain), max(domain)
    if hi - lo == 0:
        lo, hi = lo - 1.0, hi + 1.0
    pad = 0.10 * (hi - lo)
    lo -= pad
    hi += pad
    n = len(vals)

    def x_of(i: int) -> float:
        return (x0 + x1) / 2 if n == 1 else x0 + i * (x1 - x0) / (n - 1)

    def y_of(v: float) -> float:
        return y1 - (v - lo) / (hi - lo) * (y1 - y0)

    parts: list[str] = []
    if benchmark is not None:
        by = y_of(float(benchmark))
        band_top, band_bottom = (y0 - 4.0, by) if worse == "higher" else (by, y1 + 4.0)
        if band_bottom > band_top:
            parts.append(
                f'<rect x="{_n(x0 - 6)}" y="{_n(band_top)}" '
                f'width="{_n(x1 - x0 + 12)}" height="{_n(band_bottom - band_top)}" '
                f'fill="{accent}" opacity="0.08"/>'
            )
        parts.append(
            f'<line x1="{_n(x0 - 6)}" y1="{_n(by)}" x2="{_n(x1 + 6)}" '
            f'y2="{_n(by)}" stroke="{GRAY}" stroke-width="1" '
            'stroke-dasharray="4 3"/>'
        )
    parts.append(  # baseline under the plot, echoing the drill-scene ground
        f'<line x1="{_n(x0 - 6)}" y1="{_n(y1 + 8)}" x2="{_n(x1 + 6)}" '
        f'y2="{_n(y1 + 8)}" stroke="{GRAY}" stroke-width="1" opacity="0.6"/>'
    )
    if n >= 2:
        pts = " ".join(f"{_n(x_of(i))},{_n(y_of(v))}" for i, v in enumerate(vals))
        parts.append(
            f'<polyline points="{pts}" stroke="{primary}" stroke-width="2" '
            'fill="none" stroke-linecap="round" stroke-linejoin="round"/>'
        )
    for i, v in enumerate(vals):
        bad = benchmark is not None and (
            v > benchmark if worse == "higher" else v < benchmark
        )
        parts.append(
            f'<circle cx="{_n(x_of(i))}" cy="{_n(y_of(v))}" r="3.2" '
            f'fill="{accent if bad else primary}"/>'
        )
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 120" '
        'role="img" aria-label="session-to-session trend" class="trend-svg">'
        + "".join(parts)
        + "</svg>"
    )
