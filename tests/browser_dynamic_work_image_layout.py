from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import sync_playwright


BASE_URL = "http://127.0.0.1:8787/"
OUTPUT_DIR = Path(__file__).resolve().parent / "screenshots" / "dynamic-work-image-layout-20260818"


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    console_errors: list[str] = []
    page_errors: list[str] = []
    failed_api: list[str] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
        page.on(
            "console",
            lambda message: console_errors.append(message.text) if message.type == "error" else None,
        )
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        page.on(
            "response",
            lambda response: failed_api.append(f"{response.status} {response.url}")
            if "/api/" in response.url and response.status >= 400
            else None,
        )

        page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_selector("#routeMode", state="visible")
        page.wait_for_timeout(2_000)
        page.screenshot(path=OUTPUT_DIR / "01-home.png", full_page=True)

        page.locator("#routeMode").click()
        page.wait_for_selector("#routeEditor:not([hidden])")
        layout_buttons = page.locator(".route-layout-button")
        layout_buttons.first.wait_for(state="visible")
        page.screenshot(path=OUTPUT_DIR / "02-route-layout-buttons.png", full_page=True)

        layout_buttons.first.click()
        page.get_by_role("heading", name="调整指导书图片格数").wait_for(state="visible")
        options = page.locator(".work-layout-option")
        assert options.count() == 6
        assert [options.nth(index).locator("b").inner_text() for index in range(6)] == [
            "1 格", "2 格", "3 格", "4 格", "5 格", "6 格",
        ]
        page.screenshot(path=OUTPUT_DIR / "03-layout-picker.png", full_page=True)

        result = {
            "layout_button_count": layout_buttons.count(),
            "layout_option_count": options.count(),
            "dialog_title": page.locator("#routeDialogTitle").inner_text(),
            "summary": page.locator(".work-layout-summary").inner_text(),
            "console_errors": console_errors,
            "page_errors": page_errors,
            "failed_api": failed_api,
            "screenshots": [str(path.resolve()) for path in sorted(OUTPUT_DIR.glob("*.png"))],
        }
        (OUTPUT_DIR / "result.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        browser.close()

    assert not console_errors, console_errors
    assert not page_errors, page_errors
    assert not failed_api, failed_api
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
