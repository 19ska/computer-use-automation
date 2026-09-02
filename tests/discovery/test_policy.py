"""Tests for the discovery-time policy gate (cua.discovery.policy)."""

from cua.discovery.policy import check_policy

BASE_URL = "https://parabank.parasoft.com/parabank"
ALLOWED_HOST = "parabank.parasoft.com"


def test_allows_in_domain_relative_navigate():
    decision = check_policy(
        "navigate", {"url_path": "/transfer.htm"}, allowed_host=ALLOWED_HOST, base_url=BASE_URL
    )
    assert decision.allowed is True


def test_allows_click_action():
    decision = check_policy(
        "click", {"target_description": "Transfer button"}, allowed_host=ALLOWED_HOST, base_url=BASE_URL
    )
    assert decision.allowed is True


def test_blocks_absolute_navigate_to_external_host():
    decision = check_policy(
        "navigate",
        {"url_path": "https://evil.example.com/steal"},
        allowed_host=ALLOWED_HOST,
        base_url=BASE_URL,
    )
    assert decision.allowed is False
    assert "evil.example.com" in decision.reason


def test_blocks_navigate_to_a_subdomain_of_the_allowed_host():
    """Subdomains are NOT automatically trusted for this implementation —
    only an exact host match is allowed."""
    decision = check_policy(
        "navigate",
        {"url_path": "https://admin.parabank.parasoft.com/anything"},
        allowed_host=ALLOWED_HOST,
        base_url=BASE_URL,
    )
    assert decision.allowed is False


def test_blocks_unknown_action_name():
    decision = check_policy(
        "execute_script", {"code": "..."}, allowed_host=ALLOWED_HOST, base_url=BASE_URL
    )
    assert decision.allowed is False
    assert "execute_script" in decision.reason


def test_allows_all_declared_action_names():
    for action, args in [
        ("navigate", {"url_path": "/x.htm"}),
        ("click", {}),
        ("type_text", {}),
        ("select_option", {}),
        ("finish", {}),
        ("give_up", {}),
    ]:
        decision = check_policy(action, args, allowed_host=ALLOWED_HOST, base_url=BASE_URL)
        assert decision.allowed is True, f"{action} should be allowed"
