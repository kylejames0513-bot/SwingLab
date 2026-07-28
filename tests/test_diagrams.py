"""Diagram system: every drill renders a well-formed setup diagram and a
CSS-only animation (namespaced, reduced-motion-safe, self-contained), the
sparkline follows its drawing contract, and the new drill families are
complete and wired to their flags."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from swinglab.config import Config
from swinglab.diagrams import (
    DRILL_SCENES,
    drill_animation,
    drill_diagram,
    sparkline,
)
from swinglab.drills import DRILLS, build_drills, family_for, practice_plan

# Spec-frozen flag ids (string literals on purpose — the ids are the
# cross-team interface, see spec §1.3).
NEW_KEYS = ("head-dip", "arm-extension", "balance")

BRAND = {"primary_color": "#1a5c38", "accent_color": "#e8720c"}
CUSTOM = {"primary_color": "#0b3d91", "accent_color": "#c2185b"}

ALL_DRILL_IDS = sorted(d.id for ds in DRILLS.values() for d in ds)

# Self-contained rule: nothing that reaches outside the SVG string itself.
FORBIDDEN = ("xlink:href", "url(", "<image", "@import", "<script")


def render_all(brand=BRAND):
    for drill_id in ALL_DRILL_IDS:
        yield drill_id, drill_diagram(drill_id, brand)
        yield drill_id, drill_animation(drill_id, brand)


# -- coverage and well-formedness --------------------------------------------

def test_every_drill_has_a_scene_and_nothing_extra():
    assert set(DRILL_SCENES) == {d.id for ds in DRILLS.values() for d in ds}


def test_every_diagram_and_animation_is_wellformed_branded_selfcontained():
    for drill_id, svg in render_all():
        root = ET.fromstring(svg)  # raises on malformed XML
        assert root.tag.endswith("svg"), drill_id
        assert root.get("viewBox") == "0 0 200 200", drill_id
        assert root.get("role") == "img", drill_id
        assert root.get("aria-label"), drill_id
        assert BRAND["primary_color"] in svg, drill_id
        for needle in FORBIDDEN:
            assert needle not in svg, (drill_id, needle)


def test_unknown_drill_id_raises_keyerror():
    try:
        drill_diagram("no-such-drill", BRAND)
    except KeyError:
        pass
    else:
        raise AssertionError("expected KeyError")


# -- animation structure -----------------------------------------------------

def test_animation_keyframes_pose_groups_and_reduced_motion():
    for drill_id in ALL_DRILL_IDS:
        svg = drill_animation(drill_id, BRAND)
        key = re.sub(r"[^a-z0-9]+", "-", drill_id.lower()).strip("-")
        n = len(DRILL_SCENES[drill_id].poses)
        assert 2 <= n <= 4, drill_id
        assert f"@keyframes sl-{key}-p0" in svg, drill_id
        for k in range(n):
            assert f'class="sl-{key}-pose sl-{key}-p{k}"' in svg, drill_id
            assert f"@keyframes sl-{key}-p{k}" in svg, drill_id
        # no keyframes beyond the pose count
        assert f"@keyframes sl-{key}-p{n}" not in svg, drill_id
        assert "prefers-reduced-motion" in svg, drill_id
        assert "animation: none !important" in svg, drill_id


def test_two_drills_animations_share_no_keyframe_names():
    a = drill_animation("dip-chair-drill", BRAND)
    b = drill_animation("arm-towel-under-lead", BRAND)
    names_a = set(re.findall(r"@keyframes\s+([\w-]+)", a))
    names_b = set(re.findall(r"@keyframes\s+([\w-]+)", b))
    assert names_a and names_b
    assert not names_a & names_b


# -- white-label -------------------------------------------------------------

def test_custom_brand_swaps_both_colors():
    for drill_id, svg in render_all(CUSTOM):
        assert CUSTOM["primary_color"] in svg, drill_id
        assert "#1a5c38" not in svg, drill_id  # default green gone
    # the accent color appears wherever a scene draws a training aid
    svg = drill_diagram("sway-stick-outside-trail-foot", CUSTOM)
    assert CUSTOM["accent_color"] in svg
    assert "#e8720c" not in svg


# -- sparkline ---------------------------------------------------------------

def test_sparkline_empty_inputs_render_nothing():
    assert sparkline([], 0.5, BRAND) == ""
    assert sparkline([None, None], 0.5, BRAND) == ""


def test_sparkline_one_circle_per_value_and_parses():
    svg = sparkline([0.1, 0.2, 0.3, 0.4], 0.25, BRAND)
    ET.fromstring(svg)
    assert svg.count("<circle") == 4
    assert 'width="120"' in svg and 'height="28"' in svg


def test_sparkline_worse_higher_accents_values_above_benchmark():
    svg = sparkline([0.1, 0.4], 0.25, BRAND, worse="higher")
    assert f'fill="{BRAND["primary_color"]}"' in svg  # 0.1 is fine
    assert f'fill="{BRAND["accent_color"]}"' in svg   # 0.4 is worse
    # count exactly one accented dot
    assert svg.count(f'fill="{BRAND["accent_color"]}"') == 1


def test_sparkline_worse_lower_accents_values_below_benchmark():
    svg = sparkline([120.0, 170.0], 150.0, BRAND, worse="lower")
    circles = re.findall(r'<circle[^>]*fill="([^"]+)"', svg)
    assert circles == [BRAND["accent_color"], BRAND["primary_color"]]


def test_sparkline_none_benchmark_no_dashed_line_no_accent():
    svg = sparkline([0.1, 0.9], None, BRAND)
    assert "stroke-dasharray" not in svg
    assert BRAND["accent_color"] not in svg


def test_sparkline_none_midseries_splits_polyline():
    svg = sparkline([0.1, 0.2, None, 0.3, 0.4], 0.25, BRAND)
    assert svg.count("<polyline") == 2
    assert svg.count("<circle") == 4


def test_sparkline_flat_series_and_single_value_do_not_crash():
    flat = sparkline([0.5, 0.5, 0.5], 0.5, BRAND)  # zero span → widened
    ET.fromstring(flat)
    assert flat.count("<circle") == 3
    single = sparkline([0.5], None, BRAND)
    ET.fromstring(single)
    assert "<polyline" not in single
    assert single.count("<circle") == 1
    assert 'cx="60"' in single  # centered dot


# -- drill-library extension -------------------------------------------------

def test_new_families_present_and_complete():
    for key in NEW_KEYS:
        assert key in DRILLS
        assert len(DRILLS[key]) == 2
    seen = set()
    for drills in DRILLS.values():
        for d in drills:
            for field in ("id", "name", "aim", "dosage", "success_metric",
                          "gear_tag"):
                value = getattr(d, field)
                assert isinstance(value, str) and value.strip(), (d.id, field)
            assert d.id not in seen  # unique across the whole library
            seen.add(d.id)
            assert 3 <= len(d.protocol) <= 4, d.id
            assert all(isinstance(s, str) and s.strip() for s in d.protocol)
    for key in NEW_KEYS:
        for d in DRILLS[key]:
            assert d.gear_tag == f"swinglab:{key}", d.id


def test_shoulder_tilt_has_evidence_matched_drill_family():
    assert family_for("shoulder-tilt") == "shoulder-tilt"
    assert family_for("arm-extension") == "arm-extension"
    assert family_for("head-dip") == "head-dip"
    assert family_for("nonsense") is None


def test_arm_extension_and_shoulder_tilt_render_distinct_plan_blocks():
    plan = practice_plan(["arm-extension", "shoulder-tilt"], Config())
    assert [b["flag"] for b in plan] == [
        "arm-extension",
        "shoulder-tilt",
    ]
    plan = practice_plan(["shoulder-tilt"], Config())
    assert [b["flag"] for b in plan] == ["shoulder-tilt"]
    assert plan[0]["title"] == "Shoulder-tilt change"
    assert plan[0]["drills"][0].gear_tag == "swinglab:arm-extension"


def test_new_refilm_targets_track_config_thresholds():
    coach = dict(Config().coaching)
    coach["head_dip_warn_sw"] = 0.4
    coach["lead_arm_warn_deg"] = 160
    coach["finish_balance_warn_sw"] = 0.2
    rebuilt = build_drills(coach)
    assert any("0.40" in d.success_metric for d in rebuilt["head-dip"])
    assert any("160" in d.success_metric for d in rebuilt["arm-extension"])
    assert any("0.20" in d.success_metric for d in rebuilt["balance"])
    # defaults use the spec-frozen shipped thresholds
    assert any("0.25" in d.success_metric for d in DRILLS["head-dip"])
    assert any("150" in d.success_metric for d in DRILLS["arm-extension"])
    assert any("0.15" in d.success_metric for d in DRILLS["balance"])
