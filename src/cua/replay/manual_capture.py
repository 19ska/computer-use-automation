"""Captures human click/input/change events on the live page during a
human-intervention window — evidence that a real person interacted with
the SAME session. Never records a typed/selected VALUE: only the target
element's tag/id/name, and for clicks only, a short static label (e.g. a
button's visible text) — never `.value`, regardless of field type.
"""

from __future__ import annotations

from typing import Any, Callable, Protocol


class SupportsManualCapturePage(Protocol):
    def expose_function(self, name: str, callback: Callable[[dict[str, Any]], None]) -> None: ...
    def evaluate(self, expression: str) -> Any: ...


_INSTALL_JS = """() => {
  if (window.__cuaManualCaptureInstalled__) return;
  window.__cuaManualCaptureInstalled__ = true;
  window.__cuaManualCaptureEnabled__ = true;
  const report = (type) => (event) => {
    if (!window.__cuaManualCaptureEnabled__) return;
    const target = event.target || {};
    const tag = target.tagName ? target.tagName.toLowerCase() : null;
    const isTextEntry = tag === 'input' || tag === 'textarea';
    window.__cuaReportManualEvent__({
      type: type,
      tag: tag,
      id: target.id || null,
      name: target.name || null,
      text: (!isTextEntry && type === 'click' && target.innerText)
        ? target.innerText.trim().slice(0, 50)
        : null,
    });
  };
  document.addEventListener('click', report('click'), true);
  document.addEventListener('input', report('input'), true);
  document.addEventListener('change', report('change'), true);
}"""

_DISABLE_JS = "() => { window.__cuaManualCaptureEnabled__ = false; }"


def install(page: SupportsManualCapturePage, on_event: Callable[[dict[str, Any]], None]) -> None:
    page.expose_function("__cuaReportManualEvent__", on_event)
    page.evaluate(_INSTALL_JS)


def disable(page: SupportsManualCapturePage) -> None:
    try:
        page.evaluate(_DISABLE_JS)
    except Exception:  # noqa: BLE001 - page may have navigated away already (e.g. after a form submit)
        pass
