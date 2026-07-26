from __future__ import annotations

from swinglab.config import DEFAULTS, Config


def test_defaults_without_file():
    cfg = Config()
    assert cfg.brand["name"] == "CaddieInsight"
    assert cfg.detection["audio_height"] == 0.30
    assert cfg.coaching["tempo_target"] == 3.0


def test_partial_yaml_deep_merges(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "brand:\n  name: AceCoach\n  primary_color: '#123456'\n"
        "detection:\n  audio_height: 0.5\n"
    )
    cfg = Config.load(path)
    assert cfg.brand["name"] == "AceCoach"
    assert cfg.brand["primary_color"] == "#123456"
    # untouched keys keep defaults
    assert cfg.brand["disclaimer"] == DEFAULTS["brand"]["disclaimer"]
    assert cfg.detection["audio_height"] == 0.5
    assert cfg.detection["min_gap_s"] == 4.0


def test_missing_explicit_defaults(tmp_path):
    empty = tmp_path / "empty.yaml"
    empty.write_text("")
    cfg = Config.load(empty)
    assert cfg.data == DEFAULTS
