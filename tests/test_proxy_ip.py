"""Proxy-aware client IPs (web.trusted_proxies): behind Railway (or any
reverse proxy) every request arrives from the proxy's address, so without
X-Forwarded-For handling max_active_jobs_per_ip=3 silently caps the WHOLE
site. With trusted_proxies configured, request.client.host is rewritten to
the real visitor; with it disabled, the header is ignored (spoof-proof when
clients can reach the app directly)."""

from __future__ import annotations

import threading

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from swinglab.config import Config
from swinglab.web import jobs as jobs_module
from swinglab.web.app import create_app
from tests.test_web import fake_analyze_ok, make_blocking_fake, wait_for


def upload_as(client, forwarded_for=None):
    headers = {"X-Forwarded-For": forwarded_for} if forwarded_for else {}
    return client.post(
        "/upload",
        files={"video": ("swing.mov", b"fake video bytes", "video/quicktime")},
        data={"club": "iron"},
        headers=headers,
        follow_redirects=False,
    )


def make_client(tmp_path, monkeypatch, trusted, blocking=False):
    release, started = threading.Event(), threading.Event()
    fake = (
        make_blocking_fake(release, started) if blocking else fake_analyze_ok
    )
    monkeypatch.setattr(jobs_module, "analyze_video", fake)
    cfg = Config()
    cfg.web["max_active_jobs_per_ip"] = 1
    cfg.web["trusted_proxies"] = trusted
    client = TestClient(create_app(cfg, sessions_dir=tmp_path / "s"))
    return client, release, started


def test_forwarded_for_distinguishes_visitors_behind_proxy(tmp_path, monkeypatch):
    client, release, started = make_client(
        tmp_path, monkeypatch, trusted="*", blocking=True
    )
    try:
        first = upload_as(client, "203.0.113.5")
        assert first.status_code == 303
        assert started.wait(timeout=5)

        # A DIFFERENT visitor behind the same proxy is not blocked...
        second = upload_as(client, "198.51.100.7")
        assert second.status_code == 303

        # ...while the same visitor hitting their limit still is.
        assert upload_as(client, "203.0.113.5").status_code == 429
    finally:
        release.set()
    wait_for(client, first.headers["location"].rsplit("/", 1)[-1])
    wait_for(client, second.headers["location"].rsplit("/", 1)[-1])


def test_real_client_ip_stored_on_job(tmp_path, monkeypatch):
    client, _, _ = make_client(tmp_path, monkeypatch, trusted="*")
    resp = upload_as(client, "203.0.113.5")
    job_id = resp.headers["location"].rsplit("/", 1)[-1]
    wait_for(client, job_id)
    assert client.app.state.jobs.get(job_id).client_ip == "203.0.113.5"


def test_leftmost_forwarded_hop_wins_with_wildcard_trust(tmp_path, monkeypatch):
    # Proxy chains append: "client, cdn, lb". With "*" everything after the
    # first hop is trusted, so the original client is what limits key on.
    client, _, _ = make_client(tmp_path, monkeypatch, trusted="*")
    resp = upload_as(client, "203.0.113.5, 10.0.0.2, 10.0.0.3")
    job_id = resp.headers["location"].rsplit("/", 1)[-1]
    wait_for(client, job_id)
    assert client.app.state.jobs.get(job_id).client_ip == "203.0.113.5"


def test_disabled_trust_ignores_the_header(tmp_path, monkeypatch):
    """trusted_proxies "" = the header is attacker-controlled input, not
    identity: two different X-Forwarded-For values still share the socket
    peer's one per-IP slot."""
    client, release, started = make_client(
        tmp_path, monkeypatch, trusted="", blocking=True
    )
    try:
        first = upload_as(client, "203.0.113.5")
        assert first.status_code == 303
        assert started.wait(timeout=5)
        assert upload_as(client, "198.51.100.7").status_code == 429
    finally:
        release.set()
    wait_for(client, first.headers["location"].rsplit("/", 1)[-1])


def test_untrusted_source_cannot_spoof_with_ip_allowlist(tmp_path, monkeypatch):
    # Trust is a specific proxy IP that is NOT the test transport's address:
    # the header must be ignored entirely.
    client, _, _ = make_client(tmp_path, monkeypatch, trusted=["10.9.9.9"])
    resp = upload_as(client, "203.0.113.5")
    job_id = resp.headers["location"].rsplit("/", 1)[-1]
    wait_for(client, job_id)
    assert client.app.state.jobs.get(job_id).client_ip == "testclient"
