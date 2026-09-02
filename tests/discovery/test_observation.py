"""Tests for the pure observation summarizer (cua.discovery.observation).

build_observation() takes already-queried data and only handles bounding
and deduplication — no Playwright involved, so these are plain unit
tests. capture_observation() (the thin Playwright-calling wrapper) is
deliberately not unit-tested here — it has almost no logic of its own.
"""

from cua.discovery.observation import build_observation


def test_bounds_and_dedupes_button_and_link_lists():
    obs = build_observation(
        step_number=1,
        url="https://example.test/transfer.htm",
        title="Transfer Funds",
        heading_texts=["Transfer Funds"],
        button_names=["Transfer", "Transfer", "  Transfer  "],  # dup + whitespace variant
        link_names=["Home", "home", "About Us"],  # case-insensitive dup
        text_input_names=["Amount"],
        select_data=[("From Account", ["15009", "15120"], "fromAccountId"), ("To Account", ["15009", "15120"], "toAccountId")],
        visible_text="some page text",
        max_items=15,
    )
    assert [b.name for b in obs.buttons] == ["Transfer"]
    assert [l.name for l in obs.links] == ["Home", "About Us"]


def test_excludes_blank_names():
    obs = build_observation(
        step_number=1, url="u", title="t",
        heading_texts=["", "  ", "Real Heading"],
        button_names=[""], link_names=[""], text_input_names=[""],
        select_data=[("", ["x"], None)],  # blank select name is dropped entirely
        visible_text="",
    )
    assert obs.headings == ["Real Heading"]
    assert obs.buttons == []
    assert obs.selects == []


def test_bounds_item_counts_to_max_items():
    many_buttons = [f"Button {i}" for i in range(50)]
    obs = build_observation(
        step_number=1, url="u", title="t",
        heading_texts=[], button_names=many_buttons, link_names=[], text_input_names=[],
        select_data=[], visible_text="", max_items=5,
    )
    assert len(obs.buttons) == 5


def test_truncates_visible_text_excerpt():
    obs = build_observation(
        step_number=1, url="u", title="t",
        heading_texts=[], button_names=[], link_names=[], text_input_names=[],
        select_data=[], visible_text="x" * 5000, max_text_chars=800,
    )
    assert len(obs.visible_text_excerpt) == 800


def test_select_options_are_preserved_and_bounded():
    obs = build_observation(
        step_number=1, url="u", title="t",
        heading_texts=[], button_names=[], link_names=[], text_input_names=[],
        select_data=[("From Account", [str(i) for i in range(30)], "fromAccountId")],
        visible_text="", max_items=10,
    )
    assert obs.selects[0].name == "From Account"
    assert len(obs.selects[0].options) == 10
    assert obs.selects[0].options[0] == "0"


def test_select_id_is_preserved():
    obs = build_observation(
        step_number=1, url="u", title="t",
        heading_texts=[], button_names=[], link_names=[], text_input_names=[],
        select_data=[
            ("fromAccountId", ["15009", "15120"], "fromAccountId"),
            ("toAccountId", ["15009", "15120"], "toAccountId"),
        ],
        visible_text="",
    )
    assert obs.selects[0].id == "fromAccountId"
    assert obs.selects[1].id == "toAccountId"


def test_select_with_no_id_reports_none():
    obs = build_observation(
        step_number=1, url="u", title="t",
        heading_texts=[], button_names=[], link_names=[], text_input_names=[],
        select_data=[("Account Type", ["CHECKING", "SAVINGS"], None)],
        visible_text="",
    )
    assert obs.selects[0].id is None


def test_to_prompt_text_includes_all_sections():
    obs = build_observation(
        step_number=3, url="https://x.test/transfer.htm", title="Transfer Funds",
        heading_texts=["Transfer Funds"], button_names=["Transfer"], link_names=["Home"],
        text_input_names=["Amount"], select_data=[("From Account", ["15009"], "fromAccountId")],
        visible_text="body text", previous_action_summary="navigate succeeded",
    )
    text = obs.to_prompt_text()
    assert "Step: 3" in text
    assert "transfer.htm" in text
    assert "Transfer Funds" in text
    assert "Transfer" in text
    assert "Home" in text
    assert "Amount" in text
    assert "From Account" in text
    assert "15009" in text
    assert "fromAccountId" in text
    assert "navigate succeeded" in text
