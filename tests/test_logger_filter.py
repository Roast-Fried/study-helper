"""LOG-SYS-3 회귀 방지: SensitiveFilter 가 PII/OAuth 를 마스킹하는지."""

from __future__ import annotations

import logging

from src.logger import SensitiveFilter


def _make_record(msg: str, args: tuple | dict | None = None) -> logging.LogRecord:
    return logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="x",
        lineno=1,
        msg=msg,
        args=args,
        exc_info=None,
    )


def test_filter_masks_plain_kv() -> None:
    f = SensitiveFilter()
    record = _make_record("user_email=foo@bar.com extra")
    f.filter(record)
    assert "foo@bar.com" not in record.msg
    assert "REDACTED" in record.msg


def test_filter_masks_urlencoded_kv() -> None:
    f = SensitiveFilter()
    record = _make_record("oauth_signature%3DabCdEf123 trailing")
    f.filter(record)
    assert "abCdEf123" not in record.msg


def test_filter_masks_args_tuple() -> None:
    f = SensitiveFilter()
    record = _make_record("body=%s", ("user_email=x@y.com",))
    f.filter(record)
    assert "x@y.com" not in record.args[0]


def test_filter_idempotent() -> None:
    """이미 마스킹된 값은 재적용해도 안전해야 한다."""
    f = SensitiveFilter()
    once = "***REDACTED***"
    record = _make_record(once)
    f.filter(record)
    assert record.msg == once


def test_filter_passes_through_non_sensitive() -> None:
    f = SensitiveFilter()
    original = "safe message with no secrets"
    record = _make_record(original)
    f.filter(record)
    assert record.msg == original


def test_filter_applies_to_propagated_records_via_handler(tmp_path, monkeypatch) -> None:
    """회귀 방지: Python logging 은 로거에 붙인 filter 가 propagate 레코드에
    적용되지 않는다. handler 에 filter 를 붙여야 child 로거의 로그도 마스킹된다.

    이 테스트는 child 로거(study_helper.downloader) 에서 민감 값을 찍고,
    실제 파일에 마스킹된 내용이 기록되는지 확인한다.
    """
    import importlib
    import logging as _std_logging

    import src.logger as logger_mod

    # 기존 app logger 초기화 (다른 테스트에서 이미 부트스트랩됐을 수 있음)
    if logger_mod._app_logger is not None:
        for h in list(logger_mod._app_logger.handlers):
            h.close()
            logger_mod._app_logger.removeHandler(h)
        for f in list(logger_mod._app_logger.filters):
            logger_mod._app_logger.removeFilter(f)
    logger_mod._app_logger = None

    # logs dir 을 tmp_path 로 강제
    monkeypatch.setattr(logger_mod, "_logs_dir", lambda: tmp_path)
    importlib.reload(logger_mod)
    monkeypatch.setattr(logger_mod, "_logs_dir", lambda: tmp_path)

    # 부트스트랩 + 민감 로그 기록
    app = logger_mod.get_logger("main")
    child = logger_mod.get_logger("downloader")  # study_helper.downloader
    child.info("oauth_signature=TOPSECRET123 body=xyz")

    for h in app.handlers:
        h.flush()

    content = (tmp_path / "study_helper.log").read_text(encoding="utf-8")
    assert "TOPSECRET123" not in content, f"PII masking 실패 — child 로거 propagate 경로에서 마스킹 누락: {content[-200:]}"
    assert "REDACTED" in content or "oauth_signature=***" in content

    # 다음 테스트 오염 방지 — app logger 다시 초기화
    for h in list(app.handlers):
        h.close()
        app.removeHandler(h)
    logger_mod._app_logger = None
    _std_logging.Logger.manager.loggerDict.pop("study_helper", None)
