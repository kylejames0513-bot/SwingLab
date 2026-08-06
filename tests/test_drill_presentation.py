"""Authored, safety-aware presentation for every drill that can lead a plan."""

from swinglab.caddie_brief import (
    readability_maintenance_drill,
    rhythm_maintenance_drill,
)
from swinglab.config import Config
from swinglab.drills import (
    build_drill_presentations,
    build_drills,
    drill_presentation,
    practice_plan,
)


def test_every_primary_drill_has_a_unique_authored_three_stage_presentation():
    cfg = Config()
    primary = [
        practice_plan([key], cfg)[0]["drills"][0]
        for key in build_drills(cfg.coaching)
    ] + [
        rhythm_maintenance_drill(cfg),
        readability_maintenance_drill(),
    ]
    presentations = build_drill_presentations(cfg.coaching)
    assert len({drill.id for drill in primary}) == len(primary)
    for drill in primary:
        presentation = drill_presentation(drill, cfg)
        assert presentations[drill.id] == presentation
        assert len(presentation.summary_steps) == 3
        assert all(stage.strip() for stage in presentation.summary_steps)
        assert presentation.setup.strip()
        assert presentation.feel_cue.strip()
        if len(drill.protocol) == 4:
            assert presentation.summary_steps != drill.protocol[:3]
