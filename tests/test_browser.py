"""Real Chromium journeys against a running HTTP server and SQLite, using the scripted graph."""

import socket
import threading
import time

import pytest
import uvicorn
from playwright.sync_api import expect, sync_playwright

from backend.server import create_app


@pytest.fixture(scope="module")
def browser_app(tmp_path_factory):
    db = tmp_path_factory.mktemp("browser") / "browser.sqlite3"
    app = create_app(db, provider="scripted")
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.03)
    assert server.started
    with sync_playwright() as p:
        browser = p.chromium.launch()
        yield browser, f"http://127.0.0.1:{port}"
        browser.close()
    server.should_exit = True
    thread.join(timeout=5)


@pytest.mark.parametrize(
    "case_id,title",
    [
        ("recommendation-review", "Modify the purchase recommendation"),
        ("supplier-shortfall", "Prepare a supplemental purchase"),
        ("demand-spike", "Adjust the purchasing plan"),
    ],
)
def test_buyer_can_analyze_and_approve_all_three_features(browser_app, case_id, title):
    browser, url = browser_app
    page = browser.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(url)
    page.select_option("#case-select", case_id)
    page.click("#reset-button")
    expect(page.locator("#analyze-button")).to_be_enabled()
    page.click("#analyze-button")
    expect(page.locator(".result-title")).to_have_text(title)
    expect(page.locator("#trace-count")).to_have_text("6 calls")
    page.click("#approve-button")
    expect(page.locator("#validation")).to_contain_text("Validated")
    expect(page.locator("#validation")).to_contain_text("150/150 units confirmed")
    expect(page.locator("#case-summary")).to_contain_text("$1,500" if case_id == "recommendation-review" else "$1,200")
    assert not errors
    page.close()


def test_browser_supplier_failure_shows_follow_up(browser_app):
    browser, url = browser_app
    page = browser.new_page()
    page.goto(url)
    page.select_option("#case-select", "supplier-shortfall")
    page.click("#reset-button")
    page.click("#analyze-button")
    expect(page.locator("#approve-button")).to_be_visible()
    page.select_option("#supplier-outcome", "rejected")
    page.click("#approve-button")
    expect(page.locator("#validation")).to_contain_text("Buyer review required")
    expect(page.locator("#validation")).to_contain_text("Remaining gap: 110 units")
    page.close()


def test_browser_constrained_purchase_has_no_approve_button(browser_app):
    browser, url = browser_app
    page = browser.new_page(viewport={"width": 390, "height": 844})
    page.goto(url)
    page.select_option("#case-select", "budget-constrained")
    page.click("#analyze-button")
    expect(page.locator(".result-title")).to_have_text("Buyer intervention is needed")
    expect(page.locator("#approve-button")).to_have_count(0)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.close()


def test_browser_evaluation_results_and_buyer_rejection(browser_app):
    browser, url = browser_app
    page = browser.new_page()
    page.goto(url)
    page.click("#reset-button")
    page.click("#analyze-button")
    expect(page.locator("#reject-button")).to_be_visible()
    page.click("#reject-button")
    expect(page.locator("#validation")).to_contain_text("No purchase was placed")
    page.click("#eval-button")
    expect(page.locator(".eval-table tbody tr")).to_have_count(14, timeout=30000)
    expect(page.locator(".eval-table .fail")).to_have_count(0)
    page.close()
