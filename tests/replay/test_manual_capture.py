"""Tests for cua.replay.manual_capture — installing/disabling the
browser-side click/input/change event capture used during human
intervention. No real browser: verifies the Python-side wiring
(expose_function + evaluate calls), not actual DOM behavior.
"""

from cua.replay import manual_capture

from .fakes import FakePage


def test_install_exposes_the_report_function_and_injects_the_capture_script():
    page = FakePage()
    received = []

    manual_capture.install(page, received.append)

    assert "__cuaReportManualEvent__" in page.exposed_functions
    page.exposed_functions["__cuaReportManualEvent__"]({"type": "click"})
    assert received == [{"type": "click"}]
    assert len(page.evaluate_calls) == 1
    script = page.evaluate_calls[0]
    assert "addEventListener" in script
    assert "click" in script and "input" in script and "change" in script


def test_install_script_never_reads_element_value():
    page = FakePage()
    manual_capture.install(page, lambda e: None)
    script = page.evaluate_calls[0]
    # The capture script must never reference .value at all — the only
    # thing ever reported for click is a short static innerText label.
    assert ".value" not in script


def test_disable_evaluates_the_disable_script():
    page = FakePage()
    manual_capture.disable(page)
    assert len(page.evaluate_calls) == 1
    assert "__cuaManualCaptureEnabled__ = false" in page.evaluate_calls[0]


def test_disable_never_raises_if_page_already_navigated_away():
    page = FakePage()
    page.evaluate_error = RuntimeError("execution context was destroyed")
    manual_capture.disable(page)  # must not raise
