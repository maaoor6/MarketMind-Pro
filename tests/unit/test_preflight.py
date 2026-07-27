"""Unit tests for the boot-time readiness self-check (Phase 5.4)."""

from src.utils.preflight import PreflightItem, evaluate


def test_all_ok_report_passes():
    report = evaluate(
        [
            PreflightItem("a", True, critical=True),
            PreflightItem("b", True, critical=False),
        ]
    )
    assert report.ok
    assert report.failures == []


def test_critical_failure_fails_report():
    report = evaluate(
        [
            PreflightItem("telegram", False, critical=True),
            PreflightItem("db", True, critical=False),
        ]
    )
    assert not report.ok
    assert [i.name for i in report.failures] == ["telegram"]


def test_noncritical_failure_still_passes():
    # A non-critical gap (e.g. DB down) is a warning, not a boot blocker.
    report = evaluate(
        [
            PreflightItem("telegram", True, critical=True),
            PreflightItem("database", False, critical=False),
        ]
    )
    assert report.ok
    assert [i.name for i in report.failures] == ["database"]


def test_empty_report_ok():
    assert evaluate([]).ok
