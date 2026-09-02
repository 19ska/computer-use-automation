"""Resolves an artifact ElementTarget into a real Playwright Locator.

Generic on purpose: this module knows nothing about ParaBank. It only
knows how to turn a RoleLocator/LabelTextLocator/CssLocator into a
Playwright Locator, and how to walk a target's fallback chain safely:

- try strategies in artifact order,
- accept a strategy only if it resolves to exactly one VISIBLE element,
- never choose randomly between multiple candidates,
- if every strategy is exhausted without a unique visible match, fail
  with a diagnosable error distinguishing "nothing matched" from
  "something was ambiguous".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from cua.artifact.schema import CssLocator, ElementTarget, LabelTextLocator, LocatorStrategy, RoleLocator


class LocatorNotFoundError(Exception):
    """No strategy in the target's fallback chain matched anything visible."""


class LocatorAmbiguousError(Exception):
    """At least one strategy matched more than one visible element, and no
    strategy resolved to exactly one."""


class SupportsLocatorProtocol(Protocol):
    """The subset of Playwright's Locator interface this module uses.
    Exists so tests can supply lightweight fakes instead of a real browser.
    """

    def count(self) -> int: ...
    def nth(self, index: int) -> "SupportsLocatorProtocol": ...
    def is_visible(self) -> bool: ...
    def inner_text(self) -> str: ...
    def get_attribute(self, name: str) -> str | None: ...


class SupportsPageProtocol(Protocol):
    """The subset of Playwright's Page interface this module uses."""

    def get_by_role(self, role: str, *, name: str, exact: bool) -> SupportsLocatorProtocol: ...
    def get_by_text(self, text: str, *, exact: bool) -> SupportsLocatorProtocol: ...
    def locator(self, selector: str) -> SupportsLocatorProtocol: ...


@dataclass
class LocatorResolution:
    locator: SupportsLocatorProtocol
    strategy_index: int
    strategy: LocatorStrategy

    @property
    def strategy_description(self) -> str:
        return f"{self.strategy.kind}#{self.strategy_index}"


def resolve_target(page: SupportsPageProtocol, target: ElementTarget) -> LocatorResolution:
    attempts: list[tuple[int, LocatorStrategy, int]] = []

    for index, strategy in enumerate(target.strategies):
        locator = _build_locator(page, strategy)
        try:
            count = locator.count()
        except Exception:  # noqa: BLE001 - a broken locator counts as "no match"
            count = 0
        visible_count = _count_visible(locator, count)
        attempts.append((index, strategy, visible_count))

        if visible_count == 1:
            return LocatorResolution(locator=locator, strategy_index=index, strategy=strategy)
        # zero or ambiguous: fall through and try the next strategy

    if any(visible_count > 1 for _, _, visible_count in attempts):
        raise LocatorAmbiguousError(
            f"target '{target.description}': every strategy either matched nothing "
            f"or was ambiguous; attempts={_describe_attempts(attempts)}"
        )
    raise LocatorNotFoundError(
        f"target '{target.description}': no strategy matched any visible element; "
        f"attempts={_describe_attempts(attempts)}"
    )


def _build_locator(page: SupportsPageProtocol, strategy: LocatorStrategy) -> SupportsLocatorProtocol:
    if isinstance(strategy, RoleLocator):
        return page.get_by_role(strategy.role, name=strategy.name, exact=strategy.exact)
    if isinstance(strategy, LabelTextLocator):
        return page.get_by_text(strategy.text, exact=strategy.exact)
    if isinstance(strategy, CssLocator):
        return page.locator(strategy.selector)
    raise AssertionError(f"unknown locator strategy: {strategy!r}")  # pragma: no cover


def _count_visible(locator: SupportsLocatorProtocol, count: int) -> int:
    visible = 0
    for i in range(count):
        try:
            if locator.nth(i).is_visible():
                visible += 1
        except Exception:  # noqa: BLE001 - treat as not visible
            continue
    return visible


def _describe_attempts(attempts: list[tuple[int, LocatorStrategy, int]]) -> str:
    return ", ".join(f"[{i}]{s.kind}->{v} visible" for i, s, v in attempts)
