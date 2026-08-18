from __future__ import annotations

import json
import tempfile
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright

from cad_ai.sop_knowledge.models import RouteSectionDraft
from cad_ai.sop_knowledge.store import SopKnowledgeStore
from cad_ai.sop_knowledge.web import create_builtin_server
from tests.test_sop_knowledge_workflow import SECTION_TYPES, make_identity, make_route


OUTPUT_DIR = Path(__file__).resolve().parent / "screenshots" / "route-reference-files-20260817"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def make_route_with_sections(root: Path) -> int:
    store = SopKnowledgeStore(root / "knowledge.sqlite3")
    store.initialize()
    store.ensure_process_family("test_family", "Route reference browser validation")
    identity = make_identity("ROUTE-REF-UI")
    store.upsert_product(identity, {"class": "cable", "feature": "route-reference-ui"})
    route_id = store.create_route(make_route(identity, 2))
    for section_type in SECTION_TYPES:
        store.create_route_section(
            route_id,
            RouteSectionDraft(
                section_type=section_type,
                content={"Current source": "Awaiting human review"},
                review_state="needs_revision",
            ),
            created_by="browser-validation",
        )
    return route_id


def main() -> int:
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory)
        route_id = make_route_with_sections(root)
        server = create_builtin_server(root / "knowledge.sqlite3", "127.0.0.1", 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base_url = f"http://127.0.0.1:{server.server_address[1]}"
        console_errors: list[str] = []
        page_errors: list[str] = []
        bad_responses: list[str] = []

        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(channel="msedge", headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 900})
                page.on(
                    "console",
                    lambda message: console_errors.append(f"{message.text} @ {message.location}")
                    if message.type == "error"
                    else None,
                )
                page.on("pageerror", lambda error: page_errors.append(str(error)))
                page.on(
                    "response",
                    lambda response: bad_responses.append(f"{response.status} {response.url}")
                    if response.status >= 400
                    else None,
                )

                page.goto(base_url + "/workbench", wait_until="domcontentloaded", timeout=30_000)
                page.wait_for_function("document.querySelector('#routeSelect')?.options.length > 0", timeout=30_000)
                page.locator(".tab[data-tab='route']").click()
                page.wait_for_selector(".route-reference-upload", timeout=10_000)
                page.wait_for_selector("#referenceFileInput", state="attached", timeout=10_000)
                page.screenshot(path=OUTPUT_DIR / "01-route-reference-without-worker.png", full_page=True)

                page.fill("#worker", "browser-reviewer")
                page.locator("#referenceFileInput").set_input_files(
                    {
                        "name": "controlled-bom.pdf",
                        "mimeType": "application/pdf",
                        "buffer": b"%PDF-1.7\ncontrolled BOM for browser validation\n",
                    }
                )
                page.wait_for_selector("text=controlled-bom.pdf", timeout=10_000)
                page.wait_for_selector(".reference-file-state.needs_revision", timeout=10_000)
                page.screenshot(path=OUTPUT_DIR / "02-route-reference-uploaded.png", full_page=True)

                page.on("dialog", lambda dialog: dialog.accept())
                page.locator("[data-reference-confirm]").click()
                page.wait_for_selector(".reference-file-state.confirmed", timeout=10_000)
                page.screenshot(path=OUTPUT_DIR / "03-route-reference-confirmed.png", full_page=True)

                download_link = page.locator("a[download]")
                download_url = download_link.get_attribute("href")
                page.set_viewport_size({"width": 390, "height": 844})
                page.wait_for_timeout(250)
                mobile_overflow = page.evaluate("document.documentElement.scrollWidth > window.innerWidth")
                page.screenshot(path=OUTPUT_DIR / "04-route-reference-mobile.png", full_page=True)
                page.wait_for_timeout(500)
                browser.close()
        finally:
            server.shutdown()
            server.server_close()

        report = {
            "base_url": base_url,
            "route_id": route_id,
            "route_reference_input_visible": True,
            "standard_section_count": len(SECTION_TYPES),
            "download_url": download_url,
            "mobile_horizontal_overflow": mobile_overflow,
            "console_errors": console_errors,
            "page_errors": page_errors,
            "bad_responses": bad_responses,
        }
        (OUTPUT_DIR / "browser-report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if not download_url or not download_url.startswith("/api/reference-files/"):
            return 1
        return 1 if mobile_overflow or console_errors or page_errors or bad_responses else 0


if __name__ == "__main__":
    raise SystemExit(main())
