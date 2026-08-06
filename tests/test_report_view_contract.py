from __future__ import annotations

import pytest

from swinglab.report_view import (
    UnsupportedReportViewVersion,
    report_view_from_dict,
    report_view_to_dict,
)
from tests.report_view_fixtures import report_view_payload


@pytest.mark.parametrize(
    "name",
    (
        "coaching-improve-clear",
        "coaching-protect-clear",
        "coaching-limited-rendered",
        "coaching-limited-visual-unavailable",
        "capture-only",
    ),
)
def test_report_view_v1_fixtures_round_trip(name):
    payload = report_view_payload(name)
    view = report_view_from_dict(payload)
    assert report_view_to_dict(view) == payload


def test_unknown_report_view_version_fails_closed():
    payload = report_view_payload()
    payload["version"] = "report-view-v2"
    with pytest.raises(UnsupportedReportViewVersion):
        report_view_from_dict(payload)
