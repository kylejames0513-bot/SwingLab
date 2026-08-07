import logging

from swinglab.web.access_log import (
    RedactAccessLogQueryFilter,
    access_log_config,
)


def test_access_log_filter_removes_query_but_preserves_request_metadata():
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        (
            "127.0.0.1:1234",
            "GET",
            "/app/auth/callback?challenge_id=secret&code=code&state=state&host=host",
            "1.1",
            302,
        ),
        None,
    )

    assert RedactAccessLogQueryFilter().filter(record) is True

    assert record.args == (
        "127.0.0.1:1234",
        "GET",
        "/app/auth/callback",
        "1.1",
        302,
    )
    rendered = record.getMessage()
    assert "/app/auth/callback" in rendered
    assert "challenge_id" not in rendered
    assert "hmac" not in rendered
    assert "host" not in rendered
    assert "code" not in rendered
    assert "state" not in rendered


def test_access_log_filter_leaves_plain_path_unchanged():
    args = ("client", "GET", "/healthz", "1.1", 200)
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        args,
        None,
    )

    assert RedactAccessLogQueryFilter().filter(record) is True
    assert record.args == args


def test_access_log_config_is_isolated_and_wires_filter():
    first = access_log_config()
    second = access_log_config()

    assert first is not second
    assert first["handlers"]["access"]["filters"] == ["redact_query_string"]
    assert second["handlers"]["access"]["filters"] == [
        "redact_query_string"
    ]
