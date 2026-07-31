"""Gear shop tests: inert-until-configured, the /shop page, flag-driven
recommendations on finished analyses, coaching flag extraction, and the
product cache. The Storefront API itself is faked (shop._fetch) — these
tests exercise the plumbing around it.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from swinglab.coaching import (
    FLAG_CONSISTENCY,
    FLAG_HIP_SLIDE,
    FLAG_SWAY,
    FLAG_TEMPO,
    flag_keys,
)
from swinglab.config import Config
from swinglab.ffmpeg import VideoInfo
from swinglab.pipeline import SessionResult
from swinglab.web import jobs as jobs_module
from swinglab.web import shop
from swinglab.web.app import create_app


def product(title, tags, price="20.00"):
    return {
        "title": title,
        "url": f"https://shop.example/products/{title.lower().replace(' ', '-')}",
        "price_display": f"${price}",
        "image": None,
        "image_alt": None,
        "description": f"{title} description",
        "tags": set(tags),
        "available": True,
    }


CATALOG = [
    product("Tempo Wand", ["swinglab", "swinglab:tempo"], "79.00"),
    product("Swing Metronome", ["swinglab", "swinglab:tempo", "swinglab:consistency"]),
    product("Alignment Sticks", ["swinglab", "swinglab:sway"]),
    product("Hip Band", ["swinglab", "swinglab:hip-slide"]),
    product("Logo Cap", ["swinglab", "swinglab:general"]),
]


@pytest.fixture(autouse=True)
def fresh_cache():
    shop.clear_cache()
    yield
    shop.clear_cache()


@pytest.fixture
def shop_env(monkeypatch):
    monkeypatch.setenv("SHOPIFY_STORE_DOMAIN", "teststore.myshopify.com")
    monkeypatch.setattr(shop, "_fetch", lambda: [dict(p) for p in CATALOG])


def metrics_payload(tempo=3.0, sway=0.1, slide=0.1, swings=1, tempo_std=0.1):
    return {
        "swings": [
            {
                "metrics": {
                    "tempo_ratio": tempo,
                    "head_sway_backswing_sw": sway,
                    "hip_slide_backswing_sw": slide,
                }
            }
            for _ in range(swings)
        ],
        "session_stats": {"tempo_ratio": {"mean": tempo, "std": tempo_std}},
    }


def make_fake_analyze(metrics: dict):
    def fake(video_path, out_dir=None, hand="right", manual_strikes=None,
             cfg=None, keep_work=False, fast=False, log=print, progress=None,
             angle="face-on", club=None, level=None, replay_locked=False):
        session_dir = Path(out_dir) / Path(video_path).stem
        session_dir.mkdir(parents=True)
        report = session_dir / "report.html"
        report.write_text("<html><body>fake report</body></html>")
        metrics_path = session_dir / "metrics.json"
        metrics_path.write_text(json.dumps(metrics))
        info = VideoInfo(Path(video_path), 20.0, 854, 480, 30.0, 0, None, True)
        return SessionResult(
            session_dir=session_dir, report_path=report,
            metrics_path=metrics_path, video=info,
            swings=[{}] * max(1, len(metrics.get("swings", []))), stats={},
        )

    return fake


def make_client(tmp_path, monkeypatch, metrics=None):
    monkeypatch.setattr(
        jobs_module, "analyze_video", make_fake_analyze(metrics or metrics_payload())
    )
    return TestClient(create_app(Config(), sessions_dir=tmp_path / "sessions"))


def finish_upload(client) -> str:
    resp = client.post(
        "/upload",
        files={"video": ("swing.mov", b"fake video bytes", "video/quicktime")},
        data={"hand": "right", "strikes": ""},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    job_id = resp.headers["location"].rsplit("/", 1)[-1]
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if client.get(f"/api/session/{job_id}").json()["status"] in ("done", "failed"):
            return job_id
        time.sleep(0.02)
    raise TimeoutError("job never finished")


# -- inert until configured -------------------------------------------------

def test_shop_inert_without_env(tmp_path, monkeypatch):
    monkeypatch.delenv("SHOPIFY_STORE_DOMAIN", raising=False)
    monkeypatch.delenv("SHOPIFY_STOREFRONT_TOKEN", raising=False)
    client = make_client(tmp_path, monkeypatch)
    assert not shop.enabled()
    assert client.get("/shop").status_code == 404
    assert 'href="/shop"' not in client.get("/").text


def test_shop_needs_only_the_public_store_domain(monkeypatch):
    monkeypatch.setenv("SHOPIFY_STORE_DOMAIN", "teststore.myshopify.com")
    monkeypatch.delenv("SHOPIFY_STOREFRONT_TOKEN", raising=False)
    assert shop.enabled()


def test_shop_disabled_in_config(tmp_path, monkeypatch, shop_env):
    cfg = Config()
    cfg.shop["enabled"] = False
    monkeypatch.setattr(jobs_module, "analyze_video", make_fake_analyze({}))
    client = TestClient(create_app(cfg, sessions_dir=tmp_path / "sessions"))
    assert client.get("/shop").status_code == 404
    assert 'href="/shop"' not in client.get("/").text


# -- the shop page ----------------------------------------------------------

def test_shop_page_lists_products(tmp_path, monkeypatch, shop_env):
    client = make_client(tmp_path, monkeypatch)
    html = client.get("/shop").text
    for item in CATALOG:
        assert item["title"] in html
    assert "$79.00" in html
    assert 'href="/shop"' in client.get("/").text  # nav link appears


def test_shop_page_survives_api_failure(tmp_path, monkeypatch, shop_env):
    client = make_client(tmp_path, monkeypatch)
    assert "Tempo Wand" in client.get("/shop").text  # primes the cache

    def boom():
        raise OSError("shopify is down")

    monkeypatch.setattr(shop, "_fetch", boom)
    shop.clear_cache()
    html = client.get("/shop").text  # no cache, API down -> friendly empty page
    assert "restocked" in html


def test_stale_cache_beats_api_failure(monkeypatch, shop_env):
    cfg = Config()
    cfg.shop["cache_minutes"] = 0  # every call wants a refetch
    assert shop.fetch_products(cfg)[0]["title"] == "Tempo Wand"

    def boom():
        raise OSError("shopify is down")

    monkeypatch.setattr(shop, "_fetch", boom)
    assert [p["title"] for p in shop.fetch_products(cfg)][0] == "Tempo Wand"


def test_products_are_cached(monkeypatch, shop_env):
    calls = []

    def counting_fetch():
        calls.append(1)
        return [dict(p) for p in CATALOG]

    monkeypatch.setattr(shop, "_fetch", counting_fetch)
    cfg = Config()
    shop.fetch_products(cfg)
    shop.fetch_products(cfg)
    assert len(calls) == 1


# -- coaching flags ---------------------------------------------------------

def test_flag_keys_each_threshold():
    cfg = Config()
    assert flag_keys(metrics_payload(), cfg) == []
    assert flag_keys(metrics_payload(tempo=2.0), cfg) == [FLAG_TEMPO]
    assert flag_keys(metrics_payload(sway=0.5), cfg) == [FLAG_SWAY]
    assert flag_keys(metrics_payload(slide=0.5), cfg) == [FLAG_HIP_SLIDE]
    assert flag_keys(metrics_payload(swings=3, tempo_std=0.6), cfg) == [
        FLAG_CONSISTENCY
    ]
    # one noisy swing is enough to flag; single swing never flags consistency
    assert FLAG_CONSISTENCY not in flag_keys(
        metrics_payload(swings=1, tempo_std=0.6), cfg
    )


def test_flag_keys_tolerates_partial_payloads():
    cfg = Config()
    assert flag_keys({}, cfg) == []
    assert flag_keys({"swings": [{"metrics": {"tempo_ratio": None}}]}, cfg) == []
    assert flag_keys({"swings": [{}], "session_stats": {}}, cfg) == []


# -- recommendations --------------------------------------------------------

def test_recommend_round_robins_flags_then_pads_with_general():
    cfg = Config()
    picks = shop.recommend(CATALOG, [FLAG_TEMPO, FLAG_SWAY], cfg)
    titles = [p["title"] for p in picks]
    # one per flag first (tempo, sway), then the second tempo item — not
    # three tempo products crowding out sway
    assert titles == ["Tempo Wand", "Alignment Sticks", "Swing Metronome"]

    picks = shop.recommend(CATALOG, [], cfg)
    assert [p["title"] for p in picks] == ["Logo Cap"]  # general only


def test_done_page_recommends_flagged_gear(tmp_path, monkeypatch, shop_env):
    client = make_client(
        tmp_path, monkeypatch, metrics=metrics_payload(tempo=2.0)
    )
    job_id = finish_upload(client)
    html = client.get(f"/session/{job_id}").text
    assert "Train what the report flagged" in html
    assert "Tempo Wand" in html
    assert 'href="/shop"' in html


def test_done_page_clean_swing_gets_general_gear(tmp_path, monkeypatch, shop_env):
    client = make_client(tmp_path, monkeypatch)  # no flags
    job_id = finish_upload(client)
    html = client.get(f"/session/{job_id}").text
    assert "Logo Cap" in html
    assert "Tempo Wand" not in html


def test_done_page_tolerates_unreadable_metrics(tmp_path, monkeypatch, shop_env):
    client = make_client(tmp_path, monkeypatch, metrics={})
    job_id = finish_upload(client)
    html = client.get(f"/session/{job_id}").text
    assert "Results ready" in html  # page renders; general gear still shows
    assert "Logo Cap" in html


def test_done_page_without_shop_has_no_gear(tmp_path, monkeypatch):
    monkeypatch.delenv("SHOPIFY_STORE_DOMAIN", raising=False)
    monkeypatch.delenv("SHOPIFY_STOREFRONT_TOKEN", raising=False)
    client = make_client(tmp_path, monkeypatch, metrics=metrics_payload(tempo=2.0))
    job_id = finish_upload(client)
    assert "Train what the report flagged" not in client.get(f"/session/{job_id}").text


def test_storefront_collection_fetch_is_tokenless(monkeypatch):
    """The public Gear collection must not inherit an unrelated bad token."""
    from swinglab.web import shop as shop_module

    captured = {}

    def fake_urlopen(request, timeout=0):
        captured["headers"] = dict(request.header_items())
        captured["body"] = json.loads(request.data)
        import io
        return io.BytesIO(
            b'{"data": {"collection": {"products": {"edges": []}}}}'
        )

    monkeypatch.setenv("SHOPIFY_STORE_DOMAIN", "example.myshopify.com")
    # A value left over from the broken configuration is deliberately ignored.
    monkeypatch.setenv("SHOPIFY_STOREFRONT_TOKEN", "shpat_not-a-storefront-token")
    monkeypatch.setattr(shop_module.urllib.request, "urlopen", fake_urlopen)

    assert shop_module._fetch() == []
    headers = {k.lower(): v for k, v in captured["headers"].items()}
    assert "shopify-storefront-private-token" not in headers
    assert "x-shopify-storefront-access-token" not in headers
    assert 'collection(handle: "swinglab-gear")' in captured["body"]["query"]


def test_storefront_missing_gear_collection_is_empty(monkeypatch):
    from swinglab.web import shop as shop_module
    import io

    monkeypatch.setenv("SHOPIFY_STORE_DOMAIN", "example.myshopify.com")
    monkeypatch.setattr(
        shop_module.urllib.request,
        "urlopen",
        lambda request, timeout=0: io.BytesIO(b'{"data": {"collection": null}}'),
    )
    assert shop_module._fetch() == []
