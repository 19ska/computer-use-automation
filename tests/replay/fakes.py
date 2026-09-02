"""Lightweight fake Playwright Page/Locator objects for unit tests.

These implement only the methods cua.replay actually calls — no real
browser involved. Not a test module itself (no test_ prefix), just
shared support for the files in this directory.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FakeElement:
    text: str = ""
    visible: bool = True
    attributes: dict[str, str] = field(default_factory=dict)
    clicked: bool = False
    filled_value: str | None = None
    selected_value: str | None = None
    tag_name: str = "div"
    content_editable: bool = False


class FakeLocator:
    def __init__(self, elements: list[FakeElement] | None = None):
        self._elements = elements or []

    def count(self) -> int:
        return len(self._elements)

    def nth(self, index: int) -> "FakeLocator":
        return FakeLocator([self._elements[index]])

    def is_visible(self) -> bool:
        return self._elements[0].visible

    def inner_text(self) -> str:
        return self._elements[0].text

    def get_attribute(self, name: str) -> str | None:
        return self._elements[0].attributes.get(name)

    def click(self, timeout: int = 0) -> None:
        self._elements[0].clicked = True

    def fill(self, value: str, timeout: int = 0) -> None:
        self._elements[0].filled_value = value

    def select_option(self, value: str, timeout: int = 0) -> None:
        self._elements[0].selected_value = value

    def evaluate(self, expression: str) -> object:
        element = self._elements[0]
        if "tagName" in expression:
            return element.tag_name
        if "isContentEditable" in expression:
            return element.content_editable
        raise NotImplementedError(expression)


class FakePage:
    """A fake Page. Populate `role_locators`/`text_locators`/`css_locators`
    with FakeLocator instances keyed the way the real Playwright calls
    would be keyed, and set `body_text`/`url` as needed per test.
    """

    def __init__(self) -> None:
        self.url = "https://example.test/start"
        self.body_text = ""
        self.role_locators: dict[tuple[str, str], FakeLocator] = {}
        self.text_locators: dict[str, FakeLocator] = {}
        # A default, harmless "body" locator so tests that don't care about
        # body content (e.g. exercising an unrelated step) don't have to
        # register one just to satisfy an incidental extract step.
        self.css_locators: dict[str, FakeLocator] = {"body": FakeLocator([FakeElement(text="")])}

        self.goto_calls: list[str] = []
        self.goto_details: list[dict[str, object]] = []
        self.wait_for_timeout_calls: list[int] = []
        self.wait_for_selector_calls: list[tuple[str, int | None]] = []
        self.wait_for_selector_error: Exception | None = None
        self.fill_calls: list[tuple[str, str]] = []
        self.click_calls: list[str] = []
        self.query_selector_result: object | None = None
        self.exposed_functions: dict[str, object] = {}
        self.evaluate_calls: list[str] = []
        self.evaluate_error: Exception | None = None
        self.screenshot_calls: list[str] = []

    # -- locator resolution surface --
    def get_by_role(self, role: str, *, name: str, exact: bool = False) -> FakeLocator:
        return self.role_locators.get((role, name), FakeLocator([]))

    def get_by_text(self, text: str, *, exact: bool = False) -> FakeLocator:
        return self.text_locators.get(text, FakeLocator([]))

    def locator(self, selector: str) -> FakeLocator:
        return self.css_locators.get(selector, FakeLocator([]))

    # -- whole-page / navigation surface --
    def inner_text(self, selector: str) -> str:
        assert selector == "body"
        return self.body_text

    def goto(self, url: str, wait_until: str | None = None, timeout: int | None = None) -> None:
        self.goto_calls.append(url)
        self.goto_details.append({"url": url, "wait_until": wait_until, "timeout": timeout})

    def wait_for_load_state(self, state: str | None = None, timeout: int | None = None) -> None:
        return None

    def wait_for_selector(self, selector: str, timeout: int | None = None) -> None:
        self.wait_for_selector_calls.append((selector, timeout))
        if self.wait_for_selector_error is not None:
            raise self.wait_for_selector_error

    def wait_for_timeout(self, ms: int) -> None:
        self.wait_for_timeout_calls.append(ms)

    def screenshot(self, path: str, full_page: bool = True) -> None:
        self.screenshot_calls.append(path)

    def expose_function(self, name: str, callback: object) -> None:
        self.exposed_functions[name] = callback

    def evaluate(self, expression: str) -> object:
        self.evaluate_calls.append(expression)
        if self.evaluate_error is not None:
            raise self.evaluate_error
        return None

    # -- raw (non-Locator) login-form surface used by session.py --
    def fill(self, selector: str, value: str, timeout: int | None = None) -> None:
        self.fill_calls.append((selector, value))

    def click(self, selector: str, timeout: int | None = None) -> None:
        self.click_calls.append(selector)

    def query_selector(self, selector: str) -> object | None:
        return self.query_selector_result
