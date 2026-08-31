"""Milestone 1 manual exploration script.

Purpose: prove Playwright can drive the real ParaBank demo application end to
end, and observe its *actual* registration/account/transfer behavior before
any artifact schema, discovery loop, or error taxonomy gets designed around
assumptions instead of evidence.

This is throwaway exploration code — not part of the eventual `cua` package.
It intentionally does NOT assert that ParaBank behaves any particular way for
the over-balance transfer; it just does the transfer (when two real accounts
exist) and reports exactly what came back so a human can decide what it
means. It also never fabricates a second account or a fake result — if
something can't be verified reliably, it stops and says so.

Run it, watch the headed browser, and send the console output back.
"""

from __future__ import annotations

import os
import re
import time
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import Locator, Page
from playwright.sync_api import TimeoutError as PWTimeoutError
from playwright.sync_api import sync_playwright

load_dotenv()

BASE_URL = os.environ.get(
    "PARABANK_BASE_URL", "https://parabank.parasoft.com/parabank"
).rstrip("/")
SCRATCH_DIR = Path(__file__).resolve().parent.parent / "scratch"

DEFAULT_TIMEOUT_MS = 10_000


def unique_username() -> str:
    # Timestamp-suffixed so repeated runs never collide on ParaBank's
    # "username already exists" check.
    return f"cuauser{int(time.time())}"


def report_and_raise(
    page: Page,
    step_name: str,
    message: str,
    expected: str | None = None,
    available_options: list | None = None,
) -> None:
    """Fail loudly and diagnosably instead of hanging or failing silently.

    Always prints: current URL, what we expected, available options (if
    given), visible page text, and a screenshot path — then raises.
    """
    shot_path = SCRATCH_DIR / f"FAILED_{step_name}.png"
    page.screenshot(path=str(shot_path), full_page=True)
    print(f"\n--- FAILURE during step: {step_name} ---")
    print(f"Message: {message}")
    print(f"Current URL: {page.url}")
    if expected is not None:
        print(f"Expected: {expected}")
    if available_options is not None:
        print(f"Available options: {available_options}")
    print(f"Screenshot saved: {shot_path}")
    print("Visible page text at failure:\n")
    print(page.inner_text("body")[:2000])
    print("--- end failure dump ---\n")
    raise RuntimeError(message)


def describe_inputs(page: Page) -> list[dict]:
    """Enumerate every <input> on the page with the attributes we care
    about, without assuming any particular one is the login form."""
    described = []
    for el in page.query_selector_all("input"):
        described.append(
            {
                "type": el.get_attribute("type"),
                "name": el.get_attribute("name"),
                "id": el.get_attribute("id"),
                "placeholder": el.get_attribute("placeholder"),
                "value": el.get_attribute("value"),
                "visible": el.is_visible(),
            }
        )
    return described


def describe_buttons(page: Page) -> list[dict]:
    """Enumerate every clickable submit-like control on the page."""
    described = []
    for el in page.query_selector_all(
        "button, input[type='submit'], input[type='image'], input[type='button']"
    ):
        tag = el.evaluate("e => e.tagName.toLowerCase()")
        described.append(
            {
                "tag": tag,
                "type": el.get_attribute("type"),
                "name": el.get_attribute("name"),
                "id": el.get_attribute("id"),
                "value": el.get_attribute("value"),
                "text": el.inner_text().strip() if tag == "button" else None,
                "visible": el.is_visible(),
            }
        )
    return described


def _input_locator(page: Page, info: dict) -> Locator:
    """Build the most stable locator we can from a discovered <input>'s
    real attributes: id, then name, then a generic type-based fallback."""
    if info.get("id"):
        return page.locator(f"input[id='{info['id']}']")
    if info.get("name"):
        return page.locator(f"input[name='{info['name']}']")
    return page.locator(f"input[type='{info.get('type') or 'text'}']").first


def _button_locator(page: Page, info: dict) -> Locator:
    tag = info["tag"]
    if info.get("id"):
        return page.locator(f"{tag}[id='{info['id']}']")
    if info.get("name"):
        return page.locator(f"{tag}[name='{info['name']}']")
    if info.get("value"):
        return page.locator(f"{tag}[value='{info['value']}']")
    return page.locator(tag).first


def discover_login_controls(page: Page) -> dict:
    """Navigate to the ParaBank home page and inspect the REAL login form
    controls rather than assuming selectors from documentation.

    Locator priority, per the requested strategy: accessible label/role
    first, then a stable id, then a stable name attribute, then a generic
    CSS fallback based on input type. Raises via report_and_raise (with the
    full list of discovered inputs/buttons attached) if the controls can't
    be identified at all.
    """
    page.goto(f"{BASE_URL}/index.htm", wait_until="networkidle")

    inputs = describe_inputs(page)
    buttons = describe_buttons(page)

    print("\n--- Login page control inspection (live, not assumed) ---")
    print(f"URL: {page.url}")
    print("All <input> elements found:")
    for i in inputs:
        print(f"  {i}")
    print("All button-like controls found:")
    for b in buttons:
        print(f"  {b}")
    print("--- end control inspection ---\n")

    # Password field is the most reliable signal — there should be exactly
    # one visible password input, and it belongs to the login form.
    password_candidates = [i for i in inputs if i["type"] == "password" and i["visible"]]
    if not password_candidates:
        report_and_raise(
            page,
            "discover_login_controls",
            "No visible password-type input found on the home page — "
            "cannot locate a login form to test invalid credentials against.",
            available_options={"inputs": inputs, "buttons": buttons},
        )
    password_locator = _input_locator(page, password_candidates[0])

    # Username field. Priority 1: accessible label/role, best-effort since
    # ParaBank's markup may not support it.
    username_locator: Locator | None = None
    for guess in ["username", "user name", "user id"]:
        try:
            candidate = page.get_by_label(re.compile(guess, re.I))
            if candidate.count() == 1:
                username_locator = candidate
                break
        except Exception:
            pass

    # Priority 2/3/4: fall back to a real, observed text input — preferring
    # one whose id/name/placeholder actually mentions "user".
    if username_locator is None:
        text_inputs = [i for i in inputs if i["type"] in ("text", None) and i["visible"]]
        user_like = [
            i
            for i in text_inputs
            if any(
                token and "user" in token.lower()
                for token in (i["name"], i["id"], i["placeholder"])
            )
        ]
        chosen = user_like[0] if user_like else (text_inputs[0] if text_inputs else None)
        if chosen is not None:
            username_locator = _input_locator(page, chosen)

    if username_locator is None:
        report_and_raise(
            page,
            "discover_login_controls",
            "Could not identify a username input from the discovered controls.",
            available_options={"inputs": inputs, "buttons": buttons},
        )

    # Submit control: prefer one whose visible text/value looks like "log in".
    login_like = [
        b
        for b in buttons
        if b["visible"]
        and (
            (b["value"] and "log in" in b["value"].lower())
            or (b["text"] and "log in" in b["text"].lower())
        )
    ]
    chosen_button = login_like[0] if login_like else next(
        (b for b in buttons if b["visible"]), None
    )
    if chosen_button is None:
        report_and_raise(
            page,
            "discover_login_controls",
            "Could not identify a login submit control from the discovered controls.",
            available_options={"inputs": inputs, "buttons": buttons},
        )
    submit_locator = _button_locator(page, chosen_button)

    return {
        "username": username_locator,
        "password": password_locator,
        "submit": submit_locator,
        "discovered_inputs": inputs,
        "discovered_buttons": buttons,
    }


def explore_invalid_login_attempt(page: Page) -> None:
    """Observe ParaBank's real response to a login with bad credentials.

    Purely observational — no assumption about the exact wording ParaBank
    uses, and no assertion. We just capture what actually happens so a
    later milestone can model it as a typed business outcome (or not).
    """
    bogus_username = f"cua_nonexistent_{int(time.time())}"
    bogus_password = "definitely-wrong-password"  # noqa: S105 - fake test data

    controls = discover_login_controls(page)

    try:
        controls["username"].fill(bogus_username, timeout=5_000)
        controls["password"].fill(bogus_password, timeout=5_000)
        controls["submit"].click(timeout=5_000)
    except PWTimeoutError:
        report_and_raise(
            page,
            "invalid_login_submit",
            "Timed out interacting with the discovered login controls.",
            available_options={
                "inputs": controls["discovered_inputs"],
                "buttons": controls["discovered_buttons"],
            },
        )

    try:
        page.wait_for_load_state("networkidle", timeout=8_000)
    except PWTimeoutError:
        pass  # still capture whatever state we're in below

    logout_link_present = page.query_selector("a[href*='logout.htm']") is not None

    page.screenshot(path=str(SCRATCH_DIR / "00_invalid_login_attempt.png"), full_page=True)

    print("\n--- Invalid login observation ---")
    print(f"Attempted username: {bogus_username}")
    print(f"Current URL: {page.url}")
    print(f"Log Out link present (should be False if login truly failed): {logout_link_present}")
    print("Visible page text:")
    print(page.inner_text("body")[:2000])
    print("--- end invalid login observation ---\n")


def register(page: Page, username: str, password: str) -> None:
    """Fill and submit ParaBank's registration form with synthetic data."""
    page.goto(f"{BASE_URL}/register.htm", wait_until="networkidle")
    page.fill("#customer\\.firstName", "Ada")
    page.fill("#customer\\.lastName", "Tester")
    page.fill("#customer\\.address\\.street", "123 Fake St")
    page.fill("#customer\\.address\\.city", "Springfield")
    page.fill("#customer\\.address\\.state", "IL")
    page.fill("#customer\\.address\\.zipCode", "62704")
    page.fill("#customer\\.phoneNumber", "555-555-0100")
    page.fill("#customer\\.ssn", "123-45-6789")
    page.fill("#customer\\.username", username)
    page.fill("#customer\\.password", password)
    page.fill("#repeatedPassword", password)
    page.click("input[value='Register']")


def verify_authenticated(page: Page, context_label: str) -> str:
    """Confirm we're actually in an authenticated session.

    Form submission completing is not evidence of success — ParaBank can
    re-render the same URL on validation failure. We check for a real
    authenticated-state signal instead: the "Log Out" link that only
    appears in the logged-in navigation panel.
    """
    try:
        page.wait_for_selector("a[href*='logout.htm']", timeout=8_000)
    except PWTimeoutError:
        report_and_raise(
            page,
            step_name=f"verify_authenticated_{context_label}",
            message=(
                "Could not find the authenticated-state indicator "
                "(a Log Out link) — registration/login likely did not "
                "actually succeed."
            ),
        )
    indicator = "presence of a[href*='logout.htm'] (Log Out link)"
    print(f"[{context_label}] Authenticated-state indicator confirmed: {indicator}")
    return indicator


def read_accounts_overview(page: Page) -> list[dict]:
    """Go to Accounts Overview and read whatever real accounts appear.

    Makes no assumption about how many accounts exist or what types they
    are. Summary rows (e.g. "Total") are excluded by requiring the account
    id to actually be numeric, which is how ParaBank's real account ids
    look — this is a property of the id itself, not a guess about row
    position or label text.
    """
    page.goto(f"{BASE_URL}/overview.htm", wait_until="networkidle")

    accounts: list[dict] = []
    rows = page.query_selector_all("#accountTable tbody tr")
    for row in rows:
        cells = [c.inner_text().strip() for c in row.query_selector_all("td")]
        if not cells:
            continue
        link = row.query_selector("a")
        account_id = (link.inner_text().strip() if link else cells[0]).strip()
        if not account_id.isdigit():
            # Excludes summary/footer rows like "Total" and any other
            # non-account row, without assuming a specific label.
            continue
        accounts.append({"account_id": account_id, "raw_cells": cells})
    return accounts


def read_dropdown_options(page: Page, select_id: str) -> list[tuple[str, str]]:
    """Read every option's (value, visible text) from a <select>."""
    options = page.query_selector_all(f"#{select_id} option")
    return [(o.get_attribute("value") or "", o.inner_text().strip()) for o in options]


def try_open_second_account(page: Page, existing_account_id: str) -> str | None:
    """Attempt ParaBank's Open New Account flow to fund a second account.

    Returns the new account id on verified success. Returns None — without
    fabricating anything — if the form's real structure or result signal
    doesn't match what we expected closely enough to trust.
    """
    page.goto(f"{BASE_URL}/openaccount.htm", wait_until="networkidle")

    type_select = page.query_selector("#type")
    from_select = page.query_selector("#fromAccountId")
    open_button = page.query_selector("#openAccountButton") or page.query_selector(
        "input[value='Open New Account']"
    )

    if not (type_select and from_select and open_button):
        print(
            "  Open New Account form did not match the expected structure "
            "(#type / #fromAccountId / an open-account button were not all "
            "found). Not attempting to guess further."
        )
        return None

    type_options = [
        (o.get_attribute("value") or "", o.inner_text().strip())
        for o in type_select.query_selector_all("option")
    ]
    print(f"  Account type options available: {type_options}")
    if not type_options:
        print("  No account type options found; cannot proceed reliably.")
        return None

    chosen_type_value = None
    for value, label in type_options:
        if "saving" in label.lower():
            chosen_type_value = value
            break
    if chosen_type_value is None:
        chosen_type_value = type_options[-1][0]

    try:
        type_select.select_option(value=chosen_type_value, timeout=5_000)
        from_select.select_option(value=existing_account_id, timeout=5_000)
        open_button.click(timeout=5_000)
    except PWTimeoutError:
        report_and_raise(
            page,
            step_name="open_new_account_interaction",
            message="Timed out interacting with the Open New Account form.",
            expected=f"type={chosen_type_value!r} fromAccountId={existing_account_id!r}",
        )

    # This step may complete via a normal navigation or an in-page (ajax)
    # update — try to observe a concrete new-account-id signal rather than
    # assuming either mechanism.
    new_account_id: str | None = None
    try:
        page.wait_for_selector("#newAccountId", timeout=8_000)
        new_account_id = page.inner_text("#newAccountId").strip()
    except PWTimeoutError:
        pass

    if not new_account_id:
        print(
            "  Could not verify a new account id via #newAccountId within "
            "the timeout. Not fabricating a result — treating this as "
            "'second account not reliably created'."
        )
        return None

    print(f"  Open New Account reported new account id: {new_account_id!r}")
    return new_account_id


def run_transfer_experiment(page: Page) -> str | None:
    """Attempt an over-balance transfer using two REAL accounts from the
    transfer form's own dropdowns (the authoritative source of what
    ParaBank considers valid transfer accounts — not the overview table).

    Does not assert on the result. Returns the visible result text, or
    None if fewer than two distinct valid accounts are available (in which
    case nothing is fabricated and the experiment is skipped cleanly).
    """
    page.goto(f"{BASE_URL}/transfer.htm", wait_until="networkidle")

    from_options = read_dropdown_options(page, "fromAccountId")
    to_options = read_dropdown_options(page, "toAccountId")

    print("\nTransfer Funds dropdown options (source of truth for valid accounts):")
    print(f"  #fromAccountId options: {from_options}")
    print(f"  #toAccountId options:   {to_options}")

    from_ids = [v for v, _ in from_options if v]
    to_ids = [v for v, _ in to_options if v]
    common_ids = [i for i in from_ids if i in to_ids]

    if len(common_ids) < 2:
        print(
            "\nFewer than two distinct account ids are available in the "
            "transfer dropdowns. A second real account is required to run "
            "the transfer experiment; nothing will be fabricated. Skipping."
        )
        return None

    from_id, to_id = common_ids[0], common_ids[1]
    amount = Decimal("999999999")

    print(f"\nSubmitting transfer: amount={amount} from={from_id} to={to_id}")

    try:
        page.select_option("#fromAccountId", from_id, timeout=5_000)
        page.select_option("#toAccountId", to_id, timeout=5_000)
        page.fill("#amount", str(amount), timeout=5_000)
        page.click("input[value='Transfer']", timeout=5_000)
        page.wait_for_load_state("networkidle", timeout=15_000)
    except PWTimeoutError:
        report_and_raise(
            page,
            step_name="transfer_submit",
            message="Timed out during transfer submission or waiting for the result page.",
            expected=f"from={from_id} to={to_id} amount={amount}",
            available_options={"from": from_options, "to": to_options},
        )

    result_text = page.inner_text("body")
    page.screenshot(path=str(SCRATCH_DIR / "04_transfer_result.png"), full_page=True)
    return result_text


def main() -> None:
    SCRATCH_DIR.mkdir(parents=True, exist_ok=True)

    username = unique_username()
    password = "SyntheticPass123!"  # noqa: S105 - fake test data, not a real secret

    print(f"Target: {BASE_URL}")
    print(f"Registering synthetic user: {username}")

    transfer_result_text: str | None = None
    accounts: list[dict] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=150)
        page = browser.new_page()
        page.set_default_timeout(DEFAULT_TIMEOUT_MS)

        explore_invalid_login_attempt(page)

        try:
            register(page, username, password)
        except Exception as exc:
            report_and_raise(page, "register", str(exc))

        verify_authenticated(page, "post-registration")
        page.screenshot(path=str(SCRATCH_DIR / "01_post_registration.png"), full_page=True)

        try:
            accounts = read_accounts_overview(page)
        except Exception as exc:
            report_and_raise(page, "accounts_overview", str(exc))

        page.screenshot(path=str(SCRATCH_DIR / "02_accounts_overview.png"), full_page=True)

        print("\nAccounts found on Accounts Overview (summary rows excluded):")
        if not accounts:
            print("  (none — overview table was empty or had a different structure)")
        for acct in accounts:
            print(f"  id={acct['account_id']!r}  raw_cells={acct['raw_cells']}")

        if len(accounts) == 0:
            report_and_raise(
                page,
                "no_accounts_found",
                "No real (numeric-id) accounts were found on Accounts Overview.",
            )

        if len(accounts) == 1:
            print(
                f"\nOnly one account currently exists (id={accounts[0]['account_id']}). "
                "Attempting ParaBank's Open New Account flow to create a second, "
                "real account rather than fabricating or self-transferring."
            )
            new_id = try_open_second_account(page, accounts[0]["account_id"])
            if new_id is not None:
                print("Re-checking Accounts Overview after opening a new account...")
                accounts = read_accounts_overview(page)
                page.screenshot(
                    path=str(SCRATCH_DIR / "02b_accounts_overview_after_open.png"),
                    full_page=True,
                )
                print("Accounts found now:")
                for acct in accounts:
                    print(f"  id={acct['account_id']!r}  raw_cells={acct['raw_cells']}")

        transfer_result_text = run_transfer_experiment(page)

        print(
            "\nBrowser will stay open until you press Enter in this terminal, "
            "so you can inspect the final state yourself."
        )
        input("Press Enter to close the browser...")
        browser.close()

    print("\n===== SUMMARY (copy this back) =====")
    print(f"Registered username: {username}")
    print(f"Accounts found (final): {accounts}")
    if transfer_result_text is not None:
        print("Visible page text after over-balance transfer attempt:")
        print("-----")
        print(transfer_result_text)
        print("-----")
    else:
        print(
            "Transfer experiment was skipped — fewer than two real accounts "
            "were available and none were fabricated."
        )
    print(f"Screenshots saved under: {SCRATCH_DIR}")
    print("=====================================")


if __name__ == "__main__":
    main()
