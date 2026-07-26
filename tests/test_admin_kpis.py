"""GET /admin/kpis: the operator's KPI endpoint.

Inert until configured, like everything else: without SWINGLAB_ADMIN_TOKEN
the route answers 404 with a body identical to the framework's own
unknown-route 404 — its existence is invisible. With the variable set, a
wrong bearer gets the same 404 (never a 401/403 that would confirm the
route exists), the comparison runs in constant time via hmac.compare_digest,
and only the exact token gets the numbers.
"""

from __future__ import annotations

import hmac as hmac_module

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from swinglab.config import Config
from swinglab.web import jobs as jobs_module
from swinglab.web.app import create_app

from tests.test_web import fake_analyze_ok

TOKEN = "test-admin-token-abcdef"
KPI_KEYS = {
    "activation_rate", "w1_refilm_rate", "free_to_pro_rate",
    "weekly_retained_filmers", "gear_attach_per_100_reports",
}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs_module, "analyze_video", fake_analyze_ok)
    monkeypatch.delenv("SWINGLAB_ADMIN_TOKEN", raising=False)
    cfg = Config()
    cfg.web["require_account"] = True
    return TestClient(create_app(cfg, sessions_dir=tmp_path / "s"))


def get_kpis(client, token=None, path="/admin/kpis"):
    headers = {"Authorization": f"Bearer {token}"} if token is not None else {}
    return client.get(path, headers=headers)


def test_404_when_env_is_unset(client):
    assert get_kpis(client).status_code == 404
    assert get_kpis(client, token="anything").status_code == 404


def test_404_indistinguishable_from_unknown_route(client, monkeypatch):
    unknown = client.get("/admin/does-not-exist")
    # Unset env: same status, same body as a route that does not exist.
    resp = get_kpis(client)
    assert (resp.status_code, resp.json()) == (
        unknown.status_code, unknown.json(),
    )
    # Set env + wrong token: still the same 404, never a 401/403.
    monkeypatch.setenv("SWINGLAB_ADMIN_TOKEN", TOKEN)
    wrong = get_kpis(client, token="wrong-token")
    assert (wrong.status_code, wrong.json()) == (
        unknown.status_code, unknown.json(),
    )
    assert get_kpis(client).status_code == 404  # no header at all
    assert "www-authenticate" not in {k.lower() for k in wrong.headers}


def test_wrong_scheme_and_non_ascii_token_fail_cleanly(client, monkeypatch):
    monkeypatch.setenv("SWINGLAB_ADMIN_TOKEN", TOKEN)
    resp = client.get("/admin/kpis", headers={"Authorization": f"Basic {TOKEN}"})
    assert resp.status_code == 404
    # A crafted non-ASCII bearer (sent as raw latin-1 bytes, the way it
    # reaches the ASGI layer) must be a clean 404, never a 500.
    resp = client.get(
        "/admin/kpis",
        headers={"Authorization": "Bearer t\xf6ken".encode("latin-1")},
    )
    assert resp.status_code == 404


def test_correct_token_returns_the_five_kpis(client, monkeypatch):
    monkeypatch.setenv("SWINGLAB_ADMIN_TOKEN", TOKEN)
    resp = get_kpis(client, token=TOKEN)
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["window_days"] == 90.0
    assert set(payload["kpis"]) == KPI_KEYS
    for entry in payload["kpis"].values():
        assert {"label", "value", "unit", "numerator", "denominator",
                "reason"} <= set(entry)
        # Fresh install: honest Nones with reasons, no fabricated numbers
        # (except a true-zero count, which carries no reason).
        assert entry["value"] is None or entry["reason"] is None


def test_since_param(client, monkeypatch):
    monkeypatch.setenv("SWINGLAB_ADMIN_TOKEN", TOKEN)
    resp = get_kpis(client, token=TOKEN, path="/admin/kpis?since=30")
    assert resp.status_code == 200
    assert resp.json()["window_days"] == 30.0
    assert get_kpis(client, token=TOKEN, path="/admin/kpis?since=abc").status_code == 400
    assert get_kpis(client, token=TOKEN, path="/admin/kpis?since=-5").status_code == 400
    # An explicit 0 is rejected, never silently swapped for the default,
    # and non-finite floats can't reach the window math (or the JSON).
    assert get_kpis(client, token=TOKEN, path="/admin/kpis?since=0").status_code == 400
    assert get_kpis(client, token=TOKEN, path="/admin/kpis?since=nan").status_code == 400
    assert get_kpis(client, token=TOKEN, path="/admin/kpis?since=inf").status_code == 400
    # An empty since is simply absent: the default window applies.
    resp = get_kpis(client, token=TOKEN, path="/admin/kpis?since=")
    assert (resp.status_code, resp.json()["window_days"]) == (200, 90.0)


def test_comparison_is_constant_time(client, monkeypatch):
    """The bearer check must run through hmac.compare_digest — never an
    early-exit string equality an attacker could time."""
    monkeypatch.setenv("SWINGLAB_ADMIN_TOKEN", TOKEN)
    calls = []
    real = hmac_module.compare_digest

    def spy(a, b):
        calls.append((bytes(a), bytes(b)))
        return real(a, b)

    monkeypatch.setattr("swinglab.web.app.hmac.compare_digest", spy)
    assert get_kpis(client, token="wrong-token").status_code == 404
    assert (b"wrong-token", TOKEN.encode()) in calls
    calls.clear()
    assert get_kpis(client, token=TOKEN).status_code == 200
    assert (TOKEN.encode(), TOKEN.encode()) in calls
