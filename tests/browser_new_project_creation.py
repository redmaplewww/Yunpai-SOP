from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

from playwright.sync_api import Route, sync_playwright


BASE_URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8787"
OUTPUT_DIR = Path(__file__).resolve().parent / "screenshots" / "new-project-auto-intake-20260818"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_IMAGE = Path(__file__).resolve().parents[1] / "prototypes" / "sop-auto-intake-desktop-v0-intake.png"


def get_json(path: str):
    with urllib.request.urlopen(BASE_URL + path, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


products = get_json("/api/products")
existing_route_id = int(products[0]["latest_route_id"])
preview_payload = {
    "draft_id": "browser-auto-intake-draft",
    "intent": "create_project",
    "assistant_message": "文字和图片已经整理好，正在建立项目草稿。",
    "product_name": "HDMI 图文验收项目",
    "product_code": "BROWSER-AUTO-INTAKE",
    "route_name": "HDMI 图文验收工艺路线",
    "route_summary": "",
    "steps": [
        {"title": "来料核对", "action": "核对来料"},
        {"title": "裁线", "action": "按工单裁线"},
        {"title": "剥皮", "action": "剥除外皮"},
        {"title": "端子焊接", "action": "完成端子焊接"},
        {"title": "电测", "action": "连接设备检查"},
        {"title": "包装", "action": "完成成品包装"},
    ],
    "image_count": 1,
    "image_assignments": [{
        "source_id": "browser-image",
        "original_name": UPLOAD_IMAGE.name,
        "target_step_title": "来料核对",
        "caption": "核对来料",
        "reason": "验收用建议",
        "link_state": "draft",
    }],
    "unassigned_images": [],
    "ie_timing": [{"工序": "裁线", "工时": "35 秒", "状态": "待人工核对"}],
    "unknowns": [{"label": "包装工序的检查方法待补充", "blocking": False}],
    "warnings": [],
    "duplicate_matches": [],
    "can_create": True,
    "parser_kind": "llm",
    "requires_human_confirmation": True,
}
confirm_payload = {
    "status": "draft_created",
    "route_id": existing_route_id,
    "product_code": "BROWSER-AUTO-INTAKE",
    "product_name": "HDMI 图文验收项目",
    "route_status": "draft",
    "step_count": 6,
    "image_count": 1,
    "linked_image_count": 1,
    "unmatched_image_count": 0,
    "ie_timing_count": 1,
    "unknown_count": 1,
    "image_assignments": preview_payload["image_assignments"],
    "review_state": "needs_revision",
    "document": {"preview_status": "ready"},
    "document_error": "",
}


def exercise(page, name: str) -> dict[str, object]:
    console_errors: list[str] = []
    page_errors: list[str] = []
    bad_responses: list[str] = []
    captured_preview: list[dict[str, object]] = []
    confirm_calls = 0

    page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    page.on(
        "response",
        lambda response: bad_responses.append(f"{response.status} {response.url}")
        if response.status >= 400
        else None,
    )

    def preview(route: Route) -> None:
        payload = route.request.post_data_json
        captured_preview.append(payload)
        source_id = payload["images"][0]["source_id"]
        body = {**preview_payload, "image_assignments": [{**preview_payload["image_assignments"][0], "source_id": source_id}]}
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body, ensure_ascii=False))

    def confirm(route: Route) -> None:
        nonlocal confirm_calls
        confirm_calls += 1
        route.fulfill(status=200, content_type="application/json", body=json.dumps(confirm_payload, ensure_ascii=False))

    page.route("**/api/projects/preview", preview)
    page.route("**/api/projects/confirm", confirm)
    page.goto(BASE_URL + "/", wait_until="domcontentloaded", timeout=30_000)
    page.wait_for_function("document.querySelector('#product')?.options.length > 0", timeout=30_000)
    page.fill("#worker", "browser-tester")
    page.click("#newProject")
    page.wait_for_selector("#projectDescription")
    page.fill(
        "#projectDescription",
        "产品是 HDMI 图文验收项目，编号 BROWSER-AUTO-INTAKE。先来料核对，再裁线、剥皮、端子焊接、电测和包装。IE 工时：裁线 35 秒。",
    )
    page.set_input_files("#projectImageInput", str(UPLOAD_IMAGE))
    page.wait_for_selector(".project-image-item")
    page.screenshot(path=OUTPUT_DIR / f"{name}-01-intake.png", full_page=True)

    dialog_box = page.locator(".project-dialog").bounding_box()
    panel_boxes = [item.bounding_box() for item in page.locator(".project-intake-panel").all()]
    style_snapshot = page.evaluate(
        """() => {
            const style = selector => getComputedStyle(document.querySelector(selector));
            const grid = style('.project-intake-grid');
            const panel = style('.project-intake-panel');
            const step = style('.project-intake-step');
            const upload = style('.project-upload-zone');
            const input = style('.project-image-input');
            return {
                gridDisplay: grid.display,
                gridColumns: grid.gridTemplateColumns,
                panelBorderWidth: panel.borderTopWidth,
                panelBackground: panel.backgroundColor,
                stepWidth: step.width,
                stepHeight: step.height,
                uploadBorderStyle: upload.borderTopStyle,
                uploadMinHeight: upload.minHeight,
                nativeInputHidden: input.position === 'fixed' && Number.parseFloat(input.left) < -1000,
            };
        }"""
    )
    viewport_width = page.evaluate("window.innerWidth")
    dialog_clipped = bool(
        dialog_box
        and (dialog_box["x"] < 0 or dialog_box["x"] + dialog_box["width"] > viewport_width)
    )
    page.click("#projectOrganize")
    page.wait_for_selector("#projectOpenCreated", timeout=15_000)
    page.screenshot(path=OUTPUT_DIR / f"{name}-02-complete.png", full_page=True)

    request_payload = captured_preview[0] if captured_preview else {}
    image_payload = request_payload.get("images", [{}])[0] if request_payload.get("images") else {}
    return {
        "dialog_box": dialog_box,
        "dialog_clipped": dialog_clipped,
        "panel_widths": [round(box["width"]) for box in panel_boxes if box],
        "intake_styles": style_snapshot,
        "image_cards": page.locator(".project-image-item").count(),
        "preview_calls": len(captured_preview),
        "confirm_calls": confirm_calls,
        "request_has_text": "HDMI 图文验收项目" in str(request_payload.get("description", "")),
        "request_image_count": len(request_payload.get("images", [])),
        "request_image_mime": image_payload.get("mime_type"),
        "request_image_has_base64": len(str(image_payload.get("data_base64", ""))) > 100,
        "complete_visible": page.locator(".project-complete-shell").is_visible(),
        "metrics": page.locator(".project-metric").count(),
        "horizontal_overflow": page.evaluate("document.documentElement.scrollWidth > window.innerWidth"),
        "console_errors": console_errors,
        "page_errors": page_errors,
        "bad_responses": bad_responses,
    }


with sync_playwright() as playwright:
    browser = playwright.chromium.launch(channel="msedge", headless=True)
    report = {}
    for name, width, height in (
        ("compact-687", 687, 507),
        ("desktop-1366", 1366, 768),
        ("desktop-1920", 1920, 1080),
    ):
        page = browser.new_page(viewport={"width": width, "height": height})
        report[name] = exercise(page, name)
        page.close()
    browser.close()

issues = []
for viewport, result in report.items():
    if result["panel_widths"] and min(result["panel_widths"]) < 350:
        issues.append(f"{viewport}: intake columns are too narrow")
    if result["preview_calls"] != 1 or result["confirm_calls"] != 1:
        issues.append(f"{viewport}: one-click preview/confirm flow did not complete")
    if result["request_image_count"] != 1 or not result["request_image_has_base64"]:
        issues.append(f"{viewport}: uploaded image was not sent to preview")
    if result["request_image_mime"] != "image/png":
        issues.append(f"{viewport}: image MIME type was not preserved")
    if not result["request_has_text"] or not result["complete_visible"] or result["metrics"] != 4:
        issues.append(f"{viewport}: intake or completion state is incomplete")
    if result["horizontal_overflow"]:
        issues.append(f"{viewport}: horizontal overflow")
    if result["dialog_clipped"]:
        issues.append(f"{viewport}: project dialog is clipped by the viewport")
    styles = result["intake_styles"]
    if styles["gridDisplay"] != "grid":
        issues.append(f"{viewport}: project intake is not a grid layout")
    if styles["panelBorderWidth"] == "0px" or styles["panelBackground"] == "rgba(0, 0, 0, 0)":
        issues.append(f"{viewport}: project intake panels have no visual container")
    if styles["stepWidth"] != "32px" or styles["stepHeight"] != "32px":
        issues.append(f"{viewport}: project intake step markers are not fixed-size controls")
    if styles["uploadBorderStyle"] != "dashed" or float(styles["uploadMinHeight"].removesuffix("px")) < 100:
        issues.append(f"{viewport}: project image upload zone is not visibly styled")
    if not styles["nativeInputHidden"]:
        issues.append(f"{viewport}: native project image input is visible")
    if result["console_errors"] or result["page_errors"] or result["bad_responses"]:
        issues.append(f"{viewport}: browser errors detected")

final = {"base_url": BASE_URL, "report": report, "issues": issues, "screenshots": str(OUTPUT_DIR)}
(OUTPUT_DIR / "browser-report.json").write_text(json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(final, ensure_ascii=False, indent=2))
raise SystemExit(1 if issues else 0)
