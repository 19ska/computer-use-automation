"""Normalizes a successful LocatorResolution into one of the three
artifact-compatible locator representations (RoleLocator, LabelTextLocator,
or CssLocator with a REAL CSS selector) for persistence in discovery
evidence.

This is the boundary where internal-only resolution mechanisms — in
particular the associated-control XPath fallback used for legacy markup
that has no accessible name (see target_resolution._associated_control_xpath)
— get translated into the small locator vocabulary the reusable capability
artifact schema actually supports. An XPath string must never be persisted
as `{"kind": "css", "selector": "xpath=..."}`; discovery may use whatever
resolution mechanism it needs internally, but only role/name, label/text,
or genuine CSS ever reach evidence.

Priority, applied in order:
1. The resolved strategy was already a RoleLocator -> preserve directly.
2. The resolved strategy was already a LabelTextLocator -> preserve directly.
3. The resolved strategy was already a genuine (non-xpath) CssLocator -> preserve directly.
4. The resolved strategy was an internal xpath= CssLocator -> inspect the
   resolved element itself and derive a safe #id or [name="..."] selector.
   If neither is available, return None — the caller records that as "no
   representable locator for this step," never a guess.
"""

from __future__ import annotations

import re

from cua.artifact.schema import CssLocator, LabelTextLocator, LocatorStrategy, RoleLocator
from cua.replay.locators import LocatorResolution, SupportsLocatorProtocol

_SAFE_CSS_IDENT = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")


def _escape_css_string(value: str) -> str:
    """Escapes `value` for safe embedding inside a double-quoted CSS
    string literal (e.g. the value inside `[name="..."]`). Backslash and
    the quote character are the only two characters that can break out of
    a CSS string literal, so escaping just those two is sufficient to
    prevent selector injection — this does not attempt full CSS string
    normalization beyond that safety property.
    """
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _derive_css_locator_from_element(locator: SupportsLocatorProtocol) -> CssLocator | None:
    try:
        element_id = locator.get_attribute("id")
    except Exception:  # noqa: BLE001 - treat as unavailable, not a crash
        element_id = None
    if element_id and _SAFE_CSS_IDENT.match(element_id):
        return CssLocator(selector=f"#{element_id}")

    try:
        name = locator.get_attribute("name")
    except Exception:  # noqa: BLE001 - treat as unavailable, not a crash
        name = None
    if name:
        return CssLocator(selector=f'[name="{_escape_css_string(name)}"]')

    return None


def resolve_artifact_locator(resolution: LocatorResolution) -> LocatorStrategy | None:
    strategy = resolution.strategy

    if isinstance(strategy, (RoleLocator, LabelTextLocator)):
        return strategy

    if isinstance(strategy, CssLocator):
        if not strategy.selector.startswith("xpath="):
            return strategy
        return _derive_css_locator_from_element(resolution.locator)

    return None  # pragma: no cover - exhaustive over the current LocatorStrategy union
