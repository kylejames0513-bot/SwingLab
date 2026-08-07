from __future__ import annotations

from pathlib import Path

from swinglab.config import Config
from swinglab.report_html import write_report_document_html
from swinglab.report_view import GUIDED_REPORT_PRESENTATION_VERSION
from swinglab.web import jobs as jobs_module
from swinglab.web.app import create_app
from tests.report_bundle_fixtures import write_test_report_html


def test_app_composes_the_production_guided_writer_for_default_guided_jobs(
    tmp_path: Path, monkeypatch
):
    seen: dict[str, object] = {}

    def record_analyzer(*args, **kwargs):
        del args
        seen["guided_html_writer"] = kwargs["guided_html_writer"]
        raise RuntimeError("stop after guided renderer composition")

    monkeypatch.setattr(jobs_module, "analyze_video", record_analyzer)
    cfg = Config()
    cfg.report["guided_presentation_enabled"] = True
    app = create_app(
        cfg,
        sessions_dir=tmp_path / "sessions",
        start_shopify_sync_worker=False,
    )
    manager = app.state.jobs
    try:
        job = manager.create_session()
        persisted = manager.get(job.id)
        assert persisted is not None
        assert (
            persisted.report_presentation_version
            == GUIDED_REPORT_PRESENTATION_VERSION
        )

        manager._run(job, tmp_path / "source.mov")

        assert seen["guided_html_writer"] is write_report_document_html
        assert seen["guided_html_writer"] is not write_test_report_html
    finally:
        manager._pool.shutdown(wait=True)
        manager._conn.close()
