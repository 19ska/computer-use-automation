"""Turns Claude's semantic target description into an ElementTarget, and
turns a typed/selected value into a ParamRef or LiteralRef.

Reuses the exact ElementTarget/LocatorStrategy/ParamRef/LiteralRef
Pydantic classes from cua.artifact.schema — the whole point is that these
are literally the same types Milestone 5's compiler will embed into an
ActionStep. No separate discovery-only value/target vocabulary exists.

Deliberately does NOT synthesize a CSS-selector guess from Claude's
description — Claude never sees CSS, so there's no honest way to derive
one from its hints. Candidates are built only from role+name and label
text; if none resolve, that's a genuine failure fed back to Claude, not a
silent fallback (this is the same lesson Milestone 3's body-fallback bug
taught: a "fallback" that isn't a real alternative identification of the
same element is not safe to have).

Action-aware target construction: a plain label-text match (e.g.
`get_by_text("Amount:")`) resolves to the label node itself, which is
correct for `click` but is never a valid target for `type_text` or
`select_option` — those actions need an editable/select control, not the
text describing one. For those two actions, the label-text candidate is
replaced with a deterministic DOM-structure fallback: "the nearest
input/textarea/[contenteditable] (or <select>) that follows this label
text in document order." This is a real, bounded relationship in the DOM
tree — not pixel/layout-based, not fuzzy, not computer vision — for
legacy markup (like ParaBank's) where a visible label has no
programmatic association (no <label for>, no aria-label) with its
control.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from cua.artifact.schema import (
    CssLocator,
    ElementTarget,
    LabelTextLocator,
    LiteralRef,
    ParamRef,
    RoleLocator,
    ValueRef,
)

_EDITABLE_TAGS = ("input", "textarea")
_SELECT_TAGS = ("select",)

_UPPER = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_LOWER = "abcdefghijklmnopqrstuvwxyz"


def _xpath_string_literal(text: str) -> str:
    """Quotes `text` for use as an XPath 1.0 string literal, handling
    embedded quote characters without needing XPath 2.0 escaping."""
    if '"' not in text:
        return f'"{text}"'
    if "'" not in text:
        return f"'{text}'"
    parts = text.split('"')
    return "concat(" + ', \'"\', '.join(f'"{p}"' for p in parts) + ")"


def _associated_control_xpath(label_text: str, tags: tuple[str, ...], *, contenteditable: bool = False) -> str:
    """Builds a `CssLocator` selector (Playwright's `xpath=` engine) that
    matches the nearest `tags`-element (optionally also any
    `[contenteditable="true"]` element) following, in document order, the
    label text — case-insensitively, with or without a trailing colon.
    Never a fuzzy/visual match: this is an exact, deterministic DOM-tree
    relationship.

    Matches the label two ways, since legacy markup uses both shapes:
    - an ELEMENT whose own text is exactly the label (e.g. `<b>Amount:</b>`);
    - a bare TEXT NODE with no wrapping element at all (e.g. ParaBank's
      transfer page, where "From account #" and "to account #" are plain
      text sitting inline in the same paragraph as both <select>s, not
      inside any tag of their own — `//*[...]` cannot match that at all,
      since it only selects elements, so a second `//text()[...]` branch
      is required to find it).
    """
    normalized = label_text.strip().rstrip(":").strip().lower()
    bare = _xpath_string_literal(normalized)
    with_colon = _xpath_string_literal(f"{normalized}:")
    lowered_element = f'translate(normalize-space(string(.)), "{_UPPER}", "{_LOWER}")'
    lowered_text_node = f'translate(normalize-space(.), "{_UPPER}", "{_LOWER}")'
    element_condition = f"{lowered_element} = {bare} or {lowered_element} = {with_colon}"
    text_node_condition = f"{lowered_text_node} = {bare} or {lowered_text_node} = {with_colon}"

    branches = []
    for tag in tags:
        branches.append(f"//*[{element_condition}]/following::{tag}[1]")
        branches.append(f"//text()[{text_node_condition}]/following::{tag}[1]")
    if contenteditable:
        branches.append(f'//*[{element_condition}]/following::*[@contenteditable="true"][1]')
        branches.append(f'//text()[{text_node_condition}]/following::*[@contenteditable="true"][1]')
    return "xpath=" + " | ".join(branches)


def build_candidate_target(
    *,
    action: str,
    target_description: str,
    accessible_role: str | None,
    accessible_name: str | None,
) -> ElementTarget:
    strategies: list[RoleLocator | LabelTextLocator | CssLocator] = []

    if accessible_role and accessible_name:
        strategies.append(RoleLocator(role=accessible_role, name=accessible_name))

    if action in ("type_text", "select_option"):
        tags = _SELECT_TAGS if action == "select_option" else _EDITABLE_TAGS
        # Try the most specific label first (the name Claude gave the
        # control), then the target description, in case the visible
        # label text matches one but not the other.
        labels = [text for text in (accessible_name, target_description) if text]
        for label in dict.fromkeys(labels):
            strategies.append(
                CssLocator(selector=_associated_control_xpath(label, tags, contenteditable=action == "type_text"))
            )
    elif accessible_name:
        strategies.append(LabelTextLocator(text=accessible_name))

    if not strategies:
        # No usable hint at all — this is a genuine "we can't identify a
        # target" case; give the resolver something that will just fail
        # to match anything rather than skipping resolution entirely, so
        # the caller gets a normal LocatorNotFoundError to report back.
        strategies.append(LabelTextLocator(text=target_description))

    return ElementTarget(description=target_description, strategies=strategies)


def resolve_value_source(value: str, declared_params: dict[str, str]) -> ValueRef:
    """Deterministically recognizes when a typed/selected value equals a
    declared runtime parameter, so the transcript preserves the symbolic
    relationship (`ParamRef`) instead of just the literal string — this
    is exactly the information Milestone 5's compiler needs to avoid
    baking discovery-time literals into the reusable artifact.
    """
    for name, param_value in declared_params.items():
        if value == param_value:
            return ParamRef(name=name)
        try:
            if Decimal(value) == Decimal(param_value):
                return ParamRef(name=name)
        except InvalidOperation:
            pass
    return LiteralRef(value=value)
