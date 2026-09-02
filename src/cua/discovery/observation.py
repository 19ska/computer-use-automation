"""Builds a bounded, structured summary of the current page for Claude.

Split deliberately into a pure summarizer (easily unit-tested with plain
dicts) and a thin Playwright-calling wrapper (hard to unit-test, kept as
small as possible). Playwright 1.62 has no `page.accessibility` API
(removed upstream), so this uses role-based locator queries
(`page.get_by_role(role)`) instead of an accessibility-tree snapshot —
functionally equivalent for identifying interactive controls.

Deliberately does NOT dump the raw DOM: only a fixed set of roles are
queried, each list is bounded, hidden elements are excluded, and
duplicate nav entries (e.g. the same link repeated in a header and a
footer) are collapsed by name so Claude doesn't have to reason about
which of five identical "Home" links is the "real" one.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Roles queried for observation ("heading", "button", "link", "textbox"
# via names_for() below, "combobox" via select_options_for()) — a small,
# fixed set, not every possible ARIA role, chosen for what a banking-form
# UI actually has.

DEFAULT_MAX_ITEMS = 15
DEFAULT_MAX_TEXT_CHARS = 800


@dataclass
class ObservedControl:
    name: str
    options: list[str] = field(default_factory=list)  # populated only for comboboxes
    id: str | None = None  # populated only for comboboxes/selects


@dataclass
class Observation:
    step_number: int
    url: str
    title: str
    headings: list[str]
    buttons: list[ObservedControl]
    links: list[ObservedControl]
    text_inputs: list[ObservedControl]
    selects: list[ObservedControl]
    visible_text_excerpt: str
    previous_action_summary: str | None = None

    def to_prompt_text(self) -> str:
        """Compact, deterministic text representation sent to Claude as
        the tool_result / initial observation content."""
        lines = [
            f"Step: {self.step_number}",
            f"URL: {self.url}",
            f"Title: {self.title}",
        ]
        if self.previous_action_summary:
            lines.append(f"Previous action result: {self.previous_action_summary}")
        lines.append(f"Headings: {self.headings}")
        lines.append(f"Buttons: {[c.name for c in self.buttons]}")
        lines.append(f"Links: {[c.name for c in self.links]}")
        lines.append(f"Text inputs: {[c.name for c in self.text_inputs]}")
        lines.append(
            "Selects: " + str([{"name": c.name, "id": c.id, "options": c.options} for c in self.selects])
        )
        lines.append(f"Visible text (truncated): {self.visible_text_excerpt}")
        return "\n".join(lines)


def _dedupe_and_bound(names: list[str], max_items: int) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for name in names:
        normalized = name.strip()
        key = normalized.lower()
        if not normalized or key in seen:
            continue
        seen.add(key)
        result.append(normalized)
        if len(result) >= max_items:
            break
    return result


def build_observation(
    *,
    step_number: int,
    url: str,
    title: str,
    heading_texts: list[str],
    button_names: list[str],
    link_names: list[str],
    text_input_names: list[str],
    select_data: list[tuple[str, list[str], str | None]],
    visible_text: str,
    previous_action_summary: str | None = None,
    max_items: int = DEFAULT_MAX_ITEMS,
    max_text_chars: int = DEFAULT_MAX_TEXT_CHARS,
) -> Observation:
    """Pure summarizer: takes already-queried, already-visibility-filtered
    raw names/options and produces a bounded Observation. No Playwright
    calls happen here — this is the testable half.
    """
    selects = [
        ObservedControl(name=name.strip(), options=options[:max_items], id=select_id)
        for name, options, select_id in select_data
        if name.strip()
    ][:max_items]

    return Observation(
        step_number=step_number,
        url=url,
        title=title,
        headings=_dedupe_and_bound(heading_texts, max_items),
        buttons=[ObservedControl(name=n) for n in _dedupe_and_bound(button_names, max_items)],
        links=[ObservedControl(name=n) for n in _dedupe_and_bound(link_names, max_items)],
        text_inputs=[
            ObservedControl(name=n) for n in _dedupe_and_bound(text_input_names, max_items)
        ],
        selects=selects,
        visible_text_excerpt=visible_text.strip()[:max_text_chars],
        previous_action_summary=previous_action_summary,
    )


def _accessible_name(locator) -> str:  # noqa: ANN001 - real Playwright Locator
    """Best-effort accessible name, with a fallback chain for legacy
    markup that lacks proper ARIA labeling (common in this environment):
    aria-label -> associated <label> -> title -> placeholder -> id.
    Falling back to `id` is deliberate — a raw id like "fromAccountId" is
    still a meaningful, human-readable hint even though it isn't a real
    accessible name.
    """
    try:
        return locator.evaluate(
            "el => (el.getAttribute('aria-label') || "
            "(el.labels && el.labels[0] && el.labels[0].innerText) || "
            "el.getAttribute('title') || el.getAttribute('placeholder') || "
            "el.value || el.innerText || el.id || '').trim()"
        )
    except Exception:  # noqa: BLE001 - never let observation-building crash the run
        return ""


def _select_name_and_id(locator) -> tuple[str, str | None]:  # noqa: ANN001 - real Playwright Locator
    """A native <select>'s accessible-name fallback chain deliberately
    excludes `.value`/`.innerText`, unlike _accessible_name(): a select's
    `.value` is its CURRENTLY SELECTED option (e.g. "22890"), not a name —
    using it as a "name" is actively misleading (both a "from account" and
    a "to account" select can show the identical selected value, making
    them indistinguishable). `id` is reported both as the name fallback
    and as its own field, since legacy selects like ParaBank's have no
    aria-label/label/title at all.
    """
    try:
        result = locator.evaluate(
            "el => ({"
            "name: (el.getAttribute('aria-label') || "
            "(el.labels && el.labels[0] && el.labels[0].innerText) || "
            "el.getAttribute('title') || el.id || '').trim(), "
            "id: el.id || null"
            "})"
        )
        return result.get("name", ""), result.get("id")
    except Exception:  # noqa: BLE001 - never let observation-building crash the run
        return "", None


def capture_observation(
    page,  # noqa: ANN001 - real Playwright Page; SupportsPageProtocol doesn't declare get_by_role generically enough here
    step_number: int,
    previous_action_summary: str | None = None,
) -> Observation:
    """Thin wrapper: does the actual Playwright querying, then delegates
    all summarization/bounding logic to build_observation()."""

    def names_for(role: str) -> list[str]:
        elements = [el for el in page.get_by_role(role).all() if el.is_visible()]
        return [_accessible_name(el) for el in elements]

    def select_options_for() -> list[tuple[str, list[str], str | None]]:
        result: list[tuple[str, list[str], str | None]] = []
        selects = [el for el in page.get_by_role("combobox").all() if el.is_visible()]
        for select in selects:
            name, select_id = _select_name_and_id(select)
            options = select.locator("option").all_inner_texts()
            result.append((name, options, select_id))
        return result

    return build_observation(
        step_number=step_number,
        url=page.url,
        title=page.title(),
        heading_texts=names_for("heading"),
        button_names=names_for("button"),
        link_names=names_for("link"),
        text_input_names=names_for("textbox"),
        select_data=select_options_for(),
        visible_text=page.inner_text("body"),
        previous_action_summary=previous_action_summary,
    )
